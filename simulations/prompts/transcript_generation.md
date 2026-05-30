# Transcript generation prompt (Phase B)

You are simulating a working session a real user (described in the persona) would have with Claude Code. You generate the *transcript* — alternating USER and ASSISTANT turns — that vault-keeper will later process.

## Inputs

- A persona description (who the user is, what they care about, what facts they have)
- A working-session topic (one of the topics the persona realistically discusses)

## Your job

Generate a realistic multi-turn transcript where:

- The USER turns sound like the persona — same vocabulary, same level of detail, same conversational style
- The ASSISTANT turns are plausible Claude Code responses (helpful, technical, sometimes asking clarifying questions)
- The session contains substantive content — specific named entities, decisions made, status updates, concrete facts — not vague generalities
- The session ends naturally (the user gets what they came for; Claude doesn't trail off)
- Length: 8–15 USER turns, paired with ASSISTANT responses

## Format

Output the transcript as alternating blocks:

```
[USER]
<user message>

[ASSISTANT]
<assistant response>

[USER]
<next user message>

...
```

No JSON, no markdown code fences around the whole thing. Just the alternating turns separated by blank lines.

## Realism requirements

- USER turns reference specific persona facts (named companies, deals, people) — not "Company A" placeholders
- USER turns sometimes have typos, mid-sentence corrections, half-finished thoughts
- ASSISTANT turns are concrete and actionable, not "I'd be happy to help!" filler
- If the topic naturally calls for tool use, the ASSISTANT *describes* what it did (read X file, ran Y command) — you don't need to actually invoke tools
- The substantive content (the decisions, status updates, facts) should be in roughly the bottom 60% of the transcript — early turns set context, later turns produce the value

The goal: a transcript that looks indistinguishable from a real working session for this persona. Vault-keeper's job (next phase) is to extract the substantive items from this transcript and write them as schema-compliant vault entries.
