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
    # Only sessions count as "conversations" -- summing every table's rowcount
    # (messages included) inflated this past what a user would call one.
    assert report["conversations_deleted"] == 1
    assert (home / "memories" / "MEMORY.md").exists() is False
    assert (home / "cache" / "x").exists() is False
    assert (home / "skills" / "stock-a").exists()
    assert (home / "skills" / "use-angel").exists()
    assert (home / "skills" / "added-b").exists() is False  # pruned (not in manifest)
    assert (home / "config.yaml").read_text().startswith("model:")
    assert "NUMBERS_AGENT_TOKEN=keep" in (home / ".env").read_text()
    assert (home / "agent-token").read_text() == "keep-token\n"


def test_reset_wipes_nested_memory_files(tmp_path):
    """Regression: wipe() used to glob("*.md") top-level only, leaving nested
    memory directories behind a "factory" reset."""
    home = _seed_home(tmp_path)
    nested = home / "memories" / "projects"
    nested.mkdir()
    (nested / "notes.md").write_text("nested")
    reset.wipe(home, confirm_word="RESET")
    assert not (nested / "notes.md").exists()


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


# --------------------------------------------------------------------------
# Full (factory) reset — the level that also erases providers and API keys.
# --------------------------------------------------------------------------

def _seed_providers(home: Path) -> Path:
    (home / "auth.json").write_text(json.dumps({
        "credential_pool": {"deepseek": [{"source": "env:DEEPSEEK_API_KEY"}]},
        "providers": {"deepseek": {}},
        "active_provider": "deepseek",
    }), encoding="utf-8")
    (home / ".env").write_text(
        "NUMBERS_AGENT_TOKEN=keep\nDEEPSEEK_API_KEY=sk-secret\nOTHER=1\n")
    (home / "config.yaml").write_text(
        "model:\n  provider: deepseek\n  default: deepseek-chat\n"
        "display:\n  skin: numbers\n"
        "telemetry: true\n")
    (home / ".hermes_import_done").write_text("done\n")
    return home


def test_full_reset_forgets_the_provider(tmp_path):
    """The reported bug: "after resetting, NUMBERS still recalls my set
    provider". A full reset must leave nothing that remembers it."""
    home = _seed_providers(_seed_home(tmp_path))
    report = reset.wipe(home, confirm_word="RESET", full=True)
    assert report["aborted"] is False
    assert not (home / "auth.json").exists()
    env = (home / ".env").read_text()
    assert "DEEPSEEK_API_KEY" not in env
    cfg = (home / "config.yaml").read_text()
    assert "deepseek" not in cfg
    assert "provider" not in cfg
    assert "telemetry" not in cfg  # user settings go too


def test_full_reset_keeps_the_intersession_sign_in(tmp_path):
    """Product decision: a factory reset must not also sign you out."""
    home = _seed_providers(_seed_home(tmp_path))
    reset.wipe(home, confirm_word="RESET", full=True)
    assert "NUMBERS_AGENT_TOKEN=keep" in (home / ".env").read_text()
    assert (home / "agent-token").read_text() == "keep-token\n"


def test_full_reset_leaves_numbers_bootable(tmp_path):
    """A reset that trips home.require_numbers_home() takes every other
    command down with it."""
    home = _seed_providers(_seed_home(tmp_path))
    reset.wipe(home, confirm_word="RESET", full=True)
    assert (home / "numbers-home.json").exists()
    assert (home / "skills-manifest.json").exists()
    assert (home / "skills" / "stock-a").exists()   # shipped skill
    assert (home / "skills" / "use-angel").exists()
    assert (home / "skills" / "added-b").exists() is False
    # Branding survives so NUMBERS comes back looking like itself.
    assert "skin: numbers" in (home / "config.yaml").read_text()


def test_full_reset_reopens_the_hermes_import_offer(tmp_path):
    home = _seed_providers(_seed_home(tmp_path))
    reset.wipe(home, confirm_word="RESET", full=True)
    assert not (home / ".hermes_import_done").exists()


def test_light_reset_still_keeps_providers(tmp_path):
    """The two levels must stay genuinely different."""
    home = _seed_providers(_seed_home(tmp_path))
    reset.wipe(home, confirm_word="RESET", full=False)
    assert (home / "auth.json").exists()
    assert "DEEPSEEK_API_KEY=sk-secret" in (home / ".env").read_text()
    assert "deepseek" in (home / "config.yaml").read_text()
    assert (home / ".hermes_import_done").exists()


def test_full_reset_still_refuses_an_unmarked_home(tmp_path):
    (tmp_path / "auth.json").write_text("{}")
    report = reset.wipe(tmp_path, confirm_word="RESET", full=True)
    assert report["aborted"] is True
    assert (tmp_path / "auth.json").exists()


def test_reset_choices_offer_both_levels_and_cancel():
    keys = [c[0] for c in reset.RESET_CHOICES]
    assert keys == ["light", "full", "cancel"]
    labels = " ".join(c[1] + " " + c[2] for c in reset.RESET_CHOICES)
    assert "API keys" in labels          # the difference is stated, not implied
    # A factory reset must never become a thing that stops asking.
    assert not any("always" in c[1].lower() for c in reset.RESET_CHOICES)


def test_full_confirm_defaults_to_cancel():
    """The destructive option is never the one a stray Enter lands on."""
    assert reset.FULL_RESET_CONFIRM[0][0] == "cancel"
    assert [c[0] for c in reset.FULL_RESET_CONFIRM] == ["cancel", "full"]


def test_full_description_promises_what_the_wipe_does():
    text = "\n".join(reset.describe_reset(full=True))
    assert "your providers and API keys" in text
    assert "Intersession / Angel sign-in" in text
    assert "cannot be undone" in text
    light = "\n".join(reset.describe_reset(full=False))
    assert "your providers and API keys" in light  # but under "This is KEPT:"
    assert light.index("This is KEPT:") < light.index("your providers and API keys")


def test_full_reset_screen_uses_no_rich_markup():
    for tag in ("[bold red]", "[/]", "[yellow]", "[green]", "[red]", "[dim]"):
        assert tag not in "\n".join(reset.describe_reset(full=True))
        assert tag not in reset.RESET_DETAIL


def _screen(home, answer, full=False):
    """Render the /reset confirmation screen; return (lines, did_wipe)."""
    lines = []
    did = reset.run_reset(print_fn=lines.append, prompt_fn=lambda _t: answer,
                          full=full)
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
