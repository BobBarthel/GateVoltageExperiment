#!/usr/bin/env python3
"""
Live Nyquist plot for a Zurich Instruments impedance sweep using LabOne.

- All editable settings are grouped below.
- The sweeper streams data and the Nyquist plot updates as points arrive.
- Handles multiple LabOne result shapes (dict, list-wrapped dict, structured array).
"""

import csv
import os
import time
from typing import Dict, Optional, Tuple

import matplotlib

# Force an interactive backend if the default is non-interactive (e.g., Agg).
if "agg" in matplotlib.get_backend().lower():
    matplotlib.use("TkAgg")

import matplotlib.pyplot as plt
import numpy as np
import zhinst.core

# ----------------------- User-adjustable settings ----------------------- #
SERVER_HOST = "169.254.40.222" #localhost 169.254.40.222
SERVER_PORT = 8004
API_LEVEL = 6
DEVICE_ID = "dev3332"

FREQ_START_HZ = 1.0       # sweep start frequency
FREQ_STOP_HZ = 5000.0     # sweep stop frequency
SAMPLE_COUNT = 100        # number of points per sweep
LOOP_COUNT = 1            # number of sweeps
SCAN_DIRECTION = 3        # 0 forward, 3 reverse

CURRENT_RANGE_A = 1e-4    # 100 uA range
SAVE_DIR = "/Users/bob/Zurich Instruments/LabOne/WebServer"
SETTING_PATH = SAVE_DIR + "/setting"
SAVE_FILENAME = "last_compensation"
OUTPUT_CSV = "impedance_data.csv"  # output file for final data
SWEEP_OUTPUT_DIR = "sweeps"        # directory to store numbered sweep CSVs
SWEEP_BASE_NAME = "testsweep"          # base name for sweep files
NUM_SWEEP_CYCLES = 5               # how many sweeps to run in sequence

XMAPPING = 1              # 0 linear, 1 log
HISTORY_LENGTH = 100
BANDWIDTH = 10
ORDER = 8
SETTLING_INACCURACY = 0.01
SETTLING_TIME = 0
AVERAGING_TC = 15
AVERAGING_SAMPLE = 20
AVERAGING_TIME = 0.1
FILTER_MODE = 0
MAX_BANDWIDTH = 100
BANDWIDTH_OVERLAP = 1
OMEGA_SUPPRESSION = 80
PHASE_UNWRAP = 0
SINC_FILTER = 0
AWG_CONTROL = 0
ENDLESS = 0

PROGRESS_POLL_S = 0.2


# ----------------------------- Helpers --------------------------------- #
def create_daq() -> zhinst.core.ziDAQServer:
    """Connect to the Data Server."""
    return zhinst.core.ziDAQServer(SERVER_HOST, SERVER_PORT, API_LEVEL)


def configure_impedance_module(daq: zhinst.core.ziDAQServer) -> None:
    """Configure and start the impedance compensation module."""
    imp = daq.impedanceModule()
    imp.set("device", DEVICE_ID)
    imp.set("mode", 5)
    imp.set("path", SETTING_PATH)
    imp.set("validation", 1)
    imp.set("filename", SAVE_FILENAME)
    imp.execute()


def configure_sweeper(daq: zhinst.core.ziDAQServer):
    """Set up the sweeper with impedance stream subscription."""
    sweep = daq.sweep()
    sweep.set("device", DEVICE_ID)
    sweep.set("xmapping", XMAPPING)
    sweep.set("historylength", HISTORY_LENGTH)
    sweep.set("samplecount", SAMPLE_COUNT)
    sweep.set("loopcount", LOOP_COUNT)
    sweep.set("gridnode", f"/{DEVICE_ID}/oscs/0/freq")
    sweep.set("bandwidth", BANDWIDTH)
    sweep.set("order", ORDER)
    sweep.set("settling/inaccuracy", SETTLING_INACCURACY)
    sweep.set("settling/time", SETTLING_TIME)
    sweep.set("averaging/tc", AVERAGING_TC)
    sweep.set("averaging/sample", AVERAGING_SAMPLE)
    sweep.set("averaging/time", AVERAGING_TIME)
    sweep.set("filtermode", FILTER_MODE)
    sweep.set("maxbandwidth", MAX_BANDWIDTH)
    sweep.set("bandwidthoverlap", BANDWIDTH_OVERLAP)
    sweep.set("omegasuppression", OMEGA_SUPPRESSION)
    sweep.set("phaseunwrap", PHASE_UNWRAP)
    sweep.set("sincfilter", SINC_FILTER)
    sweep.set("awgcontrol", AWG_CONTROL)
    sweep.set("save/directory", SAVE_DIR)
    sweep.set("start", FREQ_START_HZ)
    sweep.set("stop", FREQ_STOP_HZ)
    sweep.set("scan", SCAN_DIRECTION)
    sweep.set("endless", ENDLESS)
    sweep.subscribe(f"/{DEVICE_ID}/imps/0/sample")
    return sweep


def set_current_range(daq: zhinst.core.ziDAQServer) -> None:
    """Set the impedance current range."""
    daq.set(f"/{DEVICE_ID}/imps/0/current/range", CURRENT_RANGE_A)


def extract_impedance_waves(
    data: Dict,
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """
    Extract freq, Re(Z), Im(Z) from sweeper result; return None if nothing yet.
    """
    path = f"/{DEVICE_ID}/imps/0/sample"
    if not data or path not in data:
        return None
    chunks = data.get(path, [])
    if not chunks:
        return None

    # Normalize chunk into a dict (some LabOne versions return a list)
    raw_chunk = chunks[0]
    if isinstance(raw_chunk, dict):
        chunk = raw_chunk
    elif isinstance(raw_chunk, list) and raw_chunk and isinstance(raw_chunk[0], dict):
        chunk = raw_chunk[0]
    else:
        print(f"Unsupported chunk type: {type(raw_chunk)}")
        return None
    
    # print(f"Chunk header: {chunk.get('header').get('changedtimestamp')[0]}")

    # Case 3: flat dict fields (your chunk keys)
    def _field(name: str) -> np.ndarray:
        arr = np.asarray(chunk.get(name, []), dtype=float)
        if arr.ndim > 1 and arr.shape[0] == 1:
            arr = arr[0]
        return arr

    freq = _field("grid")
    realz = _field("realz")
    imagz = _field("imagz")
    if freq.size and realz.size and imagz.size:
        # print("case 3")
        return freq, realz, imagz

    print(f"No impedance waves parsed; chunk keys: {list(chunk.keys())}")
    return None


def setup_plot():
    """Create and return a live Nyquist plot with current/previous curves."""
    plt.ion()
    fig, ax = plt.subplots()
    line_current, = ax.plot([], [], "o-", lw=1.5, label="Current sweep")
    line_previous, = ax.plot([], [], "o--", lw=1.0, alpha=0.6, label="Previous sweep")
    ax.set_xlabel("Re(Z) [Ohm]")
    ax.set_ylabel("-Im(Z) [Ohm]")
    ax.set_title("Live Nyquist Plot")
    ax.grid(True)
    ax.legend()
    plt.show(block=False)
    fig.show()
    plt.pause(0.05)  # allow window to appear
    return fig, ax, line_current, line_previous


def update_plot(
    fig,
    ax,
    line_current,
    line_previous,
    re_z: np.ndarray,
    im_z: np.ndarray,
    previous_data: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]] = None,
) -> None:
    """Update Nyquist plot with current sweep and optional previous sweep."""
    line_current.set_data(re_z, -im_z)  # Nyquist uses -Im(Z)
    if previous_data:
        _, prev_re, prev_im = previous_data
        line_previous.set_data(prev_re, -prev_im)
    ax.relim()
    ax.autoscale_view()
    fig.canvas.draw()
    fig.canvas.flush_events()
    plt.pause(0.01)


def save_to_csv(
    freq: np.ndarray,
    realz: np.ndarray,
    imagz: np.ndarray,
    filename: str = OUTPUT_CSV,
) -> None:
    """Save frequency, Re(Z), Im(Z) to CSV."""
    header = ["frequency_Hz", "real_z_ohm", "imag_z_ohm"]
    rows = zip(freq, realz, imagz)
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"Saved CSV: {filename}")


def progress_value(sweeper) -> float:
    """Return sweeper progress as float 0..1 handling scalar or array return."""
    prog = sweeper.progress()
    try:
        return float(prog[0])
    except Exception:
        return float(prog)
    
def remaining_value(sweeper) -> float:
    """Return sweeper remaining time in seconds from the module node."""
    try:
        val = sweeper.getDouble("remainingtime")
        return float(val[0]) if hasattr(val, "__len__") else float(val)
    except Exception:
        return float("nan")

def run_single_sweep(
    daq,
    fig,
    ax,
    line_current,
    line_previous,
    previous_data: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]],
    sweep_label: str,
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Run one sweep, update plot (current + previous), return latest data."""
    sweeper = configure_sweeper(daq)
    sweeper.execute()

    latest_data: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]] = None
    has_data = False
    print(f"Starting sweep {sweep_label}...")

    while progress_value(sweeper) < 1.0 and not sweeper.finished():
        time.sleep(PROGRESS_POLL_S)
        result = sweeper.read(True)  # blocking read until new data
        re_im = extract_impedance_waves(result)
        if re_im:
            latest_data = re_im
            has_data = True
            _, re_z, im_z = re_im
            update_plot(fig, ax, line_current, line_previous, re_z, im_z, previous_data)
        print(
            f"Sweep {sweep_label} | Progress {progress_value(sweeper) * 100:.2f} % | Remaining: {remaining_value(sweeper):.2f} s\r",
            end="",
        )

    # Final read to ensure we plot the completed sweep
    result = sweeper.read(True)
    re_im = extract_impedance_waves(result)
    if re_im:
        latest_data = re_im
        has_data = True
        _, re_z, im_z = re_im
        update_plot(fig, ax, line_current, line_previous, re_z, im_z, previous_data)
        print(f"\nSweep {sweep_label} final data plotted.")
    elif not has_data:
        print(f"\nSweep {sweep_label} returned no data.")

    sweeper.finish()
    sweeper.unsubscribe("*")
    return latest_data


def run_live_sweep() -> None:
    """Run one or more sweeps, saving each to CSV and plotting current+previous."""
    os.makedirs(SWEEP_OUTPUT_DIR, exist_ok=True)
    print("Connecting to Data Server...")
    daq = create_daq()
    print("Configuring impedance module...")
    configure_impedance_module(daq)
    print("Setting current range...")
    set_current_range(daq)

    fig, ax, line_current, line_previous = setup_plot()
    previous_data: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]] = None

    for idx in range(1, NUM_SWEEP_CYCLES + 1):
        label = f"{idx}/{NUM_SWEEP_CYCLES}"
        sweep_data = run_single_sweep(
            daq, fig, ax, line_current, line_previous, previous_data, label
        )
        if sweep_data:
            previous_data = sweep_data
            freq, re_z, im_z = sweep_data
            out_name = f"{SWEEP_BASE_NAME}_{idx:03d}.csv"
            out_path = os.path.join(SWEEP_OUTPUT_DIR, out_name)
            save_to_csv(freq, re_z, im_z, filename=out_path)
        else:
            print(f"Sweep {label} produced no data, skipping CSV.")

    plt.ioff()
    plt.show()
    print("All sweeps completed.")


if __name__ == "__main__":
    run_live_sweep()
