import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from numbers_ext import device_auth as da
from numbers_ext import home


class _FakeHub(BaseHTTPRequestHandler):
    binds = {}  # request_id -> code shown on the (fake) code page

    def log_message(self, *a):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/device"):
            rid = "req-1234"
            self.binds[rid] = body.get("code")
            self._send(201, {"request_id": rid,
                             "login_url": f"http://127.0.0.1:{self.server.server_port}/cli-auth?req={rid}",
                             "expires_in": 600})
        elif self.path.endswith("/exchange"):
            self._send(200, {"token": "tok-abc", "label": "numbers-cli:test"})
        else:
            self._send(404, {"error": "nope"})

    def do_GET(self):  # what the browser code page would fetch
        self._send(200, {"code": self.binds.get("req-1234", "XXXX")})


@pytest.fixture()
def hub():
    srv = HTTPServer(("127.0.0.1", 0), _FakeHub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture()
def marked_home(tmp_path, monkeypatch):
    home.write_marker(tmp_path, "v1.0.0")
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))
    return tmp_path


def test_full_sign_in_flow_writes_token(hub, marked_home, monkeypatch):
    monkeypatch.setattr(da, "_prompt_impl", lambda _text: "CODE1234")

    result = da.run_sign_in(print_fn=lambda *a, **k: None,
                            hub_base=hub, open_browser=False)
    assert result is not None and result["token"] == "tok-abc"
    token_file = marked_home / "agent-token"
    assert token_file.read_text().strip() == "tok-abc"
    env = (marked_home / ".env").read_text()
    assert "NUMBERS_AGENT_TOKEN=tok-abc" in env


def test_sign_in_cancelled_writes_nothing(hub, marked_home, monkeypatch):
    monkeypatch.setattr(da, "_prompt_impl", lambda _text: "")
    result = da.run_sign_in(print_fn=lambda *a, **k: None,
                            hub_base=hub, open_browser=False)
    assert result is None
    assert not (marked_home / "agent-token").exists()


def test_sign_in_refuses_unmarked_home(hub, tmp_path, monkeypatch):
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))  # no marker
    monkeypatch.setattr(da, "_prompt_impl", lambda _text: "CODE1234")
    with pytest.raises(home.NotANumbersHome):
        da.run_sign_in(print_fn=lambda *a, **k: None,
                       hub_base=hub, open_browser=False)


def test_logout_clears_token_and_env(hub, marked_home):
    (marked_home / "agent-token").write_text("tok-abc")
    (marked_home / ".env").write_text("NUMBERS_AGENT_TOKEN=tok-abc\nOTHER=1\n")
    da.run_logout(print_fn=lambda *a, **k: None, hub_base=hub, revoke=False)
    assert not (marked_home / "agent-token").exists()
    env = (marked_home / ".env").read_text()
    assert "NUMBERS_AGENT_TOKEN" not in env
    assert "OTHER=1" in env


def test_exchange_failure_no_write(hub, marked_home, monkeypatch):
    def _boom(*a, **k):
        raise da.AuthError("HTTP 401: unauthorized")

    monkeypatch.setattr(da, "_post", _boom)
    with pytest.raises(da.AuthError):
        da._exchange(hub, "req-1", "CODE")
    assert not (marked_home / "agent-token").exists()


def test_sign_in_unreachable_hub_prints_not_raises(marked_home, monkeypatch):
    """Connection failure must surface as a printed message, not a swallowed
    exception (the "/sign-in does nothing" bug)."""
    monkeypatch.setattr(da, "_prompt_impl", lambda _text: "CODE1234")
    out = []
    # Port 1 is not listening → ConnectionRefusedError inside urlopen.
    result = da.run_sign_in(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                            hub_base="http://127.0.0.1:1", open_browser=False)
    assert result is None
    assert any("Could not reach Intersession" in line for line in out)
    assert not (marked_home / "agent-token").exists()


def test_ssl_ctx_relaxes_only_for_loopback():
    loop = da._ssl_ctx("https://127.0.0.1:3000")
    assert loop.verify_mode == da.ssl.CERT_NONE and loop.check_hostname is False
    remote = da._ssl_ctx("https://hub.example.com")
    assert remote.verify_mode == da.ssl.CERT_REQUIRED and remote.check_hostname is True
