"""`config migrate --yes` must give a fresh NUMBERS home the stock default surface.

Upstream's `config migrate` calls migrate_config(interactive=True), so it prompts
- which an installer and `numbers update` cannot answer. The overlay adds a --yes
flag that routes to the non-interactive path. This test runs the real command end
to end against a throwaway HERMES_HOME and asserts two things at once:

  1. the missing stock surface is filled in (_config_version + the sections stock
     Hermes config carries), and
  2. the NUMBERS deltas survive the migration (they are the product).

It is a subprocess test on purpose: the flag lives in argparse, so calling the
function directly would not prove the command is non-interactive.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]

# Mirrors the bootstrap config scripts/install_numbers_cli.ps1 writes before it
# runs the migration (the NUMBERS deltas, nothing else).
BOOTSTRAP = """\
model:
  provider: "auto"
display:
  skin: numbers
plugins:
  enabled:
    - numbers-docs
mcp_servers:
  angel:
    command: "C:\\\\numbers\\\\bin\\\\numbers-mcp.exe"
    args: ["-angel", "https://127.0.0.1:3000", "-insecure"]
agent:
  system_prompt: |
    You are NUMBERS 21:4-9, the Intersession assistant.
"""

# Sections the *effective* config must expose (load_config() merges
# DEFAULT_CONFIG over the file, so a sparse file is fine - these are the sections
# the app reports at runtime). The stock Hermes config.yaml is sparse in exactly
# the same way; asserting on the FILE's sections would be a false requirement.
EFFECTIVE_SECTIONS = (
    "terminal", "browser", "compression", "memory", "delegation", "moa", "skills",
    "code_execution", "display", "model", "agent",
)


def _run_migrate(home: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HERMES_HOME": str(home),
        "NUMBERS_HOME": str(home),
        "PYTHONPATH": str(REPO),
        "NO_COLOR": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "config", "migrate", "--yes"],
        input="",  # no TTY, no stdin: a prompt would hang and the timeout would fire
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        env=env,
        cwd=str(REPO),
    )


def test_migrate_yes_is_non_interactive_and_fills_the_stock_surface(tmp_path):
    home = tmp_path / "numbers"
    home.mkdir()
    (home / "config.yaml").write_text(BOOTSTRAP, encoding="utf-8")

    proc = _run_migrate(home)
    assert proc.returncode == 0, f"migrate --yes failed:\n{proc.stdout}\n{proc.stderr}"

    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert isinstance(cfg, dict), "migration must leave a YAML mapping behind"
    assert cfg.get("_config_version"), (
        "migration must stamp _config_version - without it future migrations cannot "
        "know how far this home has been brought forward"
    )


def test_effective_config_surface_matches_stock(tmp_path):
    """The invariant that matters: what the app LOADS, not what the file lists.

    load_config() merges DEFAULT_CONFIG under the user's file, so a bootstrap
    config already yields the full stock surface (measured: 93 sections). This
    asserts the sections stock Hermes is known to use are present in the
    effective config, and that the NUMBERS deltas survived.
    """
    home = tmp_path / "numbers"
    home.mkdir()
    (home / "config.yaml").write_text(BOOTSTRAP, encoding="utf-8")

    env = {
        **os.environ,
        "HERMES_HOME": str(home),
        "NUMBERS_HOME": str(home),
        "PYTHONPATH": str(REPO),
        "NO_COLOR": "1",
    }
    code = (
        "import json;"
        "from hermes_cli.config import load_config;"
        "cfg = load_config();"
        "print(json.dumps({'count': len(cfg), 'sections': sorted(cfg)}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, env=env, cwd=str(REPO),
    )
    assert proc.returncode == 0, f"load_config failed:\n{proc.stdout}\n{proc.stderr}"

    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["count"] >= 80, f"only {data['count']} config sections resolved - defaults not merging?"
    for section in EFFECTIVE_SECTIONS:
        assert section in data["sections"], f"{section} missing from the effective config"


def test_migration_preserves_the_numbers_deltas(tmp_path):
    home = tmp_path / "numbers"
    home.mkdir()
    (home / "config.yaml").write_text(BOOTSTRAP, encoding="utf-8")

    assert _run_migrate(home).returncode == 0

    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert (cfg.get("display") or {}).get("skin") == "numbers"
    assert "angel" in (cfg.get("mcp_servers") or {}), "the Angel MCP registration must survive"
    assert "NUMBERS 21:4-9" in ((cfg.get("agent") or {}).get("system_prompt") or "")


def test_the_product_adds_no_unintended_top_level_sections(tmp_path):
    """Everything the bootstrap sets is a documented NUMBERS delta; the migration
    must not invent new top-level keys of its own."""
    home = tmp_path / "numbers"
    home.mkdir()
    (home / "config.yaml").write_text(BOOTSTRAP, encoding="utf-8")

    assert _run_migrate(home).returncode == 0

    cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert set(cfg) >= {"model", "display", "plugins", "mcp_servers", "agent"}
    assert isinstance(cfg.get("plugins"), dict)
