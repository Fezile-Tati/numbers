"""Shared ANSI escape constants for NUMBERS CLI output.

``cli._cprint`` renders ANSI escapes, NOT Rich markup -- passing a string like
``"[red]...[/]"`` through it prints those literal characters at the user
instead of coloring anything. Every call site that colors text through
``_cprint`` (or ``print`` in a plain terminal) must use these constants
instead of Rich-style tags.
"""
from __future__ import annotations

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BOLD_GREEN = "\033[1m\033[32m"
RST = "\033[0m"
