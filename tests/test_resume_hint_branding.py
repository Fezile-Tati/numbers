"""The resume hint must name the command that exists on THIS install.

WHY
---
The dashboard was already rebranded: apply_overlay.ps1's locale sweep rewrites
``hermes <verb>`` to ``numbers <verb>`` across web/src, so the UI tells users
``numbers ...``. The CLI's own exit summary still said::

    Resume this session with:
      hermes --resume 20260918_163733_3cdc0e

which is a command a NUMBERS-only machine does not have. One product, two
answers to "what do I type".

The name comes from ``_cli_prog_name()`` (hermes_cli/_parser.py), which reads
the active skin and falls back to the literal "hermes". That fallback is the
contract these tests pin from both sides: a stock/unskinned harness must render
byte-identically to upstream, and only a branded skin may change the output.

WHERE THIS FILE RUNS
--------------------
Home is ``<fork>/tests/test_resume_hint_branding.py``; the copy under
``numbers-dist/overlay/files/tests/`` is the delivery vehicle and skips where
hermes_cli is not next to it.
"""
from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

_FORK_ROOT = Path(__file__).resolve().parents[1]

if not (_FORK_ROOT / "hermes_cli" / "_parser.py").is_file():
    pytest.skip(
        f"not running inside the fork checkout (looked in {_FORK_ROOT}); "
        "run this file from <fork>/tests/",
        allow_module_level=True,
    )

SESSION_ID = "20260918_163733_3cdc0e"


def _use_temp_home(monkeypatch, tmp_path, *, skin_name=None):
    """Point HERMES_HOME at a temp home holding (at most) one skin file.

    Mirrors the helper in test_dashboard_port_isolation.py: the active-skin
    cache is cleared so ``set_active_skin`` really re-reads from the temp home.
    """
    from hermes_cli import skin_engine

    skins = tmp_path / "skins"
    skins.mkdir(parents=True, exist_ok=True)
    if skin_name is not None:
        (skins / f"{skin_name}.yaml").write_text(
            f'name: {skin_name}\nbranding:\n  cli_name: "{skin_name}"\n',
            encoding="utf-8",
        )

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(skin_engine, "_active_skin", None)
    monkeypatch.setattr(skin_engine, "_active_skin_name", "default")
    return skin_engine


def _make_cli(session_id=SESSION_ID):
    from cli import HermesCLI

    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.session_id = session_id
    # _print_exit_summary only prints the resume hint when msg_count > 0.
    cli_obj.conversation_history = [{"role": "user", "content": "hi"}]
    cli_obj.agent = None
    cli_obj._session_db = None
    cli_obj.session_start = datetime.now()
    return cli_obj


def _exit_summary(capsys, monkeypatch, tmp_path, *, skin_name=None):
    skin_engine = _use_temp_home(monkeypatch, tmp_path, skin_name=skin_name)
    if skin_name is not None:
        skin_engine.set_active_skin(skin_name)
    cli_obj = _make_cli()
    with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"):
        cli_obj._print_exit_summary()
    return capsys.readouterr().out


def test_unskinned_install_still_says_hermes(capsys, monkeypatch, tmp_path):
    """The upstream literal is the fallback — a stock harness is unchanged."""
    out = _exit_summary(capsys, monkeypatch, tmp_path)
    assert f"hermes --resume {SESSION_ID}" in out


def test_a_branded_skin_says_numbers(capsys, monkeypatch, tmp_path):
    out = _exit_summary(capsys, monkeypatch, tmp_path, skin_name="numbers")
    assert f"numbers --resume {SESSION_ID}" in out
    # Not merely "numbers appears somewhere": the hermes spelling must be gone,
    # or the user is still shown a command this install does not provide.
    assert "hermes --resume" not in out


def test_the_title_hint_is_branded_too(capsys, monkeypatch, tmp_path):
    """Both hint lines, not just the first — they are copied as a pair."""
    skin_engine = _use_temp_home(monkeypatch, tmp_path, skin_name="numbers")
    skin_engine.set_active_skin("numbers")
    cli_obj = _make_cli()
    with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"), \
            patch("cli.get_session_title", return_value="PDZ9RTGK code inquiry", create=True):
        cli_obj._print_exit_summary()
    out = capsys.readouterr().out
    assert "hermes -c" not in out


def _code_only(func):
    """Source minus the docstring — which names the old literal to explain it.

    Borrowed from numbers_ext/tests/test_numbers_prompt.py, for the same
    reason: save_conversation's docstring documents the ``hermes --resume``
    hint in prose, so a bare substring check reads its own explanation as
    the bug.
    """
    body = inspect.getsource(func).split('"""')
    return body[0] + "".join(body[2:])


def test_save_hint_is_branded():
    """/save prints its own resume hint, from a different code path."""
    import cli as cli_mod

    source = _code_only(cli_mod.HermesCLI.save_conversation)
    assert "hermes --resume" not in source, (
        "the /save resume hint still hardcodes 'hermes'"
    )
    assert "_cli_prog_name()" in source


def test_tui_exit_summary_is_branded():
    """`--tui` sessions print their own summary in hermes_cli/main.py.

    Driven structurally: the function opens a session DB, so a behavioural test
    would assert more about sqlite than about branding.
    """
    from hermes_cli import main as main_mod

    source = inspect.getsource(main_mod._print_tui_exit_summary)
    assert "hermes --tui --resume" not in source
    assert "hermes --tui -c" not in source
    assert "_cli_prog_name()" in source
