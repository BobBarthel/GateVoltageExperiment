// Minimal status server for sweep updates.
const http = require("http");

const PORT = process.env.PORT || 6969;
const API_PASSWORD = process.env.STATUS_PASSWORD || "CHANGE_ME_PASSWORD";

const state = {
  currentVoltage: "-",
  timeLeft: "-",
  step: "-",
  totalSteps: "-",
  lastUpdated: null,
  plotSession: null,
  plots: [],
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
    .footer { margin-top: 14px; font-size: 12px; color: #94a3b8; display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
    .btn { padding: 6px 10px; border-radius: 8px; border: 1px solid #1f2937; background: #111827; color: #e2e8f0; text-decoration: none; font-size: 12px; }
    .btn:hover { border-color: #38bdf8; }
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
      <a class="btn" href="/plots">View plots</a>
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

const plotsPage = `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Live Nyquist Plots</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; font-family: "Helvetica Neue", Arial, sans-serif; background: #0b1120; color: #e2e8f0; }
    header { padding: 18px 24px; border-bottom: 1px solid #1f2937; background: rgba(15, 23, 42, 0.8); display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0.4px; }
    main { display: grid; grid-template-columns: 260px 1fr; min-height: calc(100vh - 70px); }
    .panel { padding: 18px; border-right: 1px solid #1f2937; background: rgba(15, 23, 42, 0.6); }
    .panel h2 { margin: 0 0 12px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.12em; color: #94a3b8; }
    .list { display: grid; gap: 8px; max-height: calc(100vh - 170px); overflow: auto; }
    .plot-item { padding: 10px 12px; background: #111827; border: 1px solid #1f2937; border-radius: 10px; cursor: pointer; font-size: 13px; }
    .plot-item.active { border-color: #38bdf8; box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.4); }
    .controls { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .controls input { padding: 6px 8px; border-radius: 8px; border: 1px solid #1f2937; background: #0f172a; color: #e2e8f0; }
    .controls button { padding: 6px 10px; border-radius: 8px; border: 1px solid #1f2937; background: #1f2937; color: #e2e8f0; cursor: pointer; }
    .controls label { font-size: 13px; color: #cbd5f5; display: inline-flex; align-items: center; gap: 6px; }
    .chart-wrap { padding: 18px; }
    canvas { width: 100%; max-height: 78vh; background: #0f172a; border-radius: 12px; border: 1px solid #1f2937; }
    .meta { font-size: 12px; color: #94a3b8; margin-top: 8px; }
  </style>
</head>
<body>
  <header>
    <h1>Live Nyquist Plots</h1>
    <div class="controls">
      <input id="token" type="password" placeholder="Password for plots">
      <button id="save-token">Save</button>
      <label><input id="overlay" type="checkbox">Overlay last</label>
      <input id="overlay-count" type="number" min="1" value="3" style="width:70px">
      <button id="refresh">Refresh</button>
    </div>
  </header>
  <main>
    <aside class="panel">
      <h2>Sweeps</h2>
      <div class="list" id="plot-list"></div>
    </aside>
    <section class="chart-wrap">
      <canvas id="plot"></canvas>
      <div class="meta" id="meta"></div>
    </section>
  </main>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script>
    const list = document.getElementById('plot-list');
    const tokenInput = document.getElementById('token');
    const meta = document.getElementById('meta');
    const overlayToggle = document.getElementById('overlay');
    const overlayCount = document.getElementById('overlay-count');
    const saved = localStorage.getItem('plotToken') || '';
    tokenInput.value = saved;

    let plots = [];
    let sessionId = null;
    let chart;
    let selectedId = null;

    function buildDataset(plot, color, isDim) {
      const points = plot.real.map((x, i) => ({ x, y: -plot.imag[i] }));
      return {
        label: plot.label || plot.id,
        data: points,
        showLine: true,
        borderColor: color,
        backgroundColor: 'transparent',
        borderWidth: 1.4,
        pointRadius: 2,
        pointHoverRadius: 3,
        tension: 0.1,
        hidden: isDim,
      };
    }

    function palette(index) {
      const colors = ['#38bdf8', '#f97316', '#a855f7', '#22c55e', '#facc15', '#f43f5e'];
      return colors[index % colors.length];
    }

    function renderList(activeId) {
      list.innerHTML = '';
      plots.forEach((plot, idx) => {
        const item = document.createElement('div');
        item.className = 'plot-item' + (plot.id === activeId ? ' active' : '');
        item.textContent = plot.label || plot.id;
        item.onclick = () => {
          overlayToggle.checked = false;
          selectedId = plot.id;
          drawPlot(plot.id);
        };
        list.appendChild(item);
      });
    }

    function drawPlot(id) {
      const plot = plots.find(p => p.id === id) || plots[0];
      if (!plot) return;
      let datasets;
      if (overlayToggle.checked) {
        const count = Math.max(1, parseInt(overlayCount.value || '1', 10));
        const recent = plots.slice(-count);
        datasets = recent.map((p, idx) => buildDataset(p, palette(idx), false));
      } else {
        datasets = [buildDataset(plot, palette(0), false)];
      }
      if (!chart) {
        chart = new Chart(document.getElementById('plot'), {
          type: 'scatter',
          data: { datasets },
          options: {
            animation: false,
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: true } },
            scales: {
              x: { title: { display: true, text: 'Re(Z) [Ohm]', color: '#cbd5f5' }, ticks: { color: '#94a3b8' } },
              y: { title: { display: true, text: '-Im(Z) [Ohm]', color: '#cbd5f5' }, ticks: { color: '#94a3b8' } },
            },
          },
        });
      } else {
        chart.data.datasets = datasets;
        chart.update();
      }
      renderList(plot.id);
      const sessionText = sessionId ? \`Session: \${sessionId}\` : 'Session: -';
      const timeText = plot.timestamp ? \`Last update: \${new Date(plot.timestamp).toLocaleString()}\` : '';
      meta.textContent = \`\${sessionText}  \${timeText}\`;
    }

    async function fetchPlots() {
      const token = tokenInput.value.trim();
      const headers = token ? { Authorization: \`Bearer \${token}\` } : {};
      const res = await fetch('/plots-data', { headers });
      if (!res.ok) {
        meta.textContent = 'Unauthorized or no data';
        return;
      }
      const payload = await res.json();
      if (Array.isArray(payload)) {
        plots = payload;
      } else {
        plots = payload.plots || [];
        sessionId = payload.session || null;
      }
      if (!Array.isArray(plots) || plots.length === 0) {
        meta.textContent = 'No plot data yet';
        list.innerHTML = '';
        return;
      }
      const fallbackId = plots[plots.length - 1].id;
      const targetId = selectedId && plots.find(p => p.id === selectedId) ? selectedId : fallbackId;
      drawPlot(targetId);
    }

    document.getElementById('save-token').onclick = () => {
      localStorage.setItem('plotToken', tokenInput.value.trim());
    };
    document.getElementById('refresh').onclick = fetchPlots;
    overlayToggle.onchange = () => drawPlot(plots[plots.length - 1]?.id);
    overlayCount.onchange = () => drawPlot(plots[plots.length - 1]?.id);

    setInterval(fetchPlots, 1000);
    fetchPlots();
  </script>
</body>
</html>`;

function sendJson(res, statusCode, payload) {
  const data = JSON.stringify(payload);
  res.writeHead(statusCode, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(data),
  });
  res.end(data);
}

function unauthorized(res) {
  sendJson(res, 401, { error: "Unauthorized" });
}

function notFound(res) {
  sendJson(res, 404, { error: "Not found" });
}

function handleUpdate(req, res) {
  const authHeader = req.headers["authorization"] || "";
  const token = authHeader.startsWith("Bearer ") ? authHeader.slice(7) : null;
  if (token !== API_PASSWORD) return unauthorized(res);
  console.log("[status] /update request");

  let body = "";
  req.on("data", (chunk) => {
    body += chunk;
    if (body.length > 1e6) req.destroy(); // avoid large bodies
  });
  req.on("end", () => {
    try {
      const payload = body ? JSON.parse(body) : {};
      state.currentVoltage = payload.currentVoltage ?? state.currentVoltage;
      state.timeLeft = payload.timeLeft ?? state.timeLeft;
      state.step = payload.step ?? state.step;
      state.totalSteps = payload.totalSteps ?? state.totalSteps;
      state.lastUpdated = new Date().toISOString();
      sendJson(res, 200, { ok: true, updated: state });
    } catch (err) {
      sendJson(res, 400, { error: "Invalid JSON payload" });
    }
  });
}

function handlePlotUpdate(req, res) {
  const authHeader = req.headers["authorization"] || "";
  const token = authHeader.startsWith("Bearer ") ? authHeader.slice(7) : null;
  if (token !== API_PASSWORD) return unauthorized(res);
  console.log("[plot] /plot_update request");

  let body = "";
  req.on("data", (chunk) => {
    body += chunk;
    if (body.length > 5e7) req.destroy(); // avoid very large bodies
  });
  req.on("end", () => {
    try {
      const payload = body ? JSON.parse(body) : {};
      if (payload.session && payload.session !== state.plotSession) {
        state.plotSession = payload.session;
        state.plots = [];
      }
      if (!Array.isArray(payload.real) || !Array.isArray(payload.imag)) {
        return sendJson(res, 400, { error: "Missing plot data" });
      }
      if (
        payload.id === "session_start" &&
        payload.real.length === 0 &&
        payload.imag.length === 0
      ) {
        return sendJson(res, 200, { ok: true });
      }
      const entryId = payload.id || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const existing = state.plots.find((plot) => plot.id === entryId);
      if (existing) {
        existing.real = payload.real;
        existing.imag = payload.imag;
        existing.label = payload.label || existing.label;
        existing.timestamp = new Date().toISOString();
      } else {
        const entry = {
          id: entryId,
          label: payload.label || payload.id || "sweep",
          real: payload.real,
          imag: payload.imag,
          timestamp: new Date().toISOString(),
        };
        state.plots.push(entry);
      }
      if (state.plots.length > 50) {
        state.plots = state.plots.slice(-50);
      }
      sendJson(res, 200, { ok: true });
    } catch (err) {
      sendJson(res, 400, { error: "Invalid JSON payload" });
    }
  });
}

const server = http.createServer((req, res) => {
  if (req.method === "GET" && req.url === "/") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    return res.end(htmlPage);
  }

  if (req.method === "GET" && req.url === "/status") {
    return sendJson(res, 200, state);
  }

  if (req.method === "GET" && req.url === "/plots") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    return res.end(plotsPage);
  }

  if (req.method === "GET" && req.url === "/plots-data") {
    const authHeader = req.headers["authorization"] || "";
    const token = authHeader.startsWith("Bearer ") ? authHeader.slice(7) : null;
    if (token !== API_PASSWORD) return unauthorized(res);
    console.log("[plot] /plots-data request");
    return sendJson(res, 200, { session: state.plotSession, plots: state.plots });
  }

  if (req.method === "POST" && req.url === "/update") {
    return handleUpdate(req, res);
  }

  if (req.method === "POST" && req.url === "/plot_update") {
    return handlePlotUpdate(req, res);
  }

  return notFound(res);
});

server.listen(PORT, () => {
  console.log(`Status server running on http://localhost:${PORT}`);
});
