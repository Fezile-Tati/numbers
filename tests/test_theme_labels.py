"""Theme labels are user-visible prose and must not name another product.

WHY
---
The dashboard theme switcher renders the ``label``/``description`` the API
returns from ``_BUILTIN_DASHBOARD_THEMES`` -- NOT the bundle's own copy in
``web/src/themes/presets.ts`` (which was already rebranded). So the header tooltip
read "Switch theme: Hermes Teal" long after the frontend looked correct.

WHY AST AND NOT AN IMPORT
-------------------------
``hermes_cli/web_server.py`` builds a FastAPI app at import time; importing it in
a unit test drags in the whole web stack. The list under test is a module-level
literal, so ``ast.literal_eval`` is exact, instant, and cannot pass on a stale
import -- the same technique ``scripts/check_numbers_parity.py`` uses.

WHERE THIS FILE RUNS
--------------------
Home is ``<fork>/tests/test_theme_labels.py``; the copy under
``numbers-dist/overlay/files/tests/`` is the delivery vehicle and skips where the
server module is not next to it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SERVER = Path(__file__).resolve().parents[1] / "hermes_cli" / "web_server.py"
# "Nous" alone must be here: the `nous-blue` theme's label is "Nous Blue", which
# contains neither "Hermes" nor "Nous Research" and slipped through the first
# version of this guard -- while the dashboard header showed it to every user.
_UPSTREAM_NOUNS = ("Hermes", "Nous")


def _themes():
    tree = ast.parse(_SERVER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "_BUILTIN_DASHBOARD_THEMES" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    pytest.fail("_BUILTIN_DASHBOARD_THEMES not found in hermes_cli/web_server.py")


if not _SERVER.is_file():
    pytest.skip(
        f"dashboard server not next to this test (looked in {_SERVER}); "
        "run this file from <fork>/tests/",
        allow_module_level=True,
    )


def test_no_user_visible_upstream_branding_in_theme_labels():
    findings = [
        f"{theme['name']}: {key}={value!r}"
        for theme in _themes()
        for key in ("label", "description")
        for value in (theme.get(key) or "",)
        if any(noun in value for noun in _UPSTREAM_NOUNS)
    ]

    assert not findings, (
        "user-visible theme branding survived:\n  "
        + "\n  ".join(findings)
        + "\n\nRebrand the label/description only -- `name` is a config value "
          "(dashboard.theme) and a lookup key in web/src/themes/presets.ts."
    )


def test_theme_names_are_left_alone():
    """Names are config values and BUILTIN_THEMES keys: never rebrand them."""
    names = {theme["name"] for theme in _themes()}

    assert {"default", "default-large", "nous-blue", "midnight", "ember", "mono"} <= names
