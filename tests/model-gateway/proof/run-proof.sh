#!/usr/bin/env bash
# Local proof of the model gateway's generated config (spec §7 step 3). Needs Docker and the
# gateway image (log in to ECR first:
#   aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin 852893458518.dkr.ecr.us-east-2.amazonaws.com
# ). Runs Postgres, a mock Anthropic API and two gateways locally; nothing leaves the machine.
# --keep leaves the containers up for debugging.
set -euo pipefail
cd "$(dirname "$0")/../../.."
exec python3 tests/model-gateway/proof/proof.py "$@"
