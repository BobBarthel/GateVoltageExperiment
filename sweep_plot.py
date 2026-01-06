from __future__ import annotations

from typing import Sequence

import plot_backend  # noqa: F401
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider


class SweepPlotter:
    """Lightweight live plot that shows current + previous sweep."""

    def __init__(self, max_history: int = 6) -> None:
        plt.ion()
        self.fig, self.ax = plt.subplots()
        self._configure_window()
        self._title = self.fig.suptitle("", y=0.98)
        self._history: list[tuple[list[float], list[float]]] = []
        self._max_history = max(1, int(max_history))
        self._history_to_show = 1
        self._last_real: list[float] | None = None
        self._last_imag: list[float] | None = None
        self._last_title: str = ""
        self._pending: tuple[list[float], list[float], str] | None = None
        self._needs_draw = False
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
        self._history_lines = []
        for idx in range(self._max_history):
            label = "History" if idx == 0 else "_nolegend_"
            (line,) = self.ax.plot(
                [],
                [],
                label=label,
                color="#e26d1b",
                linestyle="--",
                marker="o",
                linewidth=1.0,
                markersize=3,
                alpha=0.4,
            )
            line.set_visible(False)
            self._history_lines.append(line)
        self.ax.set_xlabel("Re(Z) [Ohm]")
        self.ax.set_ylabel("-Im(Z) [Ohm]")
        self.ax.legend()
        self._add_slider()
        self.fig.subplots_adjust(left=0.12, right=0.96, top=0.9, bottom=0.18)
        plt.show(block=False)
        self._timer = self.fig.canvas.new_timer(interval=80)
        self._timer.add_callback(self._on_timer)
        self._timer.start()
        self.fig.canvas.draw()
        self.fig.canvas.flush_events()
        plt.pause(0.05)

    def _configure_window(self) -> None:
        manager = getattr(self.fig.canvas, "manager", None)
        if not manager:
            return
        try:
            manager.set_window_title("Live Nyquist Plot")
        except Exception:
            pass
        window = getattr(manager, "window", None)
        if not window:
            return
        if hasattr(window, "resizable"):
            try:
                window.resizable(True, True)
            except Exception:
                pass
    
    def _add_slider(self) -> None:
        slider_ax = self.fig.add_axes([0.2, 0.05, 0.6, 0.03])
        self._history_slider = Slider(
            slider_ax,
            "History",
            0,
            self._max_history,
            valinit=min(self._history_to_show, self._max_history),
            valstep=1,
        )
        self._history_slider.on_changed(self._on_slider_change)

    def _on_slider_change(self, value: float) -> None:
        self._history_to_show = int(value)
        if self._last_real is not None and self._last_imag is not None:
            self._needs_draw = True

    def record_sweep(self, real: Sequence[float], imag: Sequence[float]) -> None:
        self._history.append((list(real), list(imag)))
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]

    def _render_history(self) -> None:
        count = min(self._history_to_show, len(self._history))
        if count <= 0:
            for line in self._history_lines:
                line.set_visible(False)
            return

        history = self._history[-count:]
        for idx, line in enumerate(self._history_lines):
            if idx < count:
                real, imag = history[-(idx + 1)]
                line.set_data(real, [-v for v in imag])
                if count > 1:
                    alpha = 0.2 + 0.6 * (1.0 - idx / (count - 1))
                else:
                    alpha = 0.5
                line.set_alpha(alpha)
                line.set_visible(True)
            else:
                line.set_visible(False)

    def _redraw(self, real: Sequence[float], imag: Sequence[float], title: str) -> None:
        self.line_current.set_data(real, [-v for v in imag])
        self._render_history()
        self.ax.relim()
        self.ax.autoscale_view()
        self._title.set_text(title)
        self.fig.canvas.draw_idle()
        self.fig.canvas.flush_events()
        plt.pause(0.01)

    def _on_timer(self) -> None:
        if self._pending is None and not self._needs_draw:
            return
        if self._pending is not None:
            real, imag, title = self._pending
            self._pending = None
        else:
            real = self._last_real or []
            imag = self._last_imag or []
            title = self._last_title
        self._redraw(real, imag, title)
        self._needs_draw = False

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
        self._last_real = list(real)
        self._last_imag = list(imag)
        self._last_title = title
        self._pending = (self._last_real, self._last_imag, title)
        self._needs_draw = True
