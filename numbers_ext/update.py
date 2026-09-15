"""`numbers update` - swap in a newer NUMBERS release bundle.

WHY THIS EXISTS RATHER THAN REUSING `hermes update`
---------------------------------------------------
Upstream's updater is a git updater: it stashes, pulls, and on divergence runs
``git reset --hard origin/<branch>`` (hermes_cli/update_cmd.py). Two problems
for this product. A shipped NUMBERS install is a frozen executable with no git
checkout at all, so it has nothing to pull into. And if it did, a hard reset to
upstream is precisely the operation that would erase the fork: the branding, the
custom commands, the auth logic.

So NUMBERS updates by artifact, not by merge. The maintainer builds a release
bundle with scripts/build_numbers_runtime.ps1 - which applies the overlay first,
so branding and custom logic are already baked into the artifact - and this
command downloads that bundle and swaps it in. Upstream Hermes changes can only
ever reach a user through an artifact that has already been overlaid. The fork's
customisations cannot be overwritten by an update, by construction, rather than
by remembering to re-apply them afterwards.

WHAT IT WILL NOT TOUCH
----------------------
Everything in PRESERVED: config, credentials, sessions, memories, the local
database, docs, cron jobs and hooks. An update replaces the program, never the
user's data. Each swap keeps the previous runtime for one generation so a bad
release can be rolled back.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from numbers_ext.home import NotANumbersHome, require_numbers_home

# Directories the update replaces wholesale. These are program, not data.
UPDATABLE = ("runtime", "plugins", "skins", "skills")

# Never touched by an update. Listed explicitly (rather than implied by "not in
# UPDATABLE") so that adding a new updatable directory forces a deliberate
# decision about anything sitting next to it.
PRESERVED = (
    "config.yaml",
    "auth.json",
    "agent-token",
    ".env",
    "numbers-home.json",
    "sessions",
    "memories",
    "state.db",
    "docs",
    "cron",
    "hooks",
)

FEED_ENV = "NUMBERS_UPDATE_FEED"
CHUNK = 1 << 20


class UpdateError(RuntimeError):
    """Anything that should stop the update with a readable message."""


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    notes: str = ""

    @classmethod
    def from_feed(cls, data: dict) -> "Release":
        missing = [k for k in ("version", "url", "sha256") if not data.get(k)]
        if missing:
            raise UpdateError(
                f"Release feed is missing required field(s): {', '.join(missing)}"
            )
        return cls(
            version=str(data["version"]),
            url=str(data["url"]),
            sha256=str(data["sha256"]).lower(),
            notes=str(data.get("notes", "")),
        )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def installed_version(home: Path) -> str:
    """Version recorded by the installer in the NUMBERS home marker."""
    marker = Path(home) / "numbers-home.json"
    try:
        return str(json.loads(marker.read_text(encoding="utf-8")).get("version", "0"))
    except (OSError, ValueError):
        return "0"


def feed_url() -> str:
    url = os.environ.get(FEED_ENV, "").strip()
    if not url:
        raise UpdateError(
            f"No release feed configured. Set {FEED_ENV} to the URL (or local "
            "path) of the NUMBERS release feed, then run `numbers update` again."
        )
    return url


def _read_source(url: str) -> bytes:
    """Read a feed or bundle from an http(s) URL or a local path.

    Local paths are supported so a release can be staged from a file share, and
    so the update path is testable without a server.
    """
    if url.startswith(("http://", "https://")):
        with urllib.request.urlopen(url) as response:  # noqa: S310 - operator-supplied
            return response.read()
    path = Path(url)
    if not path.is_file():
        raise UpdateError(f"Release source not found: {url}")
    return path.read_bytes()


def fetch_release(url: Optional[str] = None) -> Release:
    raw = _read_source(url or feed_url())
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpdateError(f"Release feed is not valid JSON: {exc}") from exc
    return Release.from_feed(data)


def _version_tuple(version: str) -> tuple:
    parts = []
    for chunk in str(version).lstrip("vV").replace("-", ".").split("."):
        parts.append((0, int(chunk)) if chunk.isdigit() else (1, 0, chunk))
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    try:
        return _version_tuple(candidate) > _version_tuple(current)
    except Exception:
        # Unparseable versions: treat "different" as "newer" rather than
        # silently refusing to ever update.
        return str(candidate) != str(current)


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def _assert_vendored_runtime(home: Path) -> None:
    """Refuse to update a maintainer's dev checkout.

    A dev install points the launcher at a venv's hermes.exe; swapping a release
    bundle underneath it would neither work nor be what the maintainer wanted -
    they should rebuild from source instead.
    """
    pointer = Path(home) / "bin" / "harness-path.txt"
    try:
        harness = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if harness and (f"{os.sep}.venv{os.sep}" in harness or "/.venv/" in harness):
        raise UpdateError(
            "This install runs from a development venv, not a vendored runtime. "
            "Rebuild it with scripts/build_numbers_runtime.ps1 instead of "
            "running `numbers update`."
        )


def download_bundle(release: Release, dest_dir: Path) -> Path:
    payload = _read_source(release.url)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != release.sha256:
        raise UpdateError(
            "Downloaded bundle failed its checksum - refusing to install it.\n"
            f"  expected {release.sha256}\n  actual   {digest}"
        )
    bundle = dest_dir / "numbers-release.zip"
    bundle.write_bytes(payload)
    return bundle


def _extract(bundle: Path, into: Path) -> Path:
    with zipfile.ZipFile(bundle) as archive:
        for name in archive.namelist():
            target = (into / name).resolve()
            if not str(target).startswith(str(into.resolve())):
                raise UpdateError(f"Refusing archive entry outside the bundle: {name}")
        archive.extractall(into)

    # Tolerate both a flat bundle and one wrapped in a single top directory.
    entries = [p for p in into.iterdir() if p.name != bundle.name]
    if len(entries) == 1 and entries[0].is_dir():
        if not any((entries[0] / d).exists() for d in UPDATABLE):
            raise UpdateError(
                "Release bundle contains none of: " + ", ".join(UPDATABLE)
            )
        return entries[0]
    if not any((into / d).exists() for d in UPDATABLE):
        raise UpdateError("Release bundle contains none of: " + ", ".join(UPDATABLE))
    return into


def apply_bundle(staged: Path, home: Path) -> list[str]:
    """Swap the staged directories into the home. Returns what changed.

    Each directory is replaced by rename, not by copying over the top: a partial
    copy into a live runtime is the one failure mode that would leave an install
    unusable. The old directory is kept as ``<name>.prev`` for rollback, and a
    failure mid-swap puts it straight back.
    """
    replaced: list[str] = []
    for name in UPDATABLE:
        incoming = staged / name
        if not incoming.is_dir():
            continue
        current = home / name
        previous = home / f"{name}.prev"

        if previous.exists():
            shutil.rmtree(previous, ignore_errors=True)
        if current.exists():
            os.replace(current, previous)
        try:
            shutil.move(str(incoming), str(current))
        except Exception:
            if previous.exists() and not current.exists():
                os.replace(previous, current)
            raise
        replaced.append(name)
    return replaced


def rollback(home: Path) -> list[str]:
    """Restore the previous generation of every directory that has one."""
    restored: list[str] = []
    for name in UPDATABLE:
        previous = home / f"{name}.prev"
        if not previous.is_dir():
            continue
        current = home / name
        if current.exists():
            shutil.rmtree(current, ignore_errors=True)
        os.replace(previous, current)
        restored.append(name)
    return restored


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def run_update(check_only: bool = False, do_rollback: bool = False,
               feed: Optional[str] = None) -> int:
    try:
        home = require_numbers_home()
    except NotANumbersHome as exc:
        print(f"numbers update: {exc}")
        return 1

    if do_rollback:
        restored = rollback(home)
        if not restored:
            print("Nothing to roll back - no previous generation is stored.")
            return 1
        print("Rolled back: " + ", ".join(restored))
        print("Open a new terminal and run `numbers` to pick up the rollback.")
        return 0

    current = installed_version(home)
    try:
        release = fetch_release(feed)
    except UpdateError as exc:
        print(f"numbers update: {exc}")
        return 1

    if not is_newer(release.version, current):
        print(f"NUMBERS is up to date (v{current}).")
        return 0

    print(f"Update available: v{current} -> v{release.version}")
    if release.notes:
        print(release.notes.strip())
    if check_only:
        print("Run `numbers update` to install it.")
        return 0

    try:
        _assert_vendored_runtime(home)
        with tempfile.TemporaryDirectory(prefix="numbers-update-") as tmp:
            tmp_path = Path(tmp)
            bundle = download_bundle(release, tmp_path)
            staged = _extract(bundle, tmp_path / "staged")
            replaced = apply_bundle(staged, home)
    except UpdateError as exc:
        print(f"numbers update: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - the message is the product here
        print(f"numbers update: failed to install the release: {exc}")
        return 1

    if not replaced:
        print("The release contained nothing to install; leaving this install alone.")
        return 1

    # The marker carries the version every later check compares against, so it
    # is written last - only once the swap has actually succeeded.
    try:
        marker = home / "numbers-home.json"
        data = json.loads(marker.read_text(encoding="utf-8"))
        data["version"] = release.version
        marker.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, ValueError):
        pass

    print(f"Updated to v{release.version}: " + ", ".join(replaced))
    print("Open a new terminal and run `numbers` to see the changes.")
    return 0


def cmd_update(args) -> int:
    return run_update(
        check_only=getattr(args, "check", False),
        do_rollback=getattr(args, "rollback", False),
        feed=getattr(args, "feed", None),
    )
