import json
from pathlib import Path

import pytest

from numbers_ext import gemma

MINIMAL = {
    "model": {"default": "gemma-4", "provider": "custom",
              "base_url": "http://127.0.0.1:8082/v1", "api_key": "not-needed"},
    "numbers": {"gemma": {
        "enabled": True, "port": 8082, "context": 4096, "threads": 2,
        "engine": "C:/n/engine/llama-server.exe",
        "model": "C:/n/model/gemma-4-E2B-it-IQ4_XS.gguf",
        "mmproj": "C:/n/model/mmproj-F16.gguf",
        "alias": "gemma-4"}},
}


def _write_config(tmp_path: Path, cfg: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(json.dumps(cfg), encoding="utf-8")  # JSON is YAML-safe here
    return p


def test_defaults_without_config_block(tmp_path):
    p = _write_config(tmp_path, {"model": {"base_url": "http://127.0.0.1:9999/v1"}})
    g = gemma.GemmaSettings.load(p)
    assert g.port == 8082          # Numbers' own default, never 8081
    assert g.threads == 2
    assert g.context == 4096
    assert g.enabled is True


def test_load_full_config(tmp_path):
    p = _write_config(tmp_path, MINIMAL)
    g = gemma.GemmaSettings.load(p)
    assert g.engine == Path("C:/n/engine/llama-server.exe")
    assert g.port == 8082
    assert g.base_url().endswith(":8082/v1")


def test_build_command_has_own_port_and_alias(tmp_path):
    p = _write_config(tmp_path, MINIMAL)
    g = gemma.GemmaSettings.load(p)
    cmd = gemma.build_command(g)
    joined = " ".join(cmd)
    assert "--port" in joined and "8082" in joined
    assert "--alias" in joined and "gemma-4" in joined
    assert "otherway" not in joined and "8081" not in joined  # D4


def test_health_and_decision(monkeypatch, tmp_path):
    p = _write_config(tmp_path, MINIMAL)
    g = gemma.GemmaSettings.load(p)
    monkeypatch.setattr(gemma, "_http_ok", lambda url, timeout=2: True)
    assert gemma.decide_start(g) is False           # healthy -> do not start
    monkeypatch.setattr(gemma, "_http_ok", lambda url, timeout=2: False)
    assert gemma.decide_start(g) is True            # unhealthy -> start


def test_settings_reject_app_paths(tmp_path):
    # D4: config must never point into the Intersession App repo.
    p = _write_config(tmp_path, {"numbers": {"gemma": {
        "engine": "C:/Users/User/Desktop/otherway/tools/llama.cpp/llama-server.exe",
        "model": "C:/Users/User/Desktop/otherway/model/gemma-4-E2B-it-IQ4_XS.gguf"}}})
    with pytest.raises(gemma.GemmaError):
        gemma.GemmaSettings.load(p)


def test_start_missing_engine_raises(tmp_path, monkeypatch):
    p = _write_config(tmp_path, MINIMAL)
    g = gemma.GemmaSettings.load(p)
    monkeypatch.setattr(gemma, "_http_ok", lambda url, timeout=2: False)
    with pytest.raises(gemma.GemmaError):
        gemma.start(g, tmp_path, wait_sec=1)
