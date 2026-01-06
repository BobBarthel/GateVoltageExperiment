from __future__ import annotations

import os
import sys

import matplotlib


def _has_tk() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


def select_backend() -> None:
    if os.environ.get("MPLBACKEND"):
        return

    candidates = []
    if _has_tk():
        candidates.append("TkAgg")
    candidates.append("Qt5Agg")
    if sys.platform == "darwin":
        candidates.append("MacOSX")

    for name in candidates:
        try:
            matplotlib.use(name, force=True)
            return
        except Exception:
            continue


select_backend()
