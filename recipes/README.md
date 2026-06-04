# Example recipes

Recipes shipped with the mindframe plugin. Copy them into `~/.dispatcher/recipes/` and add matching routes to `~/.dispatcher/channels.yaml` to use them.

## Installing

```bash
# from the mindframe plugin root
make install-recipes
```

Copies every recipe under this directory into `~/.dispatcher/recipes/<name>/`. Idempotent — overwrites if the destination exists. Won't touch `channels.yaml`; routes are still up to you.

## What's here

No recipes ship in-tree right now. The block-stream demo (`mindframe-poc`) was
removed in the surface migration (2026-06-04). A surface-model example recipe —
which spawns an agent that owns one `index.html` it rewrites in place — will be
added when event→surface spawning is wired (a later migration step, in the
dispatcher repo). Until then `make install-recipes` is a no-op.

## Adding a recipe

Each recipe directory needs:

- **`recipe.yaml`** — plugins, mcps, brief schema, starter prompt.
- **`brief.json`** — operating brief (objectives / workflows / success_criteria / boundaries). Filled by the LLM dispatcher on the semantic path, or by the channels.yaml route's `brief:` block on the static path.
- **`CLAUDE.md`** — human-facing docs on what the recipe does and how to wire it. Not loaded by the spawned agent.
