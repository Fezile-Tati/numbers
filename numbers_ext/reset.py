"""Factory reset for the NUMBERS isolated home.

Deletes: conversation/session rows + VACUUM (sqlite3 fallback here; the repo's
`numbers sessions` machinery covers FTS-segment merging after restart),
memories/*.md, non-manifest skills, and regenerable caches/dumps.
Keeps:   config.yaml, .env (incl. NUMBERS_AGENT_TOKEN), agent-token,
         manifest skills (+ always use-angel), cron/, plugins/, skins/,
         auth.json, SOUL.md.

The skills manifest is written by the installer at install time
(skills-manifest.json). If it is missing, skills are NOT pruned (fail closed).
The home marker (numbers-home.json) is required: a Numbers home is never reset.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Callable, List, Optional

from numbers_ext import home

# Tables that hold per-conversation state inside <home>/state.db.
_CONVERSATION_TABLES = (
    "sessions", "messages", "system_prompts", "conversation_generations",
    "session_model_usage", "session_turn_leases", "async_delegations",
    "gateway_heartbeats", "gateway_hygiene_state", "gateway_routing",
)
_KEEP_ALWAYS_SKILLS = {"use-angel"}

# ANSI, not Rich markup -- see run_reset's docstring.
_BOLD = "\033[1m"
_RED = "\033[31m"
_RST = "\033[0m"


def _wipe_conversations(home_dir: Path) -> int:
    db_path = home_dir / "state.db"
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        cur = conn.cursor()
        existing = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        total = 0
        for t in _CONVERSATION_TABLES:
            if t in existing:
                total += cur.execute(f'DELETE FROM "{t}"').rowcount
        conn.commit()
        cur.execute("VACUUM")
        return total
    finally:
        conn.close()


def _manifest(home_dir: Path) -> Optional[set]:
    mf = home_dir / "skills-manifest.json"
    if not mf.exists():
        return None
    try:
        return set(json.loads(mf.read_text(encoding="utf-8")))
    except Exception:
        return None


def wipe(home_dir: Path, confirm_word: str) -> dict:
    """Perform the reset. home_dir must be a marked Numbers home."""
    report = {"aborted": False, "reason": "", "conversations_deleted": 0,
              "skills_pruned": []}
    if not (home_dir / home.MARKER_NAME).exists():
        report["aborted"] = True
        report["reason"] = f"{home_dir} is not a marked Numbers home"
        return report
    if confirm_word != "RESET":
        report["aborted"] = True
        report["reason"] = "confirmation word mismatch"
        return report

    report["conversations_deleted"] = _wipe_conversations(home_dir)
    memories = home_dir / "memories"
    if memories.exists():
        for md in memories.glob("*.md"):
            md.unlink()

    skills_root = home_dir / "skills"
    keep = _manifest(home_dir)
    if skills_root.exists() and keep is not None:
        keep |= _KEEP_ALWAYS_SKILLS
        for child in skills_root.iterdir():
            if child.is_dir() and child.name not in keep:
                shutil.rmtree(child, ignore_errors=True)
                report["skills_pruned"].append(child.name)

    for d in ("sessions", "terminal-sessions", "cache", "image_cache",
              "audio_cache", "pastes", "sandboxes"):
        p = home_dir / d
        if p.exists():
            for child in p.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
    hist = home_dir / ".hermes_history"
    if hist.exists():
        hist.unlink()
    return report


# Shown above the confirmation, in both the TUI and the plain terminal.
# The caller's print_fn (cli._cprint) renders ANSI escapes, NOT Rich markup --
# an earlier version used "[bold red]...[/]" and printed the tags at the user.
def describe_reset() -> List[str]:
    """The explanation screen, as lines. Same wording everywhere."""
    return [
        "",
        f"{_BOLD}{_RED}Factory reset{_RST}",
        "",
        # What survives comes first: the usual worry here is "will I have to
        # set my providers up again?" (no).
        "This ERASES, in NUMBERS only:",
        "  - every conversation and session",
        "  - your memory files",
        "  - any skill you installed yourself",
        "  - caches, terminal dumps and sandboxes",
        "",
        "This is KEPT:",
        "  - your providers and API keys",
        "  - your Intersession / Angel connection",
        "  - your settings, and the skills NUMBERS came with",
        "",
        "This cannot be undone.",
        "",
    ]


# Options for the TUI modal. (key, label, hint) -- the shape
# cli._prompt_text_input_modal expects. Deliberately NO "always approve":
# a factory reset must never become a thing that stops asking.
RESET_CHOICES = [
    ("reset", "Erase and restart NUMBERS", "cannot be undone"),
    ("cancel", "Cancel", "keep everything as it is"),
]
RESET_DETAIL = ("Erase every conversation, your memory files and any skill you "
                "installed yourself. Your providers, API keys and settings are kept.")


def perform_reset(print_fn: Callable = print) -> bool:
    """Do the wipe and report it. Confirmation is the caller's job."""
    home_dir = home.require_numbers_home()
    report = wipe(home_dir, "RESET")
    if report["aborted"]:
        print_fn(f"Reset aborted: {report['reason']}")
        return False
    pruned = ", ".join(report["skills_pruned"]) or "none"
    print_fn("")
    print_fn(f"{_BOLD}Reset complete.{_RST}")
    print_fn(f"  conversations erased: {report['conversations_deleted']}")
    print_fn(f"  skills removed:       {pruned}")
    print_fn("")
    print_fn("NUMBERS will close now. Start it again by running:  numbers")
    print_fn("Then, to finish tidying the search index:  numbers sessions optimize")
    return True


def run_reset(print_fn: Callable = print,
              prompt_fn: Optional[Callable] = None) -> bool:
    """Plain-terminal /reset: explain, ask for the word, wipe.

    This is the NON-TUI path (``numbers reset``, tests, piped stdin). Inside the
    running TUI the confirmation must go through the prompt_toolkit modal
    instead -- a stdin read from the slash-command worker thread deadlocks
    against prompt_toolkit's stdin ownership (#33961). See the handler.
    """
    prompt_fn = prompt_fn or (lambda t: input(t))
    for line in describe_reset():
        print_fn(line)
    print_fn(f"  To reset:  type {_BOLD}RESET{_RST} in capitals, then press Enter")
    print_fn("  To cancel: press Enter, or type anything else")
    print_fn("")
    # The prompt line restates both options: it is often the only thing left on
    # screen once the list above has scrolled.
    word = (prompt_fn("Type RESET to erase, or press Enter to cancel: ")
            or "").strip()
    if word != "RESET":
        print_fn("Cancelled - nothing was erased.")
        return False
    return perform_reset(print_fn)
