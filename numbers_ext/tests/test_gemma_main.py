import json
from pathlib import Path

import pytest

from numbers_ext import gemma, home

CFG = {"numbers": {"gemma": {"engine": "engine/llama-server.exe",
                             "model": "model/gemma-4-E2B-it-IQ4_XS.gguf"}}}


@pytest.fixture()
def marked_home(tmp_path, monkeypatch):
    home.write_marker(tmp_path, "v1.0.0")
    (tmp_path / "config.yaml").write_text(json.dumps(CFG), encoding="utf-8")
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))
    return tmp_path


def test_status_stopped_when_no_pid(marked_home, capsys):
    assert gemma.main(["status"]) == 0
    assert capsys.readouterr().out.strip() == "stopped"


def test_status_refuses_unmarked_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))
    assert gemma.main(["status"]) == 3
    assert "not a NUMBERS home" in capsys.readouterr().err


def test_start_missing_engine_fails_cleanly(marked_home, capsys):
    assert gemma.main(["start"]) == 1
    assert "engine not found" in capsys.readouterr().err


def test_unknown_action_exits_2(marked_home):
    with pytest.raises(SystemExit):
        gemma.main(["explode"])
