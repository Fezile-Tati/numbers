"""The banner's persisted snapshot must not freeze the skills catalogue.

WHAT WENT WRONG
---------------
`save_banner_snapshot()` persisted `skills_by_category` next to the tool panel,
and the loader accepted the blob as long as its *tools* fingerprint matched. The
skills catalogue is not part of that fingerprint, so the very first snapshot a
home wrote locked the banner's skill count forever: after the runtime started
shipping `skills/` and 36 skills were synced into NUMBERS_HOME, the banner still
printed "1 skills" while `numbers skills list` reported 36.

THE FIX (asserted here)
-----------------------
1. `save_banner_snapshot()` does not persist `skills_by_category` - the banner
   computes it live (it is ~100 ms, already prefetched off-thread by
   prefetch_banner_data()).
2. The loader REJECTS any snapshot that still carries the key, so the stale blobs
   written before the fix are invalidated once and the panel self-heals.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

hermes_cli_banner = pytest.importorskip("hermes_cli.banner")


def _legacy_blob() -> dict:
    return {
        "fingerprint": "x",
        "enabled_toolsets": [],
        "tools": [{"function": {"name": "terminal"}}],
        "toolset_map": {"terminal": "terminal"},
        "availability": {"unavailable_toolsets": [], "lazy_tools": [], "disabled_tools": []},
        "skills_by_category": {"general": ["use-angel"]},  # the stale catalogue
    }


def test_loader_rejects_a_snapshot_that_persisted_the_skill_catalogue(tmp_path, monkeypatch):
    home = tmp_path / "numbers"
    (home / "cache").mkdir(parents=True)
    (home / "cache" / "banner_snapshot.json").write_text(json.dumps(_legacy_blob()), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("NUMBERS_HOME", str(home))

    assert hermes_cli_banner.load_banner_snapshot(enabled_toolsets=[]) is None, (
        "a legacy snapshot carrying skills_by_category must be rejected, or the stale "
        "skill list keeps being replayed forever"
    )


def test_save_never_persists_the_skill_catalogue(tmp_path, monkeypatch):
    home = tmp_path / "numbers"
    (home / "cache").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("NUMBERS_HOME", str(home))
    monkeypatch.setattr(hermes_cli_banner, "banner_snapshot_fingerprint", lambda: "fp-1")

    hermes_cli_banner.save_banner_snapshot(
        tools=[{"function": {"name": "terminal"}}],
        enabled_toolsets=["hermes-cli"],
        availability={"unavailable_toolsets": [], "lazy_tools": [], "disabled_tools": []},
        toolset_map={"terminal": "terminal"},
    )

    path = home / "cache" / "banner_snapshot.json"
    assert path.is_file(), "the tool panel snapshot must still be written"
    blob = json.loads(path.read_text(encoding="utf-8"))
    assert "skills_by_category" not in blob, (
        "persisting the skill catalogue is what froze the banner's skill count"
    )
    assert blob.get("tools"), "the tool panel must still be persisted"


def test_a_snapshot_without_the_catalogue_is_still_usable(tmp_path, monkeypatch):
    """The tools panel optimisation must survive the fix."""
    home = tmp_path / "numbers"
    (home / "cache").mkdir(parents=True)
    blob = _legacy_blob()
    blob.pop("skills_by_category")
    blob["fingerprint"] = "fp-2"
    (home / "cache" / "banner_snapshot.json").write_text(json.dumps(blob), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("NUMBERS_HOME", str(home))
    monkeypatch.setattr(hermes_cli_banner, "banner_snapshot_fingerprint", lambda: "fp-2")

    loaded = hermes_cli_banner.load_banner_snapshot(enabled_toolsets=[])
    assert loaded is not None, "a catalogue-free snapshot must still be accepted"
    assert loaded.get("tools")
