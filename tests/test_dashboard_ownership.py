"""A dashboard process is OURS only when its HERMES_HOME is ours.

WHY
---
``_scan_dashboard_processes`` matches cmdline substrings ("hermes dashboard",
"hermes serve") plus the spawn ledger, and she launch path's attach decision is
``_dashboard_listening`` -- a bare TCP-connect probe. Neither can tell two
installs apart on one machine, so ``numbers dashboard --stop`` killed the stock
Hermes dashboard, ``--status`` reported the other install's servers as ours, and
``hermes dashboard`` attached to the NUMBERS UI. Both decisions now consult the
owning process's HERMES_HOME.

An unreadable home stays eligible (the pre-existing behaviour): narrowing the
kill set to "provably ours" would silently stop reaping stale backends on
machines where process-env reads are denied, which is the frontend/backend
mismatch bug the reap exists to fix.

WHERE THIS FILE RUNS
--------------------
Home is ``<fork>/tests/test_dashboard_ownership.py``; the copy under
``numbers-dist/overlay/files/tests/`` is the delivery vehicle and skips where
hermes_cli is not next to it.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

_FORK_ROOT = Path(__file__).resolve().parents[1]

if not (_FORK_ROOT / "hermes_cli" / "dashboard_procs.py").is_file():
    pytest.skip(
        f"not running inside the fork checkout (looked in {_FORK_ROOT}); "
        "run this file from <fork>/tests/",
        allow_module_level=True,
    )

_OURS = r"C:\Users\u\AppData\Local\numbers"
_FOREIGN = r"C:\Users\u\AppData\Local\hermes"


def test_scan_filter_drops_a_foreign_home():
    from hermes_cli import dashboard_procs as dp

    procs = [
        (101, "hermes dashboard", _FOREIGN),
        (202, "hermes dashboard", _OURS),
        (303, "hermes serve", None),
    ]

    kept = dp._filter_processes_by_home(procs, own_home=_OURS)

    assert [pid for pid, _cmd, _home in kept] == [202, 303]


def test_scan_filter_keeps_everything_when_the_home_is_unknown():
    """Unreadable homes stay eligible -- see the module docstring."""
    from hermes_cli import dashboard_procs as dp

    procs = [(1, "hermes dashboard", None), (2, "hermes serve", "")]

    assert dp._filter_processes_by_home(procs, own_home="/tmp/x") == procs


def test_scan_filter_is_case_and_separator_insensitive():
    from hermes_cli import dashboard_procs as dp

    procs = [(9, "hermes dashboard", r"C:\Users\U\AppData\Local\NUMBERS")]

    assert dp._filter_processes_by_home(procs, own_home="c:/users/u/appdata/local/numbers") == procs


def test_scan_filter_without_an_own_home_is_a_no_op():
    """The caller opts in; an unset own_home must not filter anything."""
    from hermes_cli import dashboard_procs as dp

    procs = [(1, "hermes dashboard", _FOREIGN), (2, "hermes dashboard", _OURS)]

    assert dp._filter_processes_by_home(procs, own_home="") == procs


def test_listener_home_returns_none_when_nothing_listens():
    from hermes_cli import dashboard_procs as dp

    # Bind and immediately release a port so the number is known-free.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()

    assert dp._dashboard_listener_home("127.0.0.1", free_port) is None


def test_listener_home_resolves_the_owning_process(monkeypatch, tmp_path):
    """The probe must answer *whose* server is on the port, not just that one is.

    The listener here is this test process itself, so the expected home is the
    HERMES_HOME we just set -- which is exactly the read the real launch path
    performs before it decides to attach.
    """
    psutil = pytest.importorskip("psutil")
    from hermes_cli import dashboard_procs as dp

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        home = dp._dashboard_listener_home("127.0.0.1", port)
    finally:
        sock.close()

    if home is None:
        pytest.skip("platform denies process-env reads for the listener")

    assert Path(home).resolve() == tmp_path.resolve()


def test_scan_accepts_the_own_home_filter():
    """The scan's kwarg is the seam the callers use; keep it keyword-only.

    The filter itself is covered above. This pins the API so a future refactor
    cannot quietly drop the argument and re-widen the kill set.
    """
    from hermes_cli import dashboard_procs as dp

    assert "own_home" in (dp._scan_dashboard_processes.__kwdefaults__ or {})
