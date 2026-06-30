# Mindframe design system

One visual language for every shell surface — the calm home, the surface
shell (dock, ribbon, rail), the drawers, and app chrome. The single source of
truth is [`dashboard/public/tokens.css`](../dashboard/public/tokens.css);
`style.css` imports it and `surface.html` links it. If a color or radius
isn't a token, it's a bug waiting to drift.

**Scope:** the shell, plus an offered base for agent pages. Conversations and
agent frames build on [`frame.css`](../dashboard/public/frame.css) — tokens +
a minimal semantic base (.card, .pill, .label, .pending-action, buttons,
tables) linked same-origin, so pages inherit the language and follow token
changes without redeploys. APPS may keep their own theme (Plugboard is light
on purpose) — for artifacts, brand belongs to the artifact.

## Principles

1. **Calm.** Near-black, low-contrast chrome; the operator's content is the
   only loud thing on screen. Nothing animates unless it means "an agent is
   working right now."
2. **Two accents, two meanings.** Indigo is ACTION — anything that does
   something when clicked (buttons, focus, working pulses). Gold is
   IDENTITY — names, counts, section labels (INBOX, RESUME). Gold is never
   a button; indigo is never decoration.
3. **Three voices.** Mono (`JetBrains Mono`) is the system speaking: labels,
   timestamps, log lines, provenance. Serif (`Source Serif 4`) is prose a
   human reads. Grotesk (`Space Grotesk`) is headings and product names.
   If text is generated *about* the system, it's mono.
4. **State is a dot.** Liveness everywhere is the same 7px mark: hollow ring
   = asleep/unprobed · steady green = alive/connected · pulsing indigo =
   working · amber = caution (paused, needs-auth) · red = crashed/failed.
5. **Chrome earns its pixels.** Default chrome is minimal (the calm home is
   one input); management density lives behind drawers; app frames hide the
   shell entirely behind one pill.

## Tokens (see tokens.css for values)

| Group | Tokens | Use |
|---|---|---|
| Surfaces | `--color-bg`, `--color-bg-raised`, `--color-surface`, `--color-surface-2`, `--color-surface-hover` | page → topbar/iframe → cards/inputs → controls → hover wash |
| Lines | `--color-border`, `--color-border-faint`, `--color-border-strong` | card borders, separators, control borders |
| Ink | `--color-text`, `--color-text-soft`, `--color-text-dim`, `--color-text-muted`, `--color-text-faint` | brightest → faintest; faint is timestamps and hints |
| Action | `--color-accent`, `--color-accent-hover`, `--color-accent-soft`, `--color-accent-wash` | buttons, focus rings, working pulses, row-hover ink |
| Identity | `--color-gold` | section labels, counts, app/agent names |
| Status | `--color-success(-bright)`, `--color-warn`, `--color-err(-bright)`, `--color-code` | dots, pills, log tool-lines |
| Type | `--font-heading`, `--font-body`, `--font-mono`, `--font-ui` | the three voices + UI fallback |
| Rhythm | `--space-*`, `--radius-*`, `--transition*` | radius-round (999px) for pills and chips |

## Component inventory

| Component | Where | Shape |
|---|---|---|
| **Calm input** | home | full-width, `--color-surface`, `--radius-xl`, indigo focus glow |
| **Attention line** (`.calm-line`) | home | gold k-label · ink title · faint mono timestamp; hover wash |
| **App chip** (`.calm-app`) | home | `--radius-round` destination pill |
| **Dock row** (`.dk`) | surface shell | state dot · title (+ provenance sub-line in inbox) · hover ✕/✓ |
| **Section header** (`.dkhdr`, `.mgmt-sec`, `.sechdr`) | everywhere | uppercase mono, `.16em` tracking, faint |
| **Thinking ribbon chip** (`.rchip`) | surface shell | round chip; colored by voice (you/tool/think/text/warn) |
| **Management card** (`.mgmt-card`) | drawers | surface card: name + state pill, sub-line, lines, actions |
| **State pill** (`.pill-*`) | drawers | uppercase 10px round pill: live/paused/unwired/kind |
| **Mini button** (`.btn-mini`) | drawers | quiet bordered control; `.btn-go` indigo ink, `.btn-danger` red hover |
| **New-thing input** (`.mgmt-new`) | drawers | dashed border until focus — "type to create" |
| **Feed line** (`.feed-item`, `.ev`) | home/briefing | faint mono time + dim mono text; deliveries in accent-wash |
| **App pill bar** (`#appbar`) | app frames | floating round bar: gold app name · home · ⚙ maintain ↔ ← back to app |

## Interaction rules

- Hover reveals destructive/secondary controls (✕, ✓) — never visible at rest.
- Destructive = native `confirm()` with consequences spelled out; archive is
  never destructive and never navigates.
- Focus ring is the indigo border (no outline hacks); `Escape` always backs
  out of a mode (log, maintain, drawer).
- Anything that waits narrates: working edge glow, ribbon chips, "waking the
  agent (~20s)…" — silence is a bug.

## Voice & copy

- Lowercase for system chrome ("everything — frames · agents"), sentence
  case for content, UPPERCASE only for the tracked mono labels.
- No emoji in shell chrome (the ⚙/✕/✓/‹›/← glyph set is the whole budget).
- Counts speak plainly: "3 awake · 19 asleep", never raw enum values.
