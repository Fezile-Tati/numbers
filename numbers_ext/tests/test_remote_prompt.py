"""The channel that lets a slash command ask a question it cannot ask itself.

WHY
---
In the Ink TUI a slash command runs in ``tui_gateway.slash_worker``, whose
stdin IS the JSON-RPC channel and whose stdout is buffered until the command
returns. Every prompt path there yields None -- which is also what a bare Enter
yields -- so "could not ask" and "the user agreed" were the same value. That is
why /sign-in printed its link and immediately cancelled, and why
/import-hermes imported every category without showing its menu.

These tests pin the two halves of the contract: None means unanswered and must
never be mistaken for an answer, and an installed channel must win over the
terminal paths.
"""
import io
import json

import pytest

from numbers_ext import remote_prompt


@pytest.fixture(autouse=True)
def _clear_channel():
    """No test may leak a channel into the next -- it is process-global."""
    remote_prompt.set_remote_prompt(None)
    yield
    remote_prompt.set_remote_prompt(None)


def test_inactive_by_default():
    """A process that owns its terminal must not be routed anywhere."""
    assert remote_prompt.is_active() is False
    assert remote_prompt.ask("anything?") is None


def test_ask_returns_the_answer():
    remote_prompt.set_remote_prompt(lambda text: f"answered:{text}")
    assert remote_prompt.is_active() is True
    assert remote_prompt.ask("code?") == "answered:code?"


def test_a_broken_channel_degrades_instead_of_killing_the_command():
    """A raising callback must read as "no answer", not abort the import."""
    def _boom(_text):
        raise RuntimeError("transport died")

    remote_prompt.set_remote_prompt(_boom)
    assert remote_prompt.ask("code?") is None


def test_none_from_the_channel_stays_none():
    """None is "nobody answered" and must survive intact to the caller."""
    remote_prompt.set_remote_prompt(lambda _t: None)
    assert remote_prompt.ask("code?") is None


def test_clearing_restores_terminal_ownership():
    remote_prompt.set_remote_prompt(lambda _t: "x")
    remote_prompt.set_remote_prompt(None)
    assert remote_prompt.is_active() is False


# --------------------------------------------------------------------------
# The worker half: a question goes UP the protocol, the answer comes back down.
# --------------------------------------------------------------------------

def _make_worker_prompt(rid, answer_line, buf=None):
    """Build the worker's prompt callback against fake pipes.

    Returns (ask, stdout_buffer, pending_buffer).
    """
    from tui_gateway import slash_worker

    buf = buf if buf is not None else io.StringIO()
    out = io.StringIO()

    class _Stdin:
        def readline(self_inner):
            return answer_line

    import sys
    real_out, real_in = sys.stdout, sys.stdin
    sys.stdout, sys.stdin = out, _Stdin()
    try:
        ask = slash_worker._make_remote_prompt(rid, buf)
        answer = ask("2) paste the code: ")
    finally:
        sys.stdout, sys.stdin = real_out, real_in
    return answer, out.getvalue(), buf


def test_worker_sends_the_question_and_reads_the_answer():
    answer, sent, _buf = _make_worker_prompt(
        7, json.dumps({"id": 7, "answer": "PDZ9RTGK"}) + "\n")
    assert answer == "PDZ9RTGK"
    frame = json.loads(sent.strip())
    assert frame["id"] == 7
    # No "ok" key: that is what distinguishes a question from a completion.
    assert "ok" not in frame
    assert frame["prompt"]["text"] == "2) paste the code: "


def test_the_question_carries_what_was_printed_before_it():
    """/sign-in's code prompt is unanswerable without the authorize link."""
    buf = io.StringIO()
    buf.write("1) Open this link:\n   https://127.0.0.1:3000/cli/authorize?x=1\n")
    answer, sent, drained = _make_worker_prompt(
        1, json.dumps({"id": 1, "answer": "CODE"}) + "\n", buf=buf)
    assert answer == "CODE"
    assert "cli/authorize" in json.loads(sent.strip())["prompt"]["pending_output"]
    # Drained, not copied -- the text must not also appear in the final output.
    assert drained.getvalue() == ""


def test_a_closed_pipe_is_unanswered_not_empty():
    """EOF mid-question must not read as the user accepting the default."""
    answer, _sent, _buf = _make_worker_prompt(3, "")
    assert answer is None


def test_the_question_survives_the_commands_stdout_capture():
    """The question must reach the PIPE, not the buffer the command prints to.

    ``_run`` builds this callback and then runs the command inside
    ``redirect_stdout(buf)``, so at ask time ``sys.stdout`` IS ``buf``. A
    callback that looked ``sys.stdout`` up when asked therefore wrote the
    question into the capture buffer the gateway never reads, and then blocked
    on stdin until the slash-worker timeout fired -- an import that hung on its
    first category, and a /sign-in that never asked for its code.
    """
    import contextlib
    import sys

    from tui_gateway import slash_worker

    buf = io.StringIO()
    pipe = io.StringIO()

    class _Stdin:
        def readline(self_inner):
            return json.dumps({"id": 11, "answer": "ESR34P9T"}) + "\n"

    real_out, real_in = sys.stdout, sys.stdin
    sys.stdout, sys.stdin = pipe, _Stdin()
    try:
        ask = slash_worker._make_remote_prompt(11, buf)
        # Exactly what _run does: the command's stdout is the capture buffer.
        with contextlib.redirect_stdout(buf):
            answer = ask("2) paste the code: ")
    finally:
        sys.stdout, sys.stdin = real_out, real_in

    assert answer == "ESR34P9T"
    frame = json.loads(pipe.getvalue().strip())
    assert frame["id"] == 11
    # The buffer holds the command's output, never the protocol frame.
    assert "prompt" not in buf.getvalue()


def test_frames_for_other_commands_are_ignored():
    """A stale or interleaved frame must not be mistaken for this answer."""
    from tui_gateway import slash_worker

    lines = iter([
        json.dumps({"id": 99, "answer": "wrong-command"}) + "\n",
        json.dumps({"id": 4, "command": "/other"}) + "\n",   # not an answer
        "not json at all\n",
        json.dumps({"id": 4, "answer": "right"}) + "\n",
    ])

    class _Stdin:
        def readline(self_inner):
            return next(lines, "")

    import sys
    real_out, real_in = sys.stdout, sys.stdin
    sys.stdout, sys.stdin = io.StringIO(), _Stdin()
    try:
        ask = slash_worker._make_remote_prompt(4, io.StringIO())
        assert ask("?") == "right"
    finally:
        sys.stdout, sys.stdin = real_out, real_in
