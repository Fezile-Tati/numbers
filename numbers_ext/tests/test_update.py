"""Tests for `numbers update`.

The property that matters most is the negative one: an update replaces the
program and leaves the user's data byte-identical. That is asserted directly -
hash every preserved path before and after a real swap - rather than inferred
from which code paths were called.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from numbers_ext import update as upd
from numbers_ext.home import write_marker


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A populated NUMBERS home: program directories plus user data."""
    h = tmp_path / "numbers"
    write_marker(h, "1.0.0")

    (h / "runtime" / "bin").mkdir(parents=True)
    (h / "runtime" / "bin" / "hermes.exe").write_text("old runtime", encoding="utf-8")
    (h / "plugins" / "numbers-docs").mkdir(parents=True)
    (h / "plugins" / "numbers-docs" / "index.js").write_text("old", encoding="utf-8")

    # User data in every shape the update must not disturb.
    (h / "config.yaml").write_text("display:\n  skin: numbers\n", encoding="utf-8")
    (h / "agent-token").write_text("secret-token", encoding="utf-8")
    (h / ".env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
    (h / "sessions").mkdir()
    (h / "sessions" / "a.jsonl").write_text('{"role":"user"}\n', encoding="utf-8")
    (h / "docs").mkdir()
    (h / "docs" / "Numbers-Documentation.pdf").write_text("%PDF", encoding="utf-8")

    monkeypatch.setenv("NUMBERS_HOME", str(h))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    return h


def make_bundle(tmp_path: Path, version: str = "1.1.0") -> Path:
    """A release bundle wrapped in a single top-level directory."""
    bundle = tmp_path / "release.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(f"numbers-{version}/runtime/bin/hermes.exe", "new runtime")
        archive.writestr(f"numbers-{version}/plugins/numbers-docs/index.js", "new")
        archive.writestr(f"numbers-{version}/skins/numbers.yaml", "name: numbers")
    return bundle


def write_feed(tmp_path: Path, bundle: Path, version: str = "1.1.0") -> Path:
    feed = tmp_path / "feed.json"
    feed.write_text(
        json.dumps(
            {
                "version": version,
                "url": str(bundle),
                "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                "notes": "Test release.",
            }
        ),
        encoding="utf-8",
    )
    return feed


def snapshot(paths) -> dict:
    out = {}
    for path in paths:
        if path.is_file():
            out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# --- version comparison ----------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("1.1.0", "1.0.0", True),
        ("v1.1.0", "1.1.0", False),
        ("1.0.0", "1.1.0", False),
        ("1.10.0", "1.9.0", True),
        ("2.0.0-beta", "2.0.0", True),
    ],
)
def test_is_newer(candidate, current, expected):
    assert upd.is_newer(candidate, current) is expected


# --- the data-preservation contract ----------------------------------------


def test_update_replaces_program_and_preserves_user_data(home, tmp_path):
    preserved = [
        home / "config.yaml",
        home / "agent-token",
        home / ".env",
        home / "sessions" / "a.jsonl",
        home / "docs" / "Numbers-Documentation.pdf",
        home / "numbers-home.json",
    ]
    before = snapshot(preserved)

    feed = write_feed(tmp_path, make_bundle(tmp_path))
    assert upd.run_update(feed=str(feed)) == 0

    after = snapshot(preserved)
    # numbers-home.json legitimately changes: it records the new version.
    del before[str(home / "numbers-home.json")]
    del after[str(home / "numbers-home.json")]
    assert after == before

    assert (home / "runtime" / "bin" / "hermes.exe").read_text(
        encoding="utf-8"
    ) == "new runtime"
    assert upd.installed_version(home) == "1.1.0"


def test_update_keeps_one_generation_for_rollback(home, tmp_path):
    feed = write_feed(tmp_path, make_bundle(tmp_path))
    assert upd.run_update(feed=str(feed)) == 0
    assert (home / "runtime.prev" / "bin" / "hermes.exe").read_text(
        encoding="utf-8"
    ) == "old runtime"

    assert upd.run_update(do_rollback=True) == 0
    assert (home / "runtime" / "bin" / "hermes.exe").read_text(
        encoding="utf-8"
    ) == "old runtime"


def test_check_only_changes_nothing(home, tmp_path, capsys):
    feed = write_feed(tmp_path, make_bundle(tmp_path))
    assert upd.run_update(check_only=True, feed=str(feed)) == 0
    assert "Update available" in capsys.readouterr().out
    assert (home / "runtime" / "bin" / "hermes.exe").read_text(
        encoding="utf-8"
    ) == "old runtime"


def test_no_update_when_versions_match(home, tmp_path, capsys):
    feed = write_feed(tmp_path, make_bundle(tmp_path, "1.0.0"), version="1.0.0")
    assert upd.run_update(feed=str(feed)) == 0
    assert "up to date" in capsys.readouterr().out


# --- refusals ---------------------------------------------------------------


def test_checksum_mismatch_aborts_before_touching_the_install(home, tmp_path, capsys):
    bundle = make_bundle(tmp_path)
    feed = tmp_path / "feed.json"
    feed.write_text(
        json.dumps({"version": "1.1.0", "url": str(bundle), "sha256": "0" * 64}),
        encoding="utf-8",
    )
    assert upd.run_update(feed=str(feed)) == 1
    assert "checksum" in capsys.readouterr().out
    assert (home / "runtime" / "bin" / "hermes.exe").read_text(
        encoding="utf-8"
    ) == "old runtime"


def test_refuses_outside_a_numbers_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path / "not-numbers"))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert upd.run_update(check_only=True) == 1
    assert "NUMBERS" in capsys.readouterr().out


def test_refuses_a_dev_venv_harness(home, tmp_path, capsys):
    pointer = home / "bin"
    pointer.mkdir(parents=True, exist_ok=True)
    (pointer / "harness-path.txt").write_text(
        str(Path("C:/work/numbers/.venv/Scripts/hermes.exe")), encoding="utf-8"
    )
    feed = write_feed(tmp_path, make_bundle(tmp_path))
    assert upd.run_update(feed=str(feed)) == 1
    assert "development venv" in capsys.readouterr().out


def test_rejects_a_traversing_archive_entry(home, tmp_path, capsys):
    bundle = tmp_path / "evil.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("runtime/ok.txt", "fine")
        archive.writestr("../escaped.txt", "not fine")
    feed = write_feed(tmp_path, bundle)
    assert upd.run_update(feed=str(feed)) == 1
    assert not (tmp_path / "escaped.txt").exists()


def test_missing_feed_is_explained_not_crashed(home, monkeypatch, capsys):
    monkeypatch.delenv(upd.FEED_ENV, raising=False)
    assert upd.run_update(check_only=True) == 1
    assert upd.FEED_ENV in capsys.readouterr().out


def test_preserved_list_and_updatable_list_do_not_overlap():
    assert not set(upd.UPDATABLE) & set(upd.PRESERVED)
