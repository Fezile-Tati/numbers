"""Keep NUMBERS branding when a profile home replaces the install root.

``numbers -p <name>`` re-points HERMES_HOME at
``%NUMBERS_HOME%\\profiles\\<name>`` (hermes_cli.profiles.resolve_profile_env),
before any hermes module is imported. Two independent things then break, and
either one on its own renders the whole CLI as stock Hermes -- the banner, the
prompt, ``--help``, and every ``_cli_prog_name()`` hint telling the user to run
``hermes ...``:

  * the skin NAME lives in the install root's ``config.yaml`` under
    ``display.skin``. A profile's own config.yaml has no ``display`` key at
    all -- imported profiles least of all, since ``import_hermes`` treats
    ``display`` as a Numbers-owned branding key and never copies it.
  * the skin FILE (``skins/numbers.yaml``) is written into the install root by
    the installer, and ``skin_engine._skins_dir()`` resolves against
    HERMES_HOME -- so from inside a profile it points at an empty
    ``profiles/<name>/skins/`` and the skin cannot be loaded even when named.

What makes this recoverable is that NUMBERS_HOME is **not** re-pointed by the
profile override: it is a fork variable upstream does not know about, so it
still names the install root. Both lookups fall back to it.

Everything here answers None when there is nothing to inherit -- off a profile,
outside a Numbers install, or on a stock checkout -- so the hooks in
skin_engine.py are no-ops unless a profile genuinely lost its branding.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from numbers_ext.home import MARKER_NAME


def install_root() -> Optional[Path]:
    """The Numbers install root, or None when this is not a Numbers install.

    Deliberately NUMBERS_HOME only: HERMES_HOME is the one the profile
    override rewrites, so reading it here would defeat the whole point.
    """
    raw = (os.environ.get("NUMBERS_HOME") or "").strip()
    if not raw:
        return None
    root = Path(raw)
    try:
        return root if (root / MARKER_NAME).exists() else None
    except OSError:
        return None


def _current_home() -> Optional[Path]:
    raw = (os.environ.get("HERMES_HOME") or "").strip()
    return Path(raw) if raw else None


def in_profile() -> bool:
    """Whether the active home is something other than the install root."""
    root, cur = install_root(), _current_home()
    if root is None or cur is None:
        return False
    try:
        return root.resolve() != cur.resolve()
    except OSError:
        return str(root) != str(cur)


def fallback_skins_dir() -> Optional[Path]:
    """The install root's ``skins/``, when a profile home shadowed it."""
    if not in_profile():
        return None
    root = install_root()
    if root is None:
        return None
    skins = root / "skins"
    try:
        return skins if skins.is_dir() else None
    except OSError:
        return None


def fallback_skin_name() -> Optional[str]:
    """``display.skin`` from the install root's config.yaml, or None.

    "default" reads as "nothing to inherit": it is what an unskinned harness
    already uses, so returning it would only mask a genuinely default install.
    """
    if not in_profile():
        return None
    root = install_root()
    if root is None:
        return None
    try:
        import yaml

        cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(cfg, dict):
        return None
    display = cfg.get("display")
    if not isinstance(display, dict):
        return None
    name = display.get("skin")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    return None if name == "default" else name
