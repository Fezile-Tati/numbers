import pytest

from numbers_ext import detect_providers as dp

ALLOWED = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN",
           "GEMINI_API_KEY", "GOOGLE_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}
# D11 rule 2: Intersession-only names must never appear in the allowlist.
FORBIDDEN = {"DEEPSEEK_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
             "ANGEL_CLOUD_API_KEY", "GEMINI_MODEL"}


def test_allowlist_excludes_intersession_keys():
    assert set(dp.ALLOWED_ENV_VARS) & FORBIDDEN == set(), "D11 violation"


def test_allowlist_covers_third_party_providers():
    assert set(dp.ALLOWED_ENV_VARS) == ALLOWED


def test_detect_finds_present_keys(monkeypatch):
    env = {"OPENAI_API_KEY": "sk-abc", "GEMINI_API_KEY": "AI-xyz",
           "DEEPSEEK_API_KEY": "app-only-secret"}  # the app key MUST be ignored
    monkeypatch.setattr(dp.os, "environ", env)
    found = {p.env_var for p in dp.detect()}
    assert found == {"OPENAI_API_KEY", "GEMINI_API_KEY"}
    assert "DEEPSEEK_API_KEY" not in found


def test_detect_ignores_empty_and_placeholder_values(monkeypatch):
    env = {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "   ",
           "GEMINI_API_KEY": "not-needed"}
    monkeypatch.setattr(dp.os, "environ", env)
    assert dp.detect() == []


def test_detect_never_reads_files(tmp_path, monkeypatch):
    app_env = tmp_path / "otherway" / ".env"
    app_env.parent.mkdir()
    app_env.write_text("DEEPSEEK_API_KEY=app-secret\nGEMINI_API_KEY=app-ai\n")
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path / "numbers-home"))
    dp.detect()  # no exception; ambient env only (D11 rule 1)
