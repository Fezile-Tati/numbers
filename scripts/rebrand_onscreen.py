"""One-shot rebrand of on-screen 'Hermes' text -> 'Numbers' across the fork.

Safe-by-case-and-boundary:
  * "Hermes"  (title case)  -> "Numbers"     — human-readable branding.
  * "HERMES"  (all caps)    -> UNTOUCHED     — env vars / constants (HERMES_HOME).
  * "hermes"  (lowercase)   -> UNTOUCHED     — code identifiers (hermes_cli),
                                               EXCEPT real command hints below.

Word boundaries (\b) mean identifiers keep their inner text: HermesCLI,
Hermes_Home, hermes-agent (URLs), hermes_cli are all skipped automatically.
Only .py files; skips .venv / .git / caches / egg-info.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", ".git", "__pycache__", "hermes_agent.egg-info", "node_modules"}

# Command hints the launcher accepts (numbers ARGS == hermes ARGS passthrough).
SUBCMDS = (
    "setup", "model", "auth", "update", "skills", "tools", "dashboard",
    "portal", "worktree", "worktrees", "sessions", "connect", "chat",
    "serve", "agent", "config", "plugins", "profiles", "run", "tui",
)

RE_HERMES_AGENT = re.compile(r"\bHermes Agent\b")
RE_HERMES = re.compile(r"\bHermes\b")
RE_HERMES_CMD = re.compile(r"\bhermes (" + "|".join(SUBCMDS) + r")\b")


def transform(text: str) -> tuple[str, int]:
    n = 0
    text, c = RE_HERMES_AGENT.subn("Numbers", text); n += c
    text, c = RE_HERMES.subn("Numbers", text); n += c
    text, c = RE_HERMES_CMD.subn(r"numbers \1", text); n += c
    return text, n


def main() -> int:
    files = 0
    total = 0
    for p in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name == "rebrand_onscreen.py":
            continue
        try:
            src = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        out, n = transform(src)
        if n and out != src:
            p.write_text(out, encoding="utf-8")
            files += 1
            total += n
    print(f"rebranded {total} occurrences across {files} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
