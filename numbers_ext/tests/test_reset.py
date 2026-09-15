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


def _screen(home, answer):
    """Render the /reset confirmation screen; return (lines, did_wipe)."""
    lines = []
    did = reset.run_reset(print_fn=lines.append, prompt_fn=lambda _t: answer)
    return "\n".join(lines), did


def test_reset_screen_uses_no_rich_markup(tmp_path, monkeypatch):
    """cli._cprint renders ANSI, not Rich -- tags would print literally."""
    home = _seed_home(tmp_path)
    monkeypatch.setattr(reset.home, "require_numbers_home", lambda: home)
    text, _ = _screen(home, "")
    for tag in ("[bold red]", "[/]", "[yellow]", "[green]", "[red]"):
        assert tag not in text


def test_reset_screen_states_the_exact_confirmation_step(tmp_path, monkeypatch):
    home = _seed_home(tmp_path)
    monkeypatch.setattr(reset.home, "require_numbers_home", lambda: home)
    text, did = _screen(home, "")
    assert "type \x1b[1mRESET\x1b[0m in capitals, then press Enter" in text
    assert "To cancel: press Enter, or type anything else" in text
    # Both halves of the stakes are spelled out, not just the erasure.
    assert "This ERASES, in NUMBERS only:" in text
    assert "This is KEPT:" in text
    assert "your providers and API keys" in text
    assert "cannot be undone" in text
    # Empty input cancels, and says so.
    assert did is False and "Cancelled - nothing was erased." in text


def test_lowercase_reset_does_not_wipe(tmp_path, monkeypatch):
    home = _seed_home(tmp_path)
    monkeypatch.setattr(reset.home, "require_numbers_home", lambda: home)
    _text, did = _screen(home, "reset")
    assert did is False


def test_confirmed_reset_tells_the_user_what_happens_next(tmp_path, monkeypatch):
    home = _seed_home(tmp_path)
    monkeypatch.setattr(reset.home, "require_numbers_home", lambda: home)
    text, did = _screen(home, "RESET")
    assert did is True
    assert "Reset complete." in text
    assert "Start it again by running:  numbers" in text
