# vault-sharing

Agent-mediated GitHub sharing for mindframe vaults. Operators don't see git, ssh, or PRs — agents on each side handle the mechanics.

## What's here

| File | Role |
|------|------|
| `agent/CLAUDE.md` | Operating instructions for the long-running `vault-sharing` taskpilot service agent |
| `share.py` | Outbound trigger — "share vault X with Y" |
| `accept.py` | Inbound trigger — list pending GitHub invites, accept one |

## Architecture

Same shape as the bundle's other write agents (vault-keeper, vault-query, dispatcher):

- **Long-running agent** registered in the session-bridge mesh as `vault-sharing`
- **Trigger scripts** drop job files in `~/.mindframe/vault-sharing/queue/`, message the agent via channel
- **Agent uses `gh` CLI** under the hood — never handles raw GitHub tokens. Inherits the operator's already-authed gh session.
- **`vaults.yaml`-aware** — outbound shares look up vault paths from `~/.mindframe/vaults.yaml`; inbound accepts register cloned vaults there

## Storage backend choice: GitHub

For v1, sharing is GitHub-backed. The operator needs `gh auth login` to be working (browser OAuth flow most users complete in 30 seconds). The recipient gets a GitHub collaboration invite — if they don't have a GitHub account, they're prompted to make one. GitHub repo permissions (`pull`, `push`, `admin`) map to vault permissions (`read-only`, `read+write`, `owner`).

Future v2 may add a softwaresoftware.dev-hosted backend so non-technical users don't need GitHub accounts. Same agent abstraction; different storage adapter underneath.

## Workflows

### Sharing a vault you own with someone else

```bash
# Operator side
vault_sharing/share.py --vault acme-matter --to beth@firm.com --permission push
# Agent: creates github.com/<owner>/vault-acme-matter, pushes contents,
# sends GitHub collab invite to beth@firm.com.
# Reply: "ok, invite sent. waiting for accept."
```

If `beth@firm.com` doesn't have a GitHub account, GitHub emails her with sign-up + accept link.

### Accepting an incoming share

```bash
# Recipient side: list pending GitHub invites
vault_sharing/accept.py --list
# pending invitations (1):
#   [123456789] alice/vault-acme-matter
#      from alice, perm=write, created 2026-06-01

# Accept and register
vault_sharing/accept.py --invitation 123456789 --wait
# Agent: accepts the GitHub invite, clones the repo to ~/mindframe-vaults/acme-matter,
# registers in vaults.yaml, notifies vault-keeper + vault-query.
# Reply: "ok, vault 'acme-matter' is now in your catalog."
```

### Once accepted, the vault is just another vault

Both sides' vault-keepers and vault-queries operate against it normally. The freshness contract (git pull → read schema fresh → write → commit → push, retry on non-fast-forward) handles concurrent edits. Two operators can write at the same time; git's standard merge semantics apply.

## What the agent does NOT do

- Doesn't push or pull vault content during normal operation — that's vault-keeper and vault-query's freshness-contract work
- Doesn't manage credentials — `gh auth` is the auth path
- Doesn't email recipients itself — GitHub's collaborator-invite email is what gets sent
- Doesn't do per-entity ACL (v2)

## Roadmap

- **Phase A (next)** — dashboard surface for `[VAULTS]` panel with per-vault `[Share]` button; routing rules; multi-vault classification when vault-keeper captures
- **Phase B** — incoming-share notifications via the dispatcher (so operators don't have to `accept.py --list` to know about new invites)
- **Phase C** — softwaresoftware.dev hosted backend as an alternative to GitHub for non-technical users
- **Phase D** — per-entity ACL (some entries are vault-team-only even within a shared vault)

## Honest gaps in v1

- No dashboard UI yet — sharing happens via CLI, listed here for clarity. Agent ballet works correctly behind it.
- Multi-vault routing (which vault does a capture land in?) is the next piece of work; today, vault-keeper writes to the configured default vault. Sharing creates additional vaults in `vaults.yaml`; the operator currently has to manually re-configure capture routing.
- Email-based GitHub invites work, but the "you don't have mindframe yet, here's how to install" guidance to the recipient is currently just GitHub's stock collab email. A mindframe-flavored invitation email is a future product piece.
- Cross-machine agent coordination (Alice's mindframe pinging Beth's mindframe) is currently *implicit* through the shared GitHub repo. A direct mesh handshake is a future iteration.
