"""Hermetic tests for the recipe-brief contract checker.

These exercise recipe_contract.check_channels against fixture recipes —
the regression guard for the static-spawn brief-composition bug, where a
`spawn:` route reached an agent without filling the recipe's brief
{{placeholders}}. No daemons, no network.
"""

import os

import recipe_contract

E2E = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(E2E, "fixtures")
RECIPES = os.path.join(FIX, "recipes")


def _check(channels_name):
    return recipe_contract.check_channels(os.path.join(FIX, channels_name), RECIPES)


def test_find_placeholders():
    found = recipe_contract.find_placeholders('{"a": "{{x}}", "b": "{{ y }}", "c": "lit"}')
    assert found == {"x", "y"}


def test_good_channels_has_no_violations():
    violations = _check("channels-good.yaml")
    assert violations == [], violations


def test_missing_required_brief_key_is_flagged():
    """A route that omits a required brief key — the exact static-spawn bug."""
    violations = _check("channels-bad.yaml")
    assert any("window" in v and "missing-window" in v for v in violations), violations


def test_unknown_brief_key_is_flagged():
    """A route that supplies a key the recipe schema doesn't declare."""
    violations = _check("channels-bad.yaml")
    assert any("bogus_key" in v for v in violations), violations


def test_recipe_placeholder_typo_is_flagged():
    """A brief.json {{placeholder}} not declared in brief_schema."""
    violations = _check("channels-typo.yaml")
    assert any("output_pth" in v for v in violations), violations


def test_optional_brief_key_may_be_omitted():
    """channels-good omits calendar_id (optional) — that must not be flagged."""
    violations = _check("channels-good.yaml")
    assert not any("calendar_id" in v for v in violations), violations


def test_missing_recipe_dir_is_flagged(tmp_path):
    ch = tmp_path / "channels.yaml"
    ch.write_text("routes:\n  - source: s\n    target: spawn:does-not-exist\n")
    violations = recipe_contract.check_channels(str(ch), RECIPES)
    assert any("not found" in v for v in violations), violations


def test_session_routes_are_ignored(tmp_path):
    """session: targets carry no recipe-brief contract."""
    ch = tmp_path / "channels.yaml"
    ch.write_text("routes:\n  - source: s\n    target: session:somewhere\n")
    assert recipe_contract.check_channels(str(ch), RECIPES) == []
