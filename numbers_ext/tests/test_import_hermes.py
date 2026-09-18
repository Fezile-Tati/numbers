"""Tests for numbers_ext.import_hermes — read-only detect + opt-in provider import."""
import json
from pathlib import Path

import pytest

from numbers_ext import home
from numbers_ext import import_hermes as ih


@pytest.fixture()
def homes(tmp_path, monkeypatch):
    """A fake Numbers home + a fake Hermes home, fully isolated from the real box.

    Pins LOCALAPPDATA and Path.home() at tmp so detection can never reach the
    operator's real ~/.hermes or %LOCALAPPDATA%\\hermes.
    """
    numbers = tmp_path / "numbers"
    home.write_marker(numbers, "v1.0.0")
    (numbers / "auth.json").write_text(json.dumps({
        "version": 1,
        "providers": {},
        "credential_pool": {"anthropic": [{"key": "numbers-own"}]},
        "active_provider": None,
    }), encoding="utf-8")

    appdata = tmp_path / "appdata"
    hermes = appdata / "hermes"
    hermes.mkdir(parents=True)
    (hermes / "auth.json").write_text(json.dumps({
        "version": 1,
        "providers": {"nous": {"oauth": True}},
        "credential_pool": {
            "anthropic": [{"source": "env:ANTHROPIC_TOKEN", "auth_type": "oauth"}],
            "deepseek": [{"source": "env:DEEPSEEK_API_KEY", "auth_type": "api_key"}],
            "zai": [{"source": "env:GLM_API_KEY", "auth_type": "api_key"}],
        },
        "active_provider": "deepseek",
    }), encoding="utf-8")
    (hermes / "config.yaml").write_text(
        "model:\n  provider: deepseek\n  default: deepseek/deepseek-chat\n", encoding="utf-8")
    # Keys live in the Hermes .env (auth.json only points at env vars). The
    # app-identity secret must never be imported.
    (hermes / ".env").write_text(
        "DEEPSEEK_API_KEY=ds-secret\n"
        "GLM_API_KEY=glm-secret\n"
        "ANGEL_CLOUD_API_KEY=app-only-must-not-import\n"
        "TERMINAL_TIMEOUT=30\n", encoding="utf-8")

    userprofile = tmp_path / "userprofile"
    userprofile.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    monkeypatch.setenv("NUMBERS_HOME", str(numbers))
    monkeypatch.setenv("HERMES_HOME", str(numbers))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: userprofile))
    return {"numbers": numbers, "hermes": hermes}


def test_detects_hermes_home_not_numbers(homes):
    assert ih.detect_hermes_home() == homes["hermes"]


def test_reads_providers_readonly(homes):
    prov = ih.read_hermes_providers(homes["hermes"])
    assert "deepseek" in prov["credential_pool"]
    assert prov["active_provider"] == "deepseek"
    assert ih.read_hermes_model(homes["hermes"]) == {
        "provider": "deepseek", "default": "deepseek/deepseek-chat"}


def test_import_merges_without_overwriting(homes):
    before = (homes["hermes"] / "auth.json").read_text(encoding="utf-8")
    rc = ih.run_import(print_fn=lambda *a, **k: None, ask=False)
    assert rc == 0
    dst = json.loads((homes["numbers"] / "auth.json").read_text(encoding="utf-8"))
    # deepseek imported; Numbers' own anthropic key preserved (not overwritten).
    assert "deepseek" in dst["credential_pool"]
    assert dst["credential_pool"]["anthropic"] == [{"key": "numbers-own"}]
    assert dst["providers"].get("nous") == {"oauth": True}
    # Hermes side is untouched (read-only).
    assert (homes["hermes"] / "auth.json").read_text(encoding="utf-8") == before


def test_provider_import_writes_env_keys(homes):
    before_env = (homes["hermes"] / ".env").read_text(encoding="utf-8")
    ih.run_import(print_fn=lambda *a, **k: None, ask=False, selection=["providers"])
    env = ih._read_env_file(homes["numbers"] / ".env")
    # User AI provider keys imported so the providers resolve in Numbers.
    assert env.get("DEEPSEEK_API_KEY") == "ds-secret"
    assert env.get("GLM_API_KEY") == "glm-secret"
    # App-identity secret never imported.
    assert "ANGEL_CLOUD_API_KEY" not in env
    # Hermes .env untouched (read-only).
    assert (homes["hermes"] / ".env").read_text(encoding="utf-8") == before_env


def test_selection_accepts_spaces_or_commas(homes):
    avail = ["providers", "skills", "memory", "config"]
    by_comma = ih._prompt_selection(avail, lambda *a, **k: None, lambda _t: "1,3")
    by_space = ih._prompt_selection(avail, lambda *a, **k: None, lambda _t: "1 3")
    # "1,3" imports lines 1 and 3 -- the numbers mean what the menu shows.
    assert by_comma.categories == by_space.categories == ["providers", "memory"]


def test_sessions_category_removed():
    assert "sessions" not in ih._CAT
    assert "sessions" not in ih._CAT_ORDER


# --------------------------------------------------------------------------
# parse_selection: the numbers on screen are the numbers you get.
#
# They used to be read as a list to LEAVE OUT, which a numbered menu cannot
# communicate: answering "1" to a menu headed "1. Providers" imported
# everything except providers, and said nothing about it.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("", ["providers", "skills", "memory"]),        # Enter == take it all
    ("all", ["providers", "skills", "memory"]),
    ("a", ["providers", "skills", "memory"]),
    ("2", ["skills"]),
    ("1,3", ["providers", "memory"]),
    ("1 3", ["providers", "memory"]),
    ("3;1", ["providers", "memory"]),               # menu order, not typing order
    ("2,2", ["skills"]),                            # a repeat is not two imports
])
def test_parse_selection(raw, expected):
    avail = ["providers", "skills", "memory"]
    assert ih.parse_selection(raw, avail) == expected


@pytest.mark.parametrize("raw", ["none", "no", "skip", "q", "quit", "cancel"])
def test_parse_selection_none_is_a_stop_not_an_empty_pick(raw):
    """None and [] are different answers: "stop" vs "you picked nothing"."""
    assert ih.parse_selection(raw, ["providers", "skills", "memory"]) is None


@pytest.mark.parametrize("bad", ["9", "wibble", "1,all", "0", "-1", "1.5"])
def test_parse_selection_rejects_what_it_cannot_interpret(bad):
    avail = ["providers", "skills", "memory"]
    with pytest.raises(ih.SpecError):
        ih.parse_selection(bad, avail)


def test_no_exclusion_parser_survives():
    """The inverted reading is gone from the module, not just from the prompt."""
    assert not hasattr(ih, "parse_exclusions")


@pytest.mark.parametrize("size, expected", [
    (1, "1"), (2, "1,2"), (3, "1,2"), (5, "1,2,5"), (9, "1,2,5,8"),
])
def test_example_answer_always_fits_the_menu(size, expected):
    """An example citing line 8 of a 5-line menu teaches a format that fails."""
    assert ih.example_answer(["x"] * size) == expected


def test_skills_not_offered_when_numbers_already_has_every_slug(rich):
    """Regression: Skills used to be offered whenever the Hermes skills/ dir
    merely existed, even with zero real delta -- reporting '0 imported'."""
    # Import once so Numbers now has every Hermes skill.
    ih.run_import(print_fn=lambda *a, **k: None, ask=False, selection=["skills"])
    src = ih._candidate_hermes_homes(rich["numbers"])
    assert "skills" not in ih.available_categories(rich["numbers"], src)


def test_offer_is_one_shot(homes):
    out = []
    ih.offer_import(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                    prompt_fn=lambda _t: "y")
    guard = homes["numbers"] / ih.GUARD_NAME
    assert guard.exists()
    # Second call is a no-op (guard present) — no new prompt/print.
    out.clear()
    ih.offer_import(print_fn=lambda *a, **k: out.append("x"), prompt_fn=lambda _t: "y")
    assert out == []


def test_decline_skips_import(homes):
    # "none" is how you decline. ("all" used to mean this, by way of excluding
    # every category -- the inversion that made the menu unreadable.)
    ih.run_import(print_fn=lambda *a, **k: None, prompt_fn=lambda _t: "none", ask=True)
    dst = json.loads((homes["numbers"] / "auth.json").read_text(encoding="utf-8"))
    assert "deepseek" not in dst["credential_pool"]


def test_accepting_imports_everything(homes):
    """The mirror of the test above: "all" takes the lot."""
    ih.run_import(print_fn=lambda *a, **k: None, prompt_fn=lambda _t: "all", ask=True)
    dst = json.loads((homes["numbers"] / "auth.json").read_text(encoding="utf-8"))
    assert "deepseek" in dst["credential_pool"]
    # Numbers' own credential still wins over the imported one.
    assert dst["credential_pool"]["anthropic"] == [{"key": "numbers-own"}]


@pytest.fixture()
def rich(tmp_path, monkeypatch):
    """A Hermes home with skills (incl. hermes-agent), memory, and config extras,
    plus a Numbers home carrying branded config.yaml + marker."""
    numbers = tmp_path / "numbers"
    home.write_marker(numbers, "v1.0.0")
    (numbers / "auth.json").write_text(json.dumps(
        {"version": 1, "providers": {}, "credential_pool": {"anthropic": [{"k": 1}]}}), encoding="utf-8")
    (numbers / "skins").mkdir()
    (numbers / "skins" / "numbers.yaml").write_text("name: numbers\n", encoding="utf-8")
    (numbers / "config.yaml").write_text(
        "model:\n  provider: anthropic\n  default: claude-opus-4-8\n"
        "display:\n  skin: numbers\n"
        "mcp_servers:\n  angel:\n    command: x\n", encoding="utf-8")

    appdata = tmp_path / "appdata"
    hermes = appdata / "hermes"
    (hermes).mkdir(parents=True)
    (hermes / "auth.json").write_text(json.dumps(
        {"version": 1, "providers": {}, "credential_pool": {"anthropic": [{"k": 2}], "deepseek": [{"k": 3}]}}),
        encoding="utf-8")
    # skills: one hermes-* (renamed on import), one plain, one that dupes a Numbers skill.
    for slug, cat in [("hermes-agent", "autonomous-ai-agents"),
                      ("my-tool", "software-development"),
                      ("dup-skill", "misc")]:
        d = hermes / "skills" / cat / slug
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: Use Hermes for {slug}. Run `hermes setup`.\n---\n# {slug}\n",
            encoding="utf-8")
    # Numbers already has dup-skill → must be skipped on import.
    dd = numbers / "skills" / "misc" / "dup-skill"
    dd.mkdir(parents=True)
    (dd / "SKILL.md").write_text("---\nname: dup-skill\n---\n# keep me\n", encoding="utf-8")
    # memory + config extras
    (hermes / "memories").mkdir()
    (hermes / "memories" / "MEMORY.md").write_text("remember this\n", encoding="utf-8")
    (hermes / "config.yaml").write_text(
        "model:\n  provider: deepseek\n  default: deepseek/chat\n"
        "display:\n  skin: default\n"
        "quick_commands:\n  gm: {type: alias, target: model}\n", encoding="utf-8")

    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    monkeypatch.setenv("NUMBERS_HOME", str(numbers))
    monkeypatch.setenv("HERMES_HOME", str(numbers))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "nohome"))
    return {"numbers": numbers, "hermes": hermes}


def test_available_categories(rich):
    src = ih._candidate_hermes_homes(rich["numbers"])
    avail = ih.available_categories(rich["numbers"], src)
    assert {"providers", "skills", "memory", "config"} <= set(avail)


def test_skills_import_renames_and_dedupes(rich):
    ih.run_import(print_fn=lambda *a, **k: None, ask=False, selection=["skills"])
    sk = rich["numbers"] / "skills"
    # hermes-agent renamed to numbers-agent (dir + frontmatter), Hermes prose gone.
    md = (sk / "autonomous-ai-agents" / "numbers-agent" / "SKILL.md")
    assert md.exists()
    body = md.read_text(encoding="utf-8")
    assert "name: numbers-agent" in body and "Hermes" not in body and "numbers setup" in body
    assert not (sk / "autonomous-ai-agents" / "hermes-agent").exists()
    # plain skill imported; duplicate skipped (Numbers' own copy kept).
    assert (sk / "software-development" / "my-tool" / "SKILL.md").exists()
    assert "keep me" in (sk / "misc" / "dup-skill" / "SKILL.md").read_text(encoding="utf-8")


def test_config_import_preserves_branding(rich):
    ih.run_import(print_fn=lambda *a, **k: None, ask=False, selection=["config"])
    import yaml
    cfg = yaml.safe_load((rich["numbers"] / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["display"]["skin"] == "numbers"          # brand key untouched
    assert cfg["model"]["default"] == "claude-opus-4-8"  # model untouched
    assert "angel" in cfg["mcp_servers"]                 # angel MCP untouched
    assert "quick_commands" in cfg                        # non-brand key imported


def test_tools_import_merges_mcp_servers_without_dropping_angel(rich):
    """The `tools` category carries the user's MCP servers across.

    mcp_servers is a Numbers-owned key, so the `config` category skips it
    wholesale -- otherwise importing config would replace the map and take
    Numbers' own `angel` server with it. `tools` merges name-by-name instead.
    """
    import yaml

    hermes_cfg = rich["hermes"] / "config.yaml"
    hermes_cfg.write_text(
        hermes_cfg.read_text(encoding="utf-8")
        + "mcp_servers:\n  mytool:\n    command: foo\n  angel:\n    command: IMPOSTOR\n",
        encoding="utf-8")

    ih.run_import(print_fn=lambda *a, **k: None, ask=False, selection=["tools"])

    cfg = yaml.safe_load((rich["numbers"] / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["mcp_servers"]["mytool"] == {"command": "foo"}
    # Existing entries win: a Hermes home cannot redefine Numbers' own server.
    assert cfg["mcp_servers"]["angel"] == {"command": "x"}


def test_tools_is_offered_when_the_only_content_is_mcp_servers(rich):
    """A Hermes home with no plugins/ dir still has tools worth importing."""
    hermes_cfg = rich["hermes"] / "config.yaml"
    hermes_cfg.write_text(
        hermes_cfg.read_text(encoding="utf-8") + "mcp_servers:\n  mytool:\n    command: foo\n",
        encoding="utf-8")
    assert not (rich["hermes"] / "plugins").exists()

    src = ih._candidate_hermes_homes(rich["numbers"])
    assert "tools" in ih.available_categories(rich["numbers"], src)


def test_selection_limits_scope_and_readonly(rich):
    before = (rich["hermes"] / "skills" / "autonomous-ai-agents" / "hermes-agent" / "SKILL.md").read_text(encoding="utf-8")
    ih.run_import(print_fn=lambda *a, **k: None, ask=False, selection=["memory"])
    # only memory ran: MEMORY.md copied, providers NOT imported (deepseek absent).
    assert (rich["numbers"] / "memories" / "MEMORY.md").exists()
    auth = json.loads((rich["numbers"] / "auth.json").read_text(encoding="utf-8"))
    assert "deepseek" not in auth["credential_pool"]
    # Hermes side untouched (read-only) + Numbers marker intact.
    assert (rich["hermes"] / "skills" / "autonomous-ai-agents" / "hermes-agent" / "SKILL.md").read_text(encoding="utf-8") == before
    assert (rich["numbers"] / home.MARKER_NAME).exists()


# --------------------------------------------------------------------------
# Categories import WHOLE. list_items survives only to describe a menu line
# ("Skills  pdf, ocr (+3 more)"), never to select with. Past chat sessions stay
# out of scope entirely.
# --------------------------------------------------------------------------

def test_list_items_enumerates_individual_skills(rich):
    src = ih._candidate_hermes_homes(rich["numbers"])
    ids = [i for i, _label in ih.list_items("skills", rich["numbers"], rich["hermes"], src)]
    # renamed on import, and the one Numbers already has is not on offer
    assert "numbers-agent" in ids and "my-tool" in ids and "dup-skill" not in ids


def test_choosing_a_category_imports_all_of_it(rich):
    """"Skills -> import all": there is no way to land a subset."""
    ih.run_import(print_fn=lambda *a, **k: None, ask=False, selection=["skills"])
    sk = rich["numbers"] / "skills"
    assert (sk / "software-development" / "my-tool" / "SKILL.md").exists()
    assert (sk / "autonomous-ai-agents" / "numbers-agent" / "SKILL.md").exists()


def test_choosing_providers_imports_every_provider(rich):
    ih.run_import(print_fn=lambda *a, **k: None, ask=False, selection=["providers"])
    auth = json.loads((rich["numbers"] / "auth.json").read_text(encoding="utf-8"))
    for name in ih.read_hermes_providers(rich["hermes"])["credential_pool"]:
        assert name in auth["credential_pool"]
    # Numbers' own credential is never clobbered by the import.
    assert auth["credential_pool"]["anthropic"] == [{"k": 1}]


def test_no_per_item_selection_survives_anywhere():
    """The item layer is gone from the module, not just from the prompt --
    it was where the earlier import attempts went wrong."""
    for gone in ("_parse_item_picks", "_parse_items", "parse_spec",
                 "_split_clauses", "_wanted"):
        assert not hasattr(ih, gone), f"{gone} still exists"
    import inspect

    assert "items" not in inspect.signature(ih.run_import).parameters
    assert not hasattr(ih.Spec(), "items")


def test_cli_list_prints_whole_categories(rich, capsys):
    """--list replaces --list-items: what you can ask for is a category."""
    assert ih.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "Skills" in out
    # It describes the contents without offering to pick among them.
    assert "my-tool" in out
    assert "[" not in out  # no item-index syntax on screen


def test_cli_offers_no_item_level_flags(capsys):
    """--items / --spec / --list-items are not accepted any more."""
    for bad in (["--items", "skills:a"], ["--spec", "1[1]"],
                ["--list-items", "skills"]):
        with pytest.raises(SystemExit) as exc:
            ih.main(bad)
        assert exc.value.code != 0


def test_no_item_catalog_ever_lists_chat_sessions(rich):
    src = ih._candidate_hermes_homes(rich["numbers"])
    for key in ih._CAT_ORDER:
        ids = [i for i, _l in ih.list_items(key, rich["numbers"], rich["hermes"], src)]
        assert not (set(ids) & ih._HISTORY_NAMES)


def test_all_and_none_are_explicit_choices():
    avail = ["providers", "skills", "env"]          # env is the advanced one
    sel = lambda answer: ih._prompt_selection(avail, lambda *a, **k: None,
                                              lambda _t: answer)
    # 'all' and a bare Enter both take everything on the menu.
    for answer in ("a", "all", "ALL", " all ", ""):
        assert sel(answer).categories == avail
        assert not sel(answer).cancelled
    # 'none' stops: nothing imported, and reported as a decision, not a miss.
    for answer in ("n", "none", "no", "skip", "cancel"):
        assert sel(answer).cancelled
        assert sel(answer).categories == []


def test_menu_shows_what_each_category_will_bring_in():
    avail = ["providers", "skills", "memory", "profiles", "tasks"]
    lines = []
    prompts = []

    def prompt_fn(text):
        prompts.append(text)
        return ""

    ih._prompt_selection(avail, lines.append, prompt_fn)
    rendered = "\n".join(lines)
    assert "pick what to bring across" in rendered
    assert "1. Providers" in rendered and "4. Profiles" in rendered


def test_menu_spells_out_the_answer_format():
    """The format was left to be guessed from a hint inside the prompt."""
    avail = ["providers", "skills", "memory", "profiles", "tasks"]
    lines = []
    ih._prompt_selection(avail, lines.append, lambda _t: "")
    rendered = "\n".join(lines)
    assert "separated by commas" in rendered
    assert "1,2,5" in rendered              # a worked example, valid for this menu
    assert "all" in rendered and "none" in rendered


def test_three_unreadable_answers_cancel_rather_than_default():
    """Guessing on the user's behalf is how an unattended terminal imports
    a category it was never shown."""
    spec = ih._prompt_selection(["providers", "skills"],
                                lambda *a, **k: None, lambda _t: "wibble")
    assert spec.cancelled and spec.categories == []


def test_slash_command_reruns_after_the_offer_was_declined(homes):
    """The point of /import-hermes: the one-shot guard must not lock you out."""
    ih.offer_import(print_fn=lambda *a, **k: None, prompt_fn=lambda _t: "n")
    guard = homes["numbers"] / ih.GUARD_NAME
    assert guard.exists()
    # offer_import is now a no-op, but the command still opens the selector.
    out = []
    ih.run_import_command(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                          prompt_fn=lambda _t: "1")
    assert any("pick what to bring across" in line for line in out)


def test_slash_command_writes_the_guard(homes):
    """Running it by hand also answers the first-run offer, so it stops asking."""
    ih.run_import_command(print_fn=lambda *a, **k: None, prompt_fn=lambda _t: "n")
    assert (homes["numbers"] / ih.GUARD_NAME).exists()


# --------------------------------------------------------------------------
# One menu, one answer: "everything, minus the numbers you type".
#
# The old flow asked twice (categories, then a nested per-category item menu),
# and that second question is where the earlier import attempts went wrong: the
# item numbers did not exist on screen until after the category answer, and a
# category with fewer than two items was imported wholesale with no prompt at
# all. Categories are now all-or-nothing, so one answer covers it -- and an
# unparseable one is reported rather than silently importing less than asked.
# --------------------------------------------------------------------------

def _catalog_fixture():
    entries = {
        "providers": [("deepseek", "deepseek"), ("gemini", "gemini"), ("nous", "nous")],
        "tasks": [("kanban.db", "kanban.db"), ("projects.db", "projects.db")],
    }
    return lambda key: entries.get(key, [])


def test_menu_lists_item_names_inline():
    avail = ["providers", "tasks"]
    lines = []
    ih._prompt_selection(avail, lines.append, lambda _t: "", _catalog_fixture())
    rendered = "\n".join(lines)
    # Real item names are shown so a bare Enter is an informed choice, not a
    # leap of faith over a meaningless "(all)".
    assert "deepseek" in rendered and "gemini" in rendered and "nous" in rendered
    assert "kanban.db" in rendered and "projects.db" in rendered


def test_menu_reasks_on_an_unparseable_answer():
    avail = ["providers", "tasks"]
    answers = iter(["wibble", "1"])
    lines = []
    spec = ih._prompt_selection(avail, lines.append, lambda _t: next(answers),
                                _catalog_fixture())
    # "1" on the second try imports line 1.
    assert spec.categories == ["providers"]
    assert any("not a category number" in ln for ln in lines)


def test_menu_out_of_range_reasks():
    avail = ["providers", "tasks"]
    answers = iter(["9", "2"])
    lines = []
    spec = ih._prompt_selection(avail, lines.append, lambda _t: next(answers),
                                _catalog_fixture())
    assert spec.categories == ["tasks"]
    assert any("is not one of the categories" in ln for ln in lines)


def test_the_typed_answer_reaches_the_importer(rich):
    """End-to-end: line 2 of the menu is the category that gets imported."""
    # avail == ["providers", "skills", "memory", "config"]; "2" picks skills.
    ih.run_import(print_fn=lambda *a, **k: None, prompt_fn=lambda _t: "2", ask=True)
    auth = json.loads((rich["numbers"] / "auth.json").read_text(encoding="utf-8"))
    assert "deepseek" not in auth["credential_pool"]   # providers NOT imported
    assert (rich["numbers"] / "skills" / "software-development" / "my-tool").exists()


def test_cancelling_the_menu_copies_nothing(rich):
    out = []
    rc = ih.run_import(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                       prompt_fn=lambda _t: "none", ask=True)
    assert rc == 0
    assert any("cancelled" in line.lower() for line in out)
    auth = json.loads((rich["numbers"] / "auth.json").read_text(encoding="utf-8"))
    assert "deepseek" not in auth["credential_pool"]
    assert not (rich["numbers"] / "skills" / "software-development").exists()


def test_one_failing_category_does_not_end_the_import(rich, monkeypatch):
    """A single unguarded loop meant the first failure ended the run wherever
    it happened to be -- and offer_import() then swallowed the exception, so
    the user saw one category copied, no error, and no summary."""
    real = ih._run_category

    def explode(key, nh, home, src):
        if key == "skills":
            raise OSError("disk went away")
        return real(key, nh, home, src)

    monkeypatch.setattr(ih, "_run_category", explode)
    out = []
    rc = ih.run_import(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                       ask=False, selection=["providers", "skills", "memory"])
    rendered = "\n".join(out)
    assert rc == 0
    assert "SKIPPED skills: disk went away" in rendered
    # The categories on either side of the failure still ran.
    auth = json.loads((rich["numbers"] / "auth.json").read_text(encoding="utf-8"))
    assert "deepseek" in auth["credential_pool"]
    assert (rich["numbers"] / "memories").exists()


def test_import_context_matches_the_typed_menu(rich):
    """The TUI picker builds itself from this; it must agree with the menu."""
    ctx = ih.import_context()
    nh, src = ctx["numbers_home"], ctx["sources"]
    assert ctx["available"] == ih.available_categories(nh, src)
    assert [key for key, _label, _detail in ctx["entries"]] == ctx["available"]
    labels = {key: label for key, label, _d in ctx["entries"]}
    assert labels["providers"] == "Providers"


def test_offer_reports_a_failure_instead_of_swallowing_it(homes, monkeypatch):
    def explode(*a, **k):
        raise OSError("hermes home vanished mid-copy")

    monkeypatch.setattr(ih, "run_import", explode)
    out = []
    ih.offer_import(print_fn=lambda *a, **k: out.append(" ".join(map(str, a))),
                    prompt_fn=lambda _t: "")
    rendered = "\n".join(out)
    assert "hermes home vanished mid-copy" in rendered
    # A failed offer must not burn the one-shot guard.
    assert not (homes["numbers"] / ih.GUARD_NAME).exists()
