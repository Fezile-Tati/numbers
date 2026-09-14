"""NUMBERS must expose the bundled skill catalogue, not only use-angel.

The installer used to copy exactly one skill into NUMBERS_HOME/skills and the
frozen runtime shipped no skills/ tree at all, so the CLI listed two skills
("general", "use-angel") where stock Hermes lists the whole catalogue.

Resolution mirrors the app: HERMES_BUNDLED_SKILLS, then the installed runtime's
_internal/skills, then this checkout's own skills/ tree.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _bundled_skills() -> Path | None:
    candidates = [
        os.environ.get("HERMES_BUNDLED_SKILLS", ""),
        str(Path(os.environ.get("NUMBERS_HOME", "")) / "runtime" / "bin" / "_internal" / "skills")
        if os.environ.get("NUMBERS_HOME")
        else "",
        str(Path(__file__).resolve().parents[1].parent / "pray" / "numbers-dist" / "runtime" / "bin" / "_internal" / "skills"),
        str(Path(__file__).resolve().parents[1] / "skills"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return None


def test_bundled_catalogue_is_a_real_catalogue():
    bundled = _bundled_skills()
    if bundled is None:
        pytest.skip("no bundled skills tree to inspect")
    skills = list(bundled.rglob("SKILL.md"))
    assert len(skills) >= 5, f"only {len(skills)} bundled skills found under {bundled}"


def test_use_angel_travels_with_the_bundled_tree():
    """The product's one extra skill must be part of the shipped catalogue, so a
    fresh runtime lists it without the installer's separate home copy."""
    bundled = _bundled_skills()
    if bundled is None:
        pytest.skip("no bundled skills tree to inspect")
    assert (bundled / "use-angel" / "SKILL.md").is_file(), (
        f"use-angel/SKILL.md missing under {bundled} - build_numbers_runtime.ps1 seeds it "
        "from numbers-dist/skills/use-angel before staging the tree"
    )
