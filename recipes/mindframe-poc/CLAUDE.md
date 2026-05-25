# mindframe-poc — end-to-end demo recipe

End-to-end smoke for the mindframe pipeline:
dispatcher webhook → frame minted → taskpilot spawn → agent narrates a softwaresoftware.dev infrastructure survey via the mindframe MCP → SPA renders blocks live.

## Wire it

A static route in `~/.dispatcher/channels.yaml`:

```yaml
- source: manual
  event_type: infra-survey
  target: spawn:mindframe-poc
```

Fire it:

```bash
curl -X POST http://127.0.0.1:8911/api/event \
  -H "Authorization: Bearer $(cat ~/.mindframe/secrets/dispatcher-bearer.token)" \
  -H "Content-Type: application/json" \
  -d '{"source":"manual","event_type":"infra-survey","event_id":"poc-001","data":{}}'
```

Watch at `http://127.0.0.1:5174/m/mindframe-poc-poc-001`.

## What it surveys

- `docker ps` containers
- `systemctl --user` running + failed services
- `/etc/nginx/sites-enabled/` + `/var/www/` deployed sites
- `curl -sI` against each `.softwaresoftware.dev` subdomain (status + Cloudflare cache header)
- `gh repo list softwaresoftware-dev` recent activity + open PRs in the top 3

## Why this recipe

This is the proof-of-life for the mindframe pipeline. It exercises:

- Dispatcher's new `frame:` recipe block handling (mints frame before spawn)
- Mindframe's spawn primitive (mkdir + meta.json + seed block)
- Taskpilot launching claude with `--name=<id> --cwd=<frame_dir>`
- The spawned agent's mindframe MCP (`write_block` resolves id from cwd)
- The dashboard's SSE stream pushing blocks to the open browser tab
- The full block-type vocabulary (summary, table, text, code, button-row, close)

If this recipe runs end-to-end, the wire is live. Subsequent recipes that want mindframe-shape just declare `frame:` and write their own CLAUDE.md.
