import pytest

from numbers_ext import home, onboarding


def _marked_home(tmp_path):
    home.write_marker(tmp_path, "v1.0.0")
    return tmp_path


def test_first_run_prints_findings_and_marks_done(tmp_path, monkeypatch, capsys):
    h = _marked_home(tmp_path)
    monkeypatch.setenv("NUMBERS_HOME", str(h))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-user")
    lines = onboarding.maybe_welcome(h, print_fn=print)
    out = capsys.readouterr().out
    assert "OPENAI_API_KEY" in out and "Gemma" in out
    assert lines >= 1
    assert (h / ".onboarded").exists()          # one-time sentinel


def test_second_run_is_silent(tmp_path, monkeypatch, capsys):
    h = _marked_home(tmp_path)
    (h / ".onboarded").write_text("1")
    monkeypatch.setenv("NUMBERS_HOME", str(h))
    onboarding.maybe_welcome(h, print_fn=print)
    assert capsys.readouterr().out == ""


def test_refuses_unmarked_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))  # no marker
    with pytest.raises(home.NotANumbersHome):
        onboarding.maybe_welcome(tmp_path, print_fn=print)


def test_no_keys_prints_default_guidance(tmp_path, monkeypatch, capsys):
    h = _marked_home(tmp_path)
    monkeypatch.setenv("NUMBERS_HOME", str(h))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    onboarding.maybe_welcome(h, print_fn=print)
    out = capsys.readouterr().out
    assert "No third-party provider keys detected" in out
    assert "never uses Intersession's API keys" in out
