"""Import an existing Hermes install into the isolated Numbers home — by category.

Numbers keeps its own `HERMES_HOME` (`%LOCALAPPDATA%\\numbers`), so anything the
user set up in stock Hermes (providers, skills, memory, profiles, cron, plugins,
tasks, config) is invisible to Numbers. This module offers an opt-in, per-category
import:

  * READ-ONLY on the Hermes side — never writes to the Hermes home.
  * Every write stays under NUMBERS_HOME (validated via numbers_ext.home).
  * The user picks categories (interactive) or scripts them (--only/--all/…).
  * Offered once on first run (guard file); on demand via `numbers import-hermes`.

Never overwrites Numbers-owned identity: `numbers-home.json`, `skins/`, `bin/`, and
the branded `config.yaml` keys (`display.skin`, `mcp_servers`, `agent`, `model`,
`_config_version`). `hermes-*` skills are renamed to `numbers-*` on import.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional

from numbers_ext import home as _home

GUARD_NAME = ".hermes_import_done"

# config.yaml keys Numbers owns — never imported/overwritten by the `config` category.
_BRAND_CONFIG_KEYS = frozenset({"display", "mcp_servers", "agent", "model", "_config_version"})
# Intersession app-IDENTITY secrets — never imported into Numbers, ever. These
# are not user AI provider keys: ANGEL_CLOUD_API_KEY is the Numbers<->Intersession
# auth token and GOOGLE_CLIENT_ID/SECRET are the app's OAuth identity. User AI
# provider keys (DEEPSEEK_API_KEY, GOOGLE_API_KEY, GEMINI_API_KEY, GLM_API_KEY, …)
# ARE imported so the user's own providers work in the isolated Numbers home.
_ENV_DENYLIST = frozenset({
    "ANGEL_CLOUD_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
})
_JUNK_SUFFIX = (".pyc", ".pyo", ".sock", ".tmp")
# Per-home history/runtime that must not carry over on a directory copy.
_HISTORY_NAMES = frozenset({
    "state.db", "state.db-wal", "state.db-shm", "sessions",
    "backups", "state-snapshots", "checkpoints",
})
_SUBCMDS = ("setup", "model", "auth", "update", "skills", "tools", "dashboard",
            "portal", "worktree", "worktrees", "sessions", "connect", "chat",
            "serve", "agent", "config", "plugins", "profiles", "run", "tui")


# --------------------------------------------------------------------------
# Home detection
# --------------------------------------------------------------------------

def _numbers_home() -> Path:
    """The validated Numbers home (raises if not a real Numbers home)."""
    return _home.require_numbers_home()


def _candidate_hermes_homes(numbers_home: Path) -> List[Path]:
    """Real Hermes homes on this machine, excluding the Numbers home.

    Probes `%LOCALAPPDATA%\\hermes` (Windows) and `~/.hermes`. A candidate
    qualifies only when it exists, is NOT a Numbers home (no numbers-home.json),
    and carries an `auth.json` or `config.yaml`.
    """
    cands: List[Path] = []
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        cands.append(Path(local_appdata) / "hermes")
    cands.append(Path.home() / ".hermes")

    try:
        nh_resolved = numbers_home.resolve()
    except Exception:
        nh_resolved = numbers_home

    out: List[Path] = []
    seen = set()
    for c in cands:
        try:
            rc = c.resolve()
        except Exception:
            rc = c
        if rc in seen:
            continue
        seen.add(rc)
        if not c.exists() or rc == nh_resolved or _home.is_numbers_home(c):
            continue
        if (c / "auth.json").exists() or (c / "config.yaml").exists():
            out.append(c)
    return out


def detect_hermes_home() -> Optional[Path]:
    """First real Hermes home distinct from the Numbers home, or None."""
    try:
        nh = _numbers_home()
    except Exception:
        raw = os.environ.get("NUMBERS_HOME") or os.environ.get("HERMES_HOME") or "."
        nh = Path(raw)
    cands = _candidate_hermes_homes(nh)
    return cands[0] if cands else None


# --------------------------------------------------------------------------
# Read-only reads from the Hermes side
# --------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_hermes_providers(hermes_home: Path) -> dict:
    """Return {credential_pool, providers, active_provider} from a Hermes auth.json."""
    d = _read_json(hermes_home / "auth.json")
    return {
        "credential_pool": d.get("credential_pool") or {},
        "providers": d.get("providers") or {},
        "active_provider": d.get("active_provider"),
    }


def read_hermes_model(hermes_home: Path) -> dict:
    """Return {provider, default} from a Hermes config.yaml (read-only)."""
    try:
        import yaml
        cfg = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    model = cfg.get("model") or {}
    if not isinstance(model, dict):
        return {}
    return {k: model[k] for k in ("provider", "default") if model.get(k)}


# --------------------------------------------------------------------------
# Write helpers — Numbers home only
# --------------------------------------------------------------------------

def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _backup(path: Path) -> None:
    """Best-effort .bak of an existing file before it is overwritten."""
    if path.exists():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except Exception:
            pass


def _read_env_file(path: Path) -> Dict[str, str]:
    """Parse a .env file into {KEY: VALUE}. Skips comments/blanks; keeps last wins."""
    out: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return out
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, val = s.split("=", 1)
        key = key.strip()
        if key.lower().startswith("export "):
            key = key[len("export "):].strip()
        if key:
            out[key] = val.strip()
    return out


def _write_env_keys(nh: Path, pairs: Dict[str, str]) -> List[str]:
    """Append KEY=VALUE pairs to the Numbers home .env (skip existing keys).

    Backs the .env up before writing. Returns the keys actually added.
    """
    if not pairs:
        return []
    dst_path = nh / ".env"
    have = set(_read_env_file(dst_path).keys()) if dst_path.exists() else set()
    to_add = {k: v for k, v in pairs.items() if k not in have}
    if not to_add:
        return []
    _backup(dst_path)
    existing = dst_path.read_text(encoding="utf-8") if dst_path.exists() else ""
    body = "".join(f"{k}={v}\n" for k, v in to_add.items())
    prefix = existing + ("\n" if existing and not existing.endswith("\n") else "")
    try:
        _atomic_write(dst_path, prefix + body)
    except Exception:
        return []
    return list(to_add.keys())


def _copy_file(src: Path, dst: Path) -> bool:
    """Copy src→dst, backing up dst first. Returns True on success."""
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        _backup(dst)
        shutil.copy2(src, dst)
        return True
    except Exception:
        return False


def _ignore_factory(src_root: Path, skip_root: frozenset = frozenset()):
    """copytree ignore: drop junk everywhere + named entries at the tree root."""
    try:
        root = src_root.resolve()
    except Exception:
        root = src_root

    def _ignore(directory: str, names: List[str]) -> List[str]:
        dropped: List[str] = []
        try:
            at_root = Path(directory).resolve() == root
        except Exception:
            at_root = False
        for n in names:
            if n == "__pycache__" or n.endswith(_JUNK_SUFFIX):
                dropped.append(n)
            elif at_root and n in skip_root:
                dropped.append(n)
        return dropped

    return _ignore


def _copytree_merge(src: Path, dst: Path, skip_root: frozenset = frozenset()) -> bool:
    """Merge-copy a directory tree into dst (existing files overwritten)."""
    try:
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_ignore_factory(src, skip_root))
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Category: providers (auth.json) + model seed
# --------------------------------------------------------------------------

def _merge_providers(numbers_home: Path, src_homes: List[Path]) -> List[str]:
    """Merge provider creds from Hermes homes into Numbers auth.json (existing win)."""
    dst_path = numbers_home / "auth.json"
    dst = _read_json(dst_path)
    dst.setdefault("version", 1)
    dst.setdefault("providers", {})
    dst.setdefault("credential_pool", {})

    added: List[str] = []
    for h in src_homes:
        src = read_hermes_providers(h)
        for name, entry in (src["credential_pool"] or {}).items():
            if name in dst["credential_pool"]:
                continue
            dst["credential_pool"][name] = entry
            added.append(name)
        for name, entry in (src["providers"] or {}).items():
            dst["providers"].setdefault(name, entry)

    if added:
        dst["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _atomic_write(dst_path, json.dumps(dst, indent=2))
    return added


def _seed_model_if_empty(numbers_home: Path, src_homes: List[Path]) -> Optional[str]:
    """Copy provider/default into Numbers config.yaml only when it has none."""
    try:
        import yaml
    except Exception:
        return None
    cfg_path = numbers_home / "config.yaml"
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    model = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    if model.get("default") or model.get("provider"):
        return None
    for h in src_homes:
        hm = read_hermes_model(h)
        if hm.get("default") or hm.get("provider"):
            cfg["model"] = {**model, **hm}
            _atomic_write(cfg_path, yaml.safe_dump(cfg, sort_keys=False))
            return hm.get("default") or hm.get("provider")
    return None


_ENV_SOURCE_RE = re.compile(r"^env:(.+)$")


def _referenced_env_vars(hermes_home: Path) -> List[str]:
    """Env-var names that a Hermes home's credentials resolve their key from.

    Each credential_pool entry may carry ``source: "env:VAR"`` — the actual key
    lives in the environment (Hermes loads its home .env), never in auth.json.
    """
    prov = read_hermes_providers(hermes_home)
    names: List[str] = []
    seen = set()
    for entries in (prov.get("credential_pool") or {}).values():
        for e in (entries if isinstance(entries, list) else [entries]):
            if not isinstance(e, dict):
                continue
            m = _ENV_SOURCE_RE.match(str(e.get("source") or "").strip())
            if m:
                var = m.group(1).strip()
                if var and var not in seen:
                    seen.add(var)
                    names.append(var)
    return names


def _import_provider_env_keys(nh: Path, src_homes: List[Path]) -> tuple[List[str], List[str]]:
    """Resolve provider key env-vars from Hermes .env(+env) into the Numbers .env.

    Returns (written_keys, skipped_denied). App-identity secrets (see
    _ENV_DENYLIST) are always skipped; user AI provider keys are imported so the
    providers resolve in the isolated Numbers home.
    """
    resolved: Dict[str, str] = {}
    denied: List[str] = []
    for h in src_homes:
        env_file = _read_env_file(h / ".env")
        for var in _referenced_env_vars(h):
            if var in _ENV_DENYLIST:
                if var not in denied:
                    denied.append(var)
                continue
            if var in resolved:
                continue
            val = env_file.get(var) or os.environ.get(var)
            if val:
                resolved[var] = val
    written = _write_env_keys(nh, resolved)
    return written, denied


def _import_providers(nh: Path, home: Path, src_homes: List[Path]) -> str:
    added = _merge_providers(nh, src_homes)
    seeded = _seed_model_if_empty(nh, src_homes)
    written, denied = _import_provider_env_keys(nh, src_homes)
    parts = []
    parts.append("providers: " + (", ".join(sorted(set(added))) if added else "none new"))
    if written:
        parts.append(f"keys imported ({', '.join(sorted(written))})")
    if denied:
        parts.append(f"app-identity secrets skipped ({', '.join(sorted(denied))})")
    if seeded:
        parts.append(f"default model {seeded}")
    return "; ".join(parts)


# --------------------------------------------------------------------------
# Category: skills (with hermes-* → numbers-* rename + dedupe)
# --------------------------------------------------------------------------

def _parse_frontmatter_name(text: str) -> Optional[str]:
    m = re.search(r"(?m)^name:\s*(.+?)\s*$", text)
    return m.group(1).strip().strip('"').strip("'") if m else None


def _existing_skill_slugs(dst_skills: Path) -> set:
    slugs = set()
    if not dst_skills.is_dir():
        return slugs
    for skill_md in dst_skills.rglob("SKILL.md"):
        try:
            name = _parse_frontmatter_name(skill_md.read_text(encoding="utf-8"))
        except Exception:
            name = None
        slugs.add((name or skill_md.parent.name).lower())
    return slugs


def _rebrand_skill_md(path: Path, new_slug: str) -> None:
    try:
        s = path.read_text(encoding="utf-8")
    except Exception:
        return
    s = re.sub(r"(?m)^name:\s*.+$", f"name: {new_slug}", s, count=1)
    s = re.sub(r"\bHermes Agent\b", "Numbers", s)
    s = re.sub(r"\bHermes\b", "Numbers", s)
    s = re.sub(r"\bhermes (" + "|".join(_SUBCMDS) + r")\b", lambda m: "numbers " + m.group(1), s)
    # related_skills entries: hermes-foo -> numbers-foo
    s = re.sub(r"\bhermes-([a-z0-9-]+)", r"numbers-\1", s)
    try:
        path.write_text(s, encoding="utf-8")
    except Exception:
        pass


def _import_skills(nh: Path, home: Path) -> str:
    src_skills = home / "skills"
    if not src_skills.is_dir():
        return "skills: none"
    dst_skills = nh / "skills"
    existing = _existing_skill_slugs(dst_skills)
    added: List[str] = []
    for skill_md in src_skills.rglob("SKILL.md"):
        parts = skill_md.parent.parts
        if any(p in {".git", ".github", ".hub", ".archive", "__pycache__"} for p in parts):
            continue
        try:
            slug = (_parse_frontmatter_name(skill_md.read_text(encoding="utf-8"))
                    or skill_md.parent.name).lower()
        except Exception:
            slug = skill_md.parent.name.lower()
        new_slug = slug.replace("hermes", "numbers") if "hermes" in slug else slug
        if new_slug in existing:
            continue  # Numbers already ships/has this skill — never clobber
        rel = list(skill_md.parent.relative_to(src_skills).parts)
        if rel:
            rel[-1] = rel[-1].replace("hermes", "numbers") if "hermes" in rel[-1].lower() else rel[-1]
        dst_dir = dst_skills.joinpath(*rel) if rel else dst_skills / new_slug
        if not _copytree_merge(skill_md.parent, dst_dir):
            continue
        if new_slug != slug or "hermes" in (dst_dir / "SKILL.md").read_text(encoding="utf-8", errors="ignore").lower():
            _rebrand_skill_md(dst_dir / "SKILL.md", new_slug)
        existing.add(new_slug)
        added.append(new_slug)
    return f"skills: {len(added)} imported" + (f" ({', '.join(sorted(added)[:6])}{'…' if len(added) > 6 else ''})" if added else "")


# --------------------------------------------------------------------------
# Categories: file/dir copiers
# --------------------------------------------------------------------------

def _import_memory(nh: Path, home: Path) -> str:
    done = []
    for name in ("memories", "knowledge", "preferences"):
        s = home / name
        if s.is_dir() and _copytree_merge(s, nh / name):
            done.append(name + "/")
    for fn in ("MEMORY.md", "USER.md"):
        s = home / fn
        if s.is_file() and _copy_file(s, nh / fn):
            done.append(fn)
    return "memory: " + (", ".join(done) if done else "none")


def _import_profiles(nh: Path, home: Path) -> str:
    src = home / "profiles"
    if not src.is_dir():
        return "profiles: none"
    names = []
    for entry in sorted(p for p in src.iterdir() if p.is_dir()):
        if entry.name.startswith("."):
            continue
        if _copytree_merge(entry, nh / "profiles" / entry.name, skip_root=_HISTORY_NAMES):
            names.append(entry.name)
    return f"profiles: {len(names)} imported" + (f" ({', '.join(names)})" if names else "")


def _import_tasks(nh: Path, home: Path) -> str:
    done = []
    for fn in ("kanban.db", "projects.db", "todo.json", "verification_evidence.db"):
        s = home / fn
        if s.is_file() and _copy_file(s, nh / fn):
            done.append(fn)
    return "tasks: " + (", ".join(done) if done else "none")


def _import_dirs(nh: Path, home: Path, dirs: List[str], label: str) -> str:
    done = []
    for name in dirs:
        s = home / name
        if s.is_dir() and any(s.iterdir()) and _copytree_merge(s, nh / name):
            done.append(name + "/")
    return f"{label}: " + (", ".join(done) if done else "none")


def _import_persona(nh: Path, home: Path) -> str:
    done = []
    for fn in ("system_prompt.md", "AGENTS.md", "CLAUDE.md", ".cursorrules"):
        s = home / fn
        if s.is_file() and not (nh / fn).exists() and _copy_file(s, nh / fn):
            done.append(fn)
    # SOUL.md ships with Numbers → only import when Numbers has none.
    if (home / "SOUL.md").is_file() and not (nh / "SOUL.md").exists():
        if _copy_file(home / "SOUL.md", nh / "SOUL.md"):
            done.append("SOUL.md")
    return "persona: " + (", ".join(done) if done else "none")


def _import_config(nh: Path, home: Path) -> str:
    try:
        import yaml
    except Exception:
        return "config: skipped (no yaml)"
    dst_path, src_path = nh / "config.yaml", home / "config.yaml"
    try:
        dst = yaml.safe_load(dst_path.read_text(encoding="utf-8")) or {}
        src = yaml.safe_load(src_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return "config: skipped (unreadable)"
    added = []
    for k, v in src.items():
        if k in _BRAND_CONFIG_KEYS or k in dst:
            continue  # keep Numbers branding + never override existing keys
        dst[k] = v
        added.append(k)
    if added:
        _backup(dst_path)
        _atomic_write(dst_path, yaml.safe_dump(dst, sort_keys=False))
    return "config: " + (", ".join(added) if added else "nothing new")


def _import_env(nh: Path, home: Path) -> str:
    src = home / ".env"
    if not src.is_file():
        return "env: none"
    try:
        src_lines = src.read_text(encoding="utf-8").splitlines()
    except Exception:
        return "env: unreadable"
    dst_path = nh / ".env"
    have = {}
    if dst_path.exists():
        for ln in dst_path.read_text(encoding="utf-8").splitlines():
            if "=" in ln and not ln.lstrip().startswith("#"):
                have[ln.split("=", 1)[0].strip()] = ln
    to_add = []
    for ln in src_lines:
        if "=" not in ln or ln.lstrip().startswith("#"):
            continue
        key = ln.split("=", 1)[0].strip()
        if key in _ENV_DENYLIST or key in have:
            continue
        to_add.append(ln)
    if to_add:
        _backup(dst_path)
        existing = dst_path.read_text(encoding="utf-8") if dst_path.exists() else ""
        _atomic_write(dst_path, existing + ("\n" if existing and not existing.endswith("\n") else "")
                      + "\n".join(to_add) + "\n")
    return f"env: {len(to_add)} keys" if to_add else "env: nothing new"


# --------------------------------------------------------------------------
# Category catalog
# --------------------------------------------------------------------------

_CAT: Dict[str, dict] = {
    "providers":  {"label": "Providers",  "advanced": False, "summary": "API keys / provider logins (auth.json)"},
    "skills":     {"label": "Skills",     "advanced": False, "summary": "your /commands (hermes-* renamed to numbers-*)"},
    "memory":     {"label": "Memory",     "advanced": False, "summary": "MEMORY.md, USER.md, knowledge, preferences"},
    "profiles":   {"label": "Profiles",   "advanced": False, "summary": "named profiles (without their history)"},
    "tasks":      {"label": "Tasks",      "advanced": False, "summary": "kanban, projects, todo"},
    "automation": {"label": "Automation", "advanced": False, "summary": "cron jobs and hooks"},
    "plugins":    {"label": "Plugins",    "advanced": False, "summary": "installed plugins / platforms"},
    "pets":       {"label": "Pets",       "advanced": False, "summary": "petdex"},
    "config":     {"label": "Config",     "advanced": False, "summary": "safe settings (keeps Numbers branding)"},
    "persona":    {"label": "Persona",    "advanced": False, "summary": "SOUL.md and prompt overrides"},
    "env":        {"label": "Env keys",   "advanced": True, "summary": "extra .env settings (app-identity secrets excluded)"},
}
_CAT_ORDER = list(_CAT.keys())


def _has_content(p: Path) -> bool:
    if p.is_dir():
        return any(p.iterdir())
    return p.is_file() and p.stat().st_size > 0


def available_categories(numbers_home: Path, src_homes: List[Path]) -> List[str]:
    """Ordered category keys that have importable content in the primary Hermes home."""
    if not src_homes:
        return []
    home = src_homes[0]
    avail: List[str] = []
    # Offer "providers" when either a provider name is new, OR a provider's key
    # env-var is still missing from the Numbers .env (the earlier import may have
    # merged the credential names but not their keys — see _import_provider_env_keys).
    if _missing_providers(numbers_home, src_homes) or _missing_provider_keys(numbers_home, src_homes):
        avail.append("providers")
    if (home / "skills").is_dir():
        avail.append("skills")
    if any(_has_content(home / n) for n in ("memories", "knowledge", "preferences")) \
            or (home / "MEMORY.md").is_file() or (home / "USER.md").is_file():
        avail.append("memory")
    if (home / "profiles").is_dir() and any(p.is_dir() for p in (home / "profiles").iterdir()):
        avail.append("profiles")
    if any((home / f).is_file() for f in ("kanban.db", "projects.db", "todo.json", "verification_evidence.db")):
        avail.append("tasks")
    if any(_has_content(home / n) for n in ("cron", "hooks")):
        avail.append("automation")
    if any(_has_content(home / n) for n in ("plugins", "desktop-plugins", "platforms")):
        avail.append("plugins")
    if _has_content(home / "pets"):
        avail.append("pets")
    if (home / "config.yaml").is_file():
        avail.append("config")
    if any((home / f).is_file() for f in ("system_prompt.md", "AGENTS.md", "CLAUDE.md", ".cursorrules", "SOUL.md")):
        avail.append("persona")
    if (home / ".env").is_file():
        avail.append("env")
    return [k for k in _CAT_ORDER if k in avail]


def _run_category(key: str, nh: Path, home: Path, src_homes: List[Path]) -> str:
    if key == "providers":
        return _import_providers(nh, home, src_homes)
    if key == "skills":
        return _import_skills(nh, home)
    if key == "memory":
        return _import_memory(nh, home)
    if key == "profiles":
        return _import_profiles(nh, home)
    if key == "tasks":
        return _import_tasks(nh, home)
    if key == "automation":
        return _import_dirs(nh, home, ["cron", "hooks"], "automation")
    if key == "plugins":
        return _import_dirs(nh, home, ["plugins", "desktop-plugins", "platforms"], "plugins")
    if key == "pets":
        return _import_dirs(nh, home, ["pets"], "pets")
    if key == "config":
        return _import_config(nh, home)
    if key == "persona":
        return _import_persona(nh, home)
    if key == "env":
        return _import_env(nh, home)
    return f"{key}: unknown category"


# --------------------------------------------------------------------------
# Selection + entrypoint
# --------------------------------------------------------------------------

def _missing_providers(numbers_home: Path, src_homes: List[Path]) -> List[str]:
    """Provider names present in a Hermes home but not yet in the Numbers home."""
    dst = _read_json(numbers_home / "auth.json")
    have = set((dst.get("credential_pool") or {}).keys()) | set((dst.get("providers") or {}).keys())
    missing = set()
    for h in src_homes:
        src = read_hermes_providers(h)
        missing |= (set((src["credential_pool"] or {}).keys()) |
                    set((src["providers"] or {}).keys())) - have
    return sorted(missing)


def _missing_provider_keys(numbers_home: Path, src_homes: List[Path]) -> List[str]:
    """Provider key env-vars referenced by Hermes creds but absent from Numbers .env.

    Excludes app-identity secrets (never imported). Used so `providers` stays on
    the menu until the user's own keys are actually present in the Numbers home.
    """
    have = set(_read_env_file(numbers_home / ".env").keys())
    missing: List[str] = []
    for h in src_homes:
        env_file = _read_env_file(h / ".env")
        for var in _referenced_env_vars(h):
            if var in _ENV_DENYLIST or var in have or var in missing:
                continue
            if env_file.get(var) or os.environ.get(var):
                missing.append(var)
    return missing


def _recommended(avail: List[str]) -> List[str]:
    return [k for k in avail if not _CAT[k]["advanced"]]


def _prompt_selection(avail: List[str], print_fn: Callable, prompt_fn: Callable) -> List[str]:
    print_fn("")
    print_fn("Select what to import from Hermes:")
    for i, key in enumerate(avail, 1):
        meta = _CAT[key]
        tag = "" if meta["advanced"] else "  (recommended)"
        print_fn(f"  {i}. {meta['label']}{tag} - {meta['summary']}")
    print_fn("")
    raw = (prompt_fn(
        "Press Enter for the recommended set, or type numbers separated by "
        "commas (e.g. 1,3,5). 'all' / 'none' also work: ") or "").strip().lower()
    if raw in ("", "y", "yes", "all", "recommended"):
        return _recommended(avail)
    if raw in ("n", "no", "none"):
        return []
    picks: List[str] = []
    # Accept commas and/or spaces (and mixed): "1,3,5", "1 3 5", "1, 3 5".
    for tok in re.split(r"[,\s]+", raw):
        if not tok:
            continue
        if tok.isdigit():
            idx = int(tok) - 1
            if 0 <= idx < len(avail):
                picks.append(avail[idx])
        elif tok in avail:
            picks.append(tok)
    return picks


def run_import(print_fn: Callable = print, prompt_fn: Optional[Callable] = None,
               *, ask: bool = True, selection: Optional[List[str]] = None) -> int:
    """Import selected categories from the detected Hermes home(s)."""
    prompt_fn = prompt_fn or (lambda t: input(t))
    try:
        nh = _numbers_home()
    except Exception as exc:
        print_fn(str(exc))
        return 1
    src = _candidate_hermes_homes(nh)
    if not src:
        print_fn("No existing Hermes install found - nothing to import.")
        return 0
    avail = available_categories(nh, src)
    if not avail:
        print_fn("Nothing new to import from Hermes.")
        return 0

    if selection is None:
        pretty = ", ".join(str(p) for p in src)
        print_fn(f"Found an existing Hermes install: {pretty}")
        selection = _prompt_selection(avail, print_fn, prompt_fn) if ask else _recommended(avail)
    selection = [c for c in selection if c in avail]
    if not selection:
        print_fn("Nothing selected - nothing imported.")
        return 0

    home = src[0]
    print_fn("")
    for key in [k for k in _CAT_ORDER if k in selection]:
        print_fn("  imported " + _run_category(key, nh, home, src))
    print_fn("")
    print_fn("Restart Numbers to pick up the imported items.")
    return 0


def offer_import(print_fn: Callable = print, prompt_fn: Optional[Callable] = None) -> int:
    """First-run, guarded offer. No-op once the guard file exists."""
    try:
        nh = _numbers_home()
    except Exception:
        return 0
    guard = nh / GUARD_NAME
    if guard.exists():
        return 0
    src = _candidate_hermes_homes(nh)
    if not src or not available_categories(nh, src):
        guard.write_text("skip\n", encoding="utf-8")
        return 0
    run_import(print_fn=print_fn, prompt_fn=prompt_fn, ask=True)
    guard.write_text("done\n", encoding="utf-8")
    return 0


def _parse_csv(value: Optional[str]) -> List[str]:
    return [t.strip() for t in (value or "").split(",") if t.strip()]


def main(argv: Optional[list] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="numbers import-hermes",
                                     description="Import an existing Hermes install into Numbers, by category.")
    parser.add_argument("--offer", action="store_true", help="First-run guarded offer (no-op after the first time).")
    parser.add_argument("--force", action="store_true", help="Ignore the one-time guard and open the selector.")
    parser.add_argument("--only", help="Import only these categories (comma list).")
    parser.add_argument("--all", action="store_true", help="Import the recommended set (no prompt).")
    parser.add_argument("--all-including", help="Recommended set plus advanced categories (e.g. env).")
    parser.add_argument("--exclude", help="Remove these categories from the selection (comma list).")
    args = parser.parse_args(argv)

    if args.offer and not args.force:
        return offer_import()

    # Build an explicit selection from flags, else fall back to the interactive prompt.
    selection: Optional[List[str]] = None
    try:
        nh = _numbers_home()
        src = _candidate_hermes_homes(nh)
        avail = available_categories(nh, src)
    except Exception:
        avail = list(_CAT_ORDER)
    if args.only:
        selection = _parse_csv(args.only)
    elif args.all or args.all_including:
        selection = _recommended(avail)
        if args.all_including:
            selection += [c for c in _parse_csv(args.all_including) if c in _CAT]
    if selection is not None and args.exclude:
        excl = set(_parse_csv(args.exclude))
        selection = [c for c in selection if c not in excl]
    return run_import(ask=(selection is None), selection=selection)


if __name__ == "__main__":
    raise SystemExit(main())
