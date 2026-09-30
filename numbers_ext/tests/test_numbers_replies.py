"""Group-chat replies, edits, /group-stats and /group-remove in the CLI.

/reply used to read the reply text with a prompt from the picker's worker
thread; cli.py's prompt returns nothing off the main thread, so every reply
printed "Reply cancelled." and the next line went out as a plain message. Now
the pick only arms the composer and the next typed line is the reply.
"""
import sys
import types

import pytest

from hermes_cli.cli_commands_mixin import CLICommandsMixin
from numbers_ext import chat

PARENT = {"id": "p1", "from": "tim", "text": "helloooo every bodyyyy", "created_at": "2026-09-30T10:42:00Z"}
REPLY = {"id": "r1", "from": "max", "text": "hi tim", "reply_to": "p1", "created_at": "2026-09-30T10:44:00Z",
         "mine": True}
LONE = {"id": "m2", "from": "joe", "text": "anyone?", "created_at": "2026-09-30T10:45:00Z"}


class _FakeCLI(CLICommandsMixin):
    """Pickers pick ``self.pick`` (an index) at once; choices answer ``self.answer``."""

    def __init__(self):
        self.pickers = []
        self.pick = 0
        self.answer = None
        self._numbers_chat_target = {"kind": "group", "id": "g1", "label": "Test-group",
                                     "seen": set(), "texts": {}}

    def _numbers_pick(self, title, hint, entries, on_select):
        self.pickers.append((title, hint, entries))
        on_select(entries[self.pick][1])

    def _numbers_choice(self, **k):
        self.pickers.append(k)
        return self.answer

    def _invalidate(self, min_interval=0.0):
        pass


@pytest.fixture()
def printed(monkeypatch):
    lines = []
    fake_cli = types.ModuleType("cli")
    fake_cli._cprint = lambda s="": lines.append(s)
    monkeypatch.setitem(sys.modules, "cli", fake_cli)
    monkeypatch.setattr(chat, "_agent_token", lambda: "tok")
    return lines


@pytest.fixture()
def sent(monkeypatch):
    calls = []

    def _send(chat_id, text, reply_to=""):
        calls.append((chat_id, text, reply_to))
        return {"id": "new", "text": text, "mine": True}

    monkeypatch.setattr(chat, "group_send", _send)
    return calls


def test_reply_arms_the_composer_and_the_next_line_is_the_reply(printed, sent, monkeypatch):
    monkeypatch.setattr(chat, "page", lambda *a, **k: {"messages": [PARENT, LONE]})
    cli = _FakeCLI()
    cli.pick = 1  # newest first: LONE, PARENT
    cli._handle_reply_command("/reply")

    assert not any("cancelled" in line for line in printed)
    assert cli._numbers_chat_target["pending"]["message"]["id"] == "p1"
    assert "↪ @tim" in cli._numbers_chat_prompt_prefix()[0][1]

    assert cli._numbers_chat_intercept("hi tim") is True
    assert sent == [("g1", "hi tim", "p1")]
    # One-shot: the line after that is a plain message again.
    assert "pending" not in cli._numbers_chat_target
    cli._numbers_chat_intercept("and everyone")
    assert sent[-1] == ("g1", "and everyone", "")


def test_replies_lists_only_messages_with_replies_then_threads(printed, sent, monkeypatch):
    monkeypatch.setattr(chat, "group_messages", lambda *a, **k: [PARENT, REPLY, LONE])
    monkeypatch.setattr(chat, "group_replies", lambda mid: (PARENT, [REPLY]))
    cli = _FakeCLI()
    cli._handle_replies_command("/replies")

    _title, _hint, entries = cli.pickers[0]
    assert [value["id"] for _label, value in entries] == ["p1"]
    assert "(1 reply)" in entries[0][0] and "@tim" in entries[0][0]
    assert cli._numbers_chat_target["thread"]["id"] == "p1"
    assert any("hi tim" in line for line in printed)

    # In the thread, every line replies to the parent until /exit-reply.
    cli._numbers_chat_intercept("amen")
    cli._numbers_chat_intercept("amen again")
    assert sent == [("g1", "amen", "p1"), ("g1", "amen again", "p1")]
    cli._handle_exit_reply_command("/exit-reply")
    assert "thread" not in cli._numbers_chat_target
    cli._numbers_chat_intercept("back to the group")
    assert sent[-1] == ("g1", "back to the group", "")


def test_replies_with_no_threads_says_so(printed, monkeypatch):
    monkeypatch.setattr(chat, "group_messages", lambda *a, **k: [LONE])
    cli = _FakeCLI()
    cli._handle_replies_command("/replies")
    assert cli.pickers == []
    assert any("No recent message here has replies" in line for line in printed)


def test_msg_edit_takes_the_next_line_as_the_new_text(printed, monkeypatch):
    monkeypatch.setattr(chat, "page", lambda *a, **k: {"messages": [REPLY]})
    edits = []
    monkeypatch.setattr(chat, "group_edit_message", lambda mid, text: edits.append((mid, text)) or {})
    cli = _FakeCLI()
    cli._handle_msg_edit_command("/msg-edit")
    assert not any("cancelled" in line for line in printed)
    cli._numbers_chat_intercept("hi tim!")
    assert edits == [("r1", "hi tim!")]


def test_exit_reply_cancels_a_pending_reply(printed, sent, monkeypatch):
    monkeypatch.setattr(chat, "page", lambda *a, **k: {"messages": [PARENT]})
    cli = _FakeCLI()
    cli._handle_reply_command("/reply")
    cli._handle_exit_reply_command("/exit-reply")
    assert any("Reply cancelled." in line for line in printed)
    cli._numbers_chat_intercept("plain")
    assert sent == [("g1", "plain", "")]


def test_msg_delete_warns_that_replies_go_too(printed, monkeypatch):
    parent = dict(PARENT, mine=True, reply_count=2)
    monkeypatch.setattr(chat, "page", lambda *a, **k: {"messages": [parent]})
    deleted = []
    monkeypatch.setattr(chat, "group_delete_message", lambda mid: deleted.append(mid))
    cli = _FakeCLI()
    cli._numbers_chat_target["thread"] = parent
    cli.answer = "delete"
    cli._handle_msg_delete_command("/msg-delete")
    assert "and its 2 replies" in cli.pickers[-1]["title"]
    assert deleted == ["p1"]
    assert "thread" not in cli._numbers_chat_target  # the thread it showed is gone


def test_group_remove_asks_first_and_skips_inactive_rows(printed, monkeypatch):
    detail = {"id": "g1", "owner_id": "u1", "my_role": "admin", "members": [
        {"user_id": "u1", "username": "max", "role": "owner", "status": "active"},
        {"user_id": "u2", "username": "tim", "role": "member", "status": "active"},
        {"user_id": "u3", "username": "gid", "role": "admin", "status": "active"},
        {"user_id": "u4", "username": "old", "role": "member", "status": "active", "blocked": True},
    ]}
    monkeypatch.setattr(chat, "group_read", lambda cid: detail)
    removed = []
    monkeypatch.setattr(chat, "group_remove_member", lambda cid, u: removed.append(u) or {})
    cli = _FakeCLI()
    cli._handle_group_remove_command("/group-remove")
    _title, _hint, entries = cli.pickers[0]
    assert [v for _l, v in entries] == ["tim"]  # not the owner, another admin or a blocked row
    assert removed == []  # the confirmation said no
    cli.answer = "remove"
    cli._handle_group_remove_command("/group-remove")
    assert removed == ["tim"]


def test_group_create_takes_the_description_after_a_bar(printed, monkeypatch):
    created = []
    monkeypatch.setattr(chat, "group_create",
                        lambda name, members, desc: created.append((name, desc)) or {"id": "g2", "name": name})
    cli = _FakeCLI()
    cli._numbers_enter_chat = lambda *a, **k: None
    cli._numbers_pick_member_to_invite = lambda *a, **k: None
    cli._handle_group_create_command("/group-create Elders | Weekly prayer")
    assert created == [("Elders", "Weekly prayer")]


def test_group_stats_lines():
    lines = chat.group_stats_lines({
        "name": "Test-group", "description": "Weekly prayer", "owner_id": "u1",
        "owner_username": "max", "owner_account_type": "leader", "member_count": 2, "max_members": 50,
        "my_role": "member",
        "members": [
            {"user_id": "u1", "username": "max", "role": "owner", "status": "active"},
            {"user_id": "u2", "username": "tim", "role": "admin", "status": "active"},
        ],
    })
    text = "\n".join(lines)
    assert "Weekly prayer" in text
    assert "@max  (leader)" in text
    assert "2 / 50" in text
    assert "Admins:       @tim" in text


def test_reply_parents_counts_and_orders_by_latest_reply():
    older = {"id": "a", "created_at": "2026-09-30T09:00:00Z", "reply_count": 3}
    newer = {"id": "b", "created_at": "2026-09-30T08:00:00Z"}
    reply_b = {"id": "c", "reply_to": "b", "created_at": "2026-09-30T11:00:00Z"}
    got = chat.reply_parents([newer, older, reply_b])
    assert [(m["id"], n) for m, n in got] == [("b", 1), ("a", 3)]
