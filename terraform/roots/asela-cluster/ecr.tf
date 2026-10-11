# ECR: the container registry, imported 2026-10-07.
#
# One account, one region (us-east-2), one repository per image we build. The
# 38 repositories that existed before this file were created by hand; 29 of
# them (experiments with no push in 170-600 days and no consumer) were deleted
# the same day, and the 9 that remained were imported with the settings they
# had, so the import plan was "9 imports, 0 changes".
#
# A new image gets a repository by adding one line to local.ecr_repositories.
# Pushing is NOT granted here: each app that builds in GitHub Actions gets a
# per-repository OIDC role from Crossplane next to the app (the pattern in
# base-apps/agent-audit-aws-infrastructure/web-ecr-push.yaml).
#
# Deleting a repository takes two PRs on purpose, each applied by Atlantis
# before it merges. Just removing its line from local.ecr_repositories fails
# the plan: prevent_destroy belongs to the whole resource block, so it refuses
# to destroy any of its keys.
#   1. Move the line to local.ecr_repositories_retiring and add a moved block
#      for it (below). The apply moves it in state and sets force_delete = true,
#      which deletes nothing. force_delete is read from state at destroy time,
#      so it has to be applied before the delete.
#   2. Once nothing pulls the image, delete the line and its moved block. The
#      apply deletes the repository, every image in it, and its lifecycle
#      policy.

locals {
  ecr_repositories = {
    # Mutability and scan-on-push are per repository because they are what the
    # repositories already had. IMMUTABLE where deployments pin a tag or digest
    # (agent-audit-web, the model images); MUTABLE where `latest` is deployed
    # (weather-kitchen) or releases re-tag (backstage-portal, oncall-agent,
    # plex-stack-mcp).
    "agent-audit-web"          = { image_tag_mutability = "IMMUTABLE", scan_on_push = true }
    "backstage-portal"         = { image_tag_mutability = "MUTABLE", scan_on_push = false }
    "models/nomic-embed-text"  = { image_tag_mutability = "IMMUTABLE", scan_on_push = false }
    "models/qwen3.5-0.8b"      = { image_tag_mutability = "IMMUTABLE", scan_on_push = false }
    "oncall-agent"             = { image_tag_mutability = "MUTABLE", scan_on_push = false }
    "plex-stack-mcp"           = { image_tag_mutability = "MUTABLE", scan_on_push = false }
    "weather-kitchen-backend"  = { image_tag_mutability = "MUTABLE", scan_on_push = false }
    "weather-kitchen-frontend" = { image_tag_mutability = "MUTABLE", scan_on_push = false }
  }

  # Repositories on their way out (step 1 above). Same settings as before the
  # move, so the only change is force_delete.
  # Empty between retirements. homelab-agent went through both steps on
  # 2026-10-11 (kagent 1.0 plan, Task 5.2).
  ecr_repositories_retiring = {}
}

resource "aws_ecr_repository" "this" {
  for_each = local.ecr_repositories

  name                 = each.key
  image_tag_mutability = each.value.image_tag_mutability
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = each.value.scan_on_push
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Deliberately unprotected: no prevent_destroy, and force_delete so that
# removing a key here deletes the repository together with its images.
resource "aws_ecr_repository" "retiring" {
  for_each = local.ecr_repositories_retiring

  name                 = each.key
  image_tag_mutability = each.value.image_tag_mutability
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = each.value.scan_on_push
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# Every repository keeps its two newest tagged images and nothing else.
#
# Every image here is multi-arch: a tagged OCI index plus untagged platform and
# attestation manifests. Counting "any image" would count those children and
# leave ONE deployable version, so rule 1 counts tagged images only. ECR will
# not expire a child while the index that references it exists, so the two
# kept versions stay pullable; rule 2 then sweeps the orphans of expired
# indexes. Previewed on 2026-10-07 across all repositories before applying:
# every image the cluster runs was kept. Retiring repositories keep their
# policy (same key, so the move above leaves it untouched) until they go.
resource "aws_ecr_lifecycle_policy" "this" {
  for_each = merge(aws_ecr_repository.this, aws_ecr_repository.retiring)

  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the 2 newest tagged images. Platform children of a kept multi-arch index are protected by ECR while the index exists."
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*"]
          countType      = "imageCountMoreThan"
          countNumber    = 2
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Expire untagged images older than a day (orphaned platform manifests and attestations of expired indexes)."
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      }
    ]
  })
}
