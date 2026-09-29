"""P11 -- `/clear` must not reset the model/provider (NUMBERS-CLEAR hooks).

Copied by apply_overlay.ps1 step 1b to ``tests/cli/test_clear_keeps_model.py``.

Two regressions, both rooted in ``HermesCLI.new_session``:

* ``/clear`` ran the full ``/new`` model reset, so a session-scoped ``/model``
  pick snapped back to the config default ("[1] Approve Once clears, but the
  provider changes back to the default").
* ``/new`` reset to ``CLI_CONFIG`` -- the startup snapshot -- so a pick saved to
  config.yaml mid-session (``--global`` / first-ever pick) was undone too.

Disk is never touched: ``load_config_readonly`` and ``switch_model`` are patched.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.cli.test_cli_new_session import (  # noqa: F401 -- fixture re-export
    _prepare_cli_with_active_session,
    _reset_session_id_context,
)

_SAVED_CFG = {"model": {"default": "cfg/saved-mid-session", "provider": "saved-provider"}}


def _with_live_pick(tmp_path):
    """A CLI whose live runtime differs from the config default (a /model pick)."""
    cli = _prepare_cli_with_active_session(tmp_path)
    cli.console = MagicMock()
    cli.show_banner = MagicMock()
    cli.agent.switch_model = MagicMock()
    cli.model = "picked/model"
    cli.provider = "picked-provider"
    return cli


def test_clear_keeps_live_model_and_provider(tmp_path):
    cli = _with_live_pick(tmp_path)
    old_session_id = cli.session_id
    switch = MagicMock(side_effect=AssertionError("/clear must not re-derive the model"))

    with patch("hermes_cli.model_switch.switch_model", switch), patch(
        "hermes_cli.config.load_config_readonly", return_value=_SAVED_CFG
    ):
        cli.process_command("/clear")

    assert cli.session_id != old_session_id  # still a fresh session...
    assert cli.conversation_history == []
    assert (cli.model, cli.provider) == ("picked/model", "picked-provider")  # ...same runtime
    switch.assert_not_called()
    cli.agent.switch_model.assert_not_called()


def test_new_resets_to_current_config_yaml_not_startup_snapshot(tmp_path):
    cli = _with_live_pick(tmp_path)
    result = SimpleNamespace(
        success=True, new_model="cfg/saved-mid-session", target_provider="saved-provider",
        api_key="", base_url="", api_mode="", runtime_capabilities=None,
    )
    switch = MagicMock(return_value=result)

    with patch("hermes_cli.model_switch.switch_model", switch), patch(
        "hermes_cli.config.load_config_readonly", return_value=_SAVED_CFG
    ):
        cli.process_command("/new")

    # The reset target is what config.yaml says NOW, not the CLI_CONFIG default.
    assert switch.call_args.kwargs["raw_input"] == "cfg/saved-mid-session"
    assert switch.call_args.kwargs["explicit_provider"] == "saved-provider"
    assert (cli.model, cli.provider) == ("cfg/saved-mid-session", "saved-provider")
