#!/usr/bin/env bash
# Post-deploy merge patches for kagent agents.
# Run this AFTER ArgoCD syncs the kagent Helm chart.
# Uses --type=merge so only specified fields are touched (safe, no SSA ownership issues).
#
# Usage: ./post-deploy-patches.sh

set -euo pipefail

echo "=== Applying memory config to all agents ==="
for agent in k8s-agent helm-agent istio-agent kgateway-agent argo-rollouts-conversion-agent observability-agent dnd-agent build-orchestrator; do
  echo -n "  $agent: "
  kubectl patch agent "$agent" -n kagent --type=merge \
    -p '{"spec":{"declarative":{"memory":{"modelConfig":"embedding-model-config"}}}}' 2>&1 || true
done

echo ""
echo "=== Applying HITL requireApproval to write-capable agents ==="

kubectl patch agent k8s-agent -n kagent --type=json -p '[
  {"op":"add","path":"/spec/declarative/tools/0/mcpServer/requireApproval","value":[
    "k8s_patch_resource","k8s_create_resource","k8s_create_resource_from_url",
    "k8s_delete_resource","k8s_apply_manifest","k8s_execute_command"
  ]}
]' 2>&1 && echo "  k8s-agent: patched" || echo "  k8s-agent: skipped (may already have requireApproval)"

kubectl patch agent helm-agent -n kagent --type=json -p '[
  {"op":"add","path":"/spec/declarative/tools/0/mcpServer/requireApproval","value":[
    "helm_upgrade","helm_uninstall","k8s_apply_manifest"
  ]}
]' 2>&1 && echo "  helm-agent: patched" || echo "  helm-agent: skipped"

kubectl patch agent istio-agent -n kagent --type=json -p '[
  {"op":"add","path":"/spec/declarative/tools/0/mcpServer/requireApproval","value":[
    "k8s_create_resource","k8s_create_resource_from_url","k8s_delete_resource",
    "k8s_patch_resource","istio_delete_waypoint","istio_apply_waypoint","istio_install_istio"
  ]}
]' 2>&1 && echo "  istio-agent: patched" || echo "  istio-agent: skipped"

kubectl patch agent kgateway-agent -n kagent --type=json -p '[
  {"op":"add","path":"/spec/declarative/tools/0/mcpServer/requireApproval","value":[
    "k8s_patch_resource","k8s_create_resource","k8s_create_resource_from_url",
    "k8s_delete_resource","k8s_apply_manifest","helm_upgrade","helm_uninstall"
  ]}
]' 2>&1 && echo "  kgateway-agent: patched" || echo "  kgateway-agent: skipped"

kubectl patch agent argo-rollouts-conversion-agent -n kagent --type=json -p '[
  {"op":"add","path":"/spec/declarative/tools/0/mcpServer/requireApproval","value":[
    "k8s_create_resource","k8s_delete_resource","k8s_apply_manifest"
  ]}
]' 2>&1 && echo "  argo-rollouts-conversion-agent: patched" || echo "  argo-rollouts-conversion-agent: skipped"

echo ""
echo "=== Done. Verify with: kubectl get agents -n kagent ==="
