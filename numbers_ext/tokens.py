"""Agent-token management for the NUMBERS CLI (the "smart token system").

Full CRUD over the same surface the web App uses
(/api/settings/agent-tokens), authenticated with the agent token this device
already holds (Bearer). The server scopes every action to the signed-in user,
so these commands only ever see or change *your* tokens.

    python -m numbers_ext.tokens list
    python -m numbers_ext.tokens create --name laptop [--scope stories:read ...]
    python -m numbers_ext.tokens rename <id> --name new-name
    python -m numbers_ext.tokens revoke <id>

stdlib-only, matching device_auth.py. Reuses device_auth's HTTP/SSL helpers and
the Numbers-home guard so credentials never leak outside a Numbers install.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Optional

from numbers_ext import device_auth, home

_BASE_PATH = "/api/settings/agent-tokens"

# Same closed scope set the server allows; kept here only for a friendlier
# client-side error than a round-trip 400.
ALLOWED_SCOPES = ("stories:read", "stories:write")


def _hub_base() -> str:
    return (os.environ.get("NUMBERS_HUB_URL") or device_auth.DEFAULT_HUB).rstrip("/")


def _read_token() -> str:
    """The bearer to authenticate management calls: env first, then the file."""
    tok = os.environ.get("NUMBERS_AGENT_TOKEN", "").strip()
    if tok:
        return tok
    home_dir = home.require_numbers_home()
    tokf = home_dir / "agent-token"
    if tokf.exists():
        return tokf.read_text(encoding="utf-8").strip()
    return ""


def _request(method: str, path: str, token: str, body: Optional[dict] = None,
             timeout: int = 30) -> dict:
    url = _hub_base() + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("X-Client-Id", device_auth.API_CLIENT_ID)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=device_auth._ssl_ctx()) as resp:
            raw = resp.read() or b"{}"
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read() or b"{}"
        try:
            detail = json.loads(raw).get("error") or json.loads(raw).get("message")
        except Exception:
            detail = e.reason
        raise device_auth.AuthError(f"HTTP {e.code}: {detail}") from e


def _require_token(print_fn) -> Optional[str]:
    token = _read_token()
    if not token:
        print_fn("[yellow]You are not signed in.[/] Run `numbers signin` first.")
        return None
    return token


def cmd_list(print_fn=print) -> int:
    token = _require_token(print_fn)
    if not token:
        return 1
    data = _request("GET", _BASE_PATH, token)
    rows = data.get("tokens") or []
    if not rows:
        print_fn("No agent tokens yet. Create one with `numbers token create`.")
        return 0
    print_fn(f"{'ID':<34}  {'NAME':<20}  {'SCOPES':<24}  STATUS")
    for t in rows:
        scopes = ",".join(t.get("scopes") or [])
        status = "revoked" if t.get("revoked") else "active"
        print_fn(f"{t.get('id',''):<34}  {(t.get('name') or 'agent'):<20}  {scopes:<24}  {status}")
    return 0


def cmd_create(name: str, scopes: list, print_fn=print) -> int:
    token = _require_token(print_fn)
    if not token:
        return 1
    for s in scopes:
        if s not in ALLOWED_SCOPES:
            print_fn(f"[red]Unknown scope:[/] {s}  (allowed: {', '.join(ALLOWED_SCOPES)})")
            return 2
    payload = {"name": name or "agent", "scopes": scopes or ["stories:read"]}
    data = _request("POST", _BASE_PATH, token, payload)
    plaintext = data.get("token")
    if not plaintext:
        print_fn("[red]Server did not return a token.[/]")
        return 1
    print_fn("[bold green]Token created.[/] Copy it now — it is shown only once:")
    print_fn(f"\n    {plaintext}\n")
    print_fn(data.get("warning") or "Store it somewhere safe; it cannot be shown again.")
    return 0


def cmd_rename(token_id: str, name: str, print_fn=print) -> int:
    token = _require_token(print_fn)
    if not token:
        return 1
    _request("PATCH", f"{_BASE_PATH}/{token_id}", token, {"name": name})
    print_fn(f"[bold green]Renamed[/] {token_id} -> {name}")
    return 0


def cmd_revoke(token_id: str, yes: bool, prompt_fn=input, print_fn=print) -> int:
    token = _require_token(print_fn)
    if not token:
        return 1
    if not yes:
        ans = (prompt_fn(f"Revoke token {token_id}? Anything using it loses access. [y/N] ") or "").strip().lower()
        if ans not in ("y", "yes"):
            print_fn("Cancelled.")
            return 0
    _request("DELETE", f"{_BASE_PATH}/{token_id}", token)
    print_fn(f"[bold green]Revoked[/] {token_id}")
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="numbers token",
                                 description="Manage your NUMBERS agent tokens")
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="list your agent tokens")
    c = sub.add_parser("create", help="mint a new agent token")
    c.add_argument("--name", default="agent")
    c.add_argument("--scope", dest="scopes", action="append", default=[],
                   help="repeatable; default stories:read")
    r = sub.add_parser("rename", help="rename a token")
    r.add_argument("id")
    r.add_argument("--name", required=True)
    v = sub.add_parser("revoke", help="revoke a token")
    v.add_argument("id")
    v.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    args = ap.parse_args(argv)

    try:
        home.require_numbers_home()
        if args.action == "list":
            return cmd_list()
        if args.action == "create":
            return cmd_create(args.name, args.scopes)
        if args.action == "rename":
            return cmd_rename(args.id, args.name)
        if args.action == "revoke":
            return cmd_revoke(args.id, args.yes)
    except home.NotANumbersHome as e:
        print(f"[numbers] {e}", file=sys.stderr)
        return 3
    except device_auth.AuthError as e:
        print(f"[numbers] token operation failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
