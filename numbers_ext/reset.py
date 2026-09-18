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
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Callable, List, Optional

from numbers_ext import home
from numbers_ext.ansi import BOLD as _BOLD
from numbers_ext.ansi import RED as _RED
from numbers_ext.ansi import RST as _RST

# Tables that hold per-conversation state inside <home>/state.db.
_CONVERSATION_TABLES = (
    "sessions", "messages", "system_prompts", "conversation_generations",
    "session_model_usage", "session_turn_leases", "async_delegations",
    "gateway_heartbeats", "gateway_hygiene_state", "gateway_routing",
)
_KEEP_ALWAYS_SKILLS = {"use-angel"}

# --- full (factory) reset only -------------------------------------------
# The only .env key a factory reset keeps. Everything else there is a provider
# credential or a user setting, which is exactly what "factory" means to erase.
# Keeping the agent token is a product decision: a factory reset must not also
# sign you out of Intersession.
_ENV_KEEP = frozenset({"NUMBERS_AGENT_TOKEN"})
# config.yaml keys Numbers owns -- the same set import_hermes refuses to import
# over, kept in sync deliberately. Everything else in config.yaml is a user
# setting and goes. `model` survives as a key but is emptied of the provider
# choice below, so nothing recalls which provider was set.
_BRAND_CONFIG_KEYS = frozenset(
    {"display", "mcp_servers", "agent", "model", "_config_version"})
_MODEL_PROVIDER_KEYS = ("provider", "default")


def _wipe_conversations(home_dir: Path) -> int:
    db_path = home_dir / "state.db"
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        cur = conn.cursor()
        existing = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        # Report only the sessions actually erased -- summing rowcount across
        # every conversation-adjacent table (messages included) inflated this
        # number far past what a user would call "a conversation".
        sessions_deleted = 0
        for t in _CONVERSATION_TABLES:
            if t in existing:
                rowcount = cur.execute(f'DELETE FROM "{t}"').rowcount
                if t == "sessions":
                    sessions_deleted = rowcount
        conn.commit()
        cur.execute("VACUUM")
        return sessions_deleted
    finally:
        conn.close()


def _prune_env(home_dir: Path, keep: frozenset) -> List[str]:
    """Drop every .env key except ``keep``. Returns the names removed.

    Shares device_auth's write shape (temp file, owner-only, atomic replace) so
    the surviving agent token is never briefly world-readable.
    """
    from numbers_ext.device_auth import _lock_down

    path = home_dir / ".env"
    if not path.is_file():
        return []
    removed, out = [], []
    for ln in path.read_text(encoding="utf-8").splitlines():
        stripped = ln.lstrip()
        if "=" in ln and not stripped.startswith("#"):
            key = ln.split("=", 1)[0].strip()
            if key not in keep:
                removed.append(key)
                continue
        out.append(ln)
    if not removed:
        return []
    fd, tmp = tempfile.mkstemp(dir=str(home_dir), prefix=".env-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out) + ("\n" if out else ""))
        _lock_down(Path(tmp))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return removed


def _strip_config(home_dir: Path) -> bool:
    """Reduce config.yaml to the Numbers-owned keys, with no provider chosen.

    Rewriting rather than deleting: config.yaml carries the branding NUMBERS
    needs to come back up looking like itself, and a missing file would be
    regenerated as plain Hermes.
    """
    path = home_dir / "config.yaml"
    if not path.is_file():
        return False
    try:
        import yaml

        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False  # unreadable or no yaml: leave it rather than corrupt it
    kept = {k: v for k, v in cfg.items() if k in _BRAND_CONFIG_KEYS}
    model = kept.get("model")
    if isinstance(model, dict):
        for k in _MODEL_PROVIDER_KEYS:
            model.pop(k, None)
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(kept, sort_keys=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        return False
    return True


def _wipe_credentials(home_dir: Path, report: dict) -> None:
    """The extra erasures a FULL reset performs on top of the light one.

    Providers live in auth.json (credential_pool / providers / active_provider)
    and as keys in .env; the active model choice lives in config.yaml. All
    three have to go or NUMBERS still "knows" the provider after a reset.
    """
    auth = home_dir / "auth.json"
    if auth.exists():
        auth.unlink()
        report["providers_cleared"] = True
    report["env_keys_removed"] = _prune_env(home_dir, _ENV_KEEP)
    report["config_reset"] = _strip_config(home_dir)
    # Let the Hermes import offer itself again on the next start.
    guard = home_dir / ".hermes_import_done"
    if guard.exists():
        guard.unlink()


def _manifest(home_dir: Path) -> Optional[set]:
    mf = home_dir / "skills-manifest.json"
    if not mf.exists():
        return None
    try:
        return set(json.loads(mf.read_text(encoding="utf-8")))
    except Exception:
        return None


def wipe(home_dir: Path, confirm_word: str, *, full: bool = False) -> dict:
    """Perform the reset. home_dir must be a marked Numbers home.

    ``full`` adds the factory erasures -- providers, API keys and settings --
    on top of everything the light reset does. Either way the home marker, the
    skills manifest and the skills NUMBERS shipped with survive: a reset that
    trips home.require_numbers_home() takes every other command down with it.
    """
    report = {"aborted": False, "reason": "", "conversations_deleted": 0,
              "skills_pruned": [], "full": full, "providers_cleared": False,
              "env_keys_removed": [], "config_reset": False}
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
        for md in memories.rglob("*.md"):
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
    if full:
        _wipe_credentials(home_dir, report)
    return report


# Shown above the confirmation, in both the TUI and the plain terminal.
# The caller's print_fn (cli._cprint) renders ANSI escapes, NOT Rich markup --
# an earlier version used "[bold red]...[/]" and printed the tags at the user.
def describe_reset(full: bool = False) -> List[str]:
    """The explanation screen, as lines. Same wording everywhere.

    Each level states its OWN erase/keep list. One shared screen was how the
    light reset came to read as a factory reset that "still remembers my
    provider" -- because that is precisely what it is.
    """
    if not full:
        return [
            "",
            f"{_BOLD}Reset NUMBERS{_RST}",
            "",
            # What survives comes first: the usual worry here is "will I have
            # to set my providers up again?" (no).
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
    return [
        "",
        f"{_BOLD}{_RED}Full factory reset{_RST}",
        "",
        "This ERASES, in NUMBERS only:",
        "  - every conversation and session",
        "  - your memory files",
        "  - any skill you installed yourself",
        "  - caches, terminal dumps and sandboxes",
        "  - your providers and API keys",
        "  - your settings, including which model you chose",
        "",
        "This is KEPT:",
        "  - your Intersession / Angel sign-in",
        "  - the skills NUMBERS came with",
        "",
        "NUMBERS will start up as if freshly installed, and will ask you to",
        "choose a provider again.",
        "",
        "This cannot be undone.",
        "",
    ]


# Options for the TUI modal. (key, label, hint) -- the shape
# cli._prompt_text_input_modal expects. Deliberately NO "always approve":
# a factory reset must never become a thing that stops asking.
RESET_CHOICES = [
    ("light", "Erase my conversations and memory",
     "providers, API keys and settings are kept"),
    ("full", "Full factory reset",
     "also erases your providers, API keys and settings"),
    ("cancel", "Cancel", "keep everything as it is"),
]
RESET_DETAIL = (
    "Both options erase every conversation, your memory files, any skill you "
    "installed yourself, and your caches. A full factory reset additionally "
    "erases your providers, API keys and settings, so NUMBERS starts up as if "
    "freshly installed. Your Intersession sign-in is kept either way. Neither "
    "can be undone.")

# Second confirmation, shown only for the full reset and only after the user
# has read describe_reset(full=True). Erasing providers and API keys is the one
# step here that costs real work to undo. Cancel leads, so a stray Enter on the
# default keeps everything.
FULL_RESET_CONFIRM = [
    ("cancel", "Cancel", "keep everything as it is"),
    ("full", "Erase everything, including my API keys",
     "cannot be undone"),
]


def perform_reset(print_fn: Callable = print, *, full: bool = False) -> bool:
    """Do the wipe and report it. Confirmation is the caller's job."""
    home_dir = home.require_numbers_home()
    report = wipe(home_dir, "RESET", full=full)
    if report["aborted"]:
        print_fn(f"Reset aborted: {report['reason']}")
        return False
    pruned = ", ".join(report["skills_pruned"]) or "none"
    print_fn("")
    print_fn(f"{_BOLD}{'Factory reset' if full else 'Reset'} complete.{_RST}")
    print_fn(f"  conversations erased: {report['conversations_deleted']}")
    print_fn(f"  skills removed:       {pruned}")
    if full:
        print_fn(f"  providers erased:     "
                 f"{'yes' if report['providers_cleared'] else 'none were set'}")
        print_fn(f"  API keys erased:      "
                 f"{len(report['env_keys_removed'])}")
        print_fn(f"  settings reset:       "
                 f"{'yes' if report['config_reset'] else 'nothing to reset'}")
        print_fn("  Intersession sign-in: kept")
    print_fn("")
    print_fn("NUMBERS will close now. Start it again by running:  numbers")
    print_fn("Then, to finish tidying the search index:  numbers sessions optimize")
    return True


def run_reset(print_fn: Callable = print,
              prompt_fn: Optional[Callable] = None, *,
              full: bool = False) -> bool:
    """Plain-terminal /reset: explain, ask for the word, wipe.

    This is the NON-TUI path (``numbers reset``, tests, piped stdin). Inside the
    running TUI the confirmation must go through the prompt_toolkit modal
    instead -- a stdin read from the slash-command worker thread deadlocks
    against prompt_toolkit's stdin ownership (#33961). See the handler.
    """
    prompt_fn = prompt_fn or (lambda t: input(t))
    for line in describe_reset(full):
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
    return perform_reset(print_fn, full=full)
