"""Startup asks for a provider whenever none is really set up.

After a factory reset, a stray provider key in the environment (a Windows
user variable ``Gemini_API_Key``) let ``auto`` resolve Gemini, so the CLI
skipped onboarding with no model at all. And ``provider: deepseek`` without
``DEEPSEEK_API_KEY`` got the generic "No inference provider is configured"
picker instead of being asked for that one key.
"""

import importlib
import os
import sys
import types
from pathlib import Path

import pytest

PROVIDER_KEYS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HERMES_INFERENCE_PROVIDER")


@pytest.fixture(autouse=True)
def _restore_cli_modules():
    prefixes = ("tools", "cli", "run_agent")
    original = {n: m for n, m in sys.modules.items() if any(n == p or n.startswith(p + ".") for p in prefixes)}
    try:
        yield
    finally:
        for name in list(sys.modules):
            if any(name == p or name.startswith(p + ".") for p in prefixes):
                sys.modules.pop(name, None)
        sys.modules.update(original)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for key in PROVIDER_KEYS:
        monkeypatch.delenv(key, raising=False)
    return Path(tmp_path)


def _shell(home: Path, config: str = ""):
    """A real CLI built from ``config`` (config.yaml text) in a temp HERMES_HOME."""
    if config:
        (home / "config.yaml").write_text(config, encoding="utf-8")
    for name in list(sys.modules):
        if name in ("cli", "run_agent", "tools") or name.startswith("tools."):
            sys.modules.pop(name, None)
    sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
    cli = importlib.import_module("cli")
    return cli.HermesCLI(compact=True, max_turns=1)


DEEPSEEK = "model:\n  provider: deepseek\n  default: deepseek-v4-flash\n"


def test_no_provider_or_model_asks_even_with_a_stray_env_key(home, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "stray-key")
    shell = _shell(home)
    assert shell._first_run_setup_reason() == "unconfigured"


def test_chosen_provider_without_its_key_is_missing_credentials(home):
    shell = _shell(home, DEEPSEEK)
    assert shell._first_run_setup_reason() == "missing_credentials"


def test_chosen_provider_with_its_key_is_ready(home, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    shell = _shell(home, DEEPSEEK)
    assert shell._first_run_setup_reason() is None


def test_missing_key_prompt_names_provider_and_asks_only_for_the_key(home, monkeypatch, capsys):
    shell = _shell(home, DEEPSEEK)
    flows = []

    def _key_flow(config, provider_id, current_model=""):
        flows.append((provider_id, current_model))
        os.environ["DEEPSEEK_API_KEY"] = "sk-entered"  # what saving the key achieves

    def _no_picker(*a, **k):
        raise AssertionError("the full provider picker must not run")

    monkeypatch.setattr("hermes_cli.model_setup_flows._model_flow_api_key_provider", _key_flow)
    monkeypatch.setattr("hermes_cli.main.select_provider_and_model", _no_picker)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    capsys.readouterr()
    try:
        assert shell._offer_numbers_setup("missing_credentials") is True
    finally:
        os.environ.pop("DEEPSEEK_API_KEY", None)
    out = capsys.readouterr().out
    assert "DeepSeek is selected but DEEPSEEK_API_KEY is not set." in out
    assert "No inference provider is configured" not in out
    assert flows == [("deepseek", "deepseek-v4-flash")]


def test_unconfigured_runs_the_full_picker(home, monkeypatch):
    shell = _shell(home)
    picked = []
    monkeypatch.setattr("hermes_cli.main.select_provider_and_model", lambda *a, **k: picked.append(1))
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    shell._offer_numbers_setup("unconfigured")
    assert picked == [1]


def test_missing_key_declined_keeps_the_hint(home, monkeypatch, capsys):
    shell = _shell(home, DEEPSEEK)

    def _no_flow(*a, **k):
        raise AssertionError("nothing may run when declined")

    monkeypatch.setattr("hermes_cli.model_setup_flows._model_flow_api_key_provider", _no_flow)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    capsys.readouterr()
    assert shell._offer_numbers_setup("missing_credentials") is False
    assert "Run 'numbers model'" in capsys.readouterr().out
