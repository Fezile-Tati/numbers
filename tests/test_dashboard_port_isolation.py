"""The NUMBERS dashboard must not share a default port with stock Hermes.

WHY
---
Ports are machine-wide. Both installs defaulted to 9119, so whichever started
first owned the port and the other one silently ATTACHED to it -- the
attach decision is ``_dashboard_listening``, a bare TCP-connect probe that
proves *a* dashboard is up, never *whose*. That is how `hermes dashboard` ended
up opening the NUMBERS UI.

The default now comes from the active skin's ``branding.dashboard_port``, so a
stock install (skin "default", or any skin without the key) keeps upstream's
literal 9119 and only a branded skin moves.

WHERE THIS FILE RUNS
--------------------
Home is ``<fork>/tests/test_dashboard_port_isolation.py``; the copy under
``numbers-dist/overlay/files/tests/`` is the delivery vehicle and skips where
hermes_cli is not next to it.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

_FORK_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_DEFAULT = 9119

if not (_FORK_ROOT / "hermes_cli" / "subcommands" / "dashboard.py").is_file():
    pytest.skip(
        f"not running inside the fork checkout (looked in {_FORK_ROOT}); "
        "run this file from <fork>/tests/",
        allow_module_level=True,
    )


def _use_temp_home(monkeypatch, tmp_path, *, port=None):
    """Point HERMES_HOME at a temp home holding (at most) one skin file.

    Returns the skin module with its active-skin cache cleared, so
    ``set_active_skin`` really re-reads from the temp home.
    """
    from hermes_cli import skin_engine

    skins = tmp_path / "skins"
    skins.mkdir(parents=True, exist_ok=True)
    if port is not None:
        (skins / "numbers.yaml").write_text(
            f'name: numbers\nbranding:\n  dashboard_port: "{port}"\n', encoding="utf-8"
        )

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(skin_engine, "_active_skin", None)
    monkeypatch.setattr(skin_engine, "_active_skin_name", "default")
    return skin_engine


def test_stock_install_keeps_upstream_default(monkeypatch, tmp_path):
    from hermes_cli._parser import _cli_dashboard_port

    _use_temp_home(monkeypatch, tmp_path)

    assert _cli_dashboard_port() == UPSTREAM_DEFAULT


def test_a_skins_dashboard_port_moves_the_default(monkeypatch, tmp_path):
    from hermes_cli._parser import _cli_dashboard_port

    skin_engine = _use_temp_home(monkeypatch, tmp_path, port=9120)
    skin_engine.set_active_skin("numbers")

    assert _cli_dashboard_port() == 9120
    assert _cli_dashboard_port() != UPSTREAM_DEFAULT


@pytest.mark.parametrize("bogus", ["not-a-port", "0", "70000", "", "-1"])
def test_a_bogus_port_falls_back_instead_of_crashing(monkeypatch, tmp_path, bogus):
    from hermes_cli._parser import _cli_dashboard_port

    skin_engine = _use_temp_home(monkeypatch, tmp_path, port=bogus)
    skin_engine.set_active_skin("numbers")

    assert _cli_dashboard_port() == UPSTREAM_DEFAULT


def test_dashboard_parser_default_comes_from_the_skin(monkeypatch, tmp_path):
    from hermes_cli.subcommands.dashboard import _add_server_runtime_args

    _use_temp_home(monkeypatch, tmp_path)

    parser = argparse.ArgumentParser()
    _add_server_runtime_args(parser)

    assert parser.parse_args([]).port == UPSTREAM_DEFAULT


def test_dashboard_parser_default_moves_with_a_branded_skin(monkeypatch, tmp_path):
    from hermes_cli.subcommands.dashboard import _add_server_runtime_args

    skin_engine = _use_temp_home(monkeypatch, tmp_path, port=9120)
    skin_engine.set_active_skin("numbers")

    parser = argparse.ArgumentParser()
    _add_server_runtime_args(parser)

    assert parser.parse_args([]).port == 9120
    # An explicit --port still wins over the skin.
    assert parser.parse_args(["--port", "9500"]).port == 9500


def _shipped_numbers_skin() -> Path | None:
    """The NUMBERS skin as shipped, wherever this checkout can see it."""
    candidates = [
        os.environ.get("NUMBERS_SKIN_PATH") or "",
        # Sibling layout: Workspace/numbers + Workspace/pray.
        str(_FORK_ROOT.parent / "pray" / "numbers-dist" / "skins" / "numbers.yaml"),
    ]
    for raw in candidates:
        if raw and Path(raw).is_file():
            return Path(raw)
    return None


def test_the_shipped_numbers_skin_declares_its_own_port():
    skin = _shipped_numbers_skin()
    if skin is None:
        pytest.skip("distribution repo not beside this checkout")

    import yaml

    data = yaml.safe_load(skin.read_text(encoding="utf-8")) or {}
    raw = (data.get("branding") or {}).get("dashboard_port")

    assert raw, f"{skin} must declare branding.dashboard_port"
    assert int(str(raw)) != UPSTREAM_DEFAULT, (
        "the shipped skin must not default to upstream's port -- that is the "
        "collision this key exists to prevent"
    )
