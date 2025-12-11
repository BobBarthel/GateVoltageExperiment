from __future__ import annotations

import csv
import os
import time
from typing import Dict, List, Sequence, Any, Protocol, runtime_checkable

from ui import COL


def build_voltage_order(
    voltages: Sequence[float], repetitions: int, alternate_with_zero: bool
) -> List[float]:
    """
    Build the exact gate order.

    Examples:
        voltages=[10, -10], repetitions=1 -> 0, 10, 0, -10, 0
        voltages=[10, -10], repetitions=2 -> 0, 10, 0, -10, 0, 10, 0, -10, 0
    """
    if repetitions < 1:
        raise ValueError("repetitions must be >= 1")
    if not voltages:
        raise ValueError("voltages list cannot be empty")

    if not alternate_with_zero:
        return [float(v) for _ in range(repetitions) for v in voltages]

    sequence: List[float] = []
    for _ in range(repetitions):
        for v in voltages:
            if not sequence:
                sequence.append(0.0)
            sequence.append(float(v))
            sequence.append(0.0)
    return sequence


def format_seconds(seconds: float) -> str:
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes:02d}:{secs:02d}"


def sanitize_voltage_for_filename(voltage: float) -> str:
    """Create a filesystem-safe voltage label."""
    return f"{voltage:+.3f}V".replace("+", "p").replace("-", "m").replace(".", "d")


def save_sweep_csv(
    voltage: float,
    step_index: int,
    sweep_index: int,
    data: Dict[str, Sequence[float]],
    measurement_elapsed: float,
    output_dir: str,
    *,
    timebase_dt: float | None = None,
    sweep_settings: Dict[str, Any] | None = None,
    run_id: str | None = None,
) -> str:
    """Save a single sweep to CSV with time, frequency, Re(Z), Im(Z)."""
    freq = data["frequency_Hz"]
    real = data["Re_Z_Ohm"]
    imag = data["Im_Z_Ohm"]
    time_col = data.get("time_s_raw") or [measurement_elapsed] * len(freq)
    time_source = data.get("time_s_source", "measurement_elapsed")
    ticks = data.get("time_ticks_raw")
    tick_start_sec = tick_end_sec = None
    if ticks and timebase_dt:
        tick_start_sec = ticks[0] * timebase_dt
        tick_end_sec = ticks[-1] * timebase_dt

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    base_prefix = f"{run_id}_" if run_id else ""
    fname = (
        f"{base_prefix}sweep_step{step_index + 1:02d}_sweep{sweep_index:02d}_"
        f"volt_{sanitize_voltage_for_filename(voltage)}_{timestamp}.csv"
    )
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, fname)
    with open(path, "w", newline="") as fh:
        # Comment metadata header
        comment_lines = {
            "voltage_V": voltage,
            "step_index": step_index,
            "sweep_index": sweep_index,
            "time_source": time_source,
            "timebase_dt_s": timebase_dt,
            "tick_start_s": tick_start_sec,
            "tick_end_s": tick_end_sec,
            "measurement_elapsed_s": measurement_elapsed,
        }
        if sweep_settings:
            comment_lines.update(sweep_settings)
        for key, value in comment_lines.items():
            fh.write(f"# {key}: {value}\n")

        writer = csv.writer(fh)
        writer.writerow(["time_s", "frequency_Hz", "Re_Z_Ohm", "Im_Z_Ohm", "measurement_elapsed_s"])
        for idx, (t, f, r, im) in enumerate(zip(time_col, freq, real, imag)):
            writer.writerow([t, f, r, im, measurement_elapsed])
    print(f"Saved sweep to {path}")
    return path


@runtime_checkable
class GateVoltageSource(Protocol):
    """Duck-typed voltage source with voltage set + optional wait."""

    def set_voltage(self, voltage: float) -> None: ...
    def set_voltage_and_wait(self, voltage: float, tolerance_v: float, timeout_s: float = 10.0) -> tuple[float, float]: ...


def set_gate_voltage(
    voltage: float,
    source: GateVoltageSource | None = None,
    *,
    tolerance_v: float | None = None,
    timeout_s: float | None = None,
) -> tuple[float | None, float | None]:
    """
    Set the gate voltage using the provided source, falling back to a log message.
    """
    message = f"[Gate source] Setting gate to {voltage:g} V"
    print(COL.wrap(message, COL.green))
    if source is not None:
        if tolerance_v is not None and hasattr(source, "set_voltage_and_wait"):
            meas_v, meas_i = source.set_voltage_and_wait(voltage, tolerance_v, timeout_s or 10.0)
            print(COL.wrap(f"Measured gate: {meas_v:.6g} V, {meas_i:.3e} A", COL.blue))
            return meas_v, meas_i
        else:
            source.set_voltage(voltage)
            meas_v = meas_i = None
    else:
        meas_v = meas_i = None
    return meas_v, meas_i
