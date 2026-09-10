"""Quote-scoped Hermes->Numbers rebrand for the web dashboard i18n files.

Replaces `Hermes`->`Numbers` and the command token `hermes `->`numbers ` ONLY
inside quoted string literals ("...", '...', `...`), so object keys and code
identifiers (updateHermes:, etc.) and filesystem paths (~/.hermes/...) are left
untouched.
"""
from __future__ import annotations

import glob
import io
import re

LIT = re.compile(
    r'"(?:\\.|[^"\\])*"'
    r"|'(?:\\.|[^'\\])*'"
    r"|`(?:\\.|[^`\\])*`",
    re.S,
)


def fix(match: "re.Match[str]") -> str:
    s = match.group(0)
    s = re.sub(r"\bHermes\b", "Numbers", s)
    s = re.sub(r"\bhermes ", "numbers ", s)
    return s


def main() -> None:
    changed = 0
    for path in sorted(glob.glob("web/src/i18n/*.ts")):
        text = io.open(path, encoding="utf-8").read()
        new = LIT.sub(fix, text)
        if new != text:
            io.open(path, "w", encoding="utf-8", newline="").write(new)
            changed += 1
            print(f"updated {path}")
    print(f"files changed: {changed}")


if __name__ == "__main__":
    main()
