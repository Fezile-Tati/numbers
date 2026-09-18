"""Cloud-HUB device sign-in for the NUMBERS CLI (RFC 8628-flavoured).

One-time code flow:
  /sign-in -> POST .../device            -> {request_id, login_url}
  user opens login_url, signs in; the page shows an 8-char code
  user pastes the code -> POST .../device/<id>/exchange {code} -> {token}
The token is written to $NUMBERS_HOME/agent-token (the file the Angel MCP
child resolves per cmd/numbers-mcp/token.go) AND upserted into
$NUMBERS_HOME/.env as NUMBERS_AGENT_TOKEN. No secret ever appears in
config.yaml or in logs.

stdlib-only on purpose (matches hermes_cli/auth.py): no new dependencies.
Every write path validates the Numbers home marker first (numbers_ext.home)
so a misconfigured HERMES_HOME can never receive Numbers credentials.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from numbers_ext import home
from numbers_ext.ansi import BOLD, BOLD_GREEN, DIM, RED, RST, YELLOW

API_CLIENT_ID = "numbers-cli"


def _hub_base() -> str:
    """NUMBERS_HUB_URL, read lazily so setting it after import still takes
    effect (module-load-time binding meant a later os.environ write here was
    silently ignored -- tokens.py._hub_base already reads it lazily)."""
    return os.environ.get("NUMBERS_HUB_URL", "https://127.0.0.1:3000")

def _is_headless() -> bool:
    """True when there is no local display to pop a browser window into.

    Covers remote SSH sessions (SSH_CLIENT/SSH_TTY set, no DISPLAY forwarded),
    Linux/mac headless containers (no DISPLAY/WAYLAND_DISPLAY at all), and CI
    runners (CI=true). Windows has no DISPLAY concept, so an RDP-less Windows
    box is only caught by the SSH/CI checks -- webbrowser.open() there simply
    fails silently if there's truly no desktop, which run_sign_in already
    tolerates.
    """
    env = os.environ
    if env.get("CI", "").strip().lower() in ("1", "true", "yes"):
        return True
    if env.get("SSH_CLIENT") or env.get("SSH_TTY"):
        # A forwarded X11 DISPLAY over SSH still counts as "has a display".
        if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
            return True
    if sys.platform not in ("win32", "darwin") and not (
        env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")
    ):
        return True
    return False


def _lock_down(path: Path) -> None:
    """Best-effort owner-only permissions, on whichever platform this runs.

    os.chmod(0o600) is a real restriction on POSIX and a silent no-op on
    Windows (NTFS ACLs, not POSIX mode bits) -- calling only chmod would make
    the "owner-only" claim false on the platform this product mostly ships
    on. icacls failures (e.g. non-NTFS volume) are swallowed: the token file
    is written with a temp-file-then-replace either way, so this is defense
    in depth, not the only thing standing between the token and the disk.
    """
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import subprocess

            user = os.environ.get("USERNAME", "")
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                capture_output=True, timeout=10, check=False,
            )
        except Exception:
            pass


class AuthError(RuntimeError):
    """Raised for any non-2xx from the Cloud-HUB."""


class HubUnreachable(AuthError):
    """The hub could not be contacted at all: down, refused, DNS, or TLS.

    Carries the pieces needed to explain itself instead of a socket error
    string. Subclasses AuthError so existing `except AuthError` handlers keep
    working; catch this FIRST where a friendlier message is wanted.
    """

    def __init__(self, base: str, tls_problem: bool = False):
        self.base = base
        self.tls_problem = tls_problem
        super().__init__(f"Could not reach Intersession at {base}")

    def lines(self) -> list:
        return _explain_unreachable(self.base, self.tls_problem)


# Hosts where TLS verification is relaxed automatically: a loopback endpoint has
# no interceptable network hop, so a self-signed dev cert is not a downgrade. This
# mirrors hermes_cli.model_switch._LOOPBACK_HOSTS. Remote hubs stay fully verified
# unless the operator explicitly opts out with NUMBERS_INSECURE=1.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


def _is_loopback(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").strip().lower()
    except Exception:
        return False
    return host in _LOOPBACK_HOSTS


def _ssl_ctx(url: str = "") -> ssl.SSLContext:
    # Explicit opt-out, or an implicit loopback dev endpoint (self-signed cert).
    if os.environ.get("NUMBERS_INSECURE") == "1" or _is_loopback(url):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def _origin(url: str) -> str:
    """"https://host:3000/api/..." -> "https://host:3000".

    The address worth showing a user is the one they can start or point
    NUMBERS_HUB_URL at -- never the API path that happened to fail.
    """
    try:
        p = urllib.parse.urlparse(url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        pass
    return url


def _explain_unreachable(base: str, tls_problem: bool = False) -> list:
    """The "Intersession isn't there" screen, as lines.

    Deliberately free of exception text: a WinError number, an errno or a
    urllib repr tells the person reading it nothing they can act on. Name the
    address tried and the one thing that fixes it.
    """
    out = [
        "",
        f"{RED}NUMBERS could not reach Intersession.{RST}",
        "",
        f"  Tried:  {_origin(base)}",
    ]
    if tls_problem:
        out += [
            "  The address answered, but the secure connection failed.",
            "",
            "  If that is a self-signed development certificate, set "
            "NUMBERS_INSECURE=1 and try again.",
        ]
    else:
        out.append("  Fix:    start the Intersession app, then run /sign-in again")
    out += [
        "",
        f"{DIM}If Intersession runs somewhere else, set NUMBERS_HUB_URL to its "
        f"address.{RST}",
        "",
    ]
    return out


def _hub_reachable(base: str, timeout: float = 3.0) -> tuple:
    """(reachable, tls_problem) for the hub behind ``base``.

    A short TCP (plus TLS, for https) probe so /sign-in never opens a browser
    and asks for a code it has no way to exchange. Uses the same _ssl_ctx as
    the real requests, so a loopback dev cert does not read as unreachable.
    """
    try:
        parts = urllib.parse.urlparse(base)
    except Exception:
        return False, False
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError:
        return False, False
    try:
        if parts.scheme == "https":
            try:
                with _ssl_ctx(base).wrap_socket(sock, server_hostname=host):
                    return True, False
            except (ssl.SSLError, ssl.CertificateError, OSError):
                return False, True
        return True, False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _post(url: str, body: dict, token: Optional[str] = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("X-Client-Id", API_CLIENT_ID)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(url)) as resp:
            raw = resp.read() or b"{}"
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read() or b"{}"
        try:
            detail = json.loads(raw).get("error") or json.loads(raw).get("message")
        except Exception:
            detail = e.reason
        raise AuthError(f"Cloud-HUB returned HTTP {e.code}: {detail}") from e
    # A connection-level failure (server down, refused, DNS, TLS, timeout) is NOT
    # an HTTPError, so without this it would escape run_sign_in's `except AuthError`
    # and vanish upstream — the "/sign-in does nothing" bug. Convert to AuthError
    # with an actionable hint so the flow prints instead of silently aborting.
    except (urllib.error.URLError, ssl.SSLError, socket.timeout, OSError) as e:
        reason = getattr(e, "reason", None) or e
        tls = isinstance(reason, ssl.SSLError) or isinstance(e, ssl.SSLError)
        raise HubUnreachable(_origin(url), tls) from e


def _prompt_impl(text: str) -> str:  # replaced in tests
    return input(text)


def _env_upsert(path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env, preserving every other line byte-exactly."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, found = [], False
    for ln in lines:
        if ln.startswith(key + "="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out) + "\n")
        _lock_down(Path(tmp))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _persist(token: str) -> None:
    home_dir = home.require_numbers_home()
    tok = home_dir / "agent-token"
    fd, tmp = tempfile.mkstemp(dir=str(home_dir), prefix=".agent-token-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(token + "\n")
    _lock_down(Path(tmp))
    os.replace(tmp, tok)
    _env_upsert(home_dir / ".env", "NUMBERS_AGENT_TOKEN", token)
    # Deliberately NOT exported into os.environ. Doing so handed a live bearer
    # token to every process the agent spawns - shell tools, MCP servers, hooks,
    # anything - for the whole session, which is far wider than the one consumer
    # that needs it. The Angel MCP child resolves the token itself, from the
    # owner-only agent-token file written just above (resolveToken, rung 3 in
    # cmd/numbers-mcp/token.go), so the export bought nothing. Every in-process
    # reader (numbers_ext.tokens._read_token, run_logout below) already falls
    # back to that same file.


def persist_agent_token(token: str, print_fn: Callable = print) -> int:
    """Store a pasted agent token exactly the way the device flow does.

    `numbers connect <TOKEN>` used to write the token through a shell redirect in
    the launcher: no owner-only ACL (unlike _lock_down) and the secret echoed to
    the console. Both entry points now share _persist, so every write of an agent
    token goes through one locked path and the value never reaches stdout.
    """
    token = (token or "").strip()
    if not token:
        print_fn(f"{RED}No token given.{RST} Create one at Settings -> Agent Tokens, "
                 "then run: numbers connect <TOKEN>")
        return 1
    home.require_numbers_home()  # never write a Numbers token into a non-Numbers home
    _persist(token)
    print_fn(f"{BOLD_GREEN}Token saved.{RST} Restart NUMBERS to pick up the angel tools.")
    return 0


def _env_remove(path: Path, key: str) -> None:
    """Delete every KEY= line from a .env, preserving other lines exactly."""
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    out = [ln for ln in lines if not ln.startswith(key + "=")]
    if len(out) == len(lines):
        return  # key absent — leave the file untouched (mtime preserved)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out) + ("\n" if out else ""))
        _lock_down(Path(tmp))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _clear() -> None:
    home_dir = home.require_numbers_home()
    tok = home_dir / "agent-token"
    if tok.exists():
        tok.unlink()
    _env_remove(home_dir / ".env", "NUMBERS_AGENT_TOKEN")


def _exchange(hub_base: str, request_id: str, code: str) -> dict:
    return _post(f"{hub_base}/api/settings/agent-tokens/device/{request_id}/exchange",
                 {"code": code.strip().upper()})


def run_sign_in(print_fn: Callable = print, prompt_fn: Optional[Callable] = None,
                hub_base: Optional[str] = None, open_browser: bool = True) -> Optional[dict]:
    """Drive the device flow. Returns the token payload, or None if aborted."""
    prompt_fn = prompt_fn or _prompt_impl
    home.require_numbers_home()  # fail fast: never exchange into a non-Numbers home
    base = (hub_base or _hub_base()).rstrip("/")
    # Pre-flight: never open a browser and ask for a code we cannot exchange.
    ok, tls_problem = _hub_reachable(base)
    if not ok:
        for line in _explain_unreachable(base, tls_problem):
            print_fn(line)
        return None
    try:
        grant = _post(f"{base}/api/settings/agent-tokens/device",
                      {"client": API_CLIENT_ID,
                       "label": f"numbers-cli:{socket.gethostname()[:24]}"})
    except HubUnreachable as e:  # before AuthError: it is a subclass
        for line in e.lines():
            print_fn(line)
        return None
    except AuthError as e:
        print_fn(f"{RED}Could not start sign-in: {e}{RST}")
        return None
    rid, login_url = grant.get("request_id"), grant.get("login_url")
    if not rid or not login_url:
        print_fn(f"{RED}Sign-in failed: unexpected response from Intersession.{RST}")
        return None
    print_fn(f"{BOLD}1) Open this link in your browser:{RST}\n   {login_url}\n")
    if open_browser and not _is_headless():
        try:
            webbrowser.open(login_url)
        except Exception:
            pass
    elif open_browser:
        print_fn(f"{DIM}(No local display detected -- open the link above on a "
                 f"machine with a browser.){RST}")
    code = (prompt_fn("\n2) Sign in on the page, then paste the code shown here: ") or "").strip()
    if not code:
        print_fn(f"{YELLOW}Sign-in cancelled.{RST}")
        return None
    try:
        payload = _exchange(base, rid, code)
    except HubUnreachable as e:  # the app went away mid-flow
        for line in e.lines():
            print_fn(line)
        return None
    except AuthError as e:
        print_fn(f"{RED}Sign-in failed: {e}{RST}  (codes expire after 10 minutes — try /sign-in again)")
        return None
    token = payload.get("token")
    if not token:
        print_fn(f"{RED}Sign-in failed: unexpected response from Intersession.{RST}")
        return None
    _persist(token)
    print_fn(f"{BOLD_GREEN}Signed in{RST} as {payload.get('label', 'your account')}. "
             f"Restart NUMBERS to pick up the angel tools.")
    return payload


def run_logout(print_fn: Callable = print, hub_base: Optional[str] = None,
               revoke: bool = True) -> None:
    home_dir = home.require_numbers_home()
    base = (hub_base or _hub_base()).rstrip("/")
    token = os.environ.get("NUMBERS_AGENT_TOKEN", "")
    if not token:
        tokf = home_dir / "agent-token"
        if tokf.exists():
            token = tokf.read_text(encoding="utf-8").strip()
    if not token:
        print_fn("You were not signed in.")
        return
    # Clear locally FIRST. Revoking first meant an unreachable Intersession
    # left the token sitting on disk -- /logout has to work whether or not the
    # app is running, and "still signed in because the server was down" is the
    # one outcome a logout must never produce.
    _clear()
    os.environ.pop("NUMBERS_AGENT_TOKEN", None)
    revoked = True
    if revoke:
        try:
            _post(f"{base}/api/settings/agent-tokens/revoke", {"revoke": True}, token=token)
        except AuthError:
            revoked = False
    # The already-running Angel MCP child still holds the old token in memory
    # until the process restarts -- "disabled" here would be false.
    if revoked:
        print_fn(f"{BOLD_GREEN}Signed out.{RST} "
                 f"Restart NUMBERS to fully disable the angel tools.")
        return
    # Local sign-out succeeded; only the account-wide revocation did not. Say
    # exactly that -- the old wording pasted the socket error into a warning
    # and read like the whole logout had failed.
    print_fn(f"{BOLD_GREEN}Signed out on this computer.{RST} "
             f"Restart NUMBERS to fully disable the angel tools.")
    print_fn(f"{YELLOW}Intersession could not be reached, so this device's token is "
             f"still active on your account.{RST}")
    print_fn("  Revoke it at Settings -> Agent Tokens to disable it everywhere.")


def main(argv: Optional[list] = None) -> int:
    """`python -m numbers_ext.device_auth signin|logout` — the CLI sign-in entry.

    The `numbers` launcher maps `numbers signin` / `numbers logout` here so a
    user never types the module path.
    """
    import argparse

    ap = argparse.ArgumentParser(prog="numbers auth",
                                 description="Sign in to enable the Angel MCP tools")
    ap.add_argument("action", choices=["signin", "sign-in", "login", "logout", "connect"])
    ap.add_argument("token", nargs="?", default="",
                    help="agent token (connect only: numbers connect <TOKEN>)")
    args = ap.parse_args(argv)
    try:
        home.require_numbers_home()
        if args.action == "logout":
            run_logout()
            return 0
        if args.action == "connect":
            return persist_agent_token(args.token)
        return 0 if run_sign_in() else 1
    except home.NotANumbersHome as e:
        print(f"[numbers] {e}", file=__import__("sys").stderr)
        return 3


if __name__ == "__main__":
    import sys
    sys.exit(main())
