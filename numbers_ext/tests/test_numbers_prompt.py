"""The shared prompt helper behind /reset, /sign-in and /import-hermes.

Regression guard: an earlier version called prompt_toolkit.shortcuts.prompt(),
which builds a second Application on top of the running one. Every NUMBERS
command that asks a question died with "aclose(): asynchronous generator is
already running", dropping the user at "Press ENTER to continue...".
"""
import inspect

from hermes_cli.cli_commands_mixin import CLICommandsMixin


def _source():
    return inspect.getsource(CLICommandsMixin._numbers_prompt)


def _code_only():
    """Source minus the docstring -- which names the banned call to explain it."""
    src = _source()
    body = src.split('"""')
    return body[0] + "".join(body[2:])


def test_never_builds_a_nested_prompt_toolkit_application():
    src = _code_only()
    assert "shortcuts" not in src
    assert "prompt_toolkit" not in src


def test_delegates_to_the_thread_aware_helper():
    assert "_prompt_text_input" in _source()


class _FakeCLI(CLICommandsMixin):
    def __init__(self, answer):
        self._answer = answer
        self.asked = []

    def _prompt_text_input(self, text):
        self.asked.append(text)
        return self._answer


def test_none_becomes_empty_string_so_callers_can_default_safely():
    """/reset reads "" as cancel -- it must never see None and crash."""
    assert _FakeCLI(None)._numbers_prompt("Type RESET: ") == ""


def test_passes_the_prompt_text_through_and_returns_the_answer():
    cli = _FakeCLI("RESET")
    assert cli._numbers_prompt("Type RESET to erase: ") == "RESET"
    assert cli.asked == ["Type RESET to erase: "]


def test_falls_back_to_input_when_no_tui_helper_is_present(monkeypatch):
    class _Bare(CLICommandsMixin):
        pass

    monkeypatch.setattr("builtins.input", lambda _t: "RESET")
    assert _Bare()._numbers_prompt("? ") == "RESET"


def test_fallback_treats_eof_as_no_answer(monkeypatch):
    class _Bare(CLICommandsMixin):
        pass

    def _boom(_t):
        raise EOFError

    monkeypatch.setattr("builtins.input", _boom)
    assert _Bare()._numbers_prompt("? ") == ""
