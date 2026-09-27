"""Textual messages used to hand process events from asyncio callbacks to the app."""

from __future__ import annotations

from textual.message import Message

from devp.process import ProcessState


class LogLine(Message):
    """A new line of output was produced by `process_name`."""

    def __init__(self, process_name: str, line: str) -> None:
        super().__init__()
        self.process_name = process_name
        self.line = line


class ProcessStateChanged(Message):
    """`process_name` transitioned to a new `ProcessState`."""

    def __init__(self, process_name: str, state: ProcessState) -> None:
        super().__init__()
        self.process_name = process_name
        self.state = state


class ProcessError(Message):
    """`process_name` failed to start or exited with an error."""

    def __init__(self, process_name: str, text: str) -> None:
        super().__init__()
        self.process_name = process_name
        self.text = text
