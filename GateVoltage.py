#!/usr/bin/env python3
"""
Gate voltage orchestration for Zurich Instruments sweeps.

- Uses ZurichInstruments.py (new version) for all instrument settings and sweep handling.
- Applies a gate-voltage sequence (with optional zero in between), repeating for
  the requested number of cycles.
- At each voltage it keeps running sweeps until the configured voltage_time expires.
- A single-sweep test mode is available to verify the setup quickly.
- Includes a reset option to restore Zurich settings to their shipped defaults.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import json
from dataclasses import asdict
import random
import os
import sys
import time
from typing import Dict, List, Sequence
import urllib.error
import urllib.request
import urllib.parse

from config import (
    ExperimentOptions,
    GateSourceSettings,
    InstrumentSettings,
    build_voltage_schedule,
    parse_float_list,
    parse_voltage_list,
)
from Keithley import Keithley2450GateSource, choose_visa_resource, list_visa_resources
from sweep_plot import SweepPlotter
from sweep_runner import get_timebase_dt, prepare_instrument, stream_impedance_sweep
from ui import COL, print_order, print_run_options, settings_dashboard
from voltage_plan import format_seconds, save_sweep_csv, set_gate_voltage


def voltage_list_arg(raw: str) -> List[float]:
    try:
        return parse_voltage_list(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def time_list_arg(raw: str) -> List[float]:
    try:
        return parse_float_list(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


SETTINGS_FILE = Path("gate_settings.json")
STATUS_PUSH_TIMEOUT_S = 2.0
DEBUG_MODE = os.environ.get("DEBUG", "").strip() not in ("", "0", "false", "False")


def _sanitize_series(values: object) -> object:
    if not isinstance(values, list):
        return values
    cleaned = []
    for val in values:
        try:
            num = float(val)
            if not math.isfinite(num):
                cleaned.append(None)
            else:
                cleaned.append(num)
        except Exception:
            cleaned.append(None)
    return cleaned


def _finite_prefix(real: List[float], imag: List[float]) -> int:
    max_len = min(len(real), len(imag))
    for idx in range(max_len):
        if not math.isfinite(real[idx]) or not math.isfinite(imag[idx]):
            return idx
    return max_len


def preview_live_plot(plotter: SweepPlotter, sweeps: int = 6, points: int = 60, pause_s: float = 0.2) -> None:
    prev_real: List[float] | None = None
    prev_imag: List[float] | None = None
    points = max(2, points)
    for idx in range(sweeps):
        phase = idx * 0.6
        center = 900.0 + idx * 30.0
        radius = 500.0 + 40.0 * math.sin(phase)
        theta = [i / (points - 1) * math.pi for i in range(points)]
        real = [center + radius * math.cos(t) + random.uniform(-12.0, 12.0) for t in theta]
        imag = [-(radius * (0.9 + 0.1 * math.cos(phase)) * math.sin(t)) + random.uniform(-12.0, 12.0) for t in theta]
        plotter.update(
            real,
            imag,
            prev_real,
            prev_imag,
            title=f"Preview sweep {idx + 1}/{sweeps} (fake data)",
        )
        prev_real = real
        prev_imag = imag
        plotter.pause(pause_s)


class NullPlotter:
    def update(
        self,
        real: Sequence[float],
        imag: Sequence[float],
        prev_real: Sequence[float] | None,
        prev_imag: Sequence[float] | None,
        title: str,
    ) -> None:
        return

    def pause(self, seconds: float) -> None:
        return


class Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data: str) -> int:
        for stream in self._streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()

    def isatty(self) -> bool:
        return any(getattr(stream, "isatty", lambda: False)() for stream in self._streams)


def start_run_logging(log_path: Path) -> tuple[object, object, object]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = Tee(original_stdout, log_file)
    sys.stderr = Tee(original_stderr, log_file)
    return log_file, original_stdout, original_stderr



def preview_server_plots(
    status_config: Dict[str, str] | None,
    sweeps: int = 6,
    points: int = 60,
    pause_s: float = 0.5,
) -> None:
    if not status_config or not status_config.get("plots_enabled", True):
        return
    points = max(2, points)
    session_id = f"preview_{time.strftime('%Y%m%d-%H%M%S')}"
    push_plot_session(
        status_config.get("url") if status_config else None,
        status_config.get("password") if status_config else None,
        session_id,
    )
    for idx in range(sweeps):
        phase = idx * 0.6
        center = 900.0 + idx * 30.0
        radius = 500.0 + 40.0 * math.sin(phase)
        theta = [i / (points - 1) * math.pi for i in range(points)]
        full_real = [center + radius * math.cos(t) + random.uniform(-12.0, 12.0) for t in theta]
        full_imag = [-(radius * (0.9 + 0.1 * math.cos(phase)) * math.sin(t)) + random.uniform(-12.0, 12.0) for t in theta]
        step_size = max(4, points // 6)
        for end_idx in range(step_size, points + step_size, step_size):
            real = full_real[: min(points, end_idx)]
            imag = full_imag[: min(points, end_idx)]
            push_plot_update(
                status_config.get("url") if status_config else None,
                status_config.get("password") if status_config else None,
                {
                    "session": session_id,
                    "id": f"{session_id}_sweep{idx + 1}",
                    "label": f"Preview sweep {idx + 1}/{sweeps}",
                    "real": real,
                    "imag": imag,
                },
            )
            time.sleep(pause_s)


def _fake_sweep_data(sweep_settings: Dict[str, object]) -> Dict[str, List[float]]:
    points = int(sweep_settings.get("points_per_sweep") or 80)
    points = max(10, points)
    f_start = float(sweep_settings.get("freq_start_hz") or 1.0)
    f_stop = float(sweep_settings.get("freq_stop_hz") or 1e6)
    if points > 1:
        step = (f_stop - f_start) / (points - 1)
        freq = [f_start + i * step for i in range(points)]
    else:
        freq = [f_start]
    base = 800.0 + random.uniform(-20.0, 20.0)
    radius = 400.0 + random.uniform(-30.0, 30.0)
    theta = [i / (points - 1) * math.pi for i in range(points)]
    real = [base + radius * math.cos(t) + random.uniform(-6.0, 6.0) for t in theta]
    imag = [-(radius * 0.85 * math.sin(t)) + random.uniform(-6.0, 6.0) for t in theta]
    return {
        "frequency_Hz": freq,
        "Re_Z_Ohm": real,
        "Im_Z_Ohm": imag,
        "time_s_raw": [0.0] * len(freq),
        "time_s_source": "debug",
    }


def load_saved_state() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as fh:
                return json.load(fh)
        except Exception:
            return {}
    return {}


def save_state(
    options: ExperimentOptions,
    settings: InstrumentSettings,
    gate: GateSourceSettings,
    run_label: str,
    output_dir: str,
    status_server_url: str,
    status_password: str,
    enable_live_plot: bool,
    enable_server_plots: bool,
) -> None:
    data = {
        "options": asdict(options),
        "instrument": asdict(settings),
        "gate": asdict(gate),
        "run_label": run_label,
        "output_dir": output_dir,
        "status_server_url": status_server_url,
        "status_password": status_password,
        "enable_live_plot": enable_live_plot,
        "enable_server_plots": enable_server_plots,
    }
    try:
        with open(SETTINGS_FILE, "w") as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        pass


_STATUS_UPDATES_DISABLED = False
_STATUS_UPDATES_DISABLED_REASON = ""


def _disable_status_updates(reason: str) -> None:
    global _STATUS_UPDATES_DISABLED, _STATUS_UPDATES_DISABLED_REASON
    if _STATUS_UPDATES_DISABLED:
        return
    _STATUS_UPDATES_DISABLED = True
    _STATUS_UPDATES_DISABLED_REASON = reason
    print(f"[status] disabling status/plot updates for this run: {reason}")


def push_status_update(url: str | None, password: str | None, payload: Dict[str, object]) -> None:
    """POST the latest sweep status to the Node dashboard; errors are ignored."""
    if _STATUS_UPDATES_DISABLED:
        return
    if not url:
        return
    parsed = urllib.parse.urlparse(url if "://" in url else f"http://{url}")
    if not parsed.scheme or not parsed.netloc:
        return
    target = parsed.geturl().rstrip("/")
    if not target.endswith("/update"):
        target = f"{target}/update"
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    if password:
        headers["Authorization"] = f"Bearer {password}"
    request = urllib.request.Request(target, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=STATUS_PUSH_TIMEOUT_S):
            return
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        _disable_status_updates(str(exc))
        print(f"[status] POST failed to {target}: {exc}")
        return


def push_plot_update(url: str | None, password: str | None, payload: Dict[str, object]) -> None:
    """POST the latest sweep plot data to the Node dashboard; errors are ignored."""
    if _STATUS_UPDATES_DISABLED:
        return
    if not url:
        return
    parsed = urllib.parse.urlparse(url if "://" in url else f"http://{url}")
    if not parsed.scheme or not parsed.netloc:
        return
    target = parsed.geturl().rstrip("/")
    if not target.endswith("/plot_update"):
        target = f"{target}/plot_update"
    safe_payload = dict(payload)
    safe_payload["real"] = _sanitize_series(payload.get("real"))
    safe_payload["imag"] = _sanitize_series(payload.get("imag"))
    body = json.dumps(safe_payload, allow_nan=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    if password:
        headers["Authorization"] = f"Bearer {password}"
    request = urllib.request.Request(target, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=STATUS_PUSH_TIMEOUT_S):
            return
    except urllib.error.HTTPError as exc:
        _disable_status_updates(str(exc))
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            detail = ""
        extra = f" body={detail}" if detail else ""
        print(f"[plot] POST failed to {target}: {exc}{extra}")
        return
    except (urllib.error.URLError, TimeoutError) as exc:
        _disable_status_updates(str(exc))
        print(f"[plot] POST failed to {target}: {exc}")
        return


def push_plot_session(url: str | None, password: str | None, session_id: str) -> None:
    payload = {"session": session_id, "real": [], "imag": [], "id": "session_start", "label": "session_start"}
    push_plot_update(url, password, payload)


def build_parser(
    default_settings: InstrumentSettings,
    default_options: ExperimentOptions,
    default_run_label: str,
    default_output_dir: str,
    default_gate: GateSourceSettings,
    default_status_url: str,
    default_status_password: str,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gate voltage sweeps with Zurich Instruments.")
    parser.add_argument(
        "--voltages",
        type=voltage_list_arg,
        default=default_options.voltages,
        help="Comma/space separated gate voltages (e.g. 10,-10,5,-5).",
    )
    parser.add_argument(
        "--voltage-time",
        type=float,
        default=default_options.voltage_time_min,
        help="Minutes to keep sweeping at each voltage.",
    )
    parser.add_argument(
        "--voltage-times",
        type=time_list_arg,
        default=default_options.voltage_times_min,
        help="Comma/space list of per-voltage durations in minutes (overrides --voltage-time).",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=default_options.repetitions,
        help="How many times to repeat the full voltage cycle.",
    )
    parser.add_argument(
        "--no-alternate-zero",
        action="store_true",
        help="Disable inserting 0 V between voltages.",
    )
    parser.add_argument(
        "--zero-time-leading",
        type=float,
        default=default_options.zero_time_leading_min,
        help="Minutes to sweep at the leading 0 V step (uses --voltage-time when unset).",
    )
    parser.add_argument(
        "--zero-times",
        type=time_list_arg,
        default=default_options.zero_times_min,
        help="Comma/space list of per-voltage zero-step durations in minutes.",
    )
    parser.add_argument(
        "--single-sweep",
        action="store_true",
        help="Run a single sweep using the first voltage in the order.",
    )
    parser.add_argument(
        "--reset-defaults",
        action="store_true",
        help="Reset Zurich instrument settings to ZurichInstruments.py defaults before running.",
    )

    parser.add_argument("--server-host", type=str, default=default_settings.server_host)
    parser.add_argument("--server-port", type=int, default=default_settings.server_port)
    parser.add_argument("--api-level", type=int, default=default_settings.api_level)
    parser.add_argument("--device-id", type=str, default=default_settings.device_id)
    parser.add_argument("--freq-start", type=float, default=default_settings.freq_start_hz)
    parser.add_argument("--freq-stop", type=float, default=default_settings.freq_stop_hz)
    parser.add_argument(
        "--points-per-sweep",
        dest="points_per_sweep",
        type=int,
        default=default_settings.points_per_sweep,
        help="Number of points per sweep (previously sample count).",
    )
    parser.add_argument("--loop-count", type=int, default=default_settings.loop_count)
    parser.add_argument("--scan-direction", type=int, default=default_settings.scan_direction)
    parser.add_argument("--current-range", type=float, default=default_settings.current_range_a)
    parser.add_argument(
        "--output-dir",
        type=str,
        default=default_output_dir,
        help="Parent directory for saved CSVs (default: sweeps).",
    )
    parser.add_argument(
        "--run-label",
        type=str,
        default=default_run_label,
        help="Base label used for both the output folder and sweep filenames (timestamp suffix is added).",
    )
    parser.add_argument(
        "--gate-visa-resource",
        type=str,
        default=default_gate.visa_resource,
        help="VISA resource string for the Keithley gate source (leave blank to pick interactively).",
    )
    parser.add_argument(
        "--gate-front-terminals",
        action="store_true",
        default=not default_gate.use_rear_terminals,
        help="Use the FRONT terminals on the Keithley (default is rear).",
    )
    parser.add_argument(
        "--gate-nplc",
        type=float,
        default=default_gate.nplc,
        help="NPLC used for gate voltage measurement sanity checks.",
    )
    parser.add_argument(
        "--gate-current-range",
        type=float,
        default=default_gate.current_range_a,
        help="Current range for the gate source in Amps (set 0 to skip setting it).",
    )
    parser.add_argument(
        "--gate-settle-tolerance",
        type=float,
        default=default_gate.settle_tolerance_v,
        help="Voltage tolerance (V) before starting sweeps.",
    )
    parser.add_argument(
        "--status-server-url",
        type=str,
        default=default_status_url,
        help="Base URL of the status server (e.g. http://localhost:3000). Leave blank to disable.",
    )
    parser.add_argument(
        "--status-server-password",
        type=str,
        default=default_status_password,
        help="Bearer token for the status server.",
    )

    parser.set_defaults(
        single_sweep=default_options.single_sweep,
        no_alternate_zero=not default_options.alternate_with_zero,
    )

    return parser


def run_single_sweep_at_voltage(
    voltage: float,
    daq,
    plotter: SweepPlotter,
    order: Sequence[float],
    measurement_t0: float,
    output_dir: Path,
    sweep_settings: Dict[str, object],
    run_id: str,
    gate_source: Keithley2450GateSource | None,
    gate_settings: GateSourceSettings,
    status_config: Dict[str, str] | None,
    debug_mode: bool = False,
) -> None:
    set_gate_voltage(voltage, gate_source, tolerance_v=gate_settings.settle_tolerance_v)
    push_status_update(
        status_config.get("url") if status_config else None,
        status_config.get("password") if status_config else None,
        {
            "currentVoltage": f"{voltage:g} V",
            "timeLeft": format_seconds(0.0),
            "step": 1,
            "totalSteps": 1,
        },
    )
    sweep_id = f"{run_id}_single"
    plots_enabled = bool(status_config and status_config.get("plots_enabled", True) and status_config.get("url"))
    last_push_len = 0

    def live_plot_cb(real: List[float], imag: List[float]) -> None:
        nonlocal last_push_len
        if not plots_enabled:
            return
        available = _finite_prefix(real, imag)
        if available - last_push_len < 5:
            return
        last_push_len = available
        real = real[:available]
        imag = imag[:available]
        push_plot_update(
            status_config.get("url"),
            status_config.get("password"),
            {
                "session": run_id,
                "id": sweep_id,
                "label": f"Single sweep {voltage:g} V",
                "real": real,
                "imag": imag,
            },
        )
    if debug_mode:
        print(COL.wrap("[debug] Using synthetic sweep data (no instruments).", COL.yellow))
        sweep_data = _fake_sweep_data(sweep_settings)
    else:
        sweep_data = stream_impedance_sweep(
            daq,
            plotter,
            prev_data=None,
            title_func=lambda: f"Single sweep at {voltage:g} V",
            live_plot_cb=live_plot_cb if plots_enabled else None,
        )
    if not sweep_data:
        raise RuntimeError("No data returned from sweeper.")
    measurement_elapsed = time.time() - measurement_t0
    save_sweep_csv(
        voltage,
        step_index=0,
        sweep_index=1,
        data=sweep_data,
        measurement_elapsed=measurement_elapsed,
        output_dir=str(output_dir),
        timebase_dt=get_timebase_dt(),
        sweep_settings=sweep_settings,
        run_id=run_id,
    )
    if plots_enabled:
        push_plot_update(
            status_config.get("url") if status_config else None,
            status_config.get("password") if status_config else None,
            {
                "session": run_id,
                "id": sweep_id,
                "label": f"Single sweep {voltage:g} V",
                "real": sweep_data["Re_Z_Ohm"],
                "imag": sweep_data["Im_Z_Ohm"],
            },
        )
    plotter.update(
        sweep_data["Re_Z_Ohm"],
        sweep_data["Im_Z_Ohm"],
        None,
        None,
        title=f"Single sweep at {voltage:g} V",
    )
    print_order(order)
    print("Single sweep complete.")


def run_voltage_block(
    voltage: float,
    step_index: int,
    total_steps: int,
    voltage_time_s: float,
    daq,
    plotter: SweepPlotter,
    prev_data: Dict[str, List[float]] | None,
    measurement_t0: float,
    output_dir: Path,
    sweep_settings: Dict[str, object],
    run_id: str,
    gate_source: Keithley2450GateSource | None,
    gate_settings: GateSourceSettings,
    status_config: Dict[str, str] | None,
    debug_mode: bool = False,
) -> Dict[str, List[float]] | None:
    set_gate_voltage(voltage, gate_source, tolerance_v=gate_settings.settle_tolerance_v)
    print(
        f"[{step_index + 1}/{total_steps}] "
        f"Starting {voltage:g} V for {format_seconds(voltage_time_s)}"
    )
    start = time.time()
    sweep_count = 0
    latest_data = prev_data
    plots_enabled = bool(status_config and status_config.get("plots_enabled", True) and status_config.get("url"))

    def send_status(time_left_val: float) -> None:
        if not status_config:
            return
        push_status_update(
            status_config.get("url"),
            status_config.get("password"),
            {
                "currentVoltage": f"{voltage:g} V",
                "timeLeft": format_seconds(max(0.0, time_left_val)),
                "step": step_index + 1,
                "totalSteps": total_steps,
            },
        )

    send_status(voltage_time_s)

    while True:
        elapsed = time.time() - start
        time_left = max(0.0, voltage_time_s - elapsed)
        if sweep_count > 0 and time_left <= 0:
            break

        def title_func() -> str:
            current_elapsed = time.time() - start
            current_left = max(0.0, voltage_time_s - current_elapsed)
            return (
                f"Step {step_index + 1}/{total_steps}  "
                f"Gate={voltage:g} V  "
                f"Time left {format_seconds(current_left)}  "
                f"Order pos {step_index + 1}"
            )

        sweep_id = f"{run_id}_step{step_index + 1}_sweep{sweep_count + 1}"
        last_push_len = 0

        def live_plot_cb(real: List[float], imag: List[float]) -> None:
            nonlocal last_push_len
            if not plots_enabled:
                return
            available = _finite_prefix(real, imag)
            if available - last_push_len < 5:
                return
            last_push_len = available
            real = real[:available]
            imag = imag[:available]
            push_plot_update(
                status_config.get("url"),
                status_config.get("password"),
                {
                    "session": run_id,
                    "id": sweep_id,
                    "label": f"Step {step_index + 1}/{total_steps} sweep {sweep_count + 1} ({voltage:g} V)",
                    "real": real,
                    "imag": imag,
                },
            )
        if debug_mode:
            print(COL.wrap("[debug] Using synthetic sweep data (no instruments).", COL.yellow))
            sweep_data = _fake_sweep_data(sweep_settings)
        else:
            sweep_data = stream_impedance_sweep(
                daq,
                plotter,
                prev_data=prev_data,
                title_func=title_func,
                live_plot_cb=live_plot_cb if plots_enabled else None,
            )
        if not sweep_data:
            print("No data returned from sweeper; stopping this voltage step early.")
            break

        sweep_count += 1
        measurement_elapsed = time.time() - measurement_t0
        save_sweep_csv(
            voltage=voltage,
            step_index=step_index,
            sweep_index=sweep_count,
            data=sweep_data,
            measurement_elapsed=measurement_elapsed,
            output_dir=str(output_dir),
            timebase_dt=get_timebase_dt(),
            sweep_settings=sweep_settings,
            run_id=run_id,
        )
        if plots_enabled:
            push_plot_update(
                status_config.get("url") if status_config else None,
                status_config.get("password") if status_config else None,
                {
                    "session": run_id,
                    "id": sweep_id,
                    "label": f"Step {step_index + 1}/{total_steps} sweep {sweep_count} ({voltage:g} V)",
                    "real": sweep_data["Re_Z_Ohm"],
                    "imag": sweep_data["Im_Z_Ohm"],
                },
            )
        latest_data = sweep_data
        prev_data = sweep_data

        elapsed = time.time() - start
        time_left = max(0.0, voltage_time_s - elapsed)
        print(
            f"[{step_index + 1}/{total_steps}] "
            f"V={voltage:g} V | sweep {sweep_count} | "
            f"time left {format_seconds(time_left)}",
            end="\r",
            flush=True,
        )
        send_status(time_left)
        if debug_mode:
            time.sleep(0.6)
            break
    print()
    send_status(0.0)
    return latest_data


def connect_gate_source(gate_settings: GateSourceSettings) -> Keithley2450GateSource:
    """Connect to the Keithley gate source, prompting for VISA if needed."""
    resource = gate_settings.visa_resource
    if not resource:
        resources = list_visa_resources()
        resource = choose_visa_resource(resources)
        gate_settings.visa_resource = resource

    gate = Keithley2450GateSource(gate_settings)
    gate.connect()
    gate.set_voltage(0.0)
    return gate


def main() -> None:
    saved = load_saved_state()

    default_settings = InstrumentSettings()
    if "instrument" in saved:
        for key, value in saved["instrument"].items():
            if hasattr(default_settings, key):
                setattr(default_settings, key, value)

    default_gate = GateSourceSettings()
    if "gate" in saved:
        for key, value in saved["gate"].items():
            if hasattr(default_gate, key):
                setattr(default_gate, key, value)

    default_options = ExperimentOptions(
        voltages=saved.get("options", {}).get("voltages", [10.0, -10.0]),
        voltage_time_min=saved.get("options", {}).get("voltage_time_min", 30.0),
        repetitions=saved.get("options", {}).get("repetitions", 1),
        alternate_with_zero=saved.get("options", {}).get("alternate_with_zero", True),
        single_sweep=saved.get("options", {}).get("single_sweep", False),
        voltage_times_min=saved.get("options", {}).get("voltage_times_min"),
        zero_time_leading_min=saved.get("options", {}).get("zero_time_leading_min"),
        zero_times_min=saved.get("options", {}).get("zero_times_min"),
    )

    default_run_label = saved.get("run_label", "run")
    default_output_dir = saved.get("output_dir", "sweeps")
    default_status_url = saved.get("status_server_url", "")
    default_status_password = saved.get("status_password", "")
    default_enable_live_plot = saved.get("enable_live_plot", True)
    default_enable_server_plots = saved.get("enable_server_plots", True)

    parser = build_parser(
        default_settings,
        default_options,
        default_run_label,
        default_output_dir,
        default_gate,
        default_status_url,
        default_status_password,
    )
    args = parser.parse_args()

    if args.reset_defaults:
        settings = InstrumentSettings.reset_to_defaults()
    else:
        settings = InstrumentSettings(
            server_host=args.server_host,
            server_port=args.server_port,
            api_level=args.api_level,
            device_id=args.device_id,
            freq_start_hz=args.freq_start,
            freq_stop_hz=args.freq_stop,
            points_per_sweep=args.points_per_sweep,
            loop_count=args.loop_count,
            scan_direction=args.scan_direction,
            current_range_a=args.current_range,
        )

    options = ExperimentOptions(
        voltages=args.voltages,
        voltage_time_min=args.voltage_time,
        repetitions=args.repetitions,
        alternate_with_zero=not args.no_alternate_zero,
        single_sweep=args.single_sweep,
        voltage_times_min=args.voltage_times,
        zero_time_leading_min=args.zero_time_leading,
        zero_times_min=args.zero_times,
    )

    gate_current_range = args.gate_current_range
    if gate_current_range == 0:
        gate_current_range = None

    gate_settings = GateSourceSettings(
        visa_resource=args.gate_visa_resource or None,
        use_rear_terminals=not args.gate_front_terminals,
        nplc=args.gate_nplc,
        current_range_a=gate_current_range,
        settle_tolerance_v=args.gate_settle_tolerance,
    )

    run_config = {
        "run_label": args.run_label,
        "output_dir": args.output_dir,
        "enable_live_plot": default_enable_live_plot,
        "enable_server_plots": default_enable_server_plots,
    }
    run_config["status_server_url"] = args.status_server_url
    run_config["status_password"] = args.status_server_password

    status_msg = None
    while True:
        options, settings, gate_settings, run_config, action = settings_dashboard(
            options, settings, gate_settings, run_config, status_msg=status_msg
        )
        status_msg = None

        if action == "quit":
            save_state(
                options,
                settings,
                gate_settings,
                run_config["run_label"],
                run_config["output_dir"],
                run_config.get("status_server_url", ""),
                run_config.get("status_password", ""),
                bool(run_config.get("enable_live_plot", True)),
                bool(run_config.get("enable_server_plots", True)),
            )
            break
        if action == "reset":
            settings = InstrumentSettings.reset_to_defaults()
            status_msg = COL.wrap("Zurich settings reset to defaults.", COL.green)
            continue
        if action == "list_visa":
            try:
                resources = list_visa_resources()
                if resources:
                    status_msg = COL.wrap("VISA resources: " + ", ".join(resources), COL.green)
                else:
                    status_msg = COL.wrap("No VISA resources detected.", COL.red)
            except Exception as exc:
                status_msg = COL.wrap(f"VISA query failed: {exc}", COL.red)
            continue
        if action == "preview":
            if not run_config.get("enable_live_plot", True):
                status_msg = COL.wrap("Live plot is disabled.", COL.yellow)
                continue
            plotter = SweepPlotter()
            preview_live_plot(plotter)
            status_msg = COL.wrap("Live plot preview complete.", COL.green)
            continue
        if action == "preview_server":
            if not run_config.get("enable_server_plots", True):
                status_msg = COL.wrap("Server plots are disabled.", COL.yellow)
                continue
            status_config = {
                "url": run_config.get("status_server_url"),
                "password": run_config.get("status_password"),
                "plots_enabled": True,
            }
            preview_server_plots(status_config)
            status_msg = COL.wrap("Server plot preview sent.", COL.green)
            continue

        try:
            schedule = build_voltage_schedule(
                options.voltages,
                options.repetitions,
                options.alternate_with_zero,
                options.voltage_time_min,
                options.voltage_times_min,
                options.zero_time_leading_min,
                options.zero_times_min,
            )
            order = [step["voltage"] for step in schedule]
        except ValueError as exc:
            status_msg = COL.wrap(f"Invalid voltage configuration: {exc}", COL.red)
            continue

        timestamp_suffix = time.strftime("%Y%m%d-%H%M%S")
        run_id = f"{run_config['run_label']}_{timestamp_suffix}"
        base_output_dir = Path(run_config["output_dir"]).expanduser()
        run_output_dir = base_output_dir / run_id

        if run_output_dir.exists():
            print(COL.wrap(f"Output folder already exists: {run_output_dir}", COL.yellow))
            choice = input("Create a new folder with a unique timestamp suffix? [Y/n]: ").strip().lower()
            if choice in ("", "y", "yes"):
                ts_suffix = time.strftime("%Y%m%d-%H%M%S")
                run_output_dir = base_output_dir / f"{run_id}_{ts_suffix}"
                print(f"Using new folder: {run_output_dir}")
            else:
                print(f"Reusing existing folder: {run_output_dir}")

        log_file = None
        original_stdout = None
        original_stderr = None
        try:
            log_path = run_output_dir / "run.log"
            log_file, original_stdout, original_stderr = start_run_logging(log_path)
            print(COL.wrap(f"[log] Writing run output to {log_path}", COL.blue))
            if DEBUG_MODE:
                print(COL.wrap("[debug] DEBUG=1: skipping instrument connections and using synthetic sweeps.", COL.yellow))

            print_run_options(
                options,
                order,
                settings,
                gate_settings,
                run_config["run_label"],
                str(run_output_dir),
                status_server_url=run_config.get("status_server_url", ""),
                status_password_set=bool(run_config.get("status_password")),
            )
            options.single_sweep = action == "single"

            gate_source: Keithley2450GateSource | None = None
            if DEBUG_MODE:
                daq = None
            else:
                try:
                    daq = prepare_instrument(settings)
                except Exception as exc:
                    status_msg = f"Connection failed: {exc}"
                    continue
                try:
                    gate_source = connect_gate_source(gate_settings)
                except Exception as exc:
                    status_msg = COL.wrap(f"Gate source error: {exc}", COL.red)
                    continue

            plotter = SweepPlotter() if run_config.get("enable_live_plot", True) else NullPlotter()
            prev_data: Dict[str, List[float]] | None = None
            measurement_t0 = time.time()
            sweep_settings = {
                "device_id": settings.device_id,
                "freq_start_hz": settings.freq_start_hz,
                "freq_stop_hz": settings.freq_stop_hz,
                "points_per_sweep": settings.points_per_sweep,
                "scan_direction": settings.scan_direction,
                "current_range_a": settings.current_range_a,
                "voltage_time_min": options.voltage_time_min,
                "voltage_times_min": options.voltage_times_min,
                "zero_time_leading_min": options.zero_time_leading_min,
                "zero_times_min": options.zero_times_min,
                "repetitions": options.repetitions,
                "alternate_with_zero": options.alternate_with_zero,
                "gate_settle_tolerance_v": gate_settings.settle_tolerance_v,
            }
            status_config = {"url": run_config.get("status_server_url"), "password": run_config.get("status_password")}
            status_config["plots_enabled"] = bool(run_config.get("enable_server_plots", True))
            if status_config.get("url") and status_config.get("plots_enabled"):
                push_plot_session(status_config.get("url"), status_config.get("password"), run_id)

            try:
                if options.single_sweep:
                    run_single_sweep_at_voltage(
                        order[0],
                        daq,
                        plotter,
                        order,
                        measurement_t0,
                        run_output_dir,
                        sweep_settings,
                        run_id,
                        gate_source,
                        gate_settings,
                        status_config,
                        debug_mode=DEBUG_MODE,
                    )
                    status_msg = COL.wrap("Single sweep finished.", COL.green)
                else:
                    total_steps = len(schedule)
                    for idx, step in enumerate(schedule):
                        voltage = step["voltage"]
                        voltage_time_s = step["time_min"] * 60.0
                        prev_data = run_voltage_block(
                            voltage=voltage,
                            step_index=idx,
                            total_steps=total_steps,
                            voltage_time_s=voltage_time_s,
                            daq=daq,
                            plotter=plotter,
                            prev_data=prev_data,
                            measurement_t0=measurement_t0,
                            output_dir=run_output_dir,
                            sweep_settings=sweep_settings,
                            run_id=run_id,
                            gate_source=gate_source,
                            gate_settings=gate_settings,
                            status_config={"url": run_config.get("status_server_url"), "password": run_config.get("status_password")},
                            debug_mode=DEBUG_MODE,
                        )
                    status_msg = COL.wrap("All voltage sweeps completed.", COL.green)
            except KeyboardInterrupt:
                status_msg = COL.wrap("Measurement interrupted by user.", COL.red)
            except Exception as exc:
                status_msg = COL.wrap(f"Run failed: {exc}", COL.red)
            finally:
                try:
                    if gate_source:
                        gate_source.shutdown()
                except Exception as exc:
                    print(COL.wrap(f"Gate source shutdown issue: {exc}", COL.yellow))

            options.single_sweep = False
            save_state(
                options,
                settings,
                gate_settings,
                run_config["run_label"],
                run_config["output_dir"],
                run_config.get("status_server_url", ""),
                run_config.get("status_password", ""),
                bool(run_config.get("enable_live_plot", True)),
                bool(run_config.get("enable_server_plots", True)),
            )
        finally:
            if log_file:
                log_file.close()
            if original_stdout is not None and original_stderr is not None:
                sys.stdout = original_stdout
                sys.stderr = original_stderr


if __name__ == "__main__":
    main()
