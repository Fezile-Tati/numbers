"""/print-token prints this device's agent token with where it is stored and
when it was saved (no network call)."""
import os
import sys
import types

import pytest

from hermes_cli.cli_commands_mixin import CLICommandsMixin
from numbers_ext import device_auth, home


class _FakeCLI(CLICommandsMixin):
    pass


@pytest.fixture()
def printed(monkeypatch):
    lines = []
    fake_cli = types.ModuleType("cli")
    fake_cli._cprint = lambda s="": lines.append(s)
    monkeypatch.setitem(sys.modules, "cli", fake_cli)
    return lines


def _field(lines, name):
    return next(line.split(":", 1)[1].strip() for line in lines if line.strip().startswith(name + ":"))


def test_token_from_the_file_shows_its_path_and_saved_time(printed, monkeypatch, tmp_path):
    monkeypatch.delenv("NUMBERS_AGENT_TOKEN", raising=False)
    monkeypatch.setattr(home, "require_numbers_home", lambda: tmp_path)
    tok = tmp_path / "agent-token"
    tok.write_text("tok-file\n", encoding="utf-8")
    os.utime(tok, (1790000000, 1790000000))
    _FakeCLI()._handle_print_token_command("/print-token")
    assert _field(printed, "Token") == "tok-file"
    assert _field(printed, "Source") == str(tok)
    assert _field(printed, "Saved").startswith(
        device_auth.token_info()["saved_at"].astimezone().strftime("%Y-%m-%d %H:%M:%S"))
    assert _field(printed, "Printed")
    assert any("Keep it secret" in line for line in printed)


def test_token_from_the_environment(printed, monkeypatch, tmp_path):
    monkeypatch.setenv("NUMBERS_AGENT_TOKEN", "tok-env")
    monkeypatch.setattr(home, "require_numbers_home", lambda: tmp_path)
    _FakeCLI()._handle_print_token_command("/print-token")
    assert _field(printed, "Token") == "tok-env"
    assert _field(printed, "Source") == "NUMBERS_AGENT_TOKEN (environment)"
    assert not any(line.strip().startswith("Saved:") for line in printed)


def test_no_token(printed, monkeypatch, tmp_path):
    monkeypatch.delenv("NUMBERS_AGENT_TOKEN", raising=False)
    monkeypatch.setattr(home, "require_numbers_home", lambda: tmp_path)
    _FakeCLI()._handle_print_token_command("/print-token")
    assert "Not detected" in printed[0]
    assert "/connect" in printed[1]
