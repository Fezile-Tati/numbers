"""Intersession-native "Share debug report" for the NUMBERS CLI dashboard.

Replaces the upstream public-paste upload. Instead of pushing the bundle to
paste.rs/dpaste, this collects the SAME force-redacted report + logs via the
shared ``hermes_cli.debug.collect_share_bundle`` collector and POSTs it to the
Intersession hub's authenticated Angel endpoint::

    POST {hub}/api/angel/v1/debug-report   (Bearer = the /sign-in agent token)

which stores it in Postgres and returns a private link. Auth reuses the agent
token minted by ``/sign-in`` (``numbers_ext/device_auth.py``): env
``NUMBERS_AGENT_TOKEN`` or ``$NUMBERS_HOME/agent-token``. The hub base is
``NUMBERS_HUB_URL`` (default ``https://127.0.0.1:3000``).

No Intersession app-identity secret is ever used here — only the user's own
agent token.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_HUB = os.environ.get("NUMBERS_HUB_URL", "https://127.0.0.1:3000")

# Loopback hosts get relaxed TLS (self-signed dev certs); remote hubs stay
# verified. Mirrors numbers_ext/device_auth.py.
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class DebugShareAuthError(RuntimeError):
    """Raised when no agent token is available (user must run /sign-in)."""


class DebugShareUploadError(RuntimeError):
    """Raised when the hub rejects or cannot store the bundle."""


def _agent_token() -> str:
    tok = os.environ.get("NUMBERS_AGENT_TOKEN", "").strip()
    if tok:
        return tok
    try:
        from hermes_constants import get_hermes_home

        tokf = Path(get_hermes_home()) / "agent-token"
        if tokf.is_file():
            return tokf.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _ssl_ctx(url: str) -> Optional[ssl.SSLContext]:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        host = ""
    if os.environ.get("NUMBERS_INSECURE") == "1" or host in _LOOPBACK_HOSTS:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None  # default verification


def share_debug_report(*, log_lines: int = 200, redact: bool = True) -> dict:
    """Build the redacted bundle and POST it to the Intersession hub.

    Returns a dict shaped like the old paste result so the dashboard's
    ``SystemPage`` renderer is unchanged::

        {ok, urls: {"Intersession": <link>}, failures: [], redacted, auto_delete_seconds}
    """
    token = _agent_token()
    if not token:
        raise DebugShareAuthError(
            "Not signed in. Run /sign-in in the NUMBERS CLI first, then retry."
        )

    from hermes_cli.debug import collect_share_bundle

    bundle = collect_share_bundle(log_lines=log_lines, redact=redact)
    report = bundle.get("report", "")
    logs = {k: v for k, v in bundle.items() if k != "report" and v}

    hub = DEFAULT_HUB.rstrip("/")
    url = f"{hub}/api/angel/v1/debug-report"
    payload = {
        "label": f"numbers-cli:{os.environ.get('COMPUTERNAME') or os.uname().nodename if hasattr(os, 'uname') else 'host'}",
        "client": "numbers-cli",
        "redacted": bool(redact),
        "report": report,
        "logs": logs,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)

    ctx = _ssl_ctx(url)
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            pass
        if e.code in (401, 403):
            raise DebugShareAuthError(
                f"Hub rejected the agent token (HTTP {e.code}). Run /sign-in again."
            ) from e
        raise DebugShareUploadError(f"Hub returned HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, ssl.SSLError, OSError) as e:
        raise DebugShareUploadError(f"Could not reach the hub at {hub}: {e}") from e

    view = (body or {}).get("data") or {}
    report_id = view.get("id", "")
    rel = view.get("url") or (f"/api/angel/v1/debug-report/{report_id}" if report_id else "")
    link = f"{hub}{rel}" if rel.startswith("/") else (rel or hub)

    auto_delete_seconds = 0
    exp = view.get("expires_at")
    if exp:
        try:
            expdt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            auto_delete_seconds = max(
                0, int((expdt - datetime.now(timezone.utc)).total_seconds())
            )
        except Exception:
            auto_delete_seconds = 0

    return {
        "ok": True,
        "urls": {"Intersession": link} if link else {},
        "failures": [],
        "redacted": bool(redact),
        "auto_delete_seconds": auto_delete_seconds,
    }
