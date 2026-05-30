# mindframe simulations

End-to-end test harness for the mindframe knowledge-base pipeline. Each simulation runs a persona through the full workflow (schema creation → working session → vault writes → queries) and produces inspectable artifacts in a sandboxed run dir.

The point: we don't ship hand-crafted starter schemas. We ship a *system* that helps a user create their own, and we validate that system by running real personas through it.

## Phase status

| Phase | Status | What it does |
|-------|--------|--------------|
| A. schema-creation | ✓ phase 0 (one-shot) | Persona facts → `schema.yaml` |
| B. transcript-gen  | ✓ phase 0 | Persona + topic → synthetic working-session transcript |
| C. vault-keeper    | ✗ pending phase 2 | Run schema-aware vault-keeper against (schema, transcript) |
| D. vault-query     | ✗ pending phase 3 | Answer persona's expected questions against the produced vault |
| E. scored eval     | ✗ pending phase 5 | Entity recall, FK resolution, query accuracy as numeric scores |

## Run it

```bash
# Phase A + B (schema + transcript) for a persona
python3 run.py --persona vc-partner

# Schema only (skip transcript generation)
python3 run.py --persona vc-partner --skip-transcript

# Specify the topic explicitly (otherwise pulled from persona file)
python3 run.py --persona vc-partner --topic "IC meeting prep"
```

Artifacts land in `~/.mindframe-sim/<run-id>/`:

```
~/.mindframe-sim/20260530-150412-vc-partner-a3f8c1/
  meta.txt              # run metadata
  persona.md            # input persona, copied for provenance
  topic.txt             # working-session topic used in phase B
  phase-a-prompt.txt    # prompt sent to claude for schema creation
  phase-a-raw.txt       # raw model output
  schema.yaml           # parsed YAML output
  phase-b-prompt.txt    # prompt sent to claude for transcript gen
  phase-b-raw.txt       # raw model output
  transcript.txt        # parsed transcript
```

Every artifact is human-reviewable. Phase 0 evaluation is eyeball; we'll add scored evals once we have enough runs to calibrate against.

## Adding a persona

A persona file is a markdown document with structured sections. See `personas/vc-partner.md` for the template. Required sections:

- `## Identity` — who they are, voice, conversational style
- `## Day-to-day pattern` — what their week looks like
- `## Facts the persona has` — specific named entities, projects, people, events
- `## Working-session topics the persona realistically discusses` — numbered list
- `## Expected entity types` — ground truth for evaluation

The simulator improvises everything else from these facts.

## Why this exists

Hand-crafted schemas are a trap. We'd ship "templates for the 5 industries we thought of" and the 6th industry's user is stuck. Worse, we'd never validate that *any* user could actually use the schema-creation flow productively because we'd skip it ourselves.

Simulations force us to:
- Build the schema-creation system as a first-class deliverable, not an afterthought
- Test it against personas with messy, realistic data (not clean made-up examples)
- Measure that vault-keeper + vault-query actually work end-to-end against the schemas this system produces
- Regress on the whole pipeline when we change anything

The simulation framework also doubles as customer onboarding demos — you can show a prospect "here's mindframe being set up for someone in your role" without it being real customer data.

## Future personas to add

- `private-credit-underwriter` (Crestborne-style: deals, covenants, portfolio)
- `oilgas-asset-engineer` (Flywheel-style: wells, AFEs, JV, HSE)
- `accountant-solo-practice` (clients, engagements, filings, deadlines)
- `lawyer-litigation` (matters, parties, documents, calendar events)
- `solo-builder` (the simplest case — projects, decisions, status updates; what Thatcher's own use looks like)

The simulator works the same way for each — only the persona file differs.
