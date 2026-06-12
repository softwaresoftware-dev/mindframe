# Delivered frames — the gold-standard agent output

An event-driven agent's deliverable is a **mindframe**, not a file dump or a
notification blurb. The agent does its work, then drops a live page in the
operator's dashboard: the verdict up top, the evidence below, and every
decision as a button. That page *is* the human-in-the-loop piece — and it
outlives the agent that wrote it.

## Why this works with zero new infrastructure

A frame is a directory convention, not a registration: any dir under
`~/.mindframe/frames/<id>/` holding an `index.html` IS a frame — the
dashboard lists it, sorts it by activity, serves it at `/m/<id>`, and proxies
messages to the task named in its `meta.json`.

The lifecycle composes with the revival machinery:

1. **While the deliverer is alive**, button clicks and operator messages
   route to it (`meta.task_id` = its task id) — it keeps working the frame.
2. **After it exits**, the frame shows as *asleep* in the dock. The next
   operator message revives a successor agent (taskpilot `start` with the
   revival brief, built from the page + `meta.prompt`) — the conversation
   continues with full context.
3. If taskpilot's row for the deliverer is ever gone entirely, the message
   path defines + starts a fresh task with cwd = the frame dir. A delivered
   frame can never go permanently dead.

So "drop a mindframe" = write two files. Everything else is already wired.

## The contract (paste into recipe starter prompts)

```
DELIVER YOUR RESULT AS A MINDFRAME — the operator reviews and decides there.

1. Your frame directory is ~/.mindframe/frames/$TASKPILOT_TASK_ID/ — your
   task id is in the $TASKPILOT_TASK_ID env var. mkdir -p it.

2. Write index.html: ONE complete, self-contained HTML document. Inline all
   CSS. Include <meta name="viewport" content="width=device-width,
   initial-scale=1"> and <meta name="mf-patch" content="safe"> (keep any
   script idempotent and event-delegated). Calm, legible, no emoji.
   Structure it as a deliverable: verdict/summary first, evidence next,
   decisions last. The page is the interface — not a log of what you did.

3. Every decision, approval, or follow-up is a BUTTON that messages this
   frame's agent (you, while you live; a revived successor after you exit):
     <button onclick="fetch(location.pathname.replace('/page','/message'),
       {method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text:'A CLEAR INSTRUCTION'})})
       .then(function(){this.disabled=true;this.textContent='on it…'}.bind(this))">Label</button>
   Anything irreversible or outward-facing: draw it as a pending action with
   an explicit approval button and wait for the operator.

4. Write meta.json next to it:
     {"id": "<your task id>", "title": "<short deliverable title>",
      "task_id": "<your task id>", "status": "active",
      "kind": "delivered",
      "origin": {"watch": "<your recipe id>",
                 "event": "<one-line event description>",
                 "at_epoch": <unix seconds now>},
      "prompt": "<2-4 sentences for your successor: what this frame is, what
                 work produced it, where supporting material lives, and how
                 to continue helping the operator>"}
   kind+origin place the frame in the operator's INBOX with provenance
   ("delivered by pr-prep · 8:02"); a newer delivery from the same watch
   supersedes an older unhandled one (it auto-archives). The prompt field
   seeds the revival brief — write it as a handoff.

5. The frame appears in the operator's dock automatically. If a notification
   tool is available, send the link (<dashboard origin>/m/<your task id>);
   if not, the dock's recency sort surfaces it.
```

## What changes for recipe authors

Replace "write your findings to an artifact file and notify" endings with the
contract block above. The agent's last act before idling is delivering the
frame; it should stay alive briefly afterward (it idles on its channel) so
immediate button clicks land on the author rather than a cold successor.

## Known limits (current)

- Dock noise: every event spawns a frame; there is no auto-archive yet.
  Operators delete from the dock; an archive verb is planned.
- The deliverer's cognition log shows in the frame's surface only while its
  transcript is the newest for the task (handled — the dashboard also looks
  up transcripts by the deliverer's taskpilot cwd).
