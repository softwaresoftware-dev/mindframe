---
name: gcp
description: Google Cloud — projects, compute, storage, IAM via the gcloud CLI. Use when a task needs GCP data or actions.
connection:
  label: GCP
  kind: cli
  access: gcloud
  auth: gcloud-cli
  check: ["gcloud", "auth", "print-access-token"]
  account: ["gcloud", "config", "get-value", "account"]
---
Reach Google Cloud through the `gcloud` CLI, which runs as the active account.

Common moves: `gcloud projects list`, `gcloud compute instances list`, `gcloud storage ls`. The `check` mints an access token, so a zero exit means the active account is genuinely usable. Anything that creates, deletes, or modifies resources — draw it as a pending action and confirm with the operator first.
