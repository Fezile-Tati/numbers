"""Chat is for signed-in users only.

Every chat command must stop before any picker or network call when this
device has no Intersession token, and a chat left open when the token goes
away must close without sending -- or handing to the agent -- what was typed.
"""
import sys
import types

import pytest

from hermes_cli.cli_commands_mixin import CLICommandsMixin
from numbers_ext import chat

CHAT_HANDLERS = [
    "_handle_chat_command", "_handle_inbox_command", "_handle_invites_command",
    "_handle_list_associates_command", "_handle_groups_command",
    "_handle_list_messages_command", "_handle_group_create_command",
    "_handle_group_add_command", "_handle_group_remove_command",
    "_handle_group_admins_command", "_handle_group_members_command",
    "_handle_group_leave_command", "_handle_group_rename_command",
    "_handle_group_delete_command", "_handle_msg_edit_command",
    "_handle_msg_delete_command", "_handle_load_more_command",
    "_handle_reply_command", "_handle_replies_command", "_handle_mentions_command",
]


class _FakeCLI(CLICommandsMixin):
    def __init__(self):
        self.pickers = []
        self.invalidated = 0

    def _numbers_open_list_picker(self, *a, **k):
        self.pickers.append(a)

    def _numbers_choice(self, **k):
        self.pickers.append(k)
        return None

    def _invalidate(self, min_interval=0.0):
        self.invalidated += 1


@pytest.fixture()
def printed(monkeypatch):
    lines = []
    fake_cli = types.ModuleType("cli")
    fake_cli._cprint = lambda s="": lines.append(s)
    monkeypatch.setitem(sys.modules, "cli", fake_cli)
    return lines


@pytest.fixture()
def signed_out(monkeypatch):
    monkeypatch.setattr(chat, "_agent_token", lambda: "")

    def _no_network(*a, **k):
        raise AssertionError("a chat request was made while signed out")

    monkeypatch.setattr(chat, "_request", _no_network)


@pytest.mark.parametrize("handler", CHAT_HANDLERS)
def test_every_chat_command_requires_sign_in(handler, printed, signed_out):
    cli = _FakeCLI()
    cli._numbers_chat_target = {"kind": "group", "id": "g1", "label": "Elders", "seen": set()}
    getattr(cli, handler)("/cmd Elders")
    assert any(chat.SIGN_IN_MESSAGE in line for line in printed)
    assert cli.pickers == []
    assert cli._numbers_chat_target is None  # any open chat is closed


def test_open_chat_is_closed_and_text_swallowed_after_sign_out(printed, signed_out):
    cli = _FakeCLI()
    cli._numbers_chat_target = {"kind": "dm", "id": "deborah", "label": "deborah", "seen": set()}
    # True == consumed: the line must not fall through to the agent either.
    assert cli._numbers_chat_intercept("private words") is True
    assert cli._numbers_chat_target is None
    assert any("not sent" in line for line in printed)


def test_auth_error_mid_call_closes_the_chat(printed):
    cli = _FakeCLI()
    cli._numbers_chat_target = {"kind": "dm", "id": "deborah", "label": "deborah", "seen": set()}

    def _expired():
        raise chat.ChatAuthError("Your Intersession session has expired or was revoked. Run /sign-in.")

    ok, _ = cli._numbers_chat_call(_expired)
    assert ok is False
    assert cli._numbers_chat_target is None
    assert any("/sign-in" in line for line in printed)


def test_exit_chat_needs_no_token(printed, signed_out):
    cli = _FakeCLI()
    cli._numbers_chat_target = {"kind": "dm", "id": "deborah", "label": "deborah", "seen": set()}
    cli._handle_exit_chat_command("/exit-chat")
    assert cli._numbers_chat_target is None
