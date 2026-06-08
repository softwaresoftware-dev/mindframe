---
name: github
description: GitHub — repos, issues, pull requests, releases. Use when a task needs GitHub data or actions.
connection:
  label: GitHub
  kind: cli
  access: gh
  auth: gh-cli
  check: ["gh", "auth", "status"]
  account: ["gh", "api", "user", "-q", ".login"]
---
Reach GitHub through the `gh` CLI, which runs as the operator (it inherits their auth — no token needed here).

Common moves: `gh repo list`, `gh pr list`, `gh issue view <n>`, `gh release list`, `gh api <endpoint>`. Anything irreversible or outward-facing (merging, closing, commenting) — draw it as a pending action and confirm with the operator first.
