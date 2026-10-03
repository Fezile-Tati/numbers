"""The group feed shows main messages only; replies live in the replies view.

- The feed (open chat, /load-more, /history-chat, /print-chat, the poll) asks
  the server for main messages only and prints "(replies N)" under each.
- /reply lists main messages with their reply counts; one with replies offers
  its replies, so a reply can be answered (3 levels max).
- /replies is read-only: the original message and its replies as a tree;
  typing there sends nothing.
- A new reply while the feed is open is one notice line, not the reply.
- /history-chat loads at most 200 messages.
"""
import sys
import types

import pytest

from hermes_cli.cli_commands_mixin import CLICommandsMixin
from numbers_ext import chat

MAIN = {"id": "p1", "from": "tim", "text": "reply twooooo", "created_at": "2026-10-02T13:20:00Z",
        "reply_count": 12}
LONE = {"id": "m2", "from": "joe", "text": "anyone?", "created_at": "2026-10-02T13:25:00Z"}
R1 = {"id": "r1", "from": "max", "text": "first", "reply_to": "p1", "created_at": "2026-10-02T13:21:00Z"}
R2 = {"id": "r2", "from": "bob", "text": "to max", "reply_to": "r1", "created_at": "2026-10-02T13:22:00Z",
      "reply_preview": {"from": "max", "text": "first"}}


class _FakeCLI(CLICommandsMixin):
    """Pickers pick ``self.picks`` in order; choices answer ``self.answer``."""

    def __init__(self):
        self.pickers = []
        self.picks = [0]
        self.answer = None
        self._numbers_chat_target = {"kind": "group", "id": "g1", "label": "New-Group",
                                     "seen": set(), "texts": {}}

    def _numbers_pick(self, title, hint, entries, on_select):
        self.pickers.append((title, hint, entries))
        on_select(entries[self.picks.pop(0)][1])

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
        return {"id": "new", "text": text, "mine": True, "reply_to": reply_to}

    monkeypatch.setattr(chat, "group_send", _send)
    return calls


@pytest.fixture()
def pages(monkeypatch):
    """chat.page calls, as (kind, before, roots_only)."""
    calls = []

    def _page(kind, chat_id, limit=30, before="", roots_only=True):
        calls.append((kind, before, roots_only))
        return {"messages": [MAIN, LONE], "has_more": False, "next_before": "p1"}

    monkeypatch.setattr(chat, "page", _page)
    return calls


def test_group_page_asks_for_main_messages_only(monkeypatch):
    seen = {}
    monkeypatch.setattr(chat, "_request", lambda method, path, query=None, **k: seen.update(query) or {})
    chat.page("group", "g1")
    assert seen["roots_only"] == "true"
    seen.clear()
    chat.group_page("g1")  # MCP-style callers keep replies by default
    assert "roots_only" not in seen


def test_feed_line_shows_the_reply_count_underneath():
    lines = chat.format_message(dict(MAIN, mine=True)).split("\n")
    assert lines[0].endswith("you: reply twooooo")
    assert lines[1] == "    (replies 12)"
    assert "(replies" not in chat.format_message(LONE)
    assert "(replies" not in chat.format_message(MAIN, counts=False)
    assert chat.message_label(MAIN, with_count=True).split("\n")[1] == "    (replies 12)"


def test_print_chat_repulls_the_latest_main_messages(printed, pages):
    cli = _FakeCLI()
    cli._numbers_chat_target["oldest_id"] = "old"
    cli._handle_print_chat_command("/print-chat")
    assert pages == [("group", "", True)]
    assert cli._numbers_chat_target["oldest_id"] == "p1"  # /load-more starts over from here
    assert any("(replies 12)" in line for line in printed)
    assert not any("first" in line for line in printed)


def test_print_chat_works_for_dms(printed, pages):
    cli = _FakeCLI()
    cli._numbers_chat_target = {"kind": "dm", "id": "tim", "label": "tim", "seen": set(), "texts": {}}
    cli._handle_print_chat_command("/print-chat")
    assert pages[0][0] == "dm"
    assert any("latest messages in @tim" in line for line in printed)


def test_history_chat_loads_at_most_200(printed, monkeypatch):
    asked = []

    def _page(kind, chat_id, limit=30, before="", roots_only=True):
        asked.append((limit, roots_only))
        n = len(asked)
        msgs = [{"id": f"m{n}_{i}", "from": "tim", "text": "x", "created_at": "2026-10-02T10:00:00Z"}
                for i in range(limit)]
        return {"messages": msgs, "has_more": True, "next_before": f"m{n}_0"}

    monkeypatch.setattr(chat, "page", _page)
    cli = _FakeCLI()
    cli.answer = "all"
    cli._handle_history_chat_command("/history-chat")
    assert chat.HISTORY_CAP == cli.HISTORY_CAP == 200
    assert sum(limit for limit, _ in asked) == 200
    assert all(roots for _, roots in asked)
    assert any("end of history: 200 messages" in line for line in printed)


def test_reply_to_a_message_without_replies_needs_no_extra_step(printed, sent, pages):
    cli = _FakeCLI()
    cli.picks = [0]  # newest first: LONE
    cli._handle_reply_command("/reply")
    _title, _hint, entries = cli.pickers[0]
    assert entries[1][0].endswith("\n    (replies 12)")  # MAIN's count, under it
    assert cli._numbers_chat_target["pending"]["message"]["id"] == "m2"


H1 = {"id": "h1", "from": "max", "text": "head one", "reply_to": "p1", "depth": 1, "reply_count": 2,
      "created_at": "2026-10-02T13:21:00Z"}
H2 = {"id": "h2", "from": "joe", "text": "head two", "reply_to": "p1", "depth": 1,
      "created_at": "2026-10-02T13:23:00Z"}
D2 = {"id": "d2", "from": "bob", "text": "to max", "reply_to": "h1", "depth": 2, "created_at": "2026-10-02T13:24:00Z"}
D3 = {"id": "d3", "from": "amy", "text": "to bob", "reply_to": "d2", "depth": 3, "created_at": "2026-10-02T13:25:00Z",
      "reply_preview": {"from": "bob", "text": "to max"}}


@pytest.fixture()
def threads(monkeypatch):
    """group_replies_page: MAIN lists its head replies (nested: and what is
    under them), h1 its two replies."""
    calls = []

    def _page(mid, limit=30, before="", nested=False):
        calls.append((mid, nested))
        if mid == "p1":
            return {"parent": MAIN, "replies": [H1, H2] + ([D2, D3] if nested else []),
                    "has_more": False, "next_before": "h1"}
        return {"parent": H1, "replies": [D2, D3], "has_more": False, "next_before": "d2"}

    monkeypatch.setattr(chat, "group_replies_page", _page)
    return calls


def test_reply_to_a_reply_of_a_reply(printed, sent, pages, threads):
    cli = _FakeCLI()
    cli.picks = [1, 1, 1]  # MAIN; then H1 (newest first: H2, H1); then D2 (newest first: D3, D2)
    cli.answer = "replies"
    cli._handle_reply_command("/reply")
    _t, _h, heads = cli.pickers[2]
    assert [v["id"] for _l, v in heads] == ["h2", "h1"]
    assert heads[1][0].endswith("\n    (sub-replies 2)")  # a head reply's own count, worded apart
    assert not any(label.startswith("⟳") for label, _v in heads)  # 30 max: nothing older to load
    assert [c[1] for c in cli.pickers[3]["choices"]][1] == "Open its sub-replies (2)"
    _t, _h, stack = cli.pickers[4]
    assert [v["id"] for _l, v in stack] == ["d3", "d2"]
    assert "↪ @bob" in stack[0][0] and "(no further replies)" in stack[0][0]
    assert cli._numbers_chat_target["pending"]["message"]["id"] == "d2"

    cli._numbers_chat_intercept("to bob too")
    assert sent == [("g1", "to bob too", "d2")]
    assert any(line.startswith("  ✓ Replied to @bob") for line in printed)


def test_a_depth_3_reply_cannot_be_answered(printed, sent, pages, threads):
    cli = _FakeCLI()
    cli.picks = [1, 1, 0]  # ... then D3
    cli.answer = "replies"
    cli._handle_reply_command("/reply")
    assert "pending" not in cli._numbers_chat_target
    assert any("3 levels max" in line for line in printed)


def test_reply_choice_can_answer_the_main_message(printed, sent, pages):
    cli = _FakeCLI()
    cli.picks = [1]
    cli.answer = "this"
    cli._handle_reply_command("/reply")
    assert cli._numbers_chat_target["pending"]["message"]["id"] == "p1"
    assert [c[0] for c in cli.pickers[1]["choices"]] == ["this", "replies", "cancel"]


@pytest.fixture()
def opened(printed, monkeypatch, threads):
    """/replies on MAIN: the read-only tree view."""
    monkeypatch.setattr(chat, "group_messages", lambda *a, **k: [MAIN, LONE])
    cli = _FakeCLI()
    cli._handle_replies_command("/replies")
    return cli


def test_replies_picker_hint(opened):
    _title, hint, entries = opened.pickers[0]
    assert hint == "Select a message to view its replies"
    assert [v["id"] for _l, v in entries] == ["p1"]


def test_replies_view_is_a_tree(opened, printed, threads):
    assert threads == [("p1", True)]  # one request: head replies and what is under them
    assert opened._numbers_chat_target["thread"]["id"] == "p1"
    start = printed.index("  ╭─ 💬 Original message")
    tree = [line.split("] ", 1)[-1] if "] " in line else line for line in printed[start:start + 8]]
    assert tree == [
        "  ╭─ 💬 Original message",
        "@tim: reply twooooo",
        "  │ (replies 12)",
        "@max: head one",
        "  │  (sub-replies 2)",
        "@bob: to max",
        "@amy: to bob",
        "@joe: head two",
    ]
    lines = printed[start:start + 8]
    assert lines[3].startswith("  ├─ [") and lines[7].startswith("  └─ [")  # head replies
    assert lines[5].startswith("  │  └─ [")      # under head one
    assert lines[6].startswith("  │     └─ [")   # under that reply
    assert printed[start + 8] == "  📖 Read-only · /reply to answer · /exit-reply to go back to the chat"
    assert "📖 replies @tim" in opened._numbers_chat_prompt_prefix()[0][1]


def test_typing_in_replies_view_is_blocked(opened, printed, sent):
    assert opened._numbers_chat_intercept("hello?") is True
    assert sent == []
    assert printed[-1].startswith("  ✗ You're reading replies, nothing was sent.")
    assert printed[-1].endswith("/reply to answer · /exit-reply to go back to the chat")


def test_reply_command_still_sends_from_inside_the_view(opened, printed, sent, pages):
    opened.picks = [1]
    opened.answer = "this"
    opened._handle_reply_command("/reply")
    opened._numbers_chat_intercept("answering")
    assert sent == [("g1", "answering", "p1")]
    assert any("Still reading replies" in line for line in printed)
    opened._numbers_chat_intercept("again")
    assert sent == [("g1", "answering", "p1")]  # read-only again


def test_load_more_in_replies_view_is_refused(opened, printed, threads):
    opened._handle_load_more_command("/load-more")
    assert threads == [("p1", True)]
    assert "Replies show in full (30 max)" in printed[-1]


def test_exit_reply_leaves_the_view(opened, printed, sent):
    opened._handle_exit_reply_command("/exit-reply")
    assert "thread" not in opened._numbers_chat_target
    opened._numbers_chat_intercept("back")
    assert sent == [("g1", "back", "")]


def test_poll_in_the_view_prints_a_notice_not_the_reply(opened, printed, monkeypatch):
    new = {"id": "n9", "from": "kay", "text": "late", "reply_to": "h2", "depth": 2}
    monkeypatch.setattr(chat, "group_replies_page",
                        lambda mid, limit=30, before="", nested=False: {"parent": MAIN, "replies": [H1, new]})
    target = opened._numbers_chat_target
    opened._numbers_poll_thread(target, target["thread"])
    assert printed[-1].startswith("  ↪ new reply from @kay · /replies to see it in place")
    assert not any("late" in line for line in printed)


def test_reply_tree_keeps_a_reply_whose_parent_is_gone():
    lines = chat.reply_tree(MAIN, [dict(D2, reply_to="erased")])
    assert lines[-1].startswith("  └─ [") and lines[-1].endswith("@bob: to max")


def test_reply_tree_without_replies():
    assert chat.reply_tree(LONE, [])[-1] == "  ╰─ No replies yet."


def test_poll_announces_new_replies_in_one_line(printed):
    cli = _FakeCLI()
    target = cli._numbers_chat_target
    cli._numbers_mark_seen(target, [MAIN])
    assert target["counts"]["p1"] == 12
    assert "replies 13" in cli._numbers_reply_notice(MAIN, 13)
    assert cli._numbers_reply_notice(MAIN, 13).startswith("  ↪ new reply to @tim")


def test_help_strings():
    from hermes_cli.commands import resolve_command
    assert "30 most recent" in resolve_command("print-chat").description
    assert "at most 200" in resolve_command("history-chat").description
    assert resolve_command("reply").description == (
        "Pick one of the 30 latest messages, or one of its 30 latest replies, then type your reply")
    assert resolve_command("replies").description == "Pick a message that has replies and view its latest replies"
    assert "agent token" in resolve_command("print-token").description
