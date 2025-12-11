from __future__ import annotations

from dataclasses import dataclass, fields
from typing import List

import ZurichInstruments as zi


@dataclass
class ExperimentOptions:
    voltages: List[float]
    voltage_time_min: float
    repetitions: int
    alternate_with_zero: bool
    single_sweep: bool


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
