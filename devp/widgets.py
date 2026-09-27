"""Small rendering helpers for devp's Textual widgets."""

from __future__ import annotations

from rich.markup import escape

from devp.process import ProcessState

# Braille-based glyphs: they render as plain monospace text in any terminal font,
# unlike symbols such as ● or ✕ that many fonts draw as (wide, colored) emoji.
# Active states cycle through frames; `frame` is a free-running counter.
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_ORBIT = "⠁⠈⠐⠠⢀⡀⠄⠂"

# Colors are theme variables, so the glyphs follow the app theme (Rosé Pine).
_GLYPHS: dict[ProcessState, tuple[str, str]] = {
    ProcessState.STARTING: (_SPINNER, "$accent"),  # waiting for its ready check
    ProcessState.RUNNING: (_SPINNER, "$success"),
    ProcessState.STOPPED: ("⠶", "$foreground 45%"),  # solid muted ($text-muted is translucent)
    ProcessState.STOPPING: (_SPINNER, "$warning"),
    ProcessState.CRASHED: ("✗", "$error"),
    ProcessState.SCHEDULED: (_ORBIT, "$primary"),
}


def is_animated(state: ProcessState) -> bool:
    """Whether `state`'s glyph cycles through frames (and so needs periodic redraws)."""
    return len(_GLYPHS[state][0]) > 1


def status_label(
    name: str, state: ProcessState, detail: str | None = None, frame: int = 0
) -> str:
    """Build the sidebar label's markup: a colored glyph, the name, and an optional detail."""
    frames, color = _GLYPHS[state]
    glyph = frames[frame % len(frames)]
    label = f"[{color}]{glyph}[/] {escape(name)}"
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
