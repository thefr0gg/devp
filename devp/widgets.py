"""Small rendering helpers for devp's Textual widgets."""

from __future__ import annotations

from rich.markup import escape

from devp.process import ProcessState

_GLYPHS: dict[ProcessState, tuple[str, str]] = {
    ProcessState.RUNNING: ("●", "green"),
    ProcessState.STOPPED: ("○", "grey50"),
    ProcessState.STOPPING: ("○", "yellow"),
    ProcessState.CRASHED: ("✕", "red"),
}


def status_label(name: str, state: ProcessState) -> str:
    """Build the Rich-markup sidebar label for a process: a colored glyph plus its name."""
    glyph, color = _GLYPHS[state]
    return f"[{color}]{glyph}[/{color}] {escape(name)}"
