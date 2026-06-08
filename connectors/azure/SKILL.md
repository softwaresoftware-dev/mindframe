---
name: azure
description: Microsoft Azure — subscriptions, resources, identities via the az CLI. Use when a task needs Azure data or actions.
connection:
  label: Azure
  kind: cli
  access: az
  auth: az-cli
  check: ["az", "account", "show"]
  account: ["az", "account", "show", "--query", "user.name", "-o", "tsv"]
---
Reach Azure through the `az` CLI, which runs as the logged-in user.

Common moves: `az account show`, `az resource list`, `az vm list`. Anything that creates, deletes, or modifies resources — draw it as a pending action and confirm with the operator first.
