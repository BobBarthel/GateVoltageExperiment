from __future__ import annotations

import sys
import termios
import time
import tty
from typing import Sequence

from config import ExperimentOptions, GateSourceSettings, InstrumentSettings, parse_voltage_list


class Colors:
    """Minimal ANSI color helper."""

    def __init__(self) -> None:
        self.enabled = sys.stdout.isatty()
        self.blue = "\033[96m" if self.enabled else ""
        self.green = "\033[92m" if self.enabled else ""
        self.yellow = "\033[93m" if self.enabled else ""
        self.red = "\033[91m" if self.enabled else ""
        self.bold = "\033[1m" if self.enabled else ""
        self.reset = "\033[0m" if self.enabled else ""

    def wrap(self, text: str, color: str) -> str:
        return f"{color}{text}{self.reset}" if self.enabled else text


COL = Colors()


def _set_voltages(options: ExperimentOptions, prompt):
    raw = prompt("Voltages (comma/space separated)", str, ",".join(str(v) for v in options.voltages))
    try:
        options.voltages = parse_voltage_list(raw)
    except ValueError as exc:
        print(COL.wrap(f"Invalid voltage list: {exc}", COL.yellow))
        time.sleep(0.7)


def _toggle_scan_direction(settings: InstrumentSettings, prompt_bool) -> None:
    """
    Toggle scan direction between forward (0) and reverse (3).
    If current value is neither, choose based on prompt.
    """
    current_forward = settings.scan_direction == 0
    choice_forward = prompt_bool("Forward sweep? (n selects reverse)", current_forward)
    settings.scan_direction = 0 if choice_forward else 3


def _set_current_range_uA(settings: InstrumentSettings, prompt) -> None:
    """Prompt current range in microamps but store in amps."""
    current_uA = settings.current_range_a * 1e6
    new_val = prompt("Current range (uA)", float, current_uA)
    settings.current_range_a = float(new_val) * 1e-6


def _set_gate_current_range(settings: GateSourceSettings, prompt) -> None:
    """Prompt gate current range; blank or 0 disables explicit setting."""
    current_a = settings.current_range_a if settings.current_range_a is not None else ""
    raw = prompt("Gate current range (A, 0/blank to skip)", str, current_a)
    if raw in ("", None):
        settings.current_range_a = None
        return
    try:
        value = float(raw)
    except Exception:
        print(COL.wrap("Invalid current range, keeping current.", COL.yellow))
        time.sleep(0.7)
        return
    settings.current_range_a = None if value == 0 else value


def clear_screen() -> None:
    """Clear terminal output and move cursor to top-left."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def read_key() -> str:
    """Read a single key (handles arrow keys); falls back to input when not a TTY."""
    if not sys.stdin.isatty():
        return input()
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch1 = sys.stdin.read(1)
        if ch1 == "\x1b":
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                return f"ESC[{ch3}"
            return ch1
        return ch1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def print_order(order: Sequence[float]) -> None:
    formatted = ", ".join(f"{v:g}V" for v in order)
    print(f"Measurement order ({len(order)} steps): {formatted}")


def print_run_options(
    options: ExperimentOptions,
    order: Sequence[float],
    settings: InstrumentSettings,
    gate_settings: GateSourceSettings,
    run_label: str,
    output_dir: str,
    status_server_url: str | None = None,
    status_password_set: bool = False,
) -> None:
    """Print current measurement options so the user sees them even on errors."""
    header = COL.wrap("=== Current measurement setup ===", COL.blue + COL.bold)
    print(header)
    print(f"Run label:          {run_label}")
    print(f"Output directory:   {output_dir}")
    print(f"Device ID:           {COL.wrap(settings.device_id, COL.green)}")
    print(f"Voltages (input):    {COL.wrap(str(options.voltages), COL.green)}")
    print(f"Voltage order:       {COL.wrap(', '.join(f'{v:g}' for v in order), COL.green)}")
    print(f"Voltage time:        {options.voltage_time_min} min per step")
    print(f"Repetitions:         {options.repetitions}")
    print(f"Alternate with zero: {options.alternate_with_zero}")
    print(f"Single sweep mode:   {options.single_sweep}")
    scan_label = "forward" if settings.scan_direction == 0 else "reverse" if settings.scan_direction == 3 else f"custom({settings.scan_direction})"
    current_uA = settings.current_range_a * 1e6
    print(
        f"Zurich: host={settings.server_host} port={settings.server_port} "
        f"freq {settings.freq_start_hz:g}->{settings.freq_stop_hz:g} "
        f"points per sweep={settings.points_per_sweep} "
        f"scan={scan_label} "
        f"bandwidth={settings.bandwidth} current_range={current_uA:.3g} uA"
    )
    gate_label = gate_settings.visa_resource or "(select at run)"
    gate_current = gate_settings.current_range_a if gate_settings.current_range_a is not None else "auto"
    print(
        f"Gate source: visa={gate_label} "
        f"terminals={'rear' if gate_settings.use_rear_terminals else 'front'} "
        f"NPLC={gate_settings.nplc} current_range={gate_current} "
        f"settle_tol={gate_settings.settle_tolerance_v} V"
    )
    status_label = status_server_url or "(disabled)"
    if status_password_set:
        status_label = f"{status_label} (auth set)"
    print(f"Status push:        {status_label}")
    print(COL.wrap("===============================", COL.blue))


def settings_dashboard(
    options: ExperimentOptions,
    settings: InstrumentSettings,
    gate_settings: GateSourceSettings,
    run_config: dict,
    status_msg: str | None = None,
) -> tuple[ExperimentOptions, InstrumentSettings, GateSourceSettings, dict, str]:
    """
    Main settings view with sections for measurement, Zurich instrument, and gate source.
    Returns action: "start", "single", "reset", "list_visa", or "quit".
    """

    def prompt(text: str, cast, current):
        raw = input(f"{text} [{current}]: ").strip()
        if raw == "":
            return current
        try:
            return cast(raw)
        except Exception:
            print(COL.wrap("Invalid input, keeping current.", COL.yellow))
            time.sleep(0.7)
            return current

    def prompt_bool(text: str, current: bool) -> bool:
        val = prompt(text, str, "y" if current else "n")
        return str(val).lower().startswith("y")

    entries = [
        {"kind": "section", "label": "Measurement Settings"},
        {
            "kind": "field",
            "label": "Voltages",
            "getter": lambda: options.voltages,
            "setter": lambda: _set_voltages(options, prompt),
        },
        {
            "kind": "field",
            "label": "Voltage time (min)",
            "getter": lambda: options.voltage_time_min,
            "setter": lambda: setattr(
                options, "voltage_time_min", prompt("Voltage time per step (min)", float, options.voltage_time_min)
            ),
        },
        {
            "kind": "field",
            "label": "Repetitions",
            "getter": lambda: options.repetitions,
            "setter": lambda: setattr(options, "repetitions", prompt("Repetitions", int, options.repetitions)),
        },
        {
            "kind": "field",
            "label": "Alternate with zero",
            "getter": lambda: options.alternate_with_zero,
            "setter": lambda: setattr(
                options, "alternate_with_zero", prompt_bool("Alternate with zero? (y/n)", options.alternate_with_zero)
            ),
        },
        {"kind": "section", "label": "Run Settings"},
        {
            "kind": "field",
            "label": "Run label",
            "getter": lambda: run_config.get("run_label", ""),
            "setter": lambda: run_config.__setitem__("run_label", prompt("Run label", str, run_config.get("run_label", ""))),
        },
        {
            "kind": "field",
            "label": "Output directory",
            "getter": lambda: run_config.get("output_dir", ""),
            "setter": lambda: run_config.__setitem__("output_dir", prompt("Output directory", str, run_config.get("output_dir", ""))),
        },
        {
            "kind": "field",
            "label": "Status server URL",
            "getter": lambda: run_config.get("status_server_url", "") or "(disabled)",
            "setter": lambda: run_config.__setitem__(
                "status_server_url", prompt("Status server URL (blank to disable)", str, run_config.get("status_server_url", ""))
            ),
        },
        {
            "kind": "field",
            "label": "Status password",
            "getter": lambda: "***" if run_config.get("status_password") else "(not set)",
            "setter": lambda: run_config.__setitem__(
                "status_password", prompt("Status password", str, run_config.get("status_password", ""))
            ),
        },
        {"kind": "section", "label": "Zurich Instrument Settings"},
        {
            "kind": "field",
            "label": "DEVICE_ID",
            "getter": lambda: settings.device_id,
            "setter": lambda: setattr(settings, "device_id", prompt("DEVICE_ID", str, settings.device_id)),
        },
        {
            "kind": "field",
            "label": "Server host",
            "getter": lambda: settings.server_host,
            "setter": lambda: setattr(settings, "server_host", prompt("Server host", str, settings.server_host)),
        },
        {
            "kind": "field",
            "label": "Server port",
            "getter": lambda: settings.server_port,
            "setter": lambda: setattr(settings, "server_port", prompt("Server port", int, settings.server_port)),
        },
        {
            "kind": "field",
            "label": "API level",
            "getter": lambda: settings.api_level,
            "setter": lambda: setattr(settings, "api_level", prompt("API level", int, settings.api_level)),
        },
        {
            "kind": "field",
            "label": "Frequency start (Hz)",
            "getter": lambda: settings.freq_start_hz,
            "setter": lambda: setattr(settings, "freq_start_hz", prompt("Frequency start (Hz)", float, settings.freq_start_hz)),
        },
        {
            "kind": "field",
            "label": "Frequency stop (Hz)",
            "getter": lambda: settings.freq_stop_hz,
            "setter": lambda: setattr(settings, "freq_stop_hz", prompt("Frequency stop (Hz)", float, settings.freq_stop_hz)),
        },
        {
            "kind": "field",
            "label": "Points per sweep",
            "getter": lambda: settings.points_per_sweep,
            "setter": lambda: setattr(settings, "points_per_sweep", prompt("Points per sweep", int, settings.points_per_sweep)),
        },
        {
            "kind": "field",
            "label": "Scan direction (forward/reverse)",
            "getter": lambda: "forward" if settings.scan_direction == 0 else "reverse" if settings.scan_direction == 3 else f"custom({settings.scan_direction})",
            "setter": lambda: _toggle_scan_direction(settings, prompt_bool),
        },
        {
            "kind": "field",
            "label": "Current range (uA)",
            "getter": lambda: settings.current_range_a * 1e6,
            "setter": lambda: _set_current_range_uA(settings, prompt),
        },
        {"kind": "section", "label": "Gate Source (Keithley 2450)"},
        {
            "kind": "field",
            "label": "VISA resource",
            "getter": lambda: gate_settings.visa_resource or "(select at run)",
            "setter": lambda: setattr(
                gate_settings,
                "visa_resource",
                (prompt("VISA resource (blank to choose at run)", str, gate_settings.visa_resource or "") or None),
            ),
        },
        {
            "kind": "field",
            "label": "Use rear terminals",
            "getter": lambda: gate_settings.use_rear_terminals,
            "setter": lambda: setattr(
                gate_settings,
                "use_rear_terminals",
                prompt_bool("Use rear terminals? (n selects front)", gate_settings.use_rear_terminals),
            ),
        },
        {
            "kind": "field",
            "label": "NPLC",
            "getter": lambda: gate_settings.nplc,
            "setter": lambda: setattr(gate_settings, "nplc", prompt("NPLC", float, gate_settings.nplc)),
        },
        {
            "kind": "field",
            "label": "Current range (A)",
            "getter": lambda: gate_settings.current_range_a if gate_settings.current_range_a is not None else "auto",
            "setter": lambda: _set_gate_current_range(gate_settings, prompt),
        },
        {
            "kind": "field",
            "label": "Settle tolerance (V)",
            "getter": lambda: gate_settings.settle_tolerance_v,
            "setter": lambda: setattr(
                gate_settings, "settle_tolerance_v", prompt("Settle tolerance (V)", float, gate_settings.settle_tolerance_v)
            ),
        },
        {"kind": "section", "label": "Actions"},
        {"kind": "action", "label": "Reset Zurich settings to defaults", "action": "reset"},
        {"kind": "action", "label": "List VISA resources", "action": "list_visa"},
        {"kind": "action", "label": "Start measurement", "action": "start"},
        {"kind": "action", "label": "Run single sweep test", "action": "single"},
        {"kind": "action", "label": "Quit", "action": "quit"},
    ]

    def next_selectable(idx: int, delta: int) -> int:
        new_idx = idx
        while True:
            new_idx = (new_idx + delta) % len(entries)
            if entries[new_idx]["kind"] != "section":
                return new_idx

    idx = next_selectable(0, 1)
    while True:
        clear_screen()
        print(COL.wrap("Settings (↑/↓ or j/k, Enter to edit/run)", COL.blue + COL.bold))
        if status_msg:
            print(COL.wrap(status_msg, COL.yellow))
        print()

        for i, entry in enumerate(entries):
            kind = entry["kind"]
            if kind == "section":
                print(COL.wrap(f"[{entry['label']}]", COL.blue + COL.bold))
                continue
            prefix = "➜ " if i == idx else "  "
            if kind == "field":
                val = entry["getter"]()
                text = f"{entry['label']}: {val}"
            else:
                text = entry["label"]
            if i == idx:
                text = COL.wrap(text, COL.green)
            print(prefix + text)

        key = read_key()
        if key in ("ESC[A", "k"):
            idx = next_selectable(idx, -1)
        elif key in ("ESC[B", "j"):
            idx = next_selectable(idx, 1)
        elif key in ("\r", "\n"):
            entry = entries[idx]
            if entry["kind"] == "field":
                entry["setter"]()
            elif entry["kind"] == "action":
                return options, settings, gate_settings, run_config, entry["action"]
        elif key in ("q", "\x1b"):
            return options, settings, gate_settings, run_config, "quit"
