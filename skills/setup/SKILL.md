---
name: setup
description: Onboard a new mindframe deployment for a customer. Walk through credentials per data system, validate connections live, bootstrap the customer-domain knowledge base from real source systems (GitHub, Sentry, GCP, PagerDuty, Slack), seed the wedge skill, and run an end-to-end smoke test. Use when asked to "set up mindframe", "onboard a customer", "install the bundle", or when starting a new mindframe deployment.
---

# Mindframe — Setup

You are the mindframe onboarding agent. The customer just installed the bundle. Your job is to walk them through one-time setup, end-to-end, dogfooding the rest of the bundle as you go.

This skill is a **placeholder** at v0.1.0. Full implementation depends on:

- KB schema landed (see `docs/kb-schema.md`) ✓
- Dispatcher spawn-on-demand mode (open thread #2 in vault note)
- Customer credentials story (open thread #6)
- Adopt-first MCPs assessed (open thread #3)

When implementing, follow the bootstrap order from the vault note:

1. KB schema (paper) — done
2. /sentry-triage skill against fake KB
3. /mindframe:setup wizard (this skill)
4. Trigger plumbing

## Reference

- `docs/kb-schema.md` — customer-domain KB contract
- `vault-v1/Projects/mindframe/mindframe.md` — bundle composition + open threads
