# Tier 3 — fresh-install dry-run

Proves that the bundle's **deterministic** install path works from scratch on a known-clean Linux box. Boots a minimal Ubuntu container with no claude binary, clones the mindframe + dispatcher repos, installs Python deps into a fresh venv, materializes `~/.dispatcher/` and `~/.mindframe/secrets/` state, then runs the full Tier 1 wire suite against the just-built install.

## What's tested (deterministic)

| Phase | Tested |
|---|---|
| `git clone` both repos | ✅ |
| `pip install` into a fresh venv | ✅ |
| Generate dispatcher bearer at `~/.mindframe/secrets/dispatcher-bearer.token` | ✅ |
| Materialize `~/.dispatcher/channels.yaml` | ✅ |
| Dispatcher + dashboard subprocess spawn on OS-assigned ports | ✅ (via Tier 1 fixture) |
| Event → frame mint → blocks → SSE | ✅ (via Tier 1 tests) |
| `lib.frame.create_frame`, `mindframe-spawn` CLI, `POST /api/frames` | ✅ |

## What's NOT tested (requires Claude session)

| Phase | Why not |
|---|---|
| `claude plugin marketplace add` / `claude plugin install` | No claude binary in the container |
| `/softwaresoftware:install mindframe` (resolver flow) | Requires interactive Claude session |
| `/mindframe:setup` wizard | Same |
| Phases 3–8 of install.txt (deployment config, env discovery, vault bootstrap, recipe authoring) | Conversational with the operator |

These are the manual checks needed to declare the full install flow ready:

1. Run `claude plugin marketplace add softwaresoftware-dev/softwaresoftware-plugins` on a clean machine.
2. Run `claude plugin install softwaresoftware@softwaresoftware-plugins`.
3. Open `claude`, run `/softwaresoftware:install mindframe`.
4. Verify the resolver picks the right providers for the host's environment.
5. Run `/mindframe:setup` and walk a deployment through phases 3–8.
6. Run `tier2` (real-agent smoke) against the resulting install.

If both Tier 3 (deterministic) AND the manual checks above pass, the install is real.

## Usage

```bash
# Build + run against main (default)
./run.sh

# Test a specific tag / branch
MINDFRAME_REF=v0.4.0 ./run.sh

# Custom report location
REPORT_HOST_PATH=/tmp/my-report.json ./run.sh
```

The container exits 0 on pass, 1 on fail. A structured report is written to `REPORT_HOST_PATH` (default `/tmp/mindframe-tier3-report.json`) with per-phase pass/fail.

## CI shape

This runs against `main` on every push or nightly. It catches regressions in:

- Plugin manifest shape (`.claude-plugin/plugin.json` validity)
- `lib/frame` imports and behavior against a known-empty `$HOME`
- Dispatcher + dashboard subprocess startup against a fresh recipe set
- The `frame:` recipe block path through dispatcher
- The bearer-file-handoff convention

If Tier 3 starts failing while Tier 1 still passes on the dev box, the regression is in *install state* (paths, env, bearer location, etc.) — not in the code itself.
