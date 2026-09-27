"""Small rendering helpers for devp's Textual widgets."""

from __future__ import annotations

from rich.markup import escape

from devp.process import ProcessState

_GLYPHS: dict[ProcessState, tuple[str, str]] = {
    ProcessState.RUNNING: ("●", "green"),
    ProcessState.STOPPED: ("○", "grey50"),
    ProcessState.STOPPING: ("○", "yellow"),
    ProcessState.CRASHED: ("✕", "red"),
    ProcessState.SCHEDULED: ("↻", "cyan"),
}


def status_label(name: str, state: ProcessState, detail: str | None = None) -> str:
    """Build the Rich-markup sidebar label: a colored glyph, the name, and an optional detail."""
    glyph, color = _GLYPHS[state]
    label = f"[{color}]{glyph}[/{color}] {escape(name)}"
    if detail:
        label += f" [dim]({escape(detail)})[/dim]"
    return label


def format_duration(seconds: float) -> str:
    """Render a non-negative duration compactly, e.g. '45s', '5m12s', '2h05m', '3d01h'."""
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}h"
