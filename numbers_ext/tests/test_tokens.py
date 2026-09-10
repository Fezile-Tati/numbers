import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from numbers_ext import home
from numbers_ext import tokens as tk


class _FakeHub(BaseHTTPRequestHandler):
    """Minimal agent-token management surface for the CLI client tests."""

    calls = []  # (method, path, body) for assertions

    def log_message(self, *a):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def do_GET(self):
        self.calls.append(("GET", self.path, None))
        self._send(200, {"tokens": [
            {"id": "id-1", "name": "laptop", "scopes": ["stories:read"],
             "created_at": "2026-09-09T00:00:00Z", "revoked": False},
        ]})

    def do_POST(self):
        b = self._body()
        self.calls.append(("POST", self.path, b))
        self._send(201, {"token": "plain-XYZ", "meta": {"id": "id-2", "name": b.get("name")},
                         "warning": "shown once"})

    def do_PATCH(self):
        b = self._body()
        self.calls.append(("PATCH", self.path, b))
        self._send(200, {"meta": {"id": "id-1", "name": b.get("name")}})

    def do_DELETE(self):
        self.calls.append(("DELETE", self.path, None))
        self._send(200, {"id": "id-1", "status": "revoked"})


@pytest.fixture()
def hub(monkeypatch):
    _FakeHub.calls = []
    srv = HTTPServer(("127.0.0.1", 0), _FakeHub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    monkeypatch.setenv("NUMBERS_HUB_URL", f"http://127.0.0.1:{srv.server_port}")
    yield srv
    srv.shutdown()


@pytest.fixture()
def signed_in_home(tmp_path, monkeypatch):
    home.write_marker(tmp_path, "v1.0.0")
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))
    (tmp_path / "agent-token").write_text("bearer-tok\n", encoding="utf-8")
    monkeypatch.delenv("NUMBERS_AGENT_TOKEN", raising=False)
    return tmp_path


def test_list_uses_bearer_and_prints(hub, signed_in_home):
    out = []
    rc = tk.cmd_list(print_fn=lambda *a, **k: out.append(" ".join(str(x) for x in a)))
    assert rc == 0
    assert any("laptop" in line for line in out)
    method, path, _ = _FakeHub.calls[-1]
    assert method == "GET" and path == "/api/settings/agent-tokens"


def test_create_prints_plaintext_once(hub, signed_in_home):
    out = []
    rc = tk.cmd_create("laptop", ["stories:read"], print_fn=lambda *a, **k: out.append(" ".join(str(x) for x in a)))
    assert rc == 0
    assert any("plain-XYZ" in line for line in out)


def test_create_rejects_unknown_scope(hub, signed_in_home):
    rc = tk.cmd_create("x", ["bogus:scope"], print_fn=lambda *a, **k: None)
    assert rc == 2
    # No POST should have been attempted.
    assert all(m != "POST" for m, _, _ in _FakeHub.calls)


def test_rename_sends_patch(hub, signed_in_home):
    rc = tk.cmd_rename("id-1", "renamed", print_fn=lambda *a, **k: None)
    assert rc == 0
    method, path, body = _FakeHub.calls[-1]
    assert method == "PATCH" and path.endswith("/id-1") and body == {"name": "renamed"}


def test_revoke_confirms_then_deletes(hub, signed_in_home):
    rc = tk.cmd_revoke("id-1", yes=False, prompt_fn=lambda _t: "y", print_fn=lambda *a, **k: None)
    assert rc == 0
    assert _FakeHub.calls[-1][0] == "DELETE"


def test_revoke_cancelled_does_nothing(hub, signed_in_home):
    rc = tk.cmd_revoke("id-1", yes=False, prompt_fn=lambda _t: "n", print_fn=lambda *a, **k: None)
    assert rc == 0
    assert all(m != "DELETE" for m, _, _ in _FakeHub.calls)


def test_requires_sign_in_when_no_token(hub, tmp_path, monkeypatch):
    home.write_marker(tmp_path, "v1.0.0")
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))
    monkeypatch.delenv("NUMBERS_AGENT_TOKEN", raising=False)
    rc = tk.cmd_list(print_fn=lambda *a, **k: None)
    assert rc == 1
