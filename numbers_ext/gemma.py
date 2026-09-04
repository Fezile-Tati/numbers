"""Numbers-owned Gemma supervisor (D4: never shares the Intersession App's
engine, weights, process, or port; never reads otherway paths).

Entry: `python -m numbers_ext.gemma start|stop|status|fetch-model`.
Config source: <home>/config.yaml -> numbers.gemma.* (see config.yaml.seed).
Defaults are Numbers-only: port 8082 (the App owns 8081).
stdlib-only.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from numbers_ext import home

DEFAULT_PORT = 8082  # NEVER 8081 — the Intersession App's Gemma owns 8081
_FORBIDDEN_PATH_TOKEN = "otherway"  # D4: App repo paths are never acceptable


class GemmaError(RuntimeError):
    pass


def _read_config(cfg_path: Path) -> dict:
    text = cfg_path.read_text(encoding="utf-8")
    if text.lstrip().startswith("{"):  # JSON used in unit tests
        return json.loads(text)
    try:
        import yaml  # pyyaml is already a Hermes dependency
        return yaml.safe_load(text) or {}
    except Exception as exc:  # noqa: BLE001
        raise GemmaError(f"cannot parse {cfg_path}: {exc}") from exc


def _reject_d4_path(path: Path, kind: str) -> Path:
    if _FORBIDDEN_PATH_TOKEN in str(path).lower():
        raise GemmaError(
            f"D4: {kind} may not live inside the Intersession App workspace "
            f"({path}). Numbers owns its own engine and model.")
    return path


@dataclass
class GemmaSettings:
    engine: Path = Path("engine/llama-server.exe")
    model: Path = Path("model/gemma-4-E2B-it-IQ4_XS.gguf")
    mmproj: Optional[Path] = None
    port: int = DEFAULT_PORT
    host: str = "127.0.0.1"
    context: int = 4096
    threads: int = 2
    alias: str = "gemma-4"
    enabled: bool = True
    model_url: str = ""      # production download source (empty = skip fetch)
    model_sha256: str = ""

    @classmethod
    def load(cls, cfg_path: Path) -> "GemmaSettings":
        cfg = _read_config(cfg_path)
        g = (cfg.get("numbers") or {}).get("gemma") or {}
        root = Path(os.environ.get("NUMBERS_HOME") or "").expanduser()
        eng = Path(str(g.get("engine", "engine/llama-server.exe")))
        mdl = Path(str(g.get("model", "model/gemma-4-E2B-it-IQ4_XS.gguf")))
        mmp = g.get("mmproj")
        if not eng.is_absolute():
            eng = root / eng
        if not mdl.is_absolute():
            mdl = root / mdl
        _reject_d4_path(eng, "engine")
        _reject_d4_path(mdl, "model")
        return cls(engine=eng, model=mdl,
                   mmproj=Path(str(mmp)) if mmp else None,
                   port=int(g.get("port", DEFAULT_PORT)),
                   host=str(g.get("host", "127.0.0.1")),
                   context=int(g.get("context", 4096)),
                   threads=int(g.get("threads", 2)),
                   alias=str(g.get("alias", "gemma-4")),
                   enabled=bool(g.get("enabled", True)),
                   model_url=str(g.get("model_url", "")),
                   model_sha256=str(g.get("model_sha256", "")))

    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"


def build_command(g: GemmaSettings) -> List[str]:
    cmd = [str(g.engine), "--host", g.host, "--port", str(g.port),
           "-m", str(g.model), "-c", str(g.context), "-t", str(g.threads),
           "--no-webui", "--alias", g.alias]
    if g.mmproj and g.mmproj.exists():
        cmd += ["--mmproj", str(g.mmproj)]
    return cmd


def _http_ok(url: str, timeout: int = 2) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def health_url(g: GemmaSettings) -> str:
    return f"http://{g.host}:{g.port}/health"


def decide_start(g: GemmaSettings) -> bool:
    return not _http_ok(health_url(g))


def pid_file(h: Path) -> Path:
    return h / "gemma.pid"


def _read_pid(h: Path) -> Optional[int]:
    pf = pid_file(h)
    if not pf.exists():
        return None
    try:
        return int(pf.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def start(g: GemmaSettings, home_dir: Path, wait_sec: int = 50) -> bool:
    if not g.enabled:
        return False
    if not g.engine.exists():
        raise GemmaError(f"engine not found: {g.engine} — re-run the installer")
    if not g.model.exists():
        raise GemmaError(f"model not found: {g.model} — "
                         f"run 'python -m numbers_ext.gemma fetch-model' first")
    if not decide_start(g):
        return True  # already healthy on our own port
    (home_dir / "logs").mkdir(parents=True, exist_ok=True)
    log = open(home_dir / "logs" / "gemma.log", "ab", buffering=0)
    proc = subprocess.Popen(build_command(g), stdout=log, stderr=log,
                            cwd=str(g.engine.parent))
    pid_file(home_dir).write_text(str(proc.pid) + "\n", encoding="utf-8")
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if _http_ok(health_url(g)):
            return True
        time.sleep(1)
    raise GemmaError(f"engine on port {g.port} did not become healthy within "
                     f"{wait_sec}s — see {home_dir / 'logs' / 'gemma.log'}")


def stop(home_dir: Path) -> bool:
    pid = _read_pid(home_dir)
    if not pid:
        return False
    try:
        os.kill(pid, signal.SIGTERM)  # Windows: TerminateProcess
    except ProcessLookupError:
        pass
    pid_file(home_dir).unlink(missing_ok=True)
    return True


def status(home_dir: Path) -> str:
    if not pid_file(home_dir).exists():
        return "stopped"
    g = GemmaSettings.load(home_dir / "config.yaml")
    return "running" if _http_ok(health_url(g)) else "stale"


def fetch_model(g: GemmaSettings, home_dir: Path) -> bool:
    """Download the model into <home>/model when missing; verify sha256."""
    import hashlib

    if not g.model_url:
        raise GemmaError("no numbers.gemma.model_url configured — the installer "
                         "must provide the production download source")
    target = home_dir / "model" / g.model.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if g.model_sha256:
            dig = hashlib.sha256(target.read_bytes()).hexdigest()
            if dig == g.model_sha256:
                return True
            raise GemmaError(f"model digest mismatch: {target.name}")
        return True
    part = target.with_suffix(target.suffix + ".part")
    print(f"Downloading {g.model_url} …")
    with urllib.request.urlopen(g.model_url, timeout=120) as resp, \
            open(part, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done // (1 << 20)} MiB / {total // (1 << 20)} MiB",
                      end="", flush=True)
    print()
    if g.model_sha256:
        dig = hashlib.sha256(part.read_bytes()).hexdigest()
        if dig != g.model_sha256:
            part.unlink(missing_ok=True)
            raise GemmaError(f"digest mismatch after download: {target.name}")
    os.replace(part, target)
    lic = home_dir / "model" / "model-license.txt"
    if not lic.exists():
        try:
            urllib.request.urlretrieve(
                g.model_url.rsplit("/", 1)[0] + "/model-license.txt", lic)
        except Exception:
            pass  # licence is best-effort; the download page carries it too
    return True


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m numbers_ext.gemma",
                                 description="Numbers-owned Gemma supervisor")
    ap.add_argument("action", choices=["start", "stop", "status", "fetch-model"])
    args = ap.parse_args(argv)
    try:
        home_dir = home.require_numbers_home()
        cfg_path = home_dir / "config.yaml"
        if args.action in ("start", "fetch-model"):
            g = GemmaSettings.load(cfg_path)
        if args.action == "start":
            start(g, home_dir)
            print(f"Gemma engine running on {health_url(g)}")
        elif args.action == "fetch-model":
            fetch_model(g, home_dir)
            print("Model ready.")
        elif args.action == "stop":
            print("Gemma engine stopped." if stop(home_dir) else "Nothing running.")
        elif args.action == "status":
            print(status(home_dir))
    except (GemmaError, home.NotANumbersHome) as e:
        print(f"[gemma] {e}", file=sys.stderr)
        return 3 if isinstance(e, home.NotANumbersHome) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
