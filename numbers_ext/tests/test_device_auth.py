import json
import os
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


def test_logout_clears_locally_even_when_the_hub_is_down(marked_home):
    """A logout must never leave the token on disk because the server happened
    to be unreachable -- "still signed in because Intersession was down" is the
    one outcome /logout cannot produce."""
    (marked_home / "agent-token").write_text("tok-abc")
    (marked_home / ".env").write_text("NUMBERS_AGENT_TOKEN=tok-abc\nOTHER=1\n")
    out = []
    da.run_logout(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                  hub_base="http://127.0.0.1:1", revoke=True)
    assert not (marked_home / "agent-token").exists()
    assert "NUMBERS_AGENT_TOKEN" not in (marked_home / ".env").read_text()
    screen = "\n".join(out)
    assert "Signed out on this computer." in screen
    assert "still active on your account" in screen
    # The old wording pasted the socket error into the warning.
    for noise in ("WinError", "Errno", "actively refused", "Traceback"):
        assert noise not in screen


def test_logout_when_hub_reachable_says_plain_signed_out(hub, marked_home, monkeypatch):
    (marked_home / "agent-token").write_text("tok-abc")
    monkeypatch.setattr(da, "_post", lambda *a, **k: {"status": "revoked"})
    out = []
    da.run_logout(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                  hub_base=hub, revoke=True)
    screen = "\n".join(out)
    assert "Signed out." in screen
    assert "still active on your account" not in screen


def test_logout_when_not_signed_in(marked_home):
    out = []
    da.run_logout(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                  hub_base="http://127.0.0.1:1", revoke=True)
    assert "You were not signed in." in "\n".join(out)


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
    # Port 1 is not listening → the pre-flight probe fails.
    result = da.run_sign_in(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                            hub_base="http://127.0.0.1:1", open_browser=False)
    assert result is None
    screen = "\n".join(out)
    assert "NUMBERS could not reach Intersession." in screen
    assert "http://127.0.0.1:1" in screen  # names the address it tried
    assert "start the Intersession app" in screen  # and the way out
    assert not (marked_home / "agent-token").exists()


def test_sign_in_unreachable_hub_says_nothing_about_winerrors(marked_home, monkeypatch):
    """The failure screen is for a person, not a stack trace.

    The reported bug pasted "[WinError 10061] No connection could be made
    because the target machine actively refused it" at the user; none of that
    tells them anything they can act on.
    """
    monkeypatch.setattr(da, "_prompt_impl", lambda _text: "CODE1234")
    out = []
    da.run_sign_in(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                   hub_base="http://127.0.0.1:1", open_browser=False)
    screen = "\n".join(out)
    for noise in ("WinError", "Errno", "urlopen", "Traceback", "actively refused",
                  "ConnectionRefused"):
        assert noise not in screen, f"raw error text leaked: {noise}"
    # ANSI escapes, never Rich markup (cli._cprint does not parse Rich tags).
    for tag in ("[red]", "[/]", "[bold red]", "[dim]", "[yellow]"):
        assert tag not in screen


def test_sign_in_does_not_open_a_browser_when_hub_is_down(marked_home, monkeypatch):
    """No flow is started that cannot be finished: a dead hub means no browser
    window and no code prompt."""
    opened, prompted = [], []
    monkeypatch.setattr(da.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(da, "_prompt_impl", lambda text: prompted.append(text) or "X")
    da.run_sign_in(print_fn=lambda *a, **k: None,
                   hub_base="http://127.0.0.1:1", open_browser=True)
    assert opened == []
    assert prompted == []


def test_hub_reachable_reports_a_listening_socket():
    import socket as _s

    srv = _s.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        ok, tls = da._hub_reachable(f"http://127.0.0.1:{srv.getsockname()[1]}",
                                    timeout=2)
        assert ok is True and tls is False
    finally:
        srv.close()


def test_origin_strips_the_api_path():
    assert da._origin(
        "https://127.0.0.1:3000/api/settings/agent-tokens/device"
    ) == "https://127.0.0.1:3000"


def test_ssl_ctx_relaxes_only_for_loopback():
    loop = da._ssl_ctx("https://127.0.0.1:3000")
    assert loop.verify_mode == da.ssl.CERT_NONE and loop.check_hostname is False
    remote = da._ssl_ctx("https://hub.example.com")
    assert remote.verify_mode == da.ssl.CERT_REQUIRED and remote.check_hostname is True


def test_is_headless_true_when_ci_env_set(monkeypatch):
    monkeypatch.setenv("CI", "true")
    assert da._is_headless() is True


def test_is_headless_false_by_default(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SSH_CLIENT", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)
    if da.sys.platform not in ("win32", "darwin"):
        monkeypatch.setenv("DISPLAY", ":0")
    assert da._is_headless() is False


def test_is_headless_true_over_ssh_without_forwarded_display(monkeypatch):
    monkeypatch.setenv("SSH_CLIENT", "10.0.0.1 1 22")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert da._is_headless() is True


def test_sign_in_headless_skips_browser_but_still_prints_link(hub, marked_home, monkeypatch):
    monkeypatch.setattr(da, "_prompt_impl", lambda _text: "CODE1234")
    monkeypatch.setenv("CI", "true")
    opened = []
    monkeypatch.setattr(da.webbrowser, "open", lambda url: opened.append(url))
    out = []
    result = da.run_sign_in(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                            hub_base=hub, open_browser=True)
    assert result is not None
    assert opened == []  # never called -- headless path must not pop a browser
    assert any("Open this link" in line for line in out)


# --- `numbers connect` (Task 1.2): one locked write path for the agent token ---

def test_persist_agent_token_writes_locked_file(tmp_path, monkeypatch, capsys):
    """`numbers connect` must use the same locked write path as the device flow."""
    (tmp_path / "numbers-home.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    assert da.persist_agent_token("tok-abc") == 0

    assert (tmp_path / "agent-token").read_text(encoding="utf-8").strip() == "tok-abc"
    assert "NUMBERS_AGENT_TOKEN=tok-abc" in (tmp_path / ".env").read_text(encoding="utf-8")
    # The value must never be echoed to the console by the storage helper.
    assert "tok-abc" not in capsys.readouterr().out


def test_persist_does_not_leak_the_token_into_the_process_environment(
    tmp_path, monkeypatch
):
    """Storing a token must not hand a bearer to every subprocess we spawn.

    _persist used to export NUMBERS_AGENT_TOKEN into os.environ, so every shell
    tool, hook and MCP server the agent started inherited a live credential for
    the rest of the session. The one consumer that needs it -- the Angel MCP
    child -- reads the owner-only agent-token file instead.
    """
    (tmp_path / "numbers-home.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("NUMBERS_AGENT_TOKEN", raising=False)

    assert da.persist_agent_token("tok-abc") == 0

    assert "NUMBERS_AGENT_TOKEN" not in os.environ


def test_persist_agent_token_refuses_a_foreign_home(tmp_path, monkeypatch):
    """A misconfigured HERMES_HOME must never receive Numbers credentials."""
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path / "not-numbers"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "not-numbers"))

    with pytest.raises(home.NotANumbersHome):
        da.persist_agent_token("tok-abc")

    assert not (tmp_path / "not-numbers" / "agent-token").exists()


def test_persist_agent_token_rejects_an_empty_value(tmp_path, monkeypatch):
    (tmp_path / "numbers-home.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))

    assert da.persist_agent_token("   ") == 1
    assert not (tmp_path / "agent-token").exists()


# --- Audit rows A4 / A3 (Docs/App/Hermes-Angle/AUTH_AUDIT.md) ---------------

def test_a4_ssl_ctx_never_relaxes_verification_for_a_remote_hub(monkeypatch):
    """A4: TLS verification is relaxed for loopback (dev cert) or an explicit
    opt-out only - never because the hub is merely remote."""
    import ssl

    monkeypatch.delenv("NUMBERS_INSECURE", raising=False)

    remote = da._ssl_ctx("https://hub.example.com/api")
    assert remote.verify_mode == ssl.CERT_REQUIRED
    assert remote.check_hostname is True

    loopback = da._ssl_ctx("https://127.0.0.1:3000/api")
    assert loopback.verify_mode == ssl.CERT_NONE
    assert loopback.check_hostname is False

    monkeypatch.setenv("NUMBERS_INSECURE", "1")
    forced = da._ssl_ctx("https://hub.example.com/api")
    assert forced.verify_mode == ssl.CERT_NONE


def test_a3_import_hermes_readers_never_write_the_personal_home(tmp_path):
    """A3: `numbers import-hermes` may read the operator's Hermes home (that is
    the command's purpose) but must leave it byte-identical."""
    from numbers_ext import import_hermes

    personal = tmp_path / ".hermes"
    personal.mkdir()
    (personal / "auth.json").write_text(
        '{"providers": {"openai": {"api_key": "sk-test"}}}', encoding="utf-8"
    )
    (personal / "config.yaml").write_text(
        "model:\n  provider: auto\n  default: anthropic/claude-opus-4.6\n", encoding="utf-8"
    )
    before = {p.name: p.read_bytes() for p in personal.iterdir() if p.is_file()}

    providers = import_hermes.read_hermes_providers(personal)
    model = import_hermes.read_hermes_model(personal)

    after = {p.name: p.read_bytes() for p in personal.iterdir() if p.is_file()}
    assert after == before, "the personal Hermes home must never be modified by the import"
    assert isinstance(providers, dict) and isinstance(model, dict)
