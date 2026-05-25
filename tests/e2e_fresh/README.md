# Tier 3 — fresh-install dry-run (native)

Proves the bundle's **deterministic** install path works from scratch on a known-clean filesystem. The harness boots a fresh tmpdir as `$HOME`, clones mindframe + dispatcher into it, builds a Python venv, installs deps, then runs the full Tier 1 wire suite against the just-cloned tree.

Runs natively on Linux, macOS, and Windows — no Docker required.

## What's tested (deterministic)

| Phase | Tested |
|---|---|
| `git clone` both repos at the given refs | ✅ |
| `python -m venv` + `pip install` of dispatcher + dashboard + mindframe deps | ✅ |
| Generate dispatcher bearer at `$HOME/.mindframe/secrets/dispatcher-bearer.token` | ✅ (openssl when available, Python `secrets` fallback for Windows) |
| Materialize `$HOME/.dispatcher/channels.yaml` | ✅ |
| Dispatcher + dashboard subprocess spawn on OS-assigned ports | ✅ (via Tier 1 fixture) |
| Event → frame mint → blocks → SSE | ✅ (via Tier 1 tests) |
| `lib.frame.create_frame`, `mindframe-spawn` CLI, dashboard `POST /api/frames` | ✅ |

## What's NOT tested (requires Claude session)

| Phase | Why not |
|---|---|
| `claude plugin marketplace add` / `claude plugin install` | No claude binary in the harness |
| `/softwaresoftware:install mindframe` (resolver flow) | Requires interactive Claude session |
| `/mindframe:setup` wizard | Same |
| Phases 3–8 of install.txt (deployment config, env discovery, vault bootstrap, recipe authoring) | Conversational with the operator |

These need manual verification on a clean machine to declare the full install ready. The harness's job is to keep the *deterministic* layer honest while we work on the rest.

## Usage

```bash
# Default — runs against main, fresh tmpdir, report at /tmp/mf-tier3-report.json
./run.sh

# Pin a tag / branch
MINDFRAME_REF=v0.4.0 ./run.sh

# Keep the workspace for inspection
python3 harness.py --keep --workdir /tmp/mf-tier3-debug

# Windows (no bash)
python harness.py
```

The harness exits 0 on pass, 1 on fail. A structured per-phase JSON report is written to `--report` (default in the system tmp dir).

## CI

Runs in `.github/workflows/test.yml` as part of the matrix:

| Platform | Tier 1 (wire) | Tier 3 (fresh) |
|---|---|---|
| ubuntu-latest | ✅ | ✅ |
| macos-latest | ✅ | ✅ |
| windows-latest | ✅ | ⚠️ skipped — taskpilot's tmux dep means Windows can't run agents yet; the wire tests still prove dispatcher + dashboard + mindframe code is portable |

If Tier 3 starts failing while Tier 1 still passes on the same platform, the regression is in *install state* (paths, env, bearer location) rather than in the code itself.
