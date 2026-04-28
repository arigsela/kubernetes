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

ACTUAL="$(crossplane render "${XR}" "${COMPO}" "${FUNCS}" --extra-resources "${XRD}")"

# Normalize before diff (yq round-trip strips comments + sorts keys)
diff <(yq -P 'sort_keys(..)' <<<"${ACTUAL}") <(yq -P 'sort_keys(..)' "${EXPECTED}")
