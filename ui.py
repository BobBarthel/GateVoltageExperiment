from __future__ import annotations

import sys
import time
from pathlib import Path
import re
import os
try:
    import termios
    import tty
except ImportError:  # Windows
    termios = None
    tty = None

try:
    import msvcrt  # type: ignore
except ImportError:
    msvcrt = None
from typing import Sequence

from config import (
    ExperimentOptions,
    GateSourceSettings,
    InstrumentSettings,
    build_voltage_schedule,
    parse_float_list,
    parse_voltage_list,
)


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
HEADER_ART = None
UI_VERSION = "v0.0.4"
UI_VERSION_DATE = "2026-01-16"  # set manually to match the build/version date
SIDE_PAD = 20  # spaces (~2 tabs) to inset content from both sides


def _visible_len(text: str) -> int:
    """Return printable length by removing ANSI codes."""
    return len(re.sub(r"\x1b\[[0-9;]*m", "", text))


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _format_duration(seconds: float) -> str:
    total = int(round(max(0, seconds)))
    hrs = total // 3600
    mins = (total % 3600) // 60
    secs = total % 60
    if hrs:
        return f"{hrs}h {mins:02d}m"
    return f"{mins:02d}m {secs:02d}s"


def _estimate_steps_and_time(options: ExperimentOptions) -> tuple[int, float]:
    """Estimate total steps and seconds based on options."""
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
    except ValueError:
        return 0, 0.0
    total_seconds = sum(step["time_min"] * 60.0 for step in schedule)
    return len(schedule), total_seconds


def _highlight_hotkeys(chunks: list[str]) -> str:
    """
    Build a string where plain text is blue+bold and hotkeys are yellow+bold.
    Provide chunks alternating as [plain, hotkey, plain, hotkey, ...].
    """
    out = []
    is_hotkey = False
    for part in chunks:
        if is_hotkey:
            out.append(COL.wrap(part, COL.yellow + COL.bold))
        else:
            out.append(COL.wrap(part, COL.blue + COL.bold))
        is_hotkey = not is_hotkey
    return "".join(out)


def _header_bounds(lines: list[str]) -> tuple[int, int]:
    """
    Find the leftmost and rightmost non-space character positions (0-based)
    across all lines (after stripping ANSI). Returns (left, right); if no
    content exists, returns (0, -1).
    """
    left = float("inf")
    right = -1
    for line in lines:
        clean = _strip_ansi(line).rstrip("\n")
        if not clean.strip():
            continue
        first = len(clean) - len(clean.lstrip(" "))
        last = len(clean.rstrip(" ")) - 1
        left = min(left, first)
        right = max(right, last)
    if right < 0:
        return 0, -1
    return int(left), int(right)


def _load_header_art() -> str:
    global HEADER_ART
    if HEADER_ART is not None:
        return HEADER_ART
    path = Path("header-img-ascii.txt")
    if path.exists():
        try:
            raw = path.read_text(errors="ignore").rstrip("\n")
            # Allow literal escape markers like "\x1b" or "\033" to become real ANSI codes.
            raw = raw.replace("\\x1b", "\x1b").replace("\\033", "\x1b")
            HEADER_ART = raw.rstrip()
        except Exception:
            HEADER_ART = ""
    else:
        HEADER_ART = ""
    return HEADER_ART


def _set_voltages(options: ExperimentOptions, prompt):
    raw = prompt("Voltages (comma/space separated)", str, ",".join(str(v) for v in options.voltages))
    try:
        options.voltages = parse_voltage_list(raw)
    except ValueError as exc:
        print(COL.wrap(f"Invalid voltage list: {exc}", COL.yellow))
        time.sleep(0.7)


def _format_optional_list(values: Sequence[float] | None) -> str:
    if not values:
        return "(use default)"
    return ", ".join(f"{v:g}" for v in values)


def _format_schedule_table(options: ExperimentOptions) -> list[str]:
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
    except ValueError as exc:
        return [f"(invalid schedule: {exc})"]
    if not schedule:
        return ["(none)"]

    rows = []
    for idx, step in enumerate(schedule, start=1):
        rows.append([str(idx), f"{step['voltage']:g}", f"{step['time_min']:g}"])

    headers = ["Step", "Voltage (V)", "Time (min)"]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(sep: str = "-") -> str:
        return "+" + "+".join(sep * (w + 2) for w in widths) + "+"

    out = [line()]
    out.append("|" + "|".join(f" {headers[i].ljust(widths[i])} " for i in range(len(headers))) + "|")
    out.append(line())
    for row in rows:
        out.append("|" + "|".join(f" {row[i].rjust(widths[i])} " for i in range(len(row))) + "|")
    out.append(line())
    return out


def _prompt_optional_float(label: str, current: float | None) -> float | None:
    current_label = "" if current is None else f"{current:g}"
    raw = input(f"{label} [{current_label or 'default'}]: ").strip()
    if raw == "":
        return current
    if raw.lower() in ("default", "none", "clear"):
        return None
    try:
        return float(raw)
    except Exception:
        print(COL.wrap("Invalid input, keeping current.", COL.yellow))
        time.sleep(0.7)
        return current


def _prompt_optional_list(label: str, current: Sequence[float] | None) -> list[float] | None:
    current_label = "" if not current else ",".join(f"{v:g}" for v in current)
    raw = input(f"{label} [{current_label or 'default'}]: ").strip()
    if raw == "":
        return list(current) if current else None
    if raw.lower() in ("default", "none", "clear"):
        return None
    try:
        return parse_float_list(raw)
    except ValueError as exc:
        print(COL.wrap(f"Invalid list: {exc}", COL.yellow))
        time.sleep(0.7)
        return list(current) if current else None


def _render_schedule_table(options: ExperimentOptions) -> None:
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
    except ValueError as exc:
        print(COL.wrap(f"Cannot build schedule: {exc}", COL.red))
        input("Press Enter to return...")
        return

    if not schedule:
        print(COL.wrap("No schedule steps to display.", COL.yellow))
        input("Press Enter to return...")
        return

    rows = []
    for idx, step in enumerate(schedule, start=1):
        rows.append([str(idx), f"{step['voltage']:g}", f"{step['time_min']:g}"])

    headers = ["Step", "Voltage (V)", "Time (min)"]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(sep: str = "-") -> str:
        return "+" + "+".join(sep * (w + 2) for w in widths) + "+"

    print(line())
    header_line = "|" + "|".join(f" {headers[i].ljust(widths[i])} " for i in range(len(headers))) + "|"
    print(header_line)
    print(line())
    for row in rows:
        print("|" + "|".join(f" {row[i].rjust(widths[i])} " for i in range(len(row))) + "|")
    print(line())
    total_min = sum(step["time_min"] for step in schedule)
    print(f"Total: {total_min:g} min, {len(schedule)} steps")
    input("Press Enter to return...")


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
    # Windows: use msvcrt for key reads to support arrows without blocking stdin.
    if msvcrt:
        first = msvcrt.getch()
        if first in (b"\x00", b"\xe0"):
            second = msvcrt.getch()
            code = second.decode("ascii", errors="ignore")
            mapping = {"H": "ESC[A", "P": "ESC[B", "K": "ESC[D", "M": "ESC[C"}
            return mapping.get(code, code)
        try:
            return first.decode("ascii")
        except Exception:
            return ""

    if not sys.stdin.isatty() or termios is None or tty is None:
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
    print("Schedule:")
    for line in _format_schedule_table(options):
        print(f"  {COL.wrap(line, COL.green)}")
    print(f"Voltage order:       {COL.wrap(', '.join(f'{v:g}' for v in order), COL.green)}")
    print(f"Default step time:   {options.voltage_time_min} min per step")
    print(f"Per-voltage times:   {_format_optional_list(options.voltage_times_min)} min")
    if options.alternate_with_zero:
        lead_zero = f"{options.zero_time_leading_min:g}" if options.zero_time_leading_min is not None else "(use default)"
        print(f"Leading zero time:   {lead_zero} min")
        print(f"Zero-after times:    {_format_optional_list(options.zero_times_min)} min")
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
            "label": "Default step time (min)",
            "getter": lambda: options.voltage_time_min,
            "setter": lambda: setattr(
                options, "voltage_time_min", prompt("Default time per step (min)", float, options.voltage_time_min)
            ),
        },
        {
            "kind": "field",
            "label": "Per-voltage times (min list)",
            "getter": lambda: _format_optional_list(options.voltage_times_min),
            "setter": lambda: setattr(
                options,
                "voltage_times_min",
                _prompt_optional_list("Per-voltage times (min, list or 'default')", options.voltage_times_min),
            ),
        },
        {
            "kind": "field",
            "label": "Leading zero time (min)",
            "getter": lambda: options.zero_time_leading_min if options.zero_time_leading_min is not None else "(use default)",
            "setter": lambda: setattr(
                options,
                "zero_time_leading_min",
                _prompt_optional_float("Leading zero time (min or 'default')", options.zero_time_leading_min),
            ),
        },
        {
            "kind": "field",
            "label": "Zero-after times (min list)",
            "getter": lambda: _format_optional_list(options.zero_times_min),
            "setter": lambda: setattr(
                options,
                "zero_times_min",
                _prompt_optional_list("Zero-after times (min, list or 'default')", options.zero_times_min),
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
        {
            "kind": "field",
            "label": "Enable live plot",
            "getter": lambda: run_config.get("enable_live_plot", True),
            "setter": lambda: run_config.__setitem__(
                "enable_live_plot", prompt_bool("Enable live plot? (y/n)", run_config.get("enable_live_plot", True))
            ),
        },
        {
            "kind": "field",
            "label": "Enable server plots",
            "getter": lambda: run_config.get("enable_server_plots", True),
            "setter": lambda: run_config.__setitem__(
                "enable_server_plots", prompt_bool("Enable server plots? (y/n)", run_config.get("enable_server_plots", True))
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
        {"kind": "action", "label": "Preview live plot (fake data)", "action": "preview"},
        {"kind": "action", "label": "Preview server plots (fake data)", "action": "preview_server"},
        {"kind": "action", "label": "Show timing schedule table", "action": "schedule"},
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
        header_art = _load_header_art()
        header_lines = header_art.splitlines()
        left_idx, right_idx = _header_bounds(header_lines)
        header_width = right_idx - left_idx + 1 if right_idx >= left_idx else 0
        base_indent = max(0, left_idx if right_idx >= left_idx else 0)
        content_width = max(0, header_width - 2 * SIDE_PAD) if header_width > 0 else 0
        est_steps, est_seconds = _estimate_steps_and_time(options)
        debug_enabled = os.environ.get("DEBUG", "").strip() not in ("", "0", "false", "False")

        def render_header_meta() -> None:
            if header_width <= 0:
                return
            left = UI_VERSION
            right = UI_VERSION_DATE
            inner_space = max(1, header_width - len(left) - len(right))
            pad = " " * base_indent
            print(f"{pad}{left}{' ' * inner_space}{right}")

        def render_line(text: str = "") -> None:
            plain_len = _visible_len(text)
            if header_width > 0:
                pad = " " * base_indent + " " * SIDE_PAD + " " * max(0, (content_width - plain_len) // 2)
                print(pad + text)
            else:
                print(text)

        def render_full_bar() -> None:
            if header_width > 0:
                print(" " * base_indent + "=" * header_width)
            else:
                print("=" * 65)

        def render_item(text: str, selected: bool, show_right_marker: bool = True) -> None:
            left_marker = "▶" if selected else ""
            right_marker = "◀" if selected and show_right_marker else ""
            left_pad = " " * base_indent + " " * SIDE_PAD  # inset to center block visually
            if header_width > 0:
                marker_space = (1 if selected else 0) + (1 if selected and show_right_marker else 0)
                width_for_content = max(0, content_width - marker_space)
                if ":" in text:
                    label, val = text.split(":", 1)
                    label_part = f"{label.strip()}:"
                    val_part = val.strip()
                    label_len = _visible_len(label_part)
                    val_len = _visible_len(val_part)
                    space_len = width_for_content - label_len - val_len
                    if space_len < 1:
                        # If too tight, truncate value.
                        max_val_len = max(0, width_for_content - label_len - 1)
                        if max_val_len > 3 and val_len > max_val_len:
                            val_part = val_part[: max_val_len - 3] + "..."
                            val_len = _visible_len(val_part)
                        space_len = max(1, width_for_content - label_len - val_len)
                    content = f"{label_part}{' ' * space_len}{val_part}"
                else:
                    content = text
                    visible_len = _visible_len(content)
                    if visible_len > width_for_content and width_for_content > 3:
                        content = content[: width_for_content - 3] + "..."
                spaces = max(0, width_for_content - _visible_len(content))
                if selected:
                    print(f"{left_pad}{left_marker}{content}{' ' * spaces}{right_marker}")
                else:
                    print(f"{left_pad}{content}")
            else:
                suffix = f" {right_marker}" if selected and show_right_marker else ""
                prefix = f"{left_marker}" if selected else ""
                print(f"{prefix}{text}{suffix}")
        render_line()
        render_full_bar()
        if header_art:
            render_line()
            print(header_art)
            render_header_meta()
        if est_steps > 0:
            render_line(f"Estimated total time: {COL.wrap(_format_duration(est_seconds), COL.bold)} ({est_steps} steps)")
        render_full_bar()
        render_line()
        render_line(
            _highlight_hotkeys(
                [
                    "Settings (",
                    "↑/↓",
                    " or ",
                    "j/k",
                    " to navigate, ",
                    "Enter",
                    " to edit/run, ", 
                    "q",
                    " to quit)",
                ]
            )
        )
        if status_msg:
            render_line(COL.wrap(status_msg, COL.yellow))
        if debug_enabled:
            render_line(COL.wrap("DEBUG MODE: Instruments disabled, synthetic sweeps active.", COL.yellow))
        render_line()
        

        for i, entry in enumerate(entries):
            kind = entry["kind"]
            if kind == "section":
                render_line()  # blank line before each section
                render_line(COL.wrap(f"[{entry['label']}]", COL.blue + COL.bold))
                continue
            if kind == "field":
                val = entry["getter"]()
                text = f"{entry['label']}: {val}"
            else:
                text = entry["label"]
            if i == idx:
                text = COL.wrap(text, COL.green)
            render_item(text, selected=i == idx, show_right_marker=(kind != "action"))
        render_line()
        render_full_bar()

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
                if entry["action"] == "schedule":
                    clear_screen()
                    _render_schedule_table(options)
                    continue
                return options, settings, gate_settings, run_config, entry["action"]
        elif key in ("q", "\x1b"):
            return options, settings, gate_settings, run_config, "quit"
        
