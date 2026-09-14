"""Guard the overlay-patched files against CP1252<->UTF-8 mojibake.

WHY
---
apply_overlay.ps1 rewrites whole files. When it read/wrote them with PowerShell's
ANSI default it double-encoded every non-ASCII glyph they already contained: the
CLI's tip markers and box-drawing rules came out as mojibake in cli.py (2824
sequences), cli_commands_mixin.py (294) and commands.py (73). The files this
script patches are exactly the ones carrying non-ASCII CLI art, so this test runs
over that same set and fails the suite if the corruption ever returns - which is
what the script's own [8/8] checker step also gates on at apply time.

Cheap: pure byte scan, no imports from the app.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Files apply_overlay.ps1 patches (Keep in sync with the Insert-Hook /
# Replace-Text calls in numbers-dist/overlay/apply_overlay.ps1.)
PATCHED = (
    "cli.py",
    "hermes_cli/commands.py",
    "hermes_cli/cli_commands_mixin.py",
    "hermes_cli/_parser.py",
    "hermes_cli/console_engine.py",
    "hermes_cli/banner.py",
    "hermes_cli/main.py",
    "hermes_cli/web_server.py",
    "web/src/App.tsx",
    "web/src/i18n/en.ts",
)

# A UTF-8 lead byte re-encoded after being mis-read as CP1252.
SIGNATURE = re.compile(
    rb"\xc3(?:\xa2[\xc2\xc5\xe2\xf0-\xf4]|\x82[\xc2\xc5\xe2\xc3]|\x83[\xc2\xc5\xe2\xc3]|\xb0[\x9f\x80-\xbf])"
)


def test_patched_files_have_no_mojibake() -> None:
    offenders = {}
    for rel in PATCHED:
        path = REPO / rel
        if not path.exists():
            continue
        hits = len(SIGNATURE.findall(path.read_bytes()))
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        f"CP1252<->UTF-8 mojibake in overlay-patched files: {offenders}. "
        "Repair: `git restore <file>` in the Hermes checkout, then re-run "
        "numbers-dist/overlay/apply_overlay.ps1 (it must not re-introduce it - "
        "the script needs its UTF-8 BOM and must write through Write-TextFile)."
    )
