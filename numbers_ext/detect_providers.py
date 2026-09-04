"""First-run provider detection for NUMBERS (D11).

SCOPE BY DESIGN: reads ONLY the ambient process environment, never any file.
Intersession's API keys live in files (otherway/.env, /opt/intersession/.env,
personal Hermes homes) and are NEVER part of Numbers' world. The allowlist is
the user's own third-party providers; Intersession-only names (DEEPSEEK_API_KEY,
GOOGLE_CLIENT_*, ANGEL_CLOUD_API_KEY) are deliberately absent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# D11 rule 2: fixed allowlist. Do not add Intersession-only names here.
ALLOWED_ENV_VARS: dict[str, str] = {
    "OPENAI_API_KEY": "openai",
    "ANTHROPIC_API_KEY": "anthropic",
    "ANTHROPIC_TOKEN": "anthropic",
    "CLAUDE_CODE_OAUTH_TOKEN": "anthropic",   # Claude Code subscription token
    "GEMINI_API_KEY": "google",
    "GOOGLE_API_KEY": "google",
}

# Keep in sync with the test's FORBIDDEN set — the test guards the boundary.
_INTERSESSION_ONLY = {"DEEPSEEK_API_KEY", "GOOGLE_CLIENT_ID",
                      "GOOGLE_CLIENT_SECRET", "ANGEL_CLOUD_API_KEY"}
assert not (set(ALLOWED_ENV_VARS) & _INTERSESSION_ONLY), "D11 allowlist violation"


@dataclass(frozen=True)
class Provider:
    provider: str          # "openai" | "anthropic" | "google"
    env_var: str           # which env var was present
    note: str = ""         # user-facing hint


def _has_usable(value: str) -> bool:
    return bool(value) and value.strip() not in ("", "not-needed")


def detect() -> list[Provider]:
    """Return the user's own detected providers (ambient env only)."""
    found: list[Provider] = []
    for var, prov in ALLOWED_ENV_VARS.items():
        if _has_usable(os.environ.get(var, "")):
            found.append(Provider(provider=prov, env_var=var))
    return found
