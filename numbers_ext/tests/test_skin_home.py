"""Branding must survive `numbers -p <profile>`.

The profile override re-points HERMES_HOME at profiles/<name> before any
hermes module loads. That home has neither the skin NAME (its config.yaml has
no `display` key -- the importer treats `display` as Numbers-owned and never
copies it) nor the skin FILE (skins/ is empty; the installer only writes the
install root). Either gap alone renders the whole CLI as stock Hermes.

NUMBERS_HOME is not re-pointed -- upstream does not know the variable -- so it
still names the install root, and both lookups fall back to it.
"""
import json

import pytest

from numbers_ext import skin_home
from numbers_ext.home import MARKER_NAME


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A Numbers install root with the `numbers` skin, plus a profile home."""
    root = tmp_path / "numbers"
    (root / "skins").mkdir(parents=True)
    (root / MARKER_NAME).write_text(json.dumps({"product": "numbers"}), encoding="utf-8")
    (root / "config.yaml").write_text("display:\n  skin: numbers\n", encoding="utf-8")
    (root / "skins" / "numbers.yaml").write_text(
        "name: numbers\nbranding:\n  agent_name: NUMBERS 21:4-9\n", encoding="utf-8")

    profile = root / "profiles" / "elirom"
    (profile / "skins").mkdir(parents=True)          # present but empty, as imported
    (profile / "config.yaml").write_text("model:\n  provider: deepseek\n", encoding="utf-8")

    monkeypatch.setenv("NUMBERS_HOME", str(root))
    monkeypatch.setenv("HERMES_HOME", str(root))     # the default home, not a profile
    return root, profile


def _enter_profile(monkeypatch, profile):
    """What hermes_cli.profiles.resolve_profile_env does for `-p elirom`."""
    monkeypatch.setenv("HERMES_HOME", str(profile))


def test_off_a_profile_nothing_is_inherited(install):
    """The default home already has its own skin -- inheriting would be noise."""
    assert skin_home.in_profile() is False
    assert skin_home.fallback_skins_dir() is None
    assert skin_home.fallback_skin_name() is None


def test_inside_a_profile_the_install_root_is_found(install, monkeypatch):
    root, profile = install
    _enter_profile(monkeypatch, profile)
    assert skin_home.in_profile() is True
    assert skin_home.install_root() == root
    assert skin_home.fallback_skins_dir() == root / "skins"
    assert skin_home.fallback_skin_name() == "numbers"


def test_a_home_without_the_marker_is_not_an_install(install, monkeypatch):
    """Never inherit branding from a directory that is not a Numbers install."""
    root, profile = install
    (root / MARKER_NAME).unlink()
    _enter_profile(monkeypatch, profile)
    assert skin_home.install_root() is None
    assert skin_home.fallback_skin_name() is None


def test_a_default_skin_is_not_worth_inheriting(install, monkeypatch):
    root, profile = install
    (root / "config.yaml").write_text("display:\n  skin: default\n", encoding="utf-8")
    _enter_profile(monkeypatch, profile)
    assert skin_home.fallback_skin_name() is None


def test_unset_numbers_home_degrades_quietly(install, monkeypatch):
    """A stock Hermes install has no NUMBERS_HOME and must be untouched."""
    _root, profile = install
    monkeypatch.delenv("NUMBERS_HOME")
    _enter_profile(monkeypatch, profile)
    assert skin_home.install_root() is None
    assert skin_home.in_profile() is False
    assert skin_home.fallback_skins_dir() is None


# --- the two skin_engine hooks, against the real functions -----------------

def test_profile_inherits_the_skin_name(install, monkeypatch):
    """init_skin_from_config: a profile config with no display key."""
    from hermes_cli import skin_engine

    _root, profile = install
    _enter_profile(monkeypatch, profile)
    try:
        skin_engine.init_skin_from_config({"model": {"provider": "deepseek"}})
        assert skin_engine.get_active_skin_name() == "numbers"
    finally:
        skin_engine.set_active_skin("default")


def test_profile_loads_the_skin_file_from_the_install_root(install, monkeypatch):
    """load_skin: profiles/<name>/skins is empty, the root's is not."""
    from hermes_cli import skin_engine

    _root, profile = install
    _enter_profile(monkeypatch, profile)
    skin = skin_engine.load_skin("numbers")
    assert skin.name == "numbers"


def test_an_explicit_profile_skin_still_wins(install, monkeypatch):
    """Inheriting is a fallback, never an override of a stated choice."""
    from hermes_cli import skin_engine

    _root, profile = install
    _enter_profile(monkeypatch, profile)
    try:
        skin_engine.init_skin_from_config({"display": {"skin": "nous-blue"}})
        assert skin_engine.get_active_skin_name() == "nous-blue"
    finally:
        skin_engine.set_active_skin("default")
