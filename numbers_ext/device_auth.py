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
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from numbers_ext import home

API_CLIENT_ID = "numbers-cli"
DEFAULT_HUB = os.environ.get("NUMBERS_HUB_URL", "https://127.0.0.1:3000")


class AuthError(RuntimeError):
    """Raised for any non-2xx from the Cloud-HUB."""


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
        hint = ""
        if isinstance(reason, ssl.SSLError) or isinstance(e, ssl.SSLError):
            hint = " (TLS error — for a self-signed dev cert set NUMBERS_INSECURE=1)"
        raise AuthError(
            f"Could not reach Intersession at {url}: {reason}. "
            f"Is the app running?{hint}") from e


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
        os.chmod(tmp, 0o600)
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
    os.chmod(tmp, 0o600)
    os.replace(tmp, tok)
    _env_upsert(home_dir / ".env", "NUMBERS_AGENT_TOKEN", token)
    os.environ["NUMBERS_AGENT_TOKEN"] = token  # live children see it


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
        os.chmod(tmp, 0o600)
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
    base = (hub_base or DEFAULT_HUB).rstrip("/")
    try:
        grant = _post(f"{base}/api/settings/agent-tokens/device",
                      {"client": API_CLIENT_ID,
                       "label": f"numbers-cli:{socket.gethostname()[:24]}"})
    except AuthError as e:
        print_fn(f"[red]Could not start sign-in: {e}[/]")
        return None
    rid, login_url = grant["request_id"], grant["login_url"]
    print_fn(f"[bold]1) Open this link in your browser:[/]\n   {login_url}\n")
    if open_browser:
        try:
            webbrowser.open(login_url)
        except Exception:
            pass
    code = (prompt_fn("\n2) Sign in on the page, then paste the code shown here: ") or "").strip()
    if not code:
        print_fn("[yellow]Sign-in cancelled.[/]")
        return None
    try:
        payload = _exchange(base, rid, code)
    except AuthError as e:
        print_fn(f"[red]Sign-in failed: {e}[/]  (codes expire after 10 minutes — try /sign-in again)")
        return None
    _persist(payload["token"])
    print_fn(f"[bold green]Signed in[/] as {payload.get('label', 'your account')}. "
             f"Angel tools are now enabled for this device.")
    return payload


def run_logout(print_fn: Callable = print, hub_base: Optional[str] = None,
               revoke: bool = True) -> None:
    home_dir = home.require_numbers_home()
    base = (hub_base or DEFAULT_HUB).rstrip("/")
    token = os.environ.get("NUMBERS_AGENT_TOKEN", "")
    if not token:
        tokf = home_dir / "agent-token"
        if tokf.exists():
            token = tokf.read_text(encoding="utf-8").strip()
    if revoke and token:
        try:
            _post(f"{base}/api/settings/agent-tokens/revoke", {"revoke": True}, token=token)
        except AuthError as e:
            print_fn(f"[yellow]Server revocation failed ({e}). "
                     f"Revoke the token at the website if you want it dead everywhere.[/]")
    _clear()
    os.environ.pop("NUMBERS_AGENT_TOKEN", None)
    print_fn("[bold green]Signed out.[/] Angel tools are disabled on this device.")


def main(argv: Optional[list] = None) -> int:
    """`python -m numbers_ext.device_auth signin|logout` — the CLI sign-in entry.

    The `numbers` launcher maps `numbers signin` / `numbers logout` here so a
    user never types the module path.
    """
    import argparse

    ap = argparse.ArgumentParser(prog="numbers auth",
                                 description="Sign in to enable the Angel MCP tools")
    ap.add_argument("action", choices=["signin", "sign-in", "login", "logout"])
    args = ap.parse_args(argv)
    try:
        home.require_numbers_home()
        if args.action == "logout":
            run_logout()
            return 0
        return 0 if run_sign_in() else 1
    except home.NotANumbersHome as e:
        print(f"[numbers] {e}", file=__import__("sys").stderr)
        return 3


if __name__ == "__main__":
    import sys
    sys.exit(main())
