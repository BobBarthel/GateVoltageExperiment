#!/usr/bin/env python3
"""
Keithley 2450 helpers for supplying gate voltage.

- Provides a small controller class used by GateVoltage.py.
- Includes VISA discovery helpers and an interactive picker.
"""

from __future__ import annotations

import time
from typing import Sequence

import pyvisa
from pymeasure.instruments.keithley import Keithley2450
from config import GateSourceSettings
from ui import COL, clear_screen, read_key


def _ensure_no_errors(smu: Keithley2450, step: str) -> None:
    """Raise with context if the instrument error queue is non-empty."""
    errors = smu.check_errors() or []
    if errors:
        details = "; ".join(f"{code} {msg}" for code, msg in errors)
        raise RuntimeError(f"{step}: instrument reported error(s): {details}")


def _scalarize(value) -> float:
    """Return a float from a single-value reading that may arrive as a list/tuple."""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if not value:
            return float("nan")
        value = value[0]
    return float(value)


def init_resource_manager() -> pyvisa.ResourceManager:
    """Return a VISA resource manager, falling back to pyvisa-py if NI-VISA is missing."""
    try:
        return pyvisa.ResourceManager()
    except ValueError as exc:
        if "Could not locate a VISA implementation" not in str(exc):
            raise
        try:
            return pyvisa.ResourceManager("@py")
        except Exception as py_exc:
            raise RuntimeError(
                "No VISA implementation found. Install NI-VISA or `pip install pyvisa-py`."
            ) from py_exc


def list_visa_resources() -> list[str]:
    """Return available VISA resources (empty list if none)."""
    rm = init_resource_manager()
    try:
        resources = list(rm.list_resources() or [])
    finally:
        try:
            rm.close()
        except Exception:
            pass
    return resources


def choose_visa_resource(resources: Sequence[str]) -> str:
    """Display VISA resources and let the user pick one with arrow keys."""
    if not resources:
        raise RuntimeError("No VISA resources detected.")

    index = 0
    while True:
        clear_screen()
        print(COL.wrap("Select VISA resource (↑/↓, Enter, q to cancel)", COL.blue + COL.bold))
        for idx, resource in enumerate(resources):
            prefix = "➜ " if idx == index else "  "
            label = COL.wrap(resource, COL.green) if idx == index else resource
            print(prefix + label)

        key = read_key()
        if key in ("ESC[A", "k"):
            index = (index - 1) % len(resources)
        elif key in ("ESC[B", "j"):
            index = (index + 1) % len(resources)
        elif key in ("\r", "\n"):
            return resources[index]
        elif key in ("q", "\x1b"):
            raise KeyboardInterrupt("Selection cancelled by user.")
        else:
            try:
                direct = int(key)
            except ValueError:
                continue
            if 0 <= direct < len(resources):
                index = direct
            elif 1 <= direct <= len(resources):
                index = direct - 1


class Keithley2450GateSource:
    """Small controller wrapper for using the 2450 as a voltage source."""

    def __init__(self, settings: GateSourceSettings) -> None:
        self.settings = settings
        self.smu: Keithley2450 | None = None

    def connect(self) -> None:
        if not self.settings.visa_resource:
            raise RuntimeError("VISA resource must be provided before connecting.")
        self.smu = Keithley2450(self.settings.visa_resource)
        self._configure()

        # Reduce read timeout to avoid blocking on shutdown/readbacks.
        try:
            self.smu.adapter.connection.timeout = 2000  # milliseconds
        except Exception:
            try:
                self.smu.adapter.timeout = 2.0  # seconds
            except Exception:
                pass

    def _configure(self) -> None:
        smu = self._require_smu()
        smu.reset()
        smu.clear()

        if self.settings.use_rear_terminals:
            smu.use_rear_terminals()
            _ensure_no_errors(smu, "rear terminals")

        smu.auto_range_source()
        _ensure_no_errors(smu, "auto range source")

        smu.measure_voltage(nplc=self.settings.nplc, auto_range=True)
        _ensure_no_errors(smu, "configure voltage measurement")

        smu.measure_current(nplc=self.settings.nplc, auto_range=True)
        _ensure_no_errors(smu, "configure current measurement")

        if self.settings.current_range_a:
            try:
                smu.current_range = self.settings.current_range_a
                _ensure_no_errors(smu, f"set current range to {self.settings.current_range_a} A")
            except Exception as exc:  # noqa: BLE001
                print(f"Skipping fine current range (instrument rejected it): {exc}")

        smu.source_voltage = 0.0
        smu.enable_source()
        _ensure_no_errors(smu, "enable source")

        print(
            f"Keithley gate source ready on {self.settings.visa_resource} "
            f"({'rear' if self.settings.use_rear_terminals else 'front'} terminals, "
            f"NPLC={self.settings.nplc})"
        )

    def set_voltage(self, voltage: float) -> None:
        smu = self._require_smu()
        smu.compliance_current = 0.1 
        smu.source_voltage = voltage
        _ensure_no_errors(smu, f"set source voltage to {voltage}")

    def set_voltage_and_wait(self, voltage: float, tolerance_v: float, timeout_s: float = 10.0) -> tuple[float, float]:
        """
        Set voltage and wait until measured voltage is within tolerance.
        Returns (voltage, current) measured when the condition is met (or timeout).
        """
        self.set_voltage(voltage)
        
        start = time.time()
        last_v, last_i = self.read_voltage_current()
        while abs(last_v - voltage) > tolerance_v:
            if (time.time() - start) > timeout_s:
                print(
                    COL.wrap(
                        f"Gate source did not reach {voltage:g} V within {tolerance_v:g} V after {timeout_s}s "
                        f"(last {last_v:.4f} V)",
                        COL.yellow,
                    )
                )
                break
            time.sleep(0.2)
            last_v, last_i = self.read_voltage_current()
        return last_v, last_i

    def read_voltage_current(self) -> tuple[float, float]:
        smu = self._require_smu()
        v = _scalarize(smu.voltage)
        i = _scalarize(smu.current)
        return v, i

    def shutdown(self) -> None:
        smu = self.smu
        if smu is None:
            return

        print("Shutting down Keithley gate source (ramp to 0 V).")
        try:
            smu.disable_source()
            smu.source_voltage = 0.0
            smu.enable_source()
            # Quick, gentle ramp: wait until the instrument reports we are near zero.
            start = time.time()
            while True:
                try:
                    v, _ = self.read_voltage_current()
                except Exception as exc:  # noqa: BLE001
                    print(COL.wrap(f"Gate source read during shutdown failed: {exc}", COL.yellow))
                    break
                if abs(v) < 0.1:
                    break
                if time.time() - start > 5.0:
                    print(COL.wrap(f"Gate source did not reach 0 V after 5s (last {v:.4g} V); proceeding.", COL.yellow))
                    break
                time.sleep(0.25)

            try:
                smu.shutdown()
                _ensure_no_errors(smu, "shutdown")
            except Exception as exc:  # noqa: BLE001
                print(COL.wrap(f"Gate source shutdown command failed: {exc}", COL.yellow))
        finally:
            try:
                smu.adapter.close()
            except Exception:
                pass
            self.smu = None

    def _require_smu(self) -> Keithley2450:
        if self.smu is None:
            raise RuntimeError("Keithley 2450 not connected.")
        return self.smu

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()
