from __future__ import annotations

from typing import Sequence

import matplotlib.pyplot as plt


class SweepPlotter:
    """Lightweight live plot that shows current + previous sweep."""

    def __init__(self) -> None:
        plt.ion()
        self.fig, self.ax = plt.subplots()
        (self.line_current,) = self.ax.plot(
            [],
            [],
            label="Current sweep",
            color="#0057b7",
            marker="o",
            linestyle="-",
            linewidth=1.2,
            markersize=4,
        )
        (self.line_previous,) = self.ax.plot(
            [],
            [],
            label="Previous sweep",
            color="#e26d1b",
            linestyle="--",
            marker="o",
            linewidth=1.0,
            markersize=3,
            alpha=0.5,
        )
        self.ax.set_xlabel("Re(Z) [Ohm]")
        self.ax.set_ylabel("-Im(Z) [Ohm]")
        self.ax.legend()
        self.fig.tight_layout(rect=[0, 0, 1, 0.92])

    def pause(self, seconds: float) -> None:
        plt.pause(seconds)

    def update(
        self,
        real: Sequence[float],
        imag: Sequence[float],
        prev_real: Sequence[float] | None,
        prev_imag: Sequence[float] | None,
        title: str,
    ) -> None:
        # Nyquist view uses -Im(Z)
        self.line_current.set_data(real, [-v for v in imag])

        if prev_real is not None and prev_imag is not None:
            self.line_previous.set_data(prev_real, [-v for v in prev_imag])
            self.line_previous.set_visible(True)
        else:
            self.line_previous.set_visible(False)

        self.ax.relim()
        self.ax.autoscale_view()
        self.ax.set_title(title)

        self.fig.canvas.draw_idle()
        self.fig.canvas.flush_events()
        plt.pause(0.01)
