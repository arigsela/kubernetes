#!/usr/bin/env bash
# tests/composition/render.sh — local TDD loop for the Composition.
# Usage:
#   ./tests/composition/render.sh xr-minimal
#   ./tests/composition/render.sh xr-with-db
# Diffs `crossplane render` output against tests/composition/expected-<name>.yaml.
# Exit code 0 = match, non-zero = diff.

set -euo pipefail

CASE="${1:?Usage: $0 <case-name without extension>}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
COMPO="${ROOT}/../../base-apps/crossplane-compositions/composition-application.yaml"
XRD="${ROOT}/../../base-apps/crossplane-compositions/xrd-application.yaml"
FUNCS="${ROOT}/functions.yaml"
XR="${ROOT}/${CASE}.yaml"
EXPECTED="${ROOT}/expected-${CASE}.yaml"

ACTUAL="$(crossplane render -x "${XR}" "${COMPO}" "${FUNCS}" --extra-resources "${XRD}")"

# Normalize both sides before diff:
# - sort_keys(..) makes key order deterministic (yq round-trip also strips comments).
# - del(.status) removes runtime-controller status (e.g., the synthetic Ready=True
#   condition `crossplane render` stamps on the XR). The test verifies Composition
#   *output* (composed children), not render-time XR status.
# - del(.metadata.ownerReferences) and the composite label removal strip render-tooling
#   plumbing that crossplane render adds to composed resources. We assert what the
#   Composition produces, not the runtime owner-ref wiring.
NORMALIZE='[.] | sort_by(.kind) | .[] | (... comments="") | del(.status) | del(.metadata.ownerReferences) | del(.metadata.labels."crossplane.io/composite") | sort_keys(..)'
diff <(yq ea -P "${NORMALIZE}" <<<"${ACTUAL}") <(yq ea -P "${NORMALIZE}" "${EXPECTED}")
