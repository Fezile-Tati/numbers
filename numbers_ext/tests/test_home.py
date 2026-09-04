import json

import pytest

from numbers_ext import home


def test_require_accepts_marked_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))
    home.write_marker(tmp_path, "v1.0.0")
    assert home.require_numbers_home() == tmp_path
    marker = json.loads((tmp_path / "numbers-home.json").read_text())
    assert marker["product"] == "numbers"


def test_require_refuses_unmarked_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))  # no marker written
    with pytest.raises(home.NotANumbersHome):
        home.require_numbers_home()


def test_require_refuses_hermes_looking_home(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"  # e.g. a default Hermes home path
    hermes.mkdir()
    monkeypatch.setenv("NUMBERS_HOME", str(hermes))
    with pytest.raises(home.NotANumbersHome):
        home.require_numbers_home()


def test_env_missing_raises(monkeypatch):
    monkeypatch.delenv("NUMBERS_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    with pytest.raises(home.NotANumbersHome):
        home.require_numbers_home()
