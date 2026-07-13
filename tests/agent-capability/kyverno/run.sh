#!/usr/bin/env bash
# Verification of ClusterPolicy/agent-capability with the kyverno CLI.
#
#   ./tests/agent-capability/kyverno/run.sh
#
# This runs the SHIPPED policy file VERBATIM — no rewriting, no substitution.
# The artifact under test is byte-for-byte the artifact Argo applies. That is why
# the policy embeds its taxonomy inline instead of reading a ConfigMap at runtime:
# a context.configMap lookup could not be resolved by the CLI, so the riskiest
# path would have been the one path never tested. See
# scripts/gen-agent-capability-policy.py for the full rationale.
#
# Asserts both directions:
#   1. every real Agent in git PASSES     — no false positive. A policy that
#      blocked a live agent would wedge the kagent app on next Argo sync.
#   2. every fixture in bad-agents.yaml is DENIED — no false negative. One fixture
#      per rule; if any starts passing, the policy has regressed.
#
# THE ONE THING MOCKED: rules 6-7 (delegation) make a Kyverno `apiCall` to read
# the delegate Agent's class, and the CLI has no cluster to call. values.yaml
# supplies `delegateClass` per resource — the deny logic is exercised for real,
# only the lookup is stubbed. That call fires solely inside
# `foreach tools[?type=='Agent']`, so an agent with no delegations never touches
# it: the blast radius of an apiCall failure is the delegating agents alone.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
HERE="$REPO_ROOT/tests/agent-capability/kyverno"
POLICY="$REPO_ROOT/base-apps/kyverno-policies/agent-capability.yaml"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

command -v kyverno >/dev/null || { echo "kyverno CLI not found" >&2; exit 127; }

fail=0

# ---------------------------------------------------------------- 0. the policy
# must be in sync with its source of truth, or we are testing a stale artifact.
echo "── 0. policy is in sync with the taxonomy ──────────────────────────────"
if python3 "$REPO_ROOT/scripts/gen-agent-capability-policy.py" --check --repo-root "$REPO_ROOT"; then
  echo "   ✓ generated policy matches the committed one"
else
  echo "   ✗ policy is stale — regenerate it" >&2
  fail=1
fi

# ------------------------------------------------------- collect the real agents
python3 - "$REPO_ROOT" "$WORK" <<'PY'
import sys, yaml, pathlib
repo, work = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
docs = []
for p in sorted((repo / "base-apps" / "kagent").rglob("*.yaml")):
    for d in yaml.safe_load_all(p.read_text()):
        if isinstance(d, dict) and d.get("kind") == "Agent":
            docs.append(d)
with open(work / "good.yaml", "w") as f:
    yaml.safe_dump_all(docs, f, sort_keys=False, width=400)
print(f"real agents under test: {len(docs)}")
PY

echo
echo "── 1. real agents must all PASS ────────────────────────────────────────"
# `|| true`: kyverno apply exits non-zero whenever a policy denies — the EXPECTED
# outcome for the fixtures in step 2. Without this, set -e aborts on the first
# correct denial.
out="$(kyverno apply "$POLICY" --resource "$WORK/good.yaml" \
        --values-file "$HERE/values.yaml" 2>&1 | tail -1 || true)"
echo "   $out"
if grep -qE 'fail: 0,.*error: 0' <<<"$out"; then
  echo "   ✓ no false positives, no rule errors"
else
  echo "   ✗ a real agent is blocked, or a rule errored" >&2
  fail=1
fi

echo
echo "── 2. every violating fixture must be DENIED ───────────────────────────"
# one resource at a time, so no fixture can mask another
python3 - "$HERE/bad-agents.yaml" "$WORK" <<'PY'
import sys, yaml, pathlib
src, work = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
for d in yaml.safe_load_all(src.read_text()):
    if isinstance(d, dict) and d.get("kind") == "Agent":
        yaml.safe_dump(d, open(work / f"bad-{d['metadata']['name']}.yaml", "w"),
                       sort_keys=False, width=400)
PY

for f in "$WORK"/bad-*.yaml; do
  name="$(basename "$f" .yaml | sed 's/^bad-//')"
  res="$(kyverno apply "$POLICY" --resource "$f" \
          --values-file "$HERE/values.yaml" 2>&1 | tail -1 || true)"
  if grep -qE 'fail: [1-9]' <<<"$res"; then
    echo "   ✓ DENIED   $name"
  else
    echo "   ✗ ALLOWED  $name   <-- policy regression ($res)" >&2
    fail=1
  fi
done

echo
if [ "$fail" -eq 0 ]; then
  echo "agent-capability policy: OK"
else
  echo "agent-capability policy: FAILED" >&2
fi
exit "$fail"
