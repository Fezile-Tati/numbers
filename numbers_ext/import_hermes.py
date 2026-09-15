"""Import an existing Hermes install into the isolated Numbers home — by category.

Numbers keeps its own `HERMES_HOME` (`%LOCALAPPDATA%\\numbers`), so anything the
user set up in stock Hermes (providers, skills, tools, memory, profiles, cron,
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

def _merge_providers(numbers_home: Path, src_homes: List[Path],
                     picks: Optional[set] = None) -> List[str]:
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
            if name in dst["credential_pool"] or not _wanted(picks, name):
                continue
            dst["credential_pool"][name] = entry
            added.append(name)
        for name, entry in (src["providers"] or {}).items():
            if _wanted(picks, name):
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


def _referenced_env_vars(hermes_home: Path, picks: Optional[set] = None) -> List[str]:
    """Env-var names that a Hermes home's credentials resolve their key from.

    Each credential_pool entry may carry ``source: "env:VAR"`` — the actual key
    lives in the environment (Hermes loads its home .env), never in auth.json.
    """
    prov = read_hermes_providers(hermes_home)
    names: List[str] = []
    seen = set()
    for cred_name, entries in (prov.get("credential_pool") or {}).items():
        if not _wanted(picks, cred_name):
            continue
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


def _import_provider_env_keys(nh: Path, src_homes: List[Path],
                              picks: Optional[set] = None) -> tuple[List[str], List[str]]:
    """Resolve provider key env-vars from Hermes .env(+env) into the Numbers .env.

    Returns (written_keys, skipped_denied). App-identity secrets (see
    _ENV_DENYLIST) are always skipped; user AI provider keys are imported so the
    providers resolve in the isolated Numbers home.
    """
    resolved: Dict[str, str] = {}
    denied: List[str] = []
    for h in src_homes:
        env_file = _read_env_file(h / ".env")
        for var in _referenced_env_vars(h, picks):
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


def _import_providers(nh: Path, home: Path, src_homes: List[Path],
                      picks: Optional[set] = None) -> str:
    added = _merge_providers(nh, src_homes, picks)
    seeded = _seed_model_if_empty(nh, src_homes)
    written, denied = _import_provider_env_keys(nh, src_homes, picks)
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

def _wanted(picks: Optional[set], item_id: str) -> bool:
    """True when this individual item is in scope.

    ``picks is None`` means "the whole category" -- the scripted/recommended
    path never has to enumerate anything.
    """
    return picks is None or item_id in picks


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


def _skill_slugs(home: Path, dst_skills: Path) -> List[tuple]:
    """(slug, source_dir) for each importable skill -- ones Numbers lacks."""
    src_skills = home / "skills"
    if not src_skills.is_dir():
        return []
    existing = _existing_skill_slugs(dst_skills)
    out: List[tuple] = []
    for skill_md in sorted(src_skills.rglob("SKILL.md")):
        if any(p in {".git", ".github", ".hub", ".archive", "__pycache__"}
               for p in skill_md.parent.parts):
            continue
        try:
            slug = (_parse_frontmatter_name(skill_md.read_text(encoding="utf-8"))
                    or skill_md.parent.name).lower()
        except Exception:
            slug = skill_md.parent.name.lower()
        new_slug = slug.replace("hermes", "numbers") if "hermes" in slug else slug
        if new_slug in existing:
            continue  # Numbers already ships/has this skill - never clobber
        existing.add(new_slug)
        out.append((new_slug, slug, skill_md.parent))
    return out


def _import_skills(nh: Path, home: Path, picks: Optional[set] = None) -> str:
    src_skills = home / "skills"
    if not src_skills.is_dir():
        return "skills: none"
    dst_skills = nh / "skills"
    added: List[str] = []
    for new_slug, slug, src_dir in _skill_slugs(home, dst_skills):
        if not _wanted(picks, new_slug):
            continue
        rel = list(src_dir.relative_to(src_skills).parts)
        if rel:
            rel[-1] = rel[-1].replace("hermes", "numbers") if "hermes" in rel[-1].lower() else rel[-1]
        dst_dir = dst_skills.joinpath(*rel) if rel else dst_skills / new_slug
        if not _copytree_merge(src_dir, dst_dir):
            continue
        if new_slug != slug or "hermes" in (dst_dir / "SKILL.md").read_text(encoding="utf-8", errors="ignore").lower():
            _rebrand_skill_md(dst_dir / "SKILL.md", new_slug)
        added.append(new_slug)
    return f"skills: {len(added)} imported" + (f" ({', '.join(sorted(added)[:6])}{'…' if len(added) > 6 else ''})" if added else "")


# --------------------------------------------------------------------------
# Categories: file/dir copiers
# --------------------------------------------------------------------------

_MEMORY_DIRS = ("memories", "knowledge", "preferences")
_MEMORY_FILES = ("MEMORY.md", "USER.md")
_TASK_FILES = ("kanban.db", "projects.db", "todo.json", "verification_evidence.db")
_PERSONA_FILES = ("system_prompt.md", "AGENTS.md", "CLAUDE.md", ".cursorrules", "SOUL.md")


def _import_memory(nh: Path, home: Path, picks: Optional[set] = None) -> str:
    done = []
    for name in _MEMORY_DIRS:
        s = home / name
        if s.is_dir() and _wanted(picks, name) and _copytree_merge(s, nh / name):
            done.append(name + "/")
    for fn in _MEMORY_FILES:
        s = home / fn
        if s.is_file() and _wanted(picks, fn) and _copy_file(s, nh / fn):
            done.append(fn)
    return "memory: " + (", ".join(done) if done else "none")


def _profile_names(home: Path) -> List[str]:
    src = home / "profiles"
    if not src.is_dir():
        return []
    return sorted(p.name for p in src.iterdir()
                  if p.is_dir() and not p.name.startswith("."))


def _import_profiles(nh: Path, home: Path, picks: Optional[set] = None) -> str:
    names = []
    for name in _profile_names(home):
        if not _wanted(picks, name):
            continue
        # skip_root=_HISTORY_NAMES is what keeps past chat sessions out: a
        # profile's sessions/, state.db and checkpoints/ never come across.
        if _copytree_merge(home / "profiles" / name, nh / "profiles" / name,
                           skip_root=_HISTORY_NAMES):
            names.append(name)
    return f"profiles: {len(names)} imported" + (f" ({', '.join(names)})" if names else "")


def _import_tasks(nh: Path, home: Path, picks: Optional[set] = None) -> str:
    done = []
    for fn in _TASK_FILES:
        s = home / fn
        if s.is_file() and _wanted(picks, fn) and _copy_file(s, nh / fn):
            done.append(fn)
    return "tasks: " + (", ".join(done) if done else "none")


def _hermes_mcp_servers(home: Path) -> dict:
    """The `mcp_servers` map from a Hermes config.yaml (read-only)."""
    try:
        import yaml
        cfg = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    servers = cfg.get("mcp_servers")
    return servers if isinstance(servers, dict) else {}


def _import_mcp_servers(nh: Path, home: Path, picks: Optional[set] = None) -> List[str]:
    """Merge the user's MCP servers (their "tools") into the Numbers config.

    `mcp_servers` is a Numbers-owned key for the `config` category -- that
    category copies it wholesale and would drop Numbers' own `angel` server.
    Here it is merged name-by-name with existing entries winning, so the user's
    tools arrive and Numbers' own wiring survives.
    """
    src = _hermes_mcp_servers(home)
    if not src:
        return []
    try:
        import yaml
        cfg_path = nh / "config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    dst = cfg.get("mcp_servers")
    if not isinstance(dst, dict):
        dst = {}
    added = [name for name in src if name not in dst and _wanted(picks, "mcp:" + name)]
    if not added:
        return []
    for name in added:
        dst[name] = src[name]
    cfg["mcp_servers"] = dst
    try:
        _backup(cfg_path)
        _atomic_write(cfg_path, yaml.safe_dump(cfg, sort_keys=False))
    except Exception:
        return []
    return added


_TOOL_DIRS = ("plugins", "desktop-plugins", "platforms")


def _import_tools(nh: Path, home: Path, picks: Optional[set] = None) -> str:
    """The `tools` category: MCP servers plus plugin/platform directories."""
    parts = []
    servers = _import_mcp_servers(nh, home, picks)
    parts.append(f"mcp servers: {', '.join(servers)}" if servers else "mcp servers: none new")
    dirs = [d for d in _TOOL_DIRS if _wanted(picks, "dir:" + d)]
    parts.append(_import_dirs(nh, home, dirs, "plugins"))
    return "; ".join(parts)


def _import_dirs(nh: Path, home: Path, dirs: List[str], label: str,
                 picks: Optional[set] = None) -> str:
    done = []
    for name in dirs:
        s = home / name
        if s.is_dir() and any(s.iterdir()) and _wanted(picks, name)                 and _copytree_merge(s, nh / name):
            done.append(name + "/")
    return f"{label}: " + (", ".join(done) if done else "none")


def _import_persona(nh: Path, home: Path, picks: Optional[set] = None) -> str:
    done = []
    # Every persona file (SOUL.md included) is import-only-if-absent: Numbers
    # ships its own SOUL.md and must never have it silently replaced.
    for fn in _PERSONA_FILES:
        s = home / fn
        if s.is_file() and not (nh / fn).exists() and _wanted(picks, fn) \
                and _copy_file(s, nh / fn):
            done.append(fn)
    return "persona: " + (", ".join(done) if done else "none")


def _import_config(nh: Path, home: Path, picks: Optional[set] = None) -> str:
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
        if k in _BRAND_CONFIG_KEYS or k in dst or not _wanted(picks, k):
            continue  # keep Numbers branding + never override existing keys
        dst[k] = v
        added.append(k)
    if added:
        _backup(dst_path)
        _atomic_write(dst_path, yaml.safe_dump(dst, sort_keys=False))
    return "config: " + (", ".join(added) if added else "nothing new")


def _import_env(nh: Path, home: Path, picks: Optional[set] = None) -> str:
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
        if key in _ENV_DENYLIST or key in have or not _wanted(picks, key):
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
    "tools":      {"label": "Tools",      "advanced": False, "summary": "your MCP servers, plugins and platforms"},
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
    if any(_has_content(home / n) for n in ("plugins", "desktop-plugins", "platforms")) \
            or _hermes_mcp_servers(home):
        avail.append("tools")
    if _has_content(home / "pets"):
        avail.append("pets")
    if (home / "config.yaml").is_file():
        avail.append("config")
    if any((home / f).is_file() for f in ("system_prompt.md", "AGENTS.md", "CLAUDE.md", ".cursorrules", "SOUL.md")):
        avail.append("persona")
    if (home / ".env").is_file():
        avail.append("env")
    return [k for k in _CAT_ORDER if k in avail]


def _run_category(key: str, nh: Path, home: Path, src_homes: List[Path],
                  picks: Optional[set] = None) -> str:
    if key == "providers":
        return _import_providers(nh, home, src_homes, picks)
    if key == "skills":
        return _import_skills(nh, home, picks)
    if key == "memory":
        return _import_memory(nh, home, picks)
    if key == "profiles":
        return _import_profiles(nh, home, picks)
    if key == "tasks":
        return _import_tasks(nh, home, picks)
    if key == "automation":
        return _import_dirs(nh, home, ["cron", "hooks"], "automation", picks)
    if key == "tools":
        return _import_tools(nh, home, picks)
    if key == "pets":
        return _import_dirs(nh, home, ["pets"], "pets", picks)
    if key == "config":
        return _import_config(nh, home, picks)
    if key == "persona":
        return _import_persona(nh, home, picks)
    if key == "env":
        return _import_env(nh, home, picks)
    return f"{key}: unknown category"


# --------------------------------------------------------------------------
# Individual items within a category
# --------------------------------------------------------------------------

def list_items(key: str, nh: Path, home: Path,
               src_homes: List[Path]) -> List[tuple]:
    """(item_id, label) for everything importable in a category, or [].

    An empty list means "this category is all-or-nothing" -- there is nothing
    meaningful to tick off one by one, so the caller should not prompt.
    The ids returned here are exactly what ``_wanted()`` matches against.
    """
    if key == "skills":
        return [(slug, slug) for slug, _old, _d in _skill_slugs(home, nh / "skills")]
    if key == "profiles":
        return [(n, n + "  (no chat history)") for n in _profile_names(home)]
    if key == "providers":
        return [(n, n) for n in _missing_providers(nh, src_homes)]
    if key == "memory":
        out = [(n, n + "/") for n in _MEMORY_DIRS if _has_content(home / n)]
        return out + [(f, f) for f in _MEMORY_FILES if (home / f).is_file()]
    if key == "persona":
        return [(f, f) for f in _PERSONA_FILES
                if (home / f).is_file() and not (nh / f).exists()]
    if key == "tasks":
        return [(f, f) for f in _TASK_FILES if (home / f).is_file()]
    if key == "automation":
        return [(n, n + "/") for n in ("cron", "hooks") if _has_content(home / n)]
    if key == "tools":
        have = _hermes_mcp_servers(home)
        out = [("mcp:" + n, n + "  (MCP server)") for n in sorted(have)]
        return out + [("dir:" + n, n + "/") for n in _TOOL_DIRS
                      if _has_content(home / n)]
    if key == "config":
        try:
            import yaml
            src = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}
            dst = yaml.safe_load((nh / "config.yaml").read_text(encoding="utf-8")) or {}
        except Exception:
            return []
        return [(k, k) for k in sorted(src)
                if k not in _BRAND_CONFIG_KEYS and k not in dst]
    if key == "env":
        have = set(_read_env_file(nh / ".env"))
        return [(k, k) for k in sorted(_read_env_file(home / ".env"))
                if k not in _ENV_DENYLIST and k not in have]
    return []


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


def _example(avail: List[str], slots: tuple = (1, 4, 5)) -> str:
    """A worked 'type this, get that' example built from the live menu.

    Falls back to whatever positions exist, so a two-entry menu reads
    'e.g. 1,2' rather than pointing at numbers that were never printed.
    """
    idx = [i for i in slots if i <= len(avail)] or [1]
    if len(idx) < 2:
        idx = list(range(1, min(len(avail), 3) + 1))
    names = [_CAT[avail[i - 1]]["label"] for i in idx]
    listed = ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]
    return f'e.g. "{",".join(str(i) for i in idx)}" imports {listed}'


def _prompt_selection(avail: List[str], print_fn: Callable, prompt_fn: Callable) -> List[str]:
    print_fn("")
    print_fn("Select what to import from Hermes:")
    for i, key in enumerate(avail, 1):
        meta = _CAT[key]
        tag = "" if meta["advanced"] else "  (recommended)"
        print_fn(f"  {i}. {meta['label']}{tag} - {meta['summary']}")
    # Spelled out as menu entries of their own: "'all' / 'none' also work"
    # tacked onto the end of a long prompt line was easy to read past.
    print_fn(f"  a. All - every category listed above (1-{len(avail)})")
    print_fn("  n. None - import nothing")
    print_fn("")
    # Worked example against this exact menu, so "several at once" is shown
    # rather than described. Built from the live list: the numbers and the
    # names can never disagree with what was just printed above.
    print_fn(f"  Pick several by separating them with commas - {_example(avail)}")
    print_fn("")
    raw = (prompt_fn(
        "Enter = recommended, 'a' = all, 'n' = none, "
        "or your numbers: ") or "").strip().lower()
    if raw in ("", "y", "yes", "recommended", "r"):
        return _recommended(avail)
    if raw in ("a", "all", "everything"):
        return list(avail)
    if raw in ("n", "no", "none", "q", "quit", "skip"):
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


def _prompt_items(key: str, items: List[tuple], print_fn: Callable,
                  prompt_fn: Callable) -> Optional[set]:
    """Let the user tick off individual items. None == "all of them".

    Only worth asking when there is an actual choice to make, so a category
    with fewer than two items is left alone.
    """
    if len(items) < 2:
        return None
    label = _CAT[key]["label"]
    print_fn("")
    print_fn(f"  {label} - pick the ones you want:")
    for i, (_id, text) in enumerate(items, 1):
        print_fn(f"    {i}. {text}")
    print_fn(f"    a. All {len(items)}")
    print_fn(f"    n. None - skip {label.lower()}")
    raw = (prompt_fn(
        "  Enter = all, 'n' = none, or numbers (e.g. 1,3): ") or "").strip().lower()
    if raw in ("", "a", "all", "y", "yes", "everything"):
        return None
    if raw in ("n", "no", "none", "q", "quit", "skip"):
        return set()
    chosen: set = set()
    for tok in re.split(r"[,\s]+", raw):
        if not tok:
            continue
        if tok.isdigit():
            idx = int(tok) - 1
            if 0 <= idx < len(items):
                chosen.add(items[idx][0])
        else:
            for item_id, _text in items:
                if tok == item_id.lower():
                    chosen.add(item_id)
    return chosen


def run_import(print_fn: Callable = print, prompt_fn: Optional[Callable] = None,
               *, ask: bool = True, selection: Optional[List[str]] = None,
               items: Optional[Dict[str, set]] = None) -> int:
    """Import selected categories from the detected Hermes home(s).

    ``items`` narrows a category to individual entries: ``{"skills": {"a"}}``.
    A category absent from the mapping imports everything it has.
    """
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
    order = [k for k in _CAT_ORDER if k in selection]
    items = dict(items or {})
    if ask:
        # Second pass: now that the categories are known, drill into each one
        # so the user can keep the two skills they care about and drop 58.
        for key in order:
            if key in items:
                continue
            catalog = list_items(key, nh, home, src)
            picked = _prompt_items(key, catalog, print_fn, prompt_fn)
            if picked is not None:
                items[key] = picked
    print_fn("")
    for key in order:
        picks = items.get(key)
        if picks is not None and not picks:
            print_fn(f"  skipped {key}: nothing selected")
            continue
        print_fn("  imported " + _run_category(key, nh, home, src, picks))
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
    # This path fires automatically from the launcher, ahead of the user's
    # actual command, so it must never be what stops them getting a prompt.
    # A piped/redirected stdin raises EOFError out of input() before anything
    # is imported; leave the guard unwritten so the offer survives to a real
    # terminal, and let the launcher continue either way.
    try:
        run_import(print_fn=print_fn, prompt_fn=prompt_fn, ask=True)
    except (EOFError, KeyboardInterrupt):
        return 0
    except Exception:
        return 0
    guard.write_text("done\n", encoding="utf-8")
    return 0


def _parse_csv(value: Optional[str]) -> List[str]:
    return [t.strip() for t in (value or "").split(",") if t.strip()]


def _parse_items(value: Optional[str]) -> Dict[str, set]:
    """'skills:a,b;profiles:work' -> {'skills': {'a','b'}, 'profiles': {'work'}}.

    Semicolons separate categories because item ids (MCP servers, env keys)
    may themselves contain commas-unfriendly characters but never ';'.
    """
    out: Dict[str, set] = {}
    for chunk in (value or "").split(";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        key, _, rest = chunk.partition(":")
        key = key.strip()
        if key in _CAT:
            out[key] = set(_parse_csv(rest))
    return out


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
    parser.add_argument("--items",
                        help='Pick individual items, e.g. "skills:pdf,ocr;profiles:work". '
                             "Categories left out import everything they have.")
    parser.add_argument("--list-items", metavar="CATEGORY",
                        help="Print the importable items in a category and exit.")
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
        nh, src = None, []
    if args.list_items:
        if nh is None or not src:
            print("No existing Hermes install found - nothing to import.")
            return 0
        for item_id, text in list_items(args.list_items, nh, src[0], src):
            print(f"{item_id}\t{text}")
        return 0
    if args.only:
        selection = _parse_csv(args.only)
    elif args.all or args.all_including:
        selection = _recommended(avail)
        if args.all_including:
            selection += [c for c in _parse_csv(args.all_including) if c in _CAT]
    if selection is not None and args.exclude:
        excl = set(_parse_csv(args.exclude))
        selection = [c for c in selection if c not in excl]
    picked = _parse_items(args.items)
    return run_import(ask=(selection is None and not picked), selection=selection,
                      items=picked or None)


if __name__ == "__main__":
    raise SystemExit(main())
