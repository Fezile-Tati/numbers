"""The one-line progress indicator for the Hermes import.

It exists because the import printed nothing between "here is what I will
copy" and the first result line: on the first-run offer that runs before the
app starts, a multi-second skills/plugins copy looked like a hang.
"""
import io
import time

import pytest

from numbers_ext import spinner


class _FakeTty(io.StringIO):
    def isatty(self):
        return True


def test_non_tty_prints_a_plain_line_and_runs_the_work():
    out = []
    plain = io.StringIO()                      # isatty() is False
    result = spinner.run_with_spinner(
        lambda *a: out.append(" ".join(map(str, a))),
        "importing Skills", lambda: 41, stream=plain)
    assert result == 41
    assert out == ["  importing Skills..."]
    assert plain.getvalue() == ""              # no escape-code noise anywhere


def test_tty_spinner_animates_then_erases_itself():
    stream = _FakeTty()

    def _work():
        time.sleep(0.05)                       # let at least one frame land
        return "done"

    result = spinner.run_with_spinner(print, "importing Skills", _work,
                                      interval=0.01, stream=stream)
    assert result == "done"
    written = stream.getvalue()
    assert "\r" in written                     # animated in place
    assert "importing Skills" in written
    assert written.endswith("\r")              # cleared before the next print


def test_a_failing_category_still_clears_the_indicator():
    stream = _FakeTty()

    def _boom():
        raise OSError("disk went away")

    with pytest.raises(OSError):
        spinner.run_with_spinner(print, "importing Skills", _boom,
                                 interval=0.01, stream=stream)
    assert stream.getvalue().endswith("\r")
