"""Guard the dashboard's cosmetic rebrand against regressions.

WHY
---
The rebrand is a *sweep*, not a fixed list of edits: apply_overlay.ps1 rewrites
every user-visible "Hermes" in web/src, across 17 locale files and ~100 prose
literals in the pages. A sweep has no natural completion signal - the way it
fails is that someone adds a new string upstream, the overlay does not know
about it, and one stray "Hermes" ships to a user. This test is that signal.

WHAT IT DELIBERATELY ALLOWS
---------------------------
Rebranding is cosmetic only. The back-end contract keeps its upstream names, so
the following are not findings and must never be "fixed":

  * import paths and module names        (from "@/lib/hermes-api")
  * the plugin SDK globals               (window.__HERMES_PLUGIN_SDK__)
  * localStorage / config keys           ("hermes.theme")
  * env vars and API routes              (HERMES_HOME, /api/update-hermes)
  * TypeScript identifiers               (updateHermes, isHermesRunning)

Every one of those is a string a *user never reads*. What a user reads is prose
and labels, and that is what the scan targets: a "Hermes" or "Nous Research"
that survives after the identifier-shaped and path-shaped occurrences are
removed from the line.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB_SRC = Path(__file__).resolve().parents[1] / "web" / "src"

# Occurrences that are machine-facing, stripped before the line is judged.
# Order matters: the broadest identifier pattern runs last.
_ALLOWED = (
    re.compile(r"__HERMES[A-Z_]*__"),                  # SDK / plugin globals
    re.compile(r"HERMES_[A-Z0-9_]+"),                  # env vars
    # Quoted strings that are addresses rather than prose: import specifiers
    # ("@hermes/shared"), routes ("/api/update-hermes"), storage and manifest
    # keys ("hermes:plugin-manifests", "hermes.theme").
    re.compile(r"""["'`][^"'`]*hermes[^"'`]*[/:.][^"'`]*["'`]""", re.I),
    re.compile(r"""["'`][^"'`]*[/:.][^"'`]*hermes[^"'`]*["'`]""", re.I),
    re.compile(r"[a-z0-9]_hermes|hermes_[a-z0-9]", re.I),  # snake_case API fields
    re.compile(r"hermes[-_.][A-Za-z0-9_.-]+", re.I),       # hermes-api, hermes.theme
    re.compile(r"(?<=[A-Za-z0-9])Hermes|Hermes(?=[A-Za-z0-9])"),      # identifiers
    re.compile(r"^\s*(//|/?\*).*$"),                   # comments
)

_FINDING = re.compile(r"Hermes|Nous Research", re.I)


def _user_visible(line: str) -> str:
    for pattern in _ALLOWED:
        line = pattern.sub("", line)
    return line


def _sources():
    if not WEB_SRC.is_dir():
        pytest.skip(f"dashboard sources not present at {WEB_SRC}")
    for path in sorted(WEB_SRC.rglob("*")):
        if path.suffix not in (".ts", ".tsx") or not path.is_file():
            continue
        # Test files assert on upstream's own wire values on purpose; rewriting
        # them would make the tests lie about what the server sends.
        if ".test." in path.name or ".spec." in path.name:
            continue
        yield path


def test_no_user_visible_hermes_branding_remains():
    findings = []
    for path in _sources():
        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if not _FINDING.search(line):
                continue
            if _FINDING.search(_user_visible(line)):
                findings.append(f"{path.relative_to(WEB_SRC)}:{number}: {line.strip()}")

    assert not findings, (
        "user-visible upstream branding survived the overlay sweep:\n  "
        + "\n  ".join(findings[:40])
        + (f"\n  ... and {len(findings) - 40} more" if len(findings) > 40 else "")
        + "\n\nAdd a Replace-Text/Replace-Regex step to apply_overlay.ps1. If a hit "
          "is machine-facing (a key, path or identifier), widen _ALLOWED instead - "
          "never rename the back-end thing itself."
    )


def test_the_scan_would_actually_catch_a_regression():
    """A filter this permissive has to be shown to still fire."""
    assert _FINDING.search(_user_visible('  title: "Update Hermes",'))
    assert _FINDING.search(_user_visible("  <p>Powered by Nous Research</p>"))
    # ... and to stay quiet on the machine-facing shapes it exists to permit.
    assert not _FINDING.search(_user_visible('import { api } from "@/lib/hermes-api";'))
    assert not _FINDING.search(_user_visible("  window.__HERMES_PLUGIN_SDK__.React;"))
    assert not _FINDING.search(_user_visible('  localStorage.getItem("hermes.theme");'))
    assert not _FINDING.search(_user_visible("  const running = isHermesRunning(s);"))
    assert not _FINDING.search(_user_visible('  env: { HERMES_HOME: home },'))
    assert not _FINDING.search(_user_visible('  import x from "@hermes/shared";'))
    assert not _FINDING.search(_user_visible('  const k = "hermes:plugin-manifests";'))
    assert not _FINDING.search(_user_visible("  if (status?.can_update_hermes) return;"))
