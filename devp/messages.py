"""Textual messages used to hand process events from asyncio callbacks to the app.

Each message carries the `generation` of the process manager that produced it, so the
app can drop events still queued from processes that a config reload has replaced.
"""

from __future__ import annotations

from textual.message import Message

from devp.process import ProcessState


class LogLine(Message):
    """A new line of output was produced by `process_name`."""

    def __init__(self, process_name: str, line: str, generation: int = 0) -> None:
        super().__init__()
        self.process_name = process_name
        self.generation = generation
        self.line = line


class ProcessStateChanged(Message):
    """`process_name` transitioned to a new `ProcessState`."""

    def __init__(self, process_name: str, state: ProcessState, generation: int = 0) -> None:
        super().__init__()
        self.process_name = process_name
        self.generation = generation
        self.state = state


class ProcessError(Message):
    """`process_name` failed to start or exited with an error."""

    def __init__(self, process_name: str, text: str, generation: int = 0) -> None:
        super().__init__()
        self.process_name = process_name
        self.generation = generation
        self.text = text
