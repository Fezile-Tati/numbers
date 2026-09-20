"""Ask the front-end a question from a process that does not own the terminal.

WHY THIS EXISTS
---------------
In the Ink TUI a slash command does not run in the UI process. It runs in
``tui_gateway.slash_worker``, a child whose **stdin is the JSON-RPC channel**
and whose stdout is captured with ``redirect_stdout`` and returned as one blob
when the command finishes. So inside that child:

  * ``input()`` reads the RPC pipe, not the user;
  * ``_prompt_text_input`` returns None (no prompt_toolkit app, wrong thread);
  * and None is also what a bare Enter returns, so "could not ask" and "the
    user accepted the default" are the same value.

That is why ``/sign-in`` printed its link and immediately said "Sign-in
cancelled" -- it was never able to ask -- and why ``/import-hermes`` imported
every category without showing its menu.

The fix is not to find a cleverer way to read stdin; there isn't one. It is to
hand the question back up the protocol to whoever owns the terminal. The worker
installs a callback here, the prompt paths look for it first, and the gateway
turns it into a UI prompt and returns the answer.

CONTRACT
--------
``ask()`` returns the user's answer, or None when there is nobody to ask. None
means "unanswered", never "empty answer" -- callers that treat an empty string
as cancel (``run_sign_in``, ``run_reset``) must keep doing so, and must not
mistake an unavailable channel for a decision the user made.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

# Process-global on purpose: the worker serves one command at a time on a single
# thread, and the alternative -- threading a prompt callable through every
# handler signature between here and device_auth -- would touch far more
# upstream code than the fork wants to own.
_lock = threading.Lock()
_asker: Optional[Callable[[str], Optional[str]]] = None


def set_remote_prompt(fn: Optional[Callable[[str], Optional[str]]]) -> None:
    """Install (or clear, with None) the callback that asks the front-end."""
    global _asker
    with _lock:
        _asker = fn


def get_remote_prompt() -> Optional[Callable[[str], Optional[str]]]:
    """The installed callback, or None when this process owns its terminal."""
    with _lock:
        return _asker


def is_active() -> bool:
    """Whether questions must be routed to a front-end rather than to stdin."""
    return get_remote_prompt() is not None


def ask(text: str) -> Optional[str]:
    """Ask the front-end ``text`` and return the answer, or None if unavailable.

    A callback that raises is treated as "no answer" rather than being allowed
    to abort the command: a broken prompt channel must degrade to the old
    behaviour (the caller cancels), not crash a command mid-import.
    """
    fn = get_remote_prompt()
    if fn is None:
        return None
    try:
        return fn(text)
    except Exception:
        return None
