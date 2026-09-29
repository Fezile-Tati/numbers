"""Picker instruction lines: the bracketed numbers are OPTION numbers.

"(0) Select a conversation, (1) — type to search" reads as two things the user
can do. The old line appended the item count ("Select a conversation (1) — type
to search"), which read like an option number and confused people.
"""
from hermes_cli.cli_commands_mixin import CLICommandsMixin


class _FakeCLI(CLICommandsMixin):
    def __init__(self, remote: bool = False):
        self.remote = remote
        self.opened = []
        self.choices = []

    def _numbers_remote_active(self):
        return self.remote

    def _numbers_open_list_picker(self, title, hint, entries, on_select):
        self.opened.append((title, hint))

    def _numbers_choice(self, **k):
        self.choices.append(k)
        return None


def _state(hint, n=3, query=""):
    return {"numbers_list": {"title": "T", "hint": hint}, "model_list": ["x"] * n, "filter": query}


def test_action_hint_gets_numbered_search_option():
    title, line = CLICommandsMixin._numbers_list_picker_labels(_FakeCLI(), _state("Select a conversation"), "", "")
    assert (title, line) == ("T", "(0) Select a conversation, (1) — type to search")


def test_item_count_is_not_shown_as_a_bracketed_number():
    _, line = CLICommandsMixin._numbers_list_picker_labels(_FakeCLI(), _state("Select someone to message", n=2), "", "")
    assert "(2)" not in line


def test_hint_with_its_own_search_option_is_kept_verbatim():
    chat_line = "✉ Invites (📥), group-chats (#), associates (@), (0) type to search"
    _, line = CLICommandsMixin._numbers_list_picker_labels(_FakeCLI(), _state(chat_line), "", "")
    assert line == chat_line


def test_search_mode_still_shows_match_count():
    state = _state("Select a conversation", n=5, query="jo")
    state["_filtered_pairs"] = [(0, "joe")]
    _, line = CLICommandsMixin._numbers_list_picker_labels(_FakeCLI(), state, "", "")
    assert line.startswith("Search: jo") and "(1/5" in line


def test_tui_menu_drops_the_search_option_it_cannot_offer():
    cli = _FakeCLI(remote=True)
    cli._numbers_pick("📥 Inbox", "(0) Select a conversation, (1) — type to search", [("@joe", "joe")], lambda v: None)
    assert cli.choices[0]["detail"] == "(0) Select a conversation"
    cli._numbers_pick("💬 Chat", "✉ Invites (📥), group-chats (#), associates (@), (0) type to search",
                      [("@joe", "joe")], lambda v: None)
    assert cli.choices[1]["detail"] == "✉ Invites (📥), group-chats (#), associates (@)"
