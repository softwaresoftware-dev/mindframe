"""Validate dispatcher channels.yaml `spawn:` routes against recipe briefs.

A `spawn:<recipe>` route skips the LLM dispatcher, so the recipe's
brief.json {{placeholders}} are filled from the route's `brief:` block
instead. This module checks that contract holds end to end:

  - every {{placeholder}} in the recipe's brief.json is declared in the
    recipe's brief_schema (catches typos in the recipe)
  - every *required* placeholder used by brief.json is supplied by the
    route's `brief:` block (catches the static-spawn bug — a route that
    spawns an agent with an unfilled {{output_path}})
  - the route's `brief:` block has no keys outside the recipe schema

It is both an importable checker (used by the hermetic pytest suite) and
a CLI (used by the live smoke test against ~/.dispatcher).

    python recipe_contract.py <channels.yaml> <recipes_dir>

Exit 0 if the contract holds, 1 if any route violates it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def find_placeholders(text: str) -> set[str]:
    """Return the set of {{name}} tokens in a string."""
    return set(_PLACEHOLDER_RE.findall(text))


def _load_routes(channels_path: Path) -> tuple[list[dict], list[str]]:
    """Parse channels.yaml. Returns (routes, errors)."""
    try:
        raw = channels_path.read_text()
    except OSError as e:
        return [], [f"channels.yaml unreadable: {e}"]
    try:
        config = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        return [], [f"channels.yaml parse error: {e}"]
    routes = config.get("routes") or []
    if not isinstance(routes, list):
        return [], ["channels.yaml: 'routes' is not a list"]
    return [r for r in routes if isinstance(r, dict)], []


def check_route(route: dict, recipes_dir: Path) -> list[str]:
    """Return contract violations for a single channels.yaml route.

    Non-spawn routes (session: targets) and routes with no target are
    skipped — they carry no recipe-brief contract.
    """
    target = route.get("target") or ""
    label = f"route {route.get('source')!r}/{route.get('event_type') or '*'}"
    if not target.startswith("spawn:"):
        return []

    recipe_id = target.split(":", 1)[1]
    rdir = recipes_dir / recipe_id
    if not rdir.is_dir():
        return [f"{label}: recipe '{recipe_id}' not found at {rdir}"]

    recipe_yaml = rdir / "recipe.yaml"
    if not recipe_yaml.exists():
        return [f"{label}: recipe '{recipe_id}' has no recipe.yaml"]
    try:
        recipe = yaml.safe_load(recipe_yaml.read_text()) or {}
    except yaml.YAMLError as e:
        return [f"{label}: recipe '{recipe_id}' recipe.yaml parse error: {e}"]

    schema = recipe.get("brief_schema") or {}
    required = set(schema.get("required") or [])
    optional = set(schema.get("optional") or [])
    declared = required | optional

    brief_json = rdir / "brief.json"
    if not brief_json.exists():
        # No brief template — nothing to compose, nothing to check.
        return []
    brief_text = brief_json.read_text()
    try:
        json.loads(brief_text)
    except (json.JSONDecodeError, ValueError) as e:
        return [f"{label}: recipe '{recipe_id}' brief.json is not valid JSON: {e}"]

    placeholders = find_placeholders(brief_text)
    route_brief = route.get("brief") or {}
    if not isinstance(route_brief, dict):
        return [f"{label}: 'brief:' block is not a mapping"]

    violations: list[str] = []

    # Every placeholder the recipe uses must be declared in its schema.
    for ph in sorted(placeholders - declared):
        violations.append(
            f"{label}: recipe '{recipe_id}' brief.json uses {{{{{ph}}}}} "
            f"but '{ph}' is not in brief_schema (required or optional)"
        )

    # Every *required* placeholder actually used must be supplied by the route.
    for ph in sorted((placeholders & required) - set(route_brief)):
        violations.append(
            f"{label}: spawns '{recipe_id}' but the route's 'brief:' block "
            f"does not supply required key '{ph}' — the spawned agent would "
            f"receive a literal {{{{{ph}}}}}"
        )

    # The route must not supply keys the recipe doesn't know about.
    for key in sorted(set(route_brief) - declared):
        violations.append(
            f"{label}: 'brief:' block supplies '{key}', not in recipe "
            f"'{recipe_id}' brief_schema — likely a typo"
        )

    return violations


def check_channels(channels_path: Path, recipes_dir: Path) -> list[str]:
    """Return all recipe-brief contract violations across every route."""
    routes, errors = _load_routes(Path(channels_path))
    if errors:
        return errors
    violations: list[str] = []
    for route in routes:
        violations.extend(check_route(route, Path(recipes_dir)))
    return violations


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <channels.yaml> <recipes_dir>", file=sys.stderr)
        return 2
    channels_path = Path(argv[1]).expanduser()
    recipes_dir = Path(argv[2]).expanduser()
    violations = check_channels(channels_path, recipes_dir)
    if violations:
        print(f"FAIL — {len(violations)} recipe-brief contract violation(s):")
        for v in violations:
            print(f"  - {v}")
        return 1
    print(f"OK — all spawn routes in {channels_path} satisfy their recipe briefs")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
