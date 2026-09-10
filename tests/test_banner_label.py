"""Banner version-label skin override (hermes-patches.md P5).

The startup panel title reads the skin's branding.agent_name (upstream fix)
and, when the skin supplies branding.agent_version, shows that instead of the
upstream engine stamp (v{VERSION} ({RELEASE_DATE})) — NUMBERS ships its own
product version and shows no Nous release branding.
"""
from hermes_cli import banner
from hermes_cli import skin_engine


class _FakeSkin:
    def __init__(self, name="Numbers", ver=""):
        self._b = {"agent_name": name, "agent_version": ver}

    def get_branding(self, key, default=None):
        return self._b.get(key, default)


def test_label_uses_skin_agent_version(monkeypatch):
    monkeypatch.setattr(skin_engine, "get_active_skin",
                        lambda: _FakeSkin("NUMBERS 21:4-9", "v1.0.0"))
    label = banner.format_banner_version_label()
    assert label.startswith("NUMBERS 21:4-9 v1.0.0")
    assert "(2026" not in label  # no upstream release-date stamp


def test_label_falls_back_to_upstream(monkeypatch):
    monkeypatch.setattr(skin_engine, "get_active_skin", lambda: None)
    label = banner.format_banner_version_label()
    assert label.startswith("Numbers v")


def test_label_default_skin_no_agent_version(monkeypatch):
    monkeypatch.setattr(skin_engine, "get_active_skin",
                        lambda: _FakeSkin("NUMBERS 21:4-9", ""))
    label = banner.format_banner_version_label()
    assert label.startswith("NUMBERS 21:4-9 v")
