from __future__ import annotations

from dataclasses import dataclass, fields
from typing import List, Sequence

import ZurichInstruments as zi


@dataclass
class ExperimentOptions:
    voltages: List[float]
    voltage_time_min: float
    repetitions: int
    alternate_with_zero: bool
    single_sweep: bool
    voltage_times_min: List[float] | None = None
    zero_time_leading_min: float | None = None
    zero_times_min: List[float] | None = None


@dataclass
class GateSourceSettings:
    visa_resource: str | None = None
    use_rear_terminals: bool = True
    nplc: float = 10.0
    current_range_a: float | None = 10e-9
    settle_tolerance_v: float = 0.1


@dataclass
class InstrumentSettings:
    """
    Container for Zurich Instruments module-level settings.
    Values default to the constants defined in ZurichInstruments.py.
    """

    server_host: str = zi.SERVER_HOST
    server_port: int = zi.SERVER_PORT
    api_level: int = zi.API_LEVEL
    device_id: str = zi.DEVICE_ID
    freq_start_hz: float = zi.FREQ_START_HZ
    freq_stop_hz: float = zi.FREQ_STOP_HZ
    points_per_sweep: int = zi.SAMPLE_COUNT
    loop_count: int = zi.LOOP_COUNT
    scan_direction: int = zi.SCAN_DIRECTION
    xmapping: int = zi.XMAPPING
    history_length: int = zi.HISTORY_LENGTH
    bandwidth: float = zi.BANDWIDTH
    order: int = zi.ORDER
    settling_inaccuracy: float = zi.SETTLING_INACCURACY
    settling_time: float = zi.SETTLING_TIME
    averaging_tc: float = zi.AVERAGING_TC
    averaging_sample: int = zi.AVERAGING_SAMPLE
    averaging_time: float = zi.AVERAGING_TIME
    filter_mode: int = zi.FILTER_MODE
    max_bandwidth: float = zi.MAX_BANDWIDTH
    bandwidth_overlap: float = zi.BANDWIDTH_OVERLAP
    omega_suppression: float = zi.OMEGA_SUPPRESSION
    phase_unwrap: int = zi.PHASE_UNWRAP
    sinc_filter: int = zi.SINC_FILTER
    awg_control: int = zi.AWG_CONTROL
    endless: int = zi.ENDLESS
    current_range_a: float = zi.CURRENT_RANGE_A
    save_dir: str = zi.SAVE_DIR
    setting_path: str = zi.SETTING_PATH
    save_filename: str = zi.SAVE_FILENAME
    sweep_output_dir: str = zi.SWEEP_OUTPUT_DIR
    sweep_base_name: str = zi.SWEEP_BASE_NAME
    num_sweep_cycles: int = zi.NUM_SWEEP_CYCLES
    progress_poll_s: float = zi.PROGRESS_POLL_S

    def apply_to_module(self) -> None:
        """Copy settings into the ZurichInstruments module variables."""
        mapping = {
            "server_host": "SERVER_HOST",
            "server_port": "SERVER_PORT",
            "api_level": "API_LEVEL",
            "device_id": "DEVICE_ID",
            "freq_start_hz": "FREQ_START_HZ",
            "freq_stop_hz": "FREQ_STOP_HZ",
            "points_per_sweep": "SAMPLE_COUNT",
            "loop_count": "LOOP_COUNT",
            "scan_direction": "SCAN_DIRECTION",
            "xmapping": "XMAPPING",
            "history_length": "HISTORY_LENGTH",
            "bandwidth": "BANDWIDTH",
            "order": "ORDER",
            "settling_inaccuracy": "SETTLING_INACCURACY",
            "settling_time": "SETTLING_TIME",
            "averaging_tc": "AVERAGING_TC",
            "averaging_sample": "AVERAGING_SAMPLE",
            "averaging_time": "AVERAGING_TIME",
            "filter_mode": "FILTER_MODE",
            "max_bandwidth": "MAX_BANDWIDTH",
            "bandwidth_overlap": "BANDWIDTH_OVERLAP",
            "omega_suppression": "OMEGA_SUPPRESSION",
            "phase_unwrap": "PHASE_UNWRAP",
            "sinc_filter": "SINC_FILTER",
            "awg_control": "AWG_CONTROL",
            "endless": "ENDLESS",
            "current_range_a": "CURRENT_RANGE_A",
            "save_dir": "SAVE_DIR",
            "setting_path": "SETTING_PATH",
            "save_filename": "SAVE_FILENAME",
            "sweep_output_dir": "SWEEP_OUTPUT_DIR",
            "sweep_base_name": "SWEEP_BASE_NAME",
            "num_sweep_cycles": "NUM_SWEEP_CYCLES",
            "progress_poll_s": "PROGRESS_POLL_S",
        }

        for field_name, module_name in mapping.items():
            setattr(zi, module_name, getattr(self, field_name))

    @classmethod
    def reset_to_defaults(cls) -> "InstrumentSettings":
        """
        Reset ZurichInstruments module variables to their shipped defaults and
        return a fresh InstrumentSettings instance with those values.
        """
        defaults = cls()
        defaults.apply_to_module()
        return defaults


def parse_voltage_list(raw: str) -> List[float]:
    """Parse comma/space separated voltages."""
    cleaned = raw.translate({ord(c): " " for c in "[]()"})
    tokens = [tok for tok in cleaned.replace(",", " ").split() if tok]
    if not tokens:
        raise ValueError("at least one voltage is required")
    return [float(tok) for tok in tokens]


def parse_float_list(raw: str) -> List[float]:
    """Parse comma/space separated float values."""
    cleaned = raw.translate({ord(c): " " for c in "[]()"})
    tokens = [tok for tok in cleaned.replace(",", " ").split() if tok]
    if not tokens:
        raise ValueError("at least one value is required")
    return [float(tok) for tok in tokens]


def build_voltage_schedule(
    voltages: Sequence[float],
    repetitions: int,
    alternate_with_zero: bool,
    voltage_time_min: float,
    voltage_times_min: Sequence[float] | None = None,
    zero_time_leading_min: float | None = None,
    zero_times_min: Sequence[float] | None = None,
) -> List[dict]:
    """
    Build voltage steps with per-step timings in minutes.
    Returns a list of dicts: {"voltage": float, "time_min": float, "kind": str}.
    """
    if repetitions < 1:
        raise ValueError("repetitions must be >= 1")
    if not voltages:
        raise ValueError("voltages list cannot be empty")

    base_voltages = [float(v) for v in voltages]

    def _validate_time_list(name: str, values: Sequence[float] | None) -> List[float] | None:
        if values is None:
            return None
        cleaned = [float(v) for v in values]
        if len(cleaned) != len(base_voltages):
            raise ValueError(f"{name} must have {len(base_voltages)} entries (one per voltage).")
        for val in cleaned:
            if val < 0:
                raise ValueError(f"{name} entries must be >= 0.")
        return cleaned

    voltage_times = _validate_time_list("voltage_times_min", voltage_times_min)
    zero_times = _validate_time_list("zero_times_min", zero_times_min)
    if zero_time_leading_min is not None and zero_time_leading_min < 0:
        raise ValueError("zero_time_leading_min must be >= 0.")

    def time_for_voltage(idx: int) -> float:
        return voltage_times[idx] if voltage_times is not None else voltage_time_min

    def time_for_zero(idx: int | None = None, *, leading: bool = False) -> float:
        if leading:
            return zero_time_leading_min if zero_time_leading_min is not None else voltage_time_min
        if zero_times is not None and idx is not None:
            return zero_times[idx]
        return voltage_time_min

    schedule: List[dict] = []
    if alternate_with_zero:
        schedule.append({"voltage": 0.0, "time_min": time_for_zero(leading=True), "kind": "leading_zero"})

    for _ in range(repetitions):
        for idx, voltage in enumerate(base_voltages):
            schedule.append({"voltage": voltage, "time_min": time_for_voltage(idx), "kind": "voltage"})
            if alternate_with_zero:
                schedule.append({"voltage": 0.0, "time_min": time_for_zero(idx), "kind": "zero_after"})
    return schedule
