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


# --- the two-step flow: /sign-in, then /sign-in <code> ---------------------
#
# The classic CLI cannot ask for the code at all: slash commands run off the
# main thread, where prompt_toolkit owns stdin and every free-text prompt
# returns None (#23185). An unanswerable prompt used to read as a cancel, so
# /sign-in printed a link and gave up -- and the code the user then pasted
# went to the model as a message. The code has to be acceptable as an argument.

def test_an_unanswered_prompt_leaves_a_resumable_sign_in(hub, marked_home, monkeypatch):
    """No channel to ask on is not a decision to cancel."""
    monkeypatch.setattr(da, "_prompt_impl", lambda _text: "")
    out = []
    result = da.run_sign_in(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                            hub_base=hub, open_browser=False)
    assert result is None
    screen = "\n".join(out)
    assert "/sign-in <code>" in screen      # the way back in is on screen
    assert "cancelled" not in screen.lower()  # and it is not a dead end
    assert da._load_pending()["request_id"] == "req-1234"


def test_the_code_is_accepted_as_an_argument(hub, marked_home, monkeypatch):
    """`/sign-in <code>` finishes what the bare `/sign-in` started."""
    monkeypatch.setattr(da, "_prompt_impl", lambda _text: "")
    da.run_sign_in(print_fn=lambda *a, **k: None, hub_base=hub, open_browser=False)

    # No hub_base second time round: the pending record carries it, because the
    # user is just typing a code and cannot be asked to restate the address.
    result = da.run_sign_in(print_fn=lambda *a, **k: None, code="CODE1234")
    assert result is not None and result["token"] == "tok-abc"
    assert (marked_home / "agent-token").read_text().strip() == "tok-abc"
    assert da._load_pending() is None  # spent, so it cannot be replayed


def test_a_code_with_nothing_pending_says_so(marked_home):
    out = []
    result = da.run_sign_in(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                            code="CODE1234")
    assert result is None
    assert "Run /sign-in to start one." in "\n".join(out)


def test_an_expired_pending_request_is_not_resumed(hub, marked_home, monkeypatch):
    """Codes die with their request; resuming a dead one would fail obscurely."""
    monkeypatch.setattr(da, "_prompt_impl", lambda _text: "")
    da.run_sign_in(print_fn=lambda *a, **k: None, hub_base=hub, open_browser=False)
    stale = json.loads((marked_home / da.PENDING_NAME).read_text())
    stale["expires_at"] = 0
    (marked_home / da.PENDING_NAME).write_text(json.dumps(stale))

    assert da._load_pending() is None
    out = []
    assert da.run_sign_in(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                          code="CODE1234") is None
    assert "expired" in "\n".join(out)


def test_a_corrupt_pending_file_is_not_a_crash(marked_home):
    (marked_home / da.PENDING_NAME).write_text("{not json")
    assert da._load_pending() is None


def test_logout_forgets_a_pending_sign_in(hub, marked_home, monkeypatch):
    """Signing out mid-flow must not leave a request the next user can finish."""
    monkeypatch.setattr(da, "_prompt_impl", lambda _text: "")
    da.run_sign_in(print_fn=lambda *a, **k: None, hub_base=hub, open_browser=False)
    (marked_home / "agent-token").write_text("tok-abc")

    da.run_logout(print_fn=lambda *a, **k: None, hub_base=hub, revoke=False)
    assert da._load_pending() is None


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


# --- the first-run "you have no token" notice ------------------------------
# Without a token the MCP child registers zero tools and says nothing, so the
# agent reads as broken rather than unconnected. The notice is the one place
# that says so -- once, and never when a token is already present.

def test_the_offer_is_shown_once_and_then_remembered(marked_home, monkeypatch):
    monkeypatch.delenv("NUMBERS_AGENT_TOKEN", raising=False)
    out = []
    da.offer_connect(print_fn=lambda *a: out.append(" ".join(map(str, a))))
    screen = "\n".join(out)
    assert "numbers signin" in screen
    assert "numbers connect <TOKEN>" in screen
    assert (marked_home / da.CONNECT_OFFER_GUARD).exists()

    out.clear()
    da.offer_connect(print_fn=lambda *a: out.append(" ".join(map(str, a))))
    assert out == [], "the offer asked a second time"


def test_no_offer_when_a_token_file_is_already_present(marked_home, monkeypatch):
    monkeypatch.delenv("NUMBERS_AGENT_TOKEN", raising=False)
    (marked_home / "agent-token").write_text("tok-abc\n", encoding="utf-8")
    out = []
    da.offer_connect(print_fn=lambda *a: out.append(" ".join(map(str, a))))
    assert out == []


def test_no_offer_when_the_token_is_in_the_environment(marked_home, monkeypatch):
    monkeypatch.setenv("NUMBERS_AGENT_TOKEN", "tok-env")
    out = []
    da.offer_connect(print_fn=lambda *a: out.append(" ".join(map(str, a))))
    assert out == []


def test_an_empty_token_file_still_counts_as_unconnected(marked_home, monkeypatch):
    """A truncated write must not silently pass for a working install."""
    monkeypatch.delenv("NUMBERS_AGENT_TOKEN", raising=False)
    (marked_home / "agent-token").write_text("\n", encoding="utf-8")
    assert da.has_agent_token() is False


def test_an_unmarked_home_is_never_nagged(tmp_path, monkeypatch):
    """Offering a token to a home that cannot store one helps nobody."""
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))  # no marker
    monkeypatch.delenv("NUMBERS_AGENT_TOKEN", raising=False)
    out = []
    assert da.offer_connect(print_fn=lambda *a: out.append(" ".join(map(str, a)))) == 0
    assert out == []


def test_the_offer_entry_point_never_fails_the_launcher(tmp_path, monkeypatch):
    """It runs ahead of the user's own command; a bad home must not exit 3."""
    monkeypatch.setenv("NUMBERS_HOME", str(tmp_path))  # no marker
    monkeypatch.delenv("NUMBERS_AGENT_TOKEN", raising=False)
    assert da.main(["offer"]) == 0


# --- a bare sign-in with no token points at `connect`, not a browser -------
# A browser-code sign-in needs a human: something to open the link, read the
# code, paste it back. None of that exists when this CLI is being driven by
# an agent -- the old behaviour just hung or printed a link nobody could use.

def test_main_signin_with_no_token_prints_the_notice_not_a_browser_flow(
    marked_home, monkeypatch, capsys
):
    monkeypatch.delenv("NUMBERS_AGENT_TOKEN", raising=False)
    opened = []
    monkeypatch.setattr(da.webbrowser, "open", lambda url: opened.append(url))
    assert da.main(["signin"]) == 1
    assert opened == []
    out = capsys.readouterr().out
    assert "No token found" in out
    assert "numbers connect <TOKEN>" in out


def test_main_signin_with_a_code_still_tries_to_resume(marked_home, monkeypatch):
    """A code means the user is finishing a flow started elsewhere (the
    browser authorize page) -- the no-token gate must not swallow it."""
    monkeypatch.delenv("NUMBERS_AGENT_TOKEN", raising=False)
    resumed = []
    monkeypatch.setattr(da, "run_sign_in",
                        lambda code="": resumed.append(code) or None)
    assert da.main(["signin", "CODE1234"]) == 1
    assert resumed == ["CODE1234"]


def test_main_signin_with_an_existing_token_still_reaches_run_sign_in(
    marked_home, monkeypatch
):
    (marked_home / "agent-token").write_text("tok-abc\n", encoding="utf-8")
    called = []
    monkeypatch.setattr(da, "run_sign_in", lambda code="": called.append(1) or None)
    da.main(["signin"])
    assert called == [1]


def test_print_no_token_notice_names_both_ways_in():
    out = []
    da.print_no_token_notice(print_fn=lambda *a: out.append(" ".join(map(str, a))))
    screen = "\n".join(out)
    assert "No token found" in screen
    assert "numbers connect <TOKEN>" in screen
    assert "/sign-in" in screen
