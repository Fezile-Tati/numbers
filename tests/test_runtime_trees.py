"""The vendored runtime must ship every tree the app resolves from disk.

WHY THIS IS NOT OPTIONAL
------------------------
`tools/` is not a data directory: discover_builtin_tools() (tools/registry.py)
globs it for *.py, AST-checks each file for a registry.register(...) call and only
then imports it. A runtime without that directory registers **zero** tools - the
CLI collapses to the skill toolset plus the deferred-tool catalog (measured:
51 discoverable modules in a stock checkout, 0 in the frozen runtime), and the
dashboard's tool list goes empty.

The other trees are data the CLI lists and loads: skills/ + optional-skills/ (the
skill catalogue), plugins/ (bundled plugins), optional-mcps/ (the MCP catalog).

Asserts against an INSTALLED runtime when one can be found (NUMBERS_RUNTIME_DIR,
then NUMBERS_HOME/runtime, then the sibling checkout's numbers-dist/runtime), and
skips otherwise so a plain checkout keeps a green suite.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

REQUIRED_TREES = ("tools", "skills", "optional-skills", "plugins", "optional-mcps")


def _runtime_root() -> Path | None:
    candidates = [
        os.environ.get("NUMBERS_RUNTIME_DIR", ""),
        str(Path(os.environ.get("NUMBERS_HOME", "")) / "runtime" / "bin" / "_internal")
        if os.environ.get("NUMBERS_HOME")
        else "",
        # The dev layout: this checkout is <workspace>/numbers, the product is
        # <workspace>/pray/numbers-dist/runtime/bin/_internal.
        str(Path(__file__).resolve().parents[1].parent / "pray" / "numbers-dist" / "runtime" / "bin" / "_internal"),
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / "hermes_cli").is_dir():
            return Path(candidate)
    return None


@pytest.mark.parametrize("tree", REQUIRED_TREES)
def test_runtime_ships_the_tree(tree):
    root = _runtime_root()
    if root is None:
        pytest.skip("no installed runtime to inspect (set NUMBERS_RUNTIME_DIR)")
    path = root / tree
    assert path.is_dir(), (
        f"{tree}/ is missing from the vendored runtime at {root}. It is an --add-data "
        "entry in scripts/build_numbers_runtime.ps1; without it the app cannot find the "
        "tree (tools/ => zero tools discovered => the CLI exposes 5 tools)."
    )
    assert any(path.rglob("*")), f"{tree}/ exists but is empty"


def test_runtime_tools_dir_is_discoverable():
    root = _runtime_root()
    if root is None:
        pytest.skip("no installed runtime to inspect (set NUMBERS_RUNTIME_DIR)")
    tools_dir = root / "tools"
    modules = sorted(tools_dir.glob("*.py"))
    assert (tools_dir / "registry.py").is_file(), "tools/registry.py must ship (discovery entry point)"
    assert len(modules) > 50, f"only {len(modules)} tool modules would be discoverable from {tools_dir}"
