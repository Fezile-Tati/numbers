"""Numbers home identity and isolation guard.

Every Numbers write path (sign-in token persistence, /reset) validates the
home through require_numbers_home() BEFORE touching
the filesystem, so a misconfigured HERMES_HOME/NUMBERS_HOME can never land
Numbers data inside a Numbers home. The marker (numbers-home.json) is written
by the installer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

MARKER_NAME = "numbers-home.json"


class NotANumbersHome(RuntimeError):
    pass


def write_marker(home: Path, version: str) -> Path:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    marker = home / MARKER_NAME
    marker.write_text(json.dumps({"product": "numbers", "version": version,
                                  "display_name": "NUMBERS 21:4-9"}),
                      encoding="utf-8")
    return marker


def is_numbers_home(home: Optional[Path]) -> bool:
    if not home:
        return False
    return (Path(home) / MARKER_NAME).exists()


def resolve_home_env() -> Optional[Path]:
    raw = os.environ.get("NUMBERS_HOME") or os.environ.get("HERMES_HOME")
    return Path(raw) if raw else None


def require_numbers_home() -> Path:
    home = resolve_home_env()
    if not home:
        raise NotANumbersHome(
            "NUMBERS_HOME is not set — launch NUMBERS via the `numbers` command, "
            "never by running the engine binary directly.")
    home = Path(home)
    if not (home / MARKER_NAME).exists():
        raise NotANumbersHome(
            f"{home} is not a NUMBERS home (no {MARKER_NAME}). Refusing to write "
            "Numbers data outside a Numbers install.")
    return home
