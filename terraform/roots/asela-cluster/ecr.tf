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

    # beacon (app repo arigsela/beacon; beacon SPEC §T.2, §V.17): one repository
    # per service image, pushed only by that repo's v* tag builds through the
    # GitHub-OIDC role in base-apps/beacon-aws-infrastructure/. IMMUTABLE because
    # base-apps/beacon/ pins every image by digest. keep_tagged = 10, not the
    # default 2: beacon rolls back and reverts injected faults with `git revert`
    # (beacon §V.13), and a reverted pin has to still be pullable. Six images
    # of ~60 MB keeping 10 versions each is ~3.6 GB, about $0.36 a month.
    "beacon-api"       = { image_tag_mutability = "IMMUTABLE", scan_on_push = true, keep_tagged = 10 }
    "beacon-checker"   = { image_tag_mutability = "IMMUTABLE", scan_on_push = true, keep_tagged = 10 }
    "beacon-mcp"       = { image_tag_mutability = "IMMUTABLE", scan_on_push = true, keep_tagged = 10 }
    "beacon-notifier"  = { image_tag_mutability = "IMMUTABLE", scan_on_push = true, keep_tagged = 10 }
    "beacon-scheduler" = { image_tag_mutability = "IMMUTABLE", scan_on_push = true, keep_tagged = 10 }
    "beacon-web"       = { image_tag_mutability = "IMMUTABLE", scan_on_push = true, keep_tagged = 10 }
  }

  # Repositories on their way out (step 1 above). Same settings as before the
  # move, so the only change is force_delete.
  # Empty between retirements. homelab-agent went through both steps on
  # 2026-10-11 (kagent 1.0 plan, Task 5.2).
  ecr_repositories_retiring = {}

  # How many tagged images each repository keeps (rule 1 of the lifecycle policy
  # below): 2 unless the repository sets keep_tagged. Keyed like the
  # repositories, so the policy's for_each can look its count up.
  ecr_keep_tagged = {
    for name, repo in merge(local.ecr_repositories, local.ecr_repositories_retiring) :
    name => try(repo.keep_tagged, 2)
  }
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

# Every repository keeps its newest tagged images (two unless it sets
# keep_tagged, see local.ecr_keep_tagged) and nothing else. The rendered policy
# of a repository on the default is unchanged by that knob: same count, same
# description text.
#
# Images here are mostly multi-arch: a tagged OCI index plus untagged platform and
# attestation manifests (beacon's are single-platform, one tagged manifest each). Counting "any image" would count those children and
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
        description  = "Keep only the ${local.ecr_keep_tagged[each.key]} newest tagged images. Platform children of a kept multi-arch index are protected by ECR while the index exists."
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*"]
          countType      = "imageCountMoreThan"
          countNumber    = local.ecr_keep_tagged[each.key]
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
