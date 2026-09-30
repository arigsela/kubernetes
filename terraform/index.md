---
type: "Directory Index"
title: "Terraform"
description: "Directory listing for the Terraform tree: the active cluster root and its reusable modules."
tags: [terraform, infrastructure]
---

# terraform Index

| path | purpose |
|---|---|
| `roots/asela-cluster/` | Active Terraform root (S3 backend `asela-terraform-states`), applied by Atlantis with OpenTofu before the PR merges (see the header of `argocd.tf`) |
| `modules/argocd/` | Argo CD install/config module, called from `roots/asela-cluster/argocd.tf` |
| `modules/application-sets/` | Dead: the old master-app definition. No root calls it; master-app now lives in `base-apps/master-app.yaml` (deletion tracked as SPEC T88) |
| `modules/kube-secrets/` | Unused: referenced only by a commented-out block in `roots/asela-cluster/kube_secrets.tf` |
