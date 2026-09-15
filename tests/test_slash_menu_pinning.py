"""A NUMBERS command must sit in the slash menu's BROWSE view, not only be
findable by typing a prefix of its own name.

WHY
---
``tui_gateway.server._rank_slash_completions`` spends the menu budget in
registry order (``commands[:_SLASH_COMPLETION_LIMIT]``). ``hermes_cli/
commands.py`` registers the NUMBERS commands LAST (positions 101-104 of 105),
so ``/import-hermes``, ``/sign-in``, ``/logout`` and ``/reset`` never appeared
on a bare "/" -- they surfaced only once the typed prefix narrowed the
completion set ("/im"). Meanwhile ``/import`` (position 19) *was* listed, which
is what made the omission look like the menu was answering a different
question. A command a user can only reach by already knowing its name is not
discoverable.

WHERE THIS FILE RUNS
--------------------
Its home is ``<fork>/tests/test_slash_menu_pinning.py``. The copy under
``numbers-dist/overlay/files/tests/`` is the DELIVERY vehicle (apply_overlay.ps1
copies it into the fork); it is not runnable from the distribution repo, because
the modules under test live in the sibling fork checkout and import their own
siblings by bare module name (``hermes_state``, ``agent``, ...). Same shape as
test_branding_sweep.py's skip-when-the-tree-is-absent.

These are invariants, not snapshots: "the pinned list is non-empty, names real
commands, and survives the cap" -- never "the list is exactly X".
"""
from __future__ import annotations

from pathlib import Path

import pytest

_FORK_ROOT = Path(__file__).resolve().parents[1]

if not (_FORK_ROOT / "hermes_cli" / "commands.py").is_file() or not (
    _FORK_ROOT / "tui_gateway" / "server.py"
).is_file():
    pytest.skip(
        f"not running inside the fork checkout (looked in {_FORK_ROOT}); "
        "run this file from <fork>/tests/",
        allow_module_level=True,
    )


def _items(names):
    return [
        {"text": f"/{n}", "display": f"/{n}", "meta": "", "kind": "command"}
        for n in names
    ]


def _usage(_name):
    return 0


def _origin(_name):
    return "bundled"


def test_pinned_commands_survive_the_browse_cap():
    from hermes_cli.commands import PINNED_MENU_COMMANDS
    from tui_gateway.server import _SLASH_COMPLETION_LIMIT, _rank_slash_completions

    filler = [f"fillercmd{i}" for i in range(_SLASH_COMPLETION_LIMIT + 20)]
    ranked = _rank_slash_completions(
        _items(filler + ["import"] + list(PINNED_MENU_COMMANDS)),
        _usage, _origin, browsing=True, score_of=None,
    )
    shown = [item["text"] for item in ranked]

    for name in PINNED_MENU_COMMANDS:
        assert f"/{name}" in shown, f"/{name} is not in the browse view"


def test_pinned_names_are_real_registry_commands():
    from hermes_cli.commands import COMMAND_REGISTRY, PINNED_MENU_COMMANDS

    assert PINNED_MENU_COMMANDS, "pinning an empty list means the menu fix is inert"
    known = {cmd.name for cmd in COMMAND_REGISTRY}
    assert set(PINNED_MENU_COMMANDS) <= known, sorted(set(PINNED_MENU_COMMANDS) - known)


def test_a_non_browse_ranking_is_left_alone():
    """Only the browse view is re-ordered; a typed query keeps its order."""
    from tui_gateway.server import _rank_slash_completions

    ranked = _rank_slash_completions(
        _items(["import", "import-hermes"]), _usage, _origin,
        browsing=False, score_of=None,
    )
    assert [i["text"] for i in ranked] == ["/import", "/import-hermes"]
