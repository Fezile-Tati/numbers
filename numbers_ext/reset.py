"""Factory reset for the NUMBERS isolated home.

Deletes: conversation/session rows + VACUUM (sqlite3 fallback here; the repo's
`hermes sessions` machinery covers FTS-segment merging after restart),
memories/*.md, non-manifest skills, and regenerable caches/dumps.
Keeps:   config.yaml, .env (incl. NUMBERS_AGENT_TOKEN), agent-token,
         manifest skills (+ always use-angel), cron/, plugins/, skins/,
         auth.json, SOUL.md.

The skills manifest is written by the installer at install time
(skills-manifest.json). If it is missing, skills are NOT pruned (fail closed).
The home marker (numbers-home.json) is required: a Hermes home is never reset.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Callable, Optional

from numbers_ext import home

# Tables that hold per-conversation state inside <home>/state.db.
_CONVERSATION_TABLES = (
    "sessions", "messages", "system_prompts", "conversation_generations",
    "session_model_usage", "session_turn_leases", "async_delegations",
    "gateway_heartbeats", "gateway_hygiene_state", "gateway_routing",
)
_KEEP_ALWAYS_SKILLS = {"use-angel"}


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


def run_reset(print_fn: Callable = print) -> bool:
    """CLI entry used by /reset. Returns True when the wipe ran."""
    home_dir = home.require_numbers_home()
    print_fn("[bold red]Factory reset[/] will erase, in this NUMBERS install:")
    print_fn("  - every conversation and session (state.db, vacuumed)")
    print_fn("  - memory files (memories/*.md)")
    print_fn("  - skills not in the install manifest (stock skills and "
             "'use angel' stay)")
    print_fn("  - caches, terminal dumps and sandbox content")
    print_fn("Kept: your provider setup (Gemma), the Angel connection, and config.")
    word = input('Type RESET to confirm, anything else to cancel: ').strip()
    if word != "RESET":
        print_fn("[yellow]Cancelled.[/]")
        return False
    report = wipe(home_dir, word)
    if report["aborted"]:
        print_fn(f"[red]{report['reason']}[/]")
        return False
    print_fn(f"[green]Done.[/] {report['conversations_deleted']} rows removed; "
             f"skills pruned: {', '.join(report['skills_pruned']) or 'none'}")
    print_fn("Run `numbers sessions optimize` after restart to merge FTS segments.")
    return True
