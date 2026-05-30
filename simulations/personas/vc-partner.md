# Persona: vc-partner

## Identity

General Partner at an early-stage VC fund. Mid-tier, ~$200M AUM. Focused on B2B SaaS at seed to Series A. 6 years at this fund, 12 years in venture total. Conversational style is direct, evidence-driven, dislikes vagueness. Uses metric-first language ("ARR," "burn," "logo retention") and references prior companies by short code names.

## Day-to-day pattern

- 2-3 sourcing calls/week (cold + warm intros)
- 5-8 portfolio check-ins/week (founders, board meetings)
- Weekly internal IC meeting
- Diligence on 1-2 prospects in parallel at any time
- Watches portfolio metrics, occasionally writes follow-on checks
- Quarterly LP update

## Facts the persona has

### Portfolio companies

| Code | Stage | ARR | Status | Notes |
|---|---|---|---|---|
| Acme Analytics | Series A | $4M | Growing 12% MoM | Just raised B; we did $250K follow-on |
| BetaForge | Seed | $0 (pre-rev) | ML platform for biotech | Missed Q2 milestone by 2 weeks |
| Gamma Cloud | Series A | $8M | Just hired VP Sales from Datadog | Strong execution, likely B in 6mo |
| Delta Health | Seed | $300K | Regulatory headwinds in Q3 | CEO transition under discussion |

### Active prospects

| Code | Stage | Status | Note |
|---|---|---|---|
| Epsilon | Pre-seed | IC approved waiting on terms | Founders ex-Stripe, legal-tech, $400K ARR |
| Zeta Insights | Seed | In diligence | AI/observability, pre-revenue but strong demo |
| Eta Bio | — | PASSED | Outside thesis (bio-AI too long-cycle) |

### Network

- **Mentors / operating advisors**:
  - Sarah Chen (ex-Twilio, SDR/sales playbooks)
  - Mike Park (ex-Datadog, GTM)
  - Priya Patel (ex-Box, enterprise sales)
- **Co-investors**: SignalFire (lead on Acme A), Founders Fund (co-led Gamma)
- **LPs**: a few endowments, Mercer Family Office (single largest), couple of HNW individuals

### Recent decisions

- PASSED on Eta Bio (outside thesis)
- Wrote $250K follow-on for Acme at recent Series B
- IC approved Epsilon seed pending term sheet
- Delta Health: open question on CEO transition — currently informal, not on books

### Recent events

- BetaForge missed Q2 milestone (beta slipped 2 weeks)
- Delta Health board meeting last week — CEO transition discussed informally
- Demo Day prep for current cohort kicked off
- Mercer Family Office annual meeting next month — need to prep portfolio summary

## Working-session topics the persona realistically discusses

These are the kinds of conversations the persona has with Claude. Each is a few-paragraph topic the simulator can elaborate into a multi-turn transcript.

1. **Sourcing call writeup**: just got off a call with a new prospect, wants to dump notes and decide whether to bring it to IC
2. **Portfolio review**: walks through 2-3 companies' latest metrics, identifies concerns, drafts asks
3. **Founder check-in summary**: just had office hours with a founder, captures what was discussed and any commitments
4. **IC meeting prep**: pre-meeting, wants to organize the case for/against a prospect
5. **LP update drafting**: quarterly update prep, pulling together portfolio status
6. **Mentor matching**: thinking through which mentor to introduce to which founder for a specific issue
7. **Pass writeup**: deciding to pass on a deal and articulating why for the firm record

## Expected entity types (ground truth for evaluation)

Vault-keeper, after processing transcripts for this persona, should produce notes that fit naturally into these entity types:

- `Company` — every portfolio company + active prospect
- `Founder` — people running companies
- `Deal` — investment terms and follow-on rounds
- `Update` — periodic founder updates (KPIs, runway, asks, blockers)
- `Decision` — IC votes (invest / pass / follow-on / defer)
- `Intro` — mentor-founder, investor-founder, customer-founder connections
- `Milestone` — company achievements or misses
- `Mentor` — operating advisors and their domains
- `LP` — limited partners and their reporting cadence

Cross-references (FKs) should resolve: a `Decision` references the `Company`; an `Update` references the `Company` and any mentioned `Milestone`; an `Intro` references both `Mentor` and `Founder`.

## Expected questions (phase D evaluation)

After vault-keeper has populated the vault, vault-query should be able to answer questions a real persona would ask. These exercise four properties:

1. **Direct lookup** — the answer is in one entry
2. **FK traversal** — the answer requires following references between entries
3. **Aggregation** — the answer summarizes across multiple entries of the same type
4. **Empty result** — the answer is "no entry found" because the vault genuinely doesn't have it (don't hallucinate from training)

The standard query set for vc-partner:

1. **Direct**: "What's the status of Procure.ai (Iota)?"
   - Expect: Pre-IC seed prospect, $180K ARR, pending references, action: ping Precursor by EOW
2. **FK**: "Who are the founders of Procure.ai?"
   - Expect: Raj Menon and Clara Sutherland, with their backgrounds and the co-founder dynamic concern flagged
3. **FK + composition**: "Which mentor is queued for Procure.ai?"
   - Expect: Mike Park (ex-Datadog GTM), hold until post-reference
4. **Aggregation**: "Which deals are pending references?"
   - Expect: Procure.ai Seed (only deal in this run)
5. **Empty result**: "How is BetaForge doing?"
   - Expect: "no entry found for BetaForge" — vault was populated from one transcript that never mentioned BetaForge, even though it's in the persona's facts

## Evaluation criteria for phase 0

Eyeball:
- Does the generated schema cover the expected entity types?
- Does the synthetic transcript read like something this persona would actually say?
- After vault-keeper processes the transcript (phase 2), do the right entities get written?
- After vault-query (phase 3), can a question like "what's BetaForge's status?" be answered correctly?

Phase 0 doesn't score automatically — just produces artifacts for human review.
