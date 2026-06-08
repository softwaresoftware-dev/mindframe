---
name: aws
description: AWS — EC2, S3, IAM, and the rest of the account via the AWS CLI. Use when a task needs AWS data or actions.
connection:
  label: AWS
  kind: cli
  access: aws
  auth: aws-cli
  check: ["aws", "sts", "get-caller-identity"]
  account: ["aws", "sts", "get-caller-identity", "--query", "Arn", "--output", "text"]
  docs: aws help
---
Reach AWS through the `aws` CLI, which runs as the operator's configured profile/credentials.

Common moves: `aws sts get-caller-identity`, `aws s3 ls`, `aws ec2 describe-instances`. Anything that creates, deletes, or modifies resources — draw it as a pending action and confirm with the operator first.
