"""Hermetic test of the mindframe bundle's install contract.

Parses the softwaresoftware marketplace registry and verifies that every
capability the mindframe bundle requires — directly and transitively —
is satisfied by at least one provider. This catches the class of failure
where a fresh install reports "agent-spawning: no provider available"
because a capability was added to `requires` with nothing to `provide` it.

No resolver import and no environment probes — this is the structural
check (does a provider exist at all), not the per-machine resolution.

If marketplace.json can't be located (e.g. the mindframe repo is checked
out in isolation), the tests skip with a clear reason rather than fail.
"""

import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_MARKETPLACE_CANDIDATES = [
    os.environ.get("MINDFRAME_MARKETPLACE_JSON", ""),
    os.path.join(
        ROOT, "..", "..", "marketplace", "softwaresoftware-marketplace",
        ".claude-plugin", "marketplace.json",
    ),
]


def _find_marketplace():
    for cand in _MARKETPLACE_CANDIDATES:
        if cand and os.path.isfile(cand):
            return os.path.abspath(cand)
    return None


@pytest.fixture(scope="module")
def plugins():
    path = _find_marketplace()
    if not path:
        pytest.skip(
            "marketplace.json not found — set MINDFRAME_MARKETPLACE_JSON to run "
            "the install-contract test against the registry"
        )
    with open(path) as f:
        data = json.load(f)
    entries = data["plugins"] if isinstance(data, dict) and "plugins" in data else data
    return {p["name"]: p for p in entries if isinstance(p, dict) and "name" in p}


def _provider_map(plugins):
    """capability -> [plugin names that provide it]."""
    out = {}
    for p in plugins.values():
        for cap in p.get("provides") or []:
            out.setdefault(cap, []).append(p["name"])
    return out


def _unsatisfied(plugins, root):
    """BFS the requires-graph from `root`; return [(plugin, capability)] gaps.

    A capability is satisfied if some plugin `provides` it, or if the
    requiring plugin lists it in its own `built_in_capabilities`. Providers
    of a satisfied capability are followed so transitive needs are checked.
    """
    provider_map = _provider_map(plugins)
    seen_plugins = set()
    seen_caps = set()
    gaps = []
    frontier = [root]
    while frontier:
        name = frontier.pop()
        if name in seen_plugins:
            continue
        seen_plugins.add(name)
        plugin = plugins.get(name)
        if not plugin:
            continue
        built_in = set(plugin.get("built_in_capabilities") or [])
        for cap in plugin.get("requires") or []:
            if cap in built_in:
                continue
            providers = provider_map.get(cap, [])
            if not providers:
                gaps.append((name, cap))
            if cap not in seen_caps:
                seen_caps.add(cap)
                frontier.extend(providers)
    return gaps


def test_mindframe_is_in_the_registry(plugins):
    assert "mindframe" in plugins, "mindframe has no marketplace.json entry"


def test_mindframe_direct_requires_each_have_a_provider(plugins):
    provider_map = _provider_map(plugins)
    mindframe = plugins["mindframe"]
    missing = [c for c in mindframe.get("requires") or [] if not provider_map.get(c)]
    assert not missing, f"mindframe requires capabilities with no provider: {missing}"


def test_mindframe_optional_capabilities_have_a_provider(plugins):
    """Optional caps should still be providable — a bundle option with no
    provider anywhere is dead config."""
    provider_map = _provider_map(plugins)
    mindframe = plugins["mindframe"]
    missing = [c for c in mindframe.get("optional") or [] if not provider_map.get(c)]
    assert not missing, f"mindframe optional capabilities with no provider: {missing}"


def test_transitive_closure_is_fully_satisfiable(plugins):
    """Every capability reachable from mindframe — through its providers'
    own requires — has a provider."""
    gaps = _unsatisfied(plugins, "mindframe")
    assert not gaps, "unsatisfiable capabilities in the bundle closure: " + ", ".join(
        f"{plugin} needs {cap}" for plugin, cap in gaps
    )


def test_bundle_providers_are_registered(plugins):
    """The seven bucket capabilities resolve to a concrete, registered plugin."""
    provider_map = _provider_map(plugins)
    for cap in plugins["mindframe"].get("requires") or []:
        providers = provider_map.get(cap, [])
        assert providers, f"{cap}: no provider"
        for name in providers:
            assert name in plugins, f"{cap} provider {name!r} is not a registry entry"
