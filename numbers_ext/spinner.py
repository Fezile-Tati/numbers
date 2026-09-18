"""One-line progress indicator for the NUMBERS import.

The import can spend seconds per category (skills/plugins trees, profiles)
while printing nothing, which reads as a hang. A spinner turns that into
visible progress -- but ONLY on a real terminal driven by the builtin
``print``: the TUI passes its own print_fn, and redirected output (the
launcher's first-run offer runs under cmd.exe) must stay clean of
carriage-return churn.

Frames are ASCII on purpose: the launcher sets chcp 65001, but the console
font is whatever the user has -- ``|/-\\`` renders everywhere.
"""
from __future__ import annotations

import sys
import threading
from typing import Callable, IO, Optional, TypeVar

T = TypeVar("T")

_FRAMES = "|/-\\"


def run_with_spinner(print_fn: Callable, label: str, work: Callable[[], T], *,
                     interval: float = 0.1,
                     stream: Optional[IO[str]] = None) -> T:
    """Run ``work()`` under a single-line ``label`` indicator and return its result.

    Falls back to a plain one-line ``label...`` announcement (no escape
    codes) whenever the output is not an interactive terminal or the caller
    brought its own print function. The indicator is always erased before
    the call returns -- on success and on failure -- so the caller's next
    line starts clean.
    """
    stream = stream if stream is not None else sys.stdout
    interactive = print_fn is print and getattr(stream, "isatty", lambda: False)()
    if not interactive:
        print_fn(f"  {label}...")
        return work()

    stop = threading.Event()

    def _spin() -> None:
        i = 0
        while True:
            stream.write(f"\r  {_FRAMES[i % len(_FRAMES)]} {label}   ")
            stream.flush()
            i += 1
            if stop.wait(interval):
                return

    thread = threading.Thread(target=_spin, name="numbers-spinner", daemon=True)
    thread.start()
    try:
        return work()
    finally:
        stop.set()
        thread.join(timeout=1.0)
        stream.write("\r" + " " * (len(label) + 8) + "\r")
        stream.flush()
