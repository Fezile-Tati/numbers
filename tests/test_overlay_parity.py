"""numbers_ext/ in this checkout must match the overlay it is derived from.

WHY THIS EXISTS
---------------
`numbers-dist/overlay/numbers_ext/` is the source of truth; the copy in this
checkout is a build output. `apply_overlay.ps1` step 1 deletes the fork's tree
outright and re-copies the overlay over it, so the two drift silently in both
directions and each direction fails differently:

  fork newer than overlay  -> the next build DELETES the work. This happened:
                              a run of build_numbers_runtime.ps1 wiped a day's
                              work on /sign-in, /reset and /import-hermes, and
                              nothing in the tree recorded that it had.
  overlay newer than fork  -> worse, because the tree looks right. Snippet
                              hooks are marker-guarded (Insert-Hook /
                              Replace-Text skip when their marker is present),
                              so an edited snippet lands only on a FRESH
                              checkout. mixin_handlers.snippet.py sat holding
                              a superseded /reset handler this way.

Both are caught here by the only check that cannot be argued with: bytes.

Skips when the overlay is not reachable, so a fork-only checkout stays green --
the same shape as test_skills_parity.py's `_bundled_skills()`.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

FORK_ROOT = Path(__file__).resolve().parents[1]


def _overlay_ext() -> Path | None:
    """Locate numbers-dist/overlay/numbers_ext, or None if this is a bare fork."""
    candidates = [
        os.environ.get("NUMBERS_OVERLAY_ROOT", ""),
        # The usual layout: `pray` and `numbers` are siblings.
        str(FORK_ROOT.parent / "pray" / "numbers-dist" / "overlay" / "numbers_ext"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return Path(candidate)
    return None


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel_py_files(root: Path) -> set:
    # __pycache__ is a build artefact of whichever tree ran the tests last; it
    # is never part of the overlay's content.
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts
    }


@pytest.fixture(scope="module")
def overlay() -> Path:
    found = _overlay_ext()
    if found is None:
        pytest.skip("no overlay checkout beside this fork")
    return found


def test_no_file_is_missing_from_either_side(overlay):
    """A file present in only one tree is the loudest form of drift: the fork
    copy is about to be deleted, or the overlay copy never shipped."""
    fork_files = _rel_py_files(FORK_ROOT / "numbers_ext")
    overlay_files = _rel_py_files(overlay)
    only_fork = sorted(fork_files - overlay_files)
    only_overlay = sorted(overlay_files - fork_files)
    assert not only_fork, (
        "these exist only in the fork and the next build will DELETE them; "
        f"mirror them into the overlay first: {only_fork}")
    assert not only_overlay, (
        "these exist only in the overlay and never reached the fork; "
        f"re-run apply_overlay.ps1: {only_overlay}")


def test_every_module_is_byte_identical(overlay):
    fork_ext = FORK_ROOT / "numbers_ext"
    differing = []
    for rel in sorted(_rel_py_files(overlay) & _rel_py_files(fork_ext)):
        if _digest(overlay / rel) != _digest(fork_ext / rel):
            differing.append(rel)
    assert not differing, (
        "fork and overlay disagree -- whichever you edited, the other is stale "
        "and one of them is about to win silently: " + ", ".join(differing))


def test_the_commands_this_guards_are_actually_present(overlay):
    """A parity test over an empty tree passes vacuously. Pin the modules the
    fork exists to deliver, so deleting one cannot look like success."""
    for name in ("device_auth.py", "reset.py", "import_hermes.py",
                 "home.py", "update.py", "tokens.py", "ansi.py"):
        assert (overlay / name).is_file(), f"overlay lost {name}"
    tests = _rel_py_files(overlay)
    assert {"tests/test_device_auth.py", "tests/test_reset.py",
            "tests/test_import_hermes.py"} <= tests
