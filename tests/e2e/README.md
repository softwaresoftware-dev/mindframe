# Mindframe — end-to-end test suite

Two layers. The **hermetic** layer runs anywhere with no daemons and no
credentials (CI-safe). The **live** layer drives the running bundle
daemons and is for manual pre-demo verification.

```
make test        # all hermetic tests (incl. this suite) — CI
make test-e2e    # just the hermetic e2e suite
make e2e-live    # the live layer — needs a running install
```

## Hermetic layer (`tests/e2e/*.py`)

| File | What it pins |
|---|---|
| `test_recipe_contract.py` | Every `spawn:` route's `brief:` block fills its recipe's required `{{placeholders}}`. The regression guard for the static-spawn brief-composition bug — a route that spawns an agent with an unfilled `{{output_path}}`. |
| `test_install_contract.py` | Every capability the mindframe bundle `requires` — directly and transitively — has a provider in the marketplace registry. Catches "agent-spawning: no provider available" on a fresh install. |
| `test_vault_fixture.py` | The demo knowledge vault's frontmatter contract, foreign keys (runbook→service, incident→service), and the grep contract the triage skills depend on. |

`recipe_contract.py` is the shared checker behind the first table row. It
is also a standalone CLI used by the live layer:

```
python3 recipe_contract.py <channels.yaml> <recipes_dir>
```

### Fixtures (`fixtures/`)

- `recipes/good-reader/` — a well-formed recipe (every placeholder declared).
- `recipes/typo-reader/` — a recipe whose `brief.json` has a placeholder typo.
- `channels-good.yaml` — a route that satisfies the contract.
- `channels-bad.yaml` — routes that omit a required key / supply an unknown key.
- `channels-typo.yaml` — routes the typo recipe.

## Live layer (`tests/e2e/live/`)

Run against a real install. **Not CI** — these talk to running services
and wait on a real agent run (~1–2 min).

| Script | What it does |
|---|---|
| `healthcheck.sh` | Confirms each bundle daemon is up: dispatcher-ingress (`/api/health`), session-bridge, the `dispatcher` and `dispatcher-ingress` systemd units, and `tmux`. |
| `smoke.sh` | Full event path: checks the live `channels.yaml`/recipes against the contract, POSTs a `test-stream/calendar-check` event, waits for taskpilot to spawn the recipe agent, and asserts the agent wrote its artifact to the composed `output_path`. |

### Env vars

| Var | Default | Used by |
|---|---|---|
| `DISPATCHER_INGRESS_URL` | `http://127.0.0.1:8911` | both |
| `SESSION_BRIDGE_URL` | `http://127.0.0.1:8910` | healthcheck |
| `DISPATCHER_DIR` | `~/.dispatcher` | smoke (channels.yaml + recipes/) |
| `DISPATCHER_INGEST_TOKEN` | auto-read from the `dispatcher-ingress` systemd unit | smoke |
| `SMOKE_TIMEOUT_SEC` | `300` | smoke |
| `DISPATCHER_UNIT` / `INGRESS_UNIT` | `dispatcher` / `dispatcher-ingress` | both |
| `MINDFRAME_MARKETPLACE_JSON` | sibling-repo path | `test_install_contract.py` |

## What this suite does not cover

- **`/mindframe:setup`** is a Claude-driven wizard, not unit-testable. The
  install contract test covers the dependency graph it resolves; the live
  smoke test covers the event path it wires up.
- **The triage wedges end-to-end** (`sentry-triage`, `k8s-triage`) need
  live error sources and a cluster. `test_vault_fixture.py` pins the vault
  inputs they read; full wedge runs remain manual.
