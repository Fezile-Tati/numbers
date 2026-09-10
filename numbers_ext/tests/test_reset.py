import json
import sqlite3
from pathlib import Path

from numbers_ext import reset


def _seed_home(tmp_path: Path) -> Path:
    (tmp_path / "skills").mkdir(parents=True)
    (tmp_path / "skills" / "stock-a").mkdir()
    (tmp_path / "skills" / "added-b").mkdir()
    (tmp_path / "skills" / "use-angel").mkdir()
    (tmp_path / "memories").mkdir()
    (tmp_path / "memories" / "MEMORY.md").write_text("remember this")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "x").write_text("y")
    (tmp_path / "config.yaml").write_text("model:\n  provider: auto\n")
    (tmp_path / ".env").write_text("NUMBERS_AGENT_TOKEN=keep\nOTHER=1\n")
    (tmp_path / "agent-token").write_text("keep-token\n")
    db = sqlite3.connect(tmp_path / "state.db")
    db.executescript("""
        CREATE TABLE sessions(id TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, body TEXT);
        INSERT INTO sessions VALUES('s1','hi');
        INSERT INTO messages VALUES(1,'s1','hello');
    """)
    db.commit()
    db.close()
    (tmp_path / "numbers-home.json").write_text(
        json.dumps({"product": "numbers"}), encoding="utf-8")
    (tmp_path / "skills-manifest.json").write_text('["stock-a","use-angel"]')
    return tmp_path


def test_reset_deletes_and_preserves(tmp_path):
    home = _seed_home(tmp_path)
    report = reset.wipe(home, confirm_word="RESET")
    assert report["conversations_deleted"] == 2  # 1 session + 1 message
    assert (home / "memories" / "MEMORY.md").exists() is False
    assert (home / "cache" / "x").exists() is False
    assert (home / "skills" / "stock-a").exists()
    assert (home / "skills" / "use-angel").exists()
    assert (home / "skills" / "added-b").exists() is False  # pruned (not in manifest)
    assert (home / "config.yaml").read_text().startswith("model:")
    assert "NUMBERS_AGENT_TOKEN=keep" in (home / ".env").read_text()
    assert (home / "agent-token").read_text() == "keep-token\n"


def test_reset_refuses_without_confirmation_word(tmp_path):
    home = _seed_home(tmp_path)
    report = reset.wipe(home, confirm_word="NOPE")
    assert report["aborted"] is True
    assert (home / "memories" / "MEMORY.md").exists()


def test_reset_refuses_unmarked_home(tmp_path):
    (tmp_path / "memories").mkdir()  # no numbers-home.json marker
    (tmp_path / "memories" / "MEMORY.md").write_text("x")
    report = reset.wipe(tmp_path, confirm_word="RESET")
    assert report["aborted"] is True
    assert (tmp_path / "memories" / "MEMORY.md").exists()


def test_reset_without_manifest_prunes_nothing(tmp_path):
    home = _seed_home(tmp_path)
    (home / "skills-manifest.json").unlink()
    report = reset.wipe(home, confirm_word="RESET")
    assert report["skills_pruned"] == []          # fail closed: no guessing
    assert (home / "skills" / "added-b").exists()
