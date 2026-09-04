"""One-time NUMBERS welcome banner: what the user owns, what Numbers provides.

Runs once (sentinel: <home>/.onboarded) before the first prompt. Never blocks,
never asks a question — the provider picker (`numbers model`) is where choices
are made. D11: Intersession keys are never mentioned because detect_providers
never sees them.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from numbers_ext import detect_providers as dp
from numbers_ext import home

SENTINEL = ".onboarded"


def maybe_welcome(home_dir: Path, print_fn: Callable = print) -> int:
    home_dir = home.require_numbers_home()
    sentinel = home_dir / SENTINEL
    if sentinel.exists():
        return 0
    found = dp.detect()
    print_fn("── NUMBERS 21:4-9 ─────────────────────────────")
    print_fn("  Default model: your own local Gemma (offline, private).")
    if found:
        print_fn("  Detected providers you can use (your own keys):")
        for p in found:
            print_fn(f"    • {p.provider:<9} via {p.env_var}")
        print_fn("  Run `numbers model` anytime to switch or add another provider.")
    else:
        print_fn("  No third-party provider keys detected in your environment.")
        print_fn("  Run `numbers model` to add your own OpenAI/Google/Anthropic key,")
        print_fn("  or keep using the built-in local Gemma.")
    print_fn("  (Numbers never uses Intersession's API keys — yours are yours.)")
    sentinel.write_text("1", encoding="utf-8")
    return 1


def main(argv: list[str] | None = None) -> int:
    try:
        home_dir = home.require_numbers_home()
    except home.NotANumbersHome as e:
        print(f"[numbers] {e}", file=sys.stderr)
        return 3
    maybe_welcome(home_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
