# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Prisma Chill
"""Thread-safe GUI worker bridge.

This module deliberately does not import ``tkinter``.  The worker thread
communicates with the UI exclusively through typed events on a
``queue.Queue``; only the main thread may touch Tk widgets or variables.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass

from ..core import RunOptions, RunSummary, run_batch


@dataclass(frozen=True)
class LogEvent:
    """A line of text to append to the GUI log."""

    text: str
    error: bool = False


@dataclass(frozen=True)
class ProgressEvent:
    """Progress-bar update; index ``done`` of ``total``."""

    done: int
    total: int
    name: str = ""


@dataclass(frozen=True)
class DoneEvent:
    """The worker finished, with either a summary or an unexpected error."""

    summary: RunSummary | None = None
    error: str | None = None


class QueueReporter:
    """Reporter implementation that only writes typed events to a queue."""

    def __init__(self, out_queue: "queue.Queue"):
        self._queue = out_queue

    def start(self, total: int) -> None:
        self._queue.put(LogEvent(f"Processing {total} image file(s)..."))

    def progress(self, done: int, total: int, name: str) -> None:
        self._queue.put(ProgressEvent(done, total, name))

    def log(self, text: str) -> None:
        self._queue.put(LogEvent(text))

    def error(self, text: str) -> None:
        self._queue.put(LogEvent(text, error=True))

    def notice(self, text: str) -> None:
        self._queue.put(LogEvent(text))


def run_worker(options: RunOptions, out_queue: "queue.Queue",
               cancel_event) -> None:
    """Entry point for the worker thread.

    Never raises: unexpected failures become an error log event followed by a
    ``DoneEvent`` so the UI always leaves its busy state.
    """
    reporter = QueueReporter(out_queue)
    summary = None
    error = None
    try:
        summary = run_batch(options, reporter, cancel_event)
    except Exception as exc:
        error = str(exc)
        out_queue.put(LogEvent(f"ERROR: {exc}", error=True))
    finally:
        out_queue.put(DoneEvent(summary, error))
