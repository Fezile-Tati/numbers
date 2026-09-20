"""NUMBERS 21:4-9 fork extensions.

Every source addition to the Numbers core is logged in
Docs/App/Hermes-Angle/hermes-patches.md (see P3). This package keeps those
additions behind one import boundary so the upstream diff stays readable.
"""
__all__ = [
    "ansi",
    "debug_share_intersession",
    "device_auth",
    "home",
    "import_hermes",
    "remote_prompt",
    "reset",
    "spinner",
    "tokens",
    "update",
]
__version__ = "1.0.0"
