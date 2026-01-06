from __future__ import annotations

import time
from typing import Callable, Dict, Optional, Tuple

import numpy as np

import ZurichInstruments as zi
from config import InstrumentSettings


# Cached timebase (seconds per tick) read from the instrument.
TIMEBASE_DT: float | None = None


def _read_timebase(daq, device_id: str) -> float | None:
    """Read device timebase (seconds per tick) for timestamp conversion."""
    try:
        dt_val = daq.getDouble(f"/{device_id}/system/properties/timebase")
        dt = float(dt_val[0]) if hasattr(dt_val, "__len__") else float(dt_val)
        print(f"[debug] timebase read: {dt} s/tick")
        return dt
    except Exception as exc:
        print(f"[debug] failed to read timebase: {exc}")
        return None


def prepare_instrument(settings: InstrumentSettings):
    """Connect to the data server and configure the impedance module."""
    global TIMEBASE_DT
    settings.apply_to_module()
    daq = zi.create_daq()
    zi.configure_impedance_module(daq)
    zi.set_current_range(daq)
    TIMEBASE_DT = _read_timebase(daq, settings.device_id)
    return daq


def get_timebase_dt() -> float | None:
    """Expose cached timebase value (seconds per tick)."""
    return TIMEBASE_DT


def _to_data_dict(
    freq: np.ndarray,
    realz: np.ndarray,
    imagz: np.ndarray,
    timestamps: Optional[np.ndarray] = None,
    meta: Optional[Dict[str, np.ndarray]] = None,
) -> Dict[str, list[float]]:
    magnitude = np.sqrt(np.square(realz) + np.square(imagz))
    # Prefer primary timestamps if they align with frequency, else fall back to nexttimestamp.
    time_axis: Optional[np.ndarray] = None
    time_source = None
    if timestamps is not None and timestamps.size == freq.size:
        time_axis = timestamps
        time_source = "timestamp"
    elif meta:
        nxt = np.ravel(meta.get("nexttimestamp", []))
        if nxt.size == freq.size:
            time_axis = nxt
            time_source = "nexttimestamp"
    # Convert ticks to seconds (relative) if timebase is known.
    time_seconds: Optional[np.ndarray] = None
    time_ticks: Optional[np.ndarray] = None
    if time_axis is not None and TIMEBASE_DT:
        time_ticks = time_axis
        time_seconds = (time_axis - time_axis[0]) * TIMEBASE_DT

    data = {
        "frequency_Hz": freq.tolist(),
        "Re_Z_Ohm": realz.tolist(),
        "Im_Z_Ohm": imagz.tolist(),
        "abs_Z_Ohm": magnitude.tolist(),
    }
    if time_ticks is not None:
        data["time_ticks_raw"] = time_ticks.tolist()
    if time_seconds is not None:
        data["time_s_raw"] = time_seconds.tolist()
        data["time_s_source"] = time_source or "ticks"
    elif time_axis is not None:
        # Fallback: still store ticks if timebase missing.
        data["time_ticks_raw"] = time_axis.tolist()
        data["time_s_source"] = f"{time_source or 'ticks'} (unconverted)"
    if meta:
        for key, arr in meta.items():
            try:
                data[f"meta_{key}"] = np.ravel(arr).tolist()
            except Exception:
                data[f"meta_{key}"] = []
    return data


def _extract_chunk_with_meta(
    result,
) -> Optional[Tuple[Dict, np.ndarray, np.ndarray, np.ndarray, np.ndarray, Dict[str, np.ndarray]]]:
    """Extract raw chunk plus arrays and metadata; returns None if missing."""
    path = f"/{zi.DEVICE_ID}/imps/0/sample"
    if not result or path not in result:
        return None
    chunks = result.get(path, [])
    if not chunks:
        return None
    raw_chunk = chunks[0]
    if isinstance(raw_chunk, dict):
        chunk = raw_chunk
    elif isinstance(raw_chunk, list) and raw_chunk and isinstance(raw_chunk[0], dict):
        chunk = raw_chunk[0]
    else:
        return None

    def _field(name: str) -> np.ndarray:
        arr = np.asarray(chunk.get(name, []), dtype=float)
        if arr.ndim == 0:
            arr = np.atleast_1d(arr)
        elif arr.ndim > 1:
            arr = np.ravel(arr)
        return arr

    freq = _field("grid")
    realz = _field("realz")
    imagz = _field("imagz")
    timestamps = _field("timestamp")
    meta = {
        "timestamp": timestamps,
        "nexttimestamp": _field("nexttimestamp"),
        "settimestamp": _field("settimestamp"),
        "count": _field("count"),
        "samplecount": _field("samplecount"),
    }
    return chunk, freq, realz, imagz, timestamps, meta


def collect_impedance_sweep(daq) -> Optional[Dict[str, list[float]]]:
    """
    Run a single impedance sweep and return the parsed data.
    Returns None if no data is produced.
    """
    sweeper = zi.configure_sweeper(daq)
    sweeper.execute()

    latest: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, Dict[str, np.ndarray]]] = None
    has_data = False

    while zi.progress_value(sweeper) < 1.0 and not sweeper.finished():
        plotter.pause(zi.PROGRESS_POLL_S)
        result = sweeper.read(True)
        parsed = _extract_chunk_with_meta(result)
        if parsed:
            chunk, freq, realz, imagz, timestamps, meta = parsed
            latest = (freq, realz, imagz, timestamps, meta)
            has_data = True

    result = sweeper.read(True)
    parsed = _extract_chunk_with_meta(result)
    if parsed:
        _, freq, realz, imagz, timestamps, meta = parsed
        latest = (freq, realz, imagz, timestamps, meta)
        has_data = True

    sweeper.finish()
    sweeper.unsubscribe("*")

    if not has_data or latest is None:
        return None

    freq, realz, imagz, timestamps, meta = latest
    return _to_data_dict(freq, realz, imagz, timestamps=timestamps, meta=meta)


def stream_impedance_sweep(
    daq,
    plotter,
    prev_data: Optional[Dict[str, list[float]]],
    title_func: Callable[[], str],
    live_plot_cb: Optional[Callable[[list[float], list[float]], None]] = None,
) -> Optional[Dict[str, list[float]]]:
    """
    Run one sweep while streaming updates to the live plot.
    Returns the latest sweep data dict (or None if no data).
    """
    sweeper = zi.configure_sweeper(daq)
    sweeper.execute()

    latest: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, Dict[str, np.ndarray]]] = None
    has_data = False

    while zi.progress_value(sweeper) < 1.0 and not sweeper.finished():
        time.sleep(zi.PROGRESS_POLL_S)
        result = sweeper.read(True)
        parsed = _extract_chunk_with_meta(result)
        if parsed:
            chunk, freq, realz, imagz, timestamps, meta = parsed
            latest = (freq, realz, imagz, timestamps, meta)
            has_data = True
            real_list = realz.tolist()
            imag_list = imagz.tolist()
            plotter.update(
                real_list,
                imag_list,
                prev_data["Re_Z_Ohm"] if prev_data else None,
                prev_data["Im_Z_Ohm"] if prev_data else None,
                title=title_func(),
            )
            if live_plot_cb:
                live_plot_cb(real_list, imag_list)

    result = sweeper.read(True)
    parsed = _extract_chunk_with_meta(result)
    if parsed:
        _, freq, realz, imagz, timestamps, meta = parsed
        latest = (freq, realz, imagz, timestamps, meta)
        has_data = True
        real_list = realz.tolist()
        imag_list = imagz.tolist()
        plotter.update(
            real_list,
            imag_list,
            prev_data["Re_Z_Ohm"] if prev_data else None,
            prev_data["Im_Z_Ohm"] if prev_data else None,
            title=title_func(),
        )
        if live_plot_cb:
            live_plot_cb(real_list, imag_list)

    sweeper.finish()
    sweeper.unsubscribe("*")

    if not has_data or latest is None:
        return None

    freq, realz, imagz, timestamps, meta = latest
    return _to_data_dict(freq, realz, imagz, timestamps=timestamps, meta=meta)
