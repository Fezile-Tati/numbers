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


# --- /reset must confirm through the TUI modal, never through stdin --------
# Slash commands run on the process_loop daemon thread; any input() there
# deadlocks against prompt_toolkit's stdin ownership (#33961). Symptom: a bare
# "> ", "aclose(): asynchronous generator is already running", "Press ENTER".

class _FakeTUI(CLICommandsMixin):
    def __init__(self, choice):
        self._choice = choice
        self.modal_kwargs = None
        self.printed = []
        self.exited = False

    def _prompt_text_input_modal(self, **kw):
        self.modal_kwargs = kw
        return self._choice

    def _prompt_text_input(self, text):  # must never be reached with a TUI up
        raise AssertionError("/reset read stdin from the slash worker thread")

    def _numbers_exit_after_reset(self):
        self.exited = True


def _run_reset_handler(cli, monkeypatch):
    """Drive /reset with a fake TUI. Returns the list of `full` flags that
    reached perform_reset (empty when nothing was erased)."""
    import numbers_ext.reset as reset

    performed = []

    def _fake_perform(print_fn=print, *, full=False):
        performed.append(full)
        return True

    monkeypatch.setattr(reset, "perform_reset", _fake_perform)
    monkeypatch.setitem(__import__("sys").modules, "cli",
                        type("m", (), {"_cprint": cli.printed.append}))
    cli._handle_reset_command("/reset")
    return performed


def test_reset_confirms_through_the_modal_not_stdin(monkeypatch):
    cli = _FakeTUI("light")
    performed = _run_reset_handler(cli, monkeypatch)
    assert cli.modal_kwargs is not None, "/reset never opened the modal"
    assert cli.exited is True
    assert performed == [False]  # the light level


def test_reset_modal_offers_both_levels_and_cancel(monkeypatch):
    cli = _FakeTUI("cancel")
    _run_reset_handler(cli, monkeypatch)
    keys = [c[0] for c in cli.modal_kwargs["choices"]]
    labels = " ".join(c[1] + " " + c[2] for c in cli.modal_kwargs["choices"])
    assert keys == ["light", "full", "cancel"]
    # The difference between the two levels is stated, not implied -- this is
    # the whole point: a reset that keeps providers must say so up front.
    assert "API keys" in labels
    # A factory reset must never offer to stop asking.
    assert "always" not in " ".join(keys).lower()


def test_full_reset_reaches_the_full_wipe(monkeypatch):
    # _FakeTUI answers every modal the same way, so this also confirms the
    # second confirmation.
    cli = _FakeTUI("full")
    performed = _run_reset_handler(cli, monkeypatch)
    assert performed == [True]
    assert cli.exited is True


def test_full_reset_asks_a_second_time(monkeypatch):
    """Erasing providers and API keys is confirmed twice: once to pick the
    level, once against the list the user has just read."""
    class _TwoStep(_FakeTUI):
        def __init__(self):
            super().__init__(None)
            self.answers = ["full", "cancel"]  # pick full, then back out
            self.titles = []

        def _prompt_text_input_modal(self, **kw):
            self.modal_kwargs = kw
            self.titles.append(kw.get("title"))
            return self.answers.pop(0)

    cli = _TwoStep()
    performed = _run_reset_handler(cli, monkeypatch)
    assert performed == []          # nothing erased
    assert cli.exited is False
    assert len(cli.titles) == 2
    assert "API keys" in cli.titles[1]
    assert any("Cancelled" in line for line in cli.printed)


def test_reset_cancels_on_anything_but_a_level(monkeypatch):
    for answer in ("cancel", None, "reset"):  # "reset" is no longer a key
        cli = _FakeTUI(answer)
        performed = _run_reset_handler(cli, monkeypatch)
        assert performed == []
        assert cli.exited is False
        assert any("Cancelled" in line for line in cli.printed)


def test_reset_explains_the_level_it_is_about_to_run(monkeypatch):
    """The erase list is printed after the choice, so it describes the level
    actually picked rather than both at once."""
    light = _FakeTUI("light")
    _run_reset_handler(light, monkeypatch)
    screen = "\n".join(light.printed)
    assert "This ERASES, in NUMBERS only:" in screen
    assert "This is KEPT:" in screen
    assert "your providers and API keys" in screen

    full = _FakeTUI("full")
    _run_reset_handler(full, monkeypatch)
    full_screen = "\n".join(full.printed)
    assert "Full factory reset" in full_screen
    assert "choose a provider again" in full_screen
    assert "your providers and API keys" in screen
