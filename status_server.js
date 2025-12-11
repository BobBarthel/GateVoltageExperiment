// Minimal status server for sweep updates.
const http = require('http');

const PORT = process.env.PORT || 3000;
const API_PASSWORD = process.env.STATUS_PASSWORD || 'CHANGE_ME_PASSWORD';

const state = {
  currentVoltage: '-',
  timeLeft: '-',
  step: '-',
  totalSteps: '-',
  lastUpdated: null,
};

const htmlPage = `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Experiment Status</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; font-family: "Helvetica Neue", Arial, sans-serif; background: #0f172a; color: #e2e8f0; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
    .card { width: min(420px, 90vw); background: rgba(15, 23, 42, 0.8); border: 1px solid #1f2937; border-radius: 14px; padding: 20px 24px 18px; box-shadow: 0 20px 50px rgba(0,0,0,0.35); }
    h1 { margin: 0 0 14px; font-size: 24px; letter-spacing: 0.4px; color: #f8fafc; }
    .grid { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0,1fr)); }
    .item { padding: 12px; border-radius: 10px; background: #111827; border: 1px solid #1f2937; }
    .label { font-size: 12px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.08em; margin-bottom: 6px; }
    .value { font-size: 18px; color: #e2e8f0; font-weight: 600; }
    .footer { margin-top: 14px; font-size: 12px; color: #94a3b8; display: flex; justify-content: space-between; align-items: center; }
    .dot { width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; background: #22c55e; box-shadow: 0 0 0 0 rgba(34,197,94, 0.6); animation: pulse 2s infinite; }
    .status { display: inline-flex; align-items: center; }
    @keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(34,197,94, 0.6);} 70% { box-shadow: 0 0 0 10px rgba(34,197,94, 0);} 100% { box-shadow: 0 0 0 0 rgba(34,197,94, 0);} }
  </style>
</head>
<body>
  <div class="card">
    <h1>Experiment Status</h1>
    <div class="grid">
      <div class="item">
        <div class="label">Current Voltage</div>
        <div class="value" id="voltage">-</div>
      </div>
      <div class="item">
        <div class="label">Time Left</div>
        <div class="value" id="time-left">-</div>
      </div>
      <div class="item">
        <div class="label">Step</div>
        <div class="value" id="step">-</div>
      </div>
      <div class="item">
        <div class="label">Last Updated</div>
        <div class="value" id="updated">-</div>
      </div>
    </div>
    <div class="footer">
      <div class="status"><span class="dot"></span>Live</div>
      <div id="updated-ago">waiting...</div>
    </div>
  </div>
  <script>
    async function refresh() {
      try {
        const res = await fetch('/status');
        if (!res.ok) throw new Error('Failed to load status');
        const data = await res.json();
        document.getElementById('voltage').textContent = data.currentVoltage ?? '-';
        document.getElementById('time-left').textContent = data.timeLeft ?? '-';
        document.getElementById('step').textContent = (data.step && data.totalSteps) ? \`\${data.step}/\${data.totalSteps}\` : '-';
        const updated = data.lastUpdated ? new Date(data.lastUpdated) : null;
        document.getElementById('updated').textContent = updated ? updated.toLocaleTimeString() : '-';
        document.getElementById('updated-ago').textContent = updated ? timeAgo(updated) : 'waiting...';
      } catch (err) {
        document.getElementById('updated-ago').textContent = 'disconnected';
      }
    }

    function timeAgo(date) {
      const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
      if (seconds < 60) return \`\${seconds}s ago\`;
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) return \`\${minutes}m ago\`;
      const hours = Math.floor(minutes / 60);
      return \`\${hours}h ago\`;
    }

    setInterval(refresh, 2000);
    refresh();
  </script>
</body>
</html>`;

function sendJson(res, statusCode, payload) {
  const data = JSON.stringify(payload);
  res.writeHead(statusCode, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) });
  res.end(data);
}

function unauthorized(res) {
  sendJson(res, 401, { error: 'Unauthorized' });
}

function notFound(res) {
  sendJson(res, 404, { error: 'Not found' });
}

function handleUpdate(req, res) {
  const authHeader = req.headers['authorization'] || '';
  const token = authHeader.startsWith('Bearer ') ? authHeader.slice(7) : null;
  if (token !== API_PASSWORD) return unauthorized(res);

  let body = '';
  req.on('data', chunk => {
    body += chunk;
    if (body.length > 1e6) req.destroy(); // avoid large bodies
  });
  req.on('end', () => {
    try {
      const payload = body ? JSON.parse(body) : {};
      state.currentVoltage = payload.currentVoltage ?? state.currentVoltage;
      state.timeLeft = payload.timeLeft ?? state.timeLeft;
      state.step = payload.step ?? state.step;
      state.totalSteps = payload.totalSteps ?? state.totalSteps;
      state.lastUpdated = new Date().toISOString();
      sendJson(res, 200, { ok: true, updated: state });
    } catch (err) {
      sendJson(res, 400, { error: 'Invalid JSON payload' });
    }
  });
}

const server = http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    return res.end(htmlPage);
  }

  if (req.method === 'GET' && req.url === '/status') {
    return sendJson(res, 200, state);
  }

  if (req.method === 'POST' && req.url === '/update') {
    return handleUpdate(req, res);
  }

  return notFound(res);
});

server.listen(PORT, () => {
  console.log(`Status server running on http://localhost:${PORT}`);
});
