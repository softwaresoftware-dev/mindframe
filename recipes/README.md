# Example recipes

Recipes shipped with the mindframe plugin. Copy them into `~/.dispatcher/recipes/` and add matching routes to `~/.dispatcher/channels.yaml` to use them.

## Installing

```bash
# from the mindframe plugin root
make install-recipes
```

Copies every recipe under this directory into `~/.dispatcher/recipes/<name>/`. Idempotent — overwrites if the destination exists. Won't touch `channels.yaml`; routes are still up to you.

Or manually:

```bash
cp -r recipes/mindframe-poc ~/.dispatcher/recipes/
```

## What's here

| Recipe | Purpose |
|---|---|
| `mindframe-poc` | End-to-end demo of the mindframe pipeline. Agent surveys local containers / systemd / nginx / Cloudflare / GitHub org and narrates progress via the mindframe MCP. Useful as the first thing you fire at a fresh install to confirm the wire works. |

## Adding a recipe

Each recipe directory needs three files:

- **`recipe.yaml`** — plugins, mcps, brief schema, optional `frame:` block (makes it a mindframe recipe), starter prompt.
- **`brief.json`** — operating brief (objectives / workflows / success_criteria / boundaries). Filled by the LLM dispatcher on the semantic path, or by the channels.yaml route's `brief:` block on the static path.
- **`CLAUDE.md`** — human-facing docs on what the recipe does and how to wire it. Not loaded by the spawned agent.

See `mindframe-poc/recipe.yaml` for the `frame:` opt-in shape — that's what makes a recipe spawn a mindframe instead of a plain task.
