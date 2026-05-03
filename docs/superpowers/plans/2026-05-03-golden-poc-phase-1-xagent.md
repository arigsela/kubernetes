# Golden POC — Phase 1: XAgent + Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define the XAgent v1alpha1 Crossplane v2 namespaced XR, write its Composition (function-python script), pre-create the per-skill RBAC, hand-write the Cluster Health XAgent, and verify it works end-to-end via its HTTP endpoint with curl. No Backstage, no surface adapters yet — just the spine.

**Architecture:** XAgent is a Crossplane v2 namespaced XR that mirrors the existing XApplication pattern. Its Composition is a single-step `function-python` pipeline that renders a kagent Agent CR, ToolServer CR(s), ExternalSecret (when surface needs creds), Service, Ingress, and TeraSky/Backstage labels. RBAC for skills is pre-created at install time per the design doc — the Composition wires the agent's ServiceAccount to existing RoleBindings by name.

**Tech Stack:** Crossplane v2 (apiextensions.crossplane.io/v2), function-python, kagent.dev/v1alpha1 CRDs (Agent, ToolServer), External Secrets, nginx-ingress, cert-manager.

**Reference design:** `docs/superpowers/specs/2026-05-02-golden-ai-platform-poc-design.md` Section 4 (XAgent + Composition), Section 7 (Cluster Health Agent).

**Dependency:** Phase 0 must be complete. Read `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md` first — its "Bucket A/B/C" determination for kagent ToolServer shape changes Task 1.4.

---

## Task 1.1: Create the agents namespace + SecretStore

**Files:**
- Create: `base-apps/agents.yaml`
- Create: `base-apps/agents/namespace.yaml`
- Create: `base-apps/agents/secret-store.yaml`

The `agents` namespace is the home for XAgent CRs and the kagent Agent CRs they render.

- [ ] **Step 1: Create namespace manifest**

`base-apps/agents/namespace.yaml`:
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: agents
  labels:
    app.kubernetes.io/managed-by: gitops
```

- [ ] **Step 2: Create SecretStore for the agents namespace**

`base-apps/agents/secret-store.yaml`:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: agents
spec:
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"
      path: "k8s-secrets"
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: "agents"
          serviceAccountRef:
            name: "default"
```

- [ ] **Step 3: Create the Vault role**

```bash
vault write auth/kubernetes/role/agents \
  bound_service_account_names=default \
  bound_service_account_namespaces=agents \
  policies=k8s-secrets-read \
  ttl=24h
```

- [ ] **Step 4: Create the ArgoCD Application**

`base-apps/agents.yaml`:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: agents
  namespace: argo-cd
spec:
  project: default
  source:
    repoURL: https://github.com/arigsela/kubernetes
    targetRevision: main
    path: base-apps/agents
  destination:
    server: https://kubernetes.default.svc
    namespace: agents
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

- [ ] **Step 5: Commit**

```bash
git add base-apps/agents.yaml base-apps/agents/
git commit -m "feat(agents): namespace + SecretStore for XAgent CRs (Phase 1)"
```

After ArgoCD sync: verify `kubectl get ns agents` and `kubectl get secretstore vault-backend -n agents`.

---

## Task 1.2: Pre-create per-skill RBAC

**Files:**
- Create: `base-apps/agents/rbac-skills.yaml`

Per design doc Section 4: "RBAC is pre-created at install time per skill; the Composition wires the agent's ServiceAccount to the appropriate existing RoleBinding by name." We define ClusterRoles per skill once; agents that use that skill get bound to it.

- [ ] **Step 1: Define ClusterRoles for each skill type**

`base-apps/agents/rbac-skills.yaml`:
```yaml
# Cluster-wide read-only Role for any agent using the kubernetes-mcp skill.
# Naming convention: agent-skill-<skill-name>
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: agent-skill-kubernetes-mcp
rules:
  - apiGroups: [""]
    resources: ["pods", "events", "namespaces", "nodes", "services"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets", "statefulsets", "daemonsets"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["batch"]
    resources: ["jobs", "cronjobs"]
    verbs: ["get", "list", "watch"]
---
# Read-only HTTP access to Coroot (which exposes a Prometheus-compatible API).
# This skill makes outbound HTTP calls; no Kubernetes API perms required, but
# we keep the ClusterRole defined for symmetry and future-proofing.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: agent-skill-prometheus-mcp
rules: []  # placeholder; this skill needs network policy, not RBAC
---
# github-mcp talks to GitHub API only — no cluster permissions.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: agent-skill-github-mcp
rules: []
---
# k8s-yaml-lint runs offline — no permissions.
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: agent-skill-k8s-yaml-lint
rules: []
```

- [ ] **Step 2: Commit**

```bash
git add base-apps/agents/rbac-skills.yaml
git commit -m "feat(agents): per-skill ClusterRoles for XAgent SAs to bind (Phase 1)"
```

After ArgoCD sync: `kubectl get clusterrole | grep agent-skill-` should show all four.

---

## Task 1.3: Define the XAgent XRD

**Files:**
- Create: `base-apps/crossplane-compositions/xrd-agent.yaml`

Mirrors the existing `xrd-application.yaml` shape: `apiextensions.crossplane.io/v2`, namespaced, single version v1alpha1.

- [ ] **Step 1: Write the XRD**

`base-apps/crossplane-compositions/xrd-agent.yaml`:
```yaml
# base-apps/crossplane-compositions/xrd-agent.yaml
# XAgent — engineer-facing API for declaring an AI agent.
# Namespaced (Crossplane v2 default). Schema is v1alpha1; iterate freely.
# Model selection, gateway routing, observability, RBAC binding are all
# platform defaults set by the Composition, not XR fields.
apiVersion: apiextensions.crossplane.io/v2
kind: CompositeResourceDefinition
metadata:
  name: xagents.platform.arigsela.com
  annotations:
    argocd.argoproj.io/sync-wave: "2"
spec:
  scope: Namespaced
  group: platform.arigsela.com
  names:
    kind: XAgent
    plural: xagents
  defaultCompositionRef:
    name: agent
  versions:
    - name: v1alpha1
      served: true
      referenceable: true
      schema:
        openAPIV3Schema:
          type: object
          required: [spec]
          properties:
            spec:
              type: object
              required: [description, systemPrompt, skills, surface]
              properties:
                description:
                  type: string
                  description: |
                    Human-readable purpose of the agent. Shown in Backstage
                    catalog. One sentence; this is not the system prompt.
                  minLength: 10
                  maxLength: 500
                systemPrompt:
                  type: string
                  description: "The agent's system prompt — what to do, how to behave, what to avoid."
                  minLength: 20
                skills:
                  type: array
                  description: "agentregistry skill references."
                  minItems: 1
                  items:
                    type: object
                    required: [ref]
                    properties:
                      ref:
                        type: string
                        pattern: "^oci://.+:.+$"
                        description: "OCI URI, e.g. oci://agentregistry.agentregistry.svc/skills/kubernetes-mcp:v1"
                      alias:
                        type: string
                        description: "Optional short name for this skill in the agent's prompt context."
                surface:
                  type: string
                  enum: [slack, http, mcp, github-webhook]
                  description: "How humans/systems reach this agent."
                model:
                  type: string
                  default: "platform-default"
                  description: |
                    Override the platform default model. POC accepts this field
                    but uses kagent's configured default regardless. Multi-model
                    support is deferred to v1.1.
            status:
              type: object
              x-kubernetes-preserve-unknown-fields: true
```

- [ ] **Step 2: Commit**

```bash
git add base-apps/crossplane-compositions/xrd-agent.yaml
git commit -m "feat(xrd): XAgent v1alpha1 namespaced XR (Phase 1)"
```

After ArgoCD sync: `kubectl get xrd xagents.platform.arigsela.com` should show as established.

---

## Task 1.4: Write the Composition function-python script

**Files:**
- Create: `base-apps/crossplane-compositions/composition-agent.yaml`

The Composition's Python script renders the child resources. The structure mirrors the existing XApplication composition (`composition-application.yaml`).

**ADAPTATION NOTE:** Read Phase 0's preflight-results doc Bucket determination for kagent ToolServer shape **before** writing Step 1. This task is written assuming **Bucket A** (kagent ToolServer accepts an OCI image directly). If Phase 0 found Bucket B (need a per-skill MCP server Deployment), insert that rendering logic in `make_tool_server_resources()`. If Bucket C (something else), adapt the `make_tool_server()` function to that shape.

- [ ] **Step 1: Write the composition manifest**

`base-apps/crossplane-compositions/composition-agent.yaml`:
````yaml
# base-apps/crossplane-compositions/composition-agent.yaml
# Composition for XAgent. Single function-python step. Renders:
#   - kagent Agent CR (model points at agentgateway endpoint)
#   - kagent ToolServer CR(s) — one per unique skill ref
#   - ExternalSecret (only when surface needs creds)
#   - Service + Ingress (always — supports HTTP fallback and "Try it" button)
#   - ServiceAccount + RoleBinding(s) wiring the agent's SA to per-skill ClusterRoles
#   - TeraSky/Backstage labels and annotations on every rendered resource
apiVersion: apiextensions.crossplane.io/v1
kind: Composition
metadata:
  name: agent
  annotations:
    argocd.argoproj.io/sync-wave: "3"
spec:
  compositeTypeRef:
    apiVersion: platform.arigsela.com/v1alpha1
    kind: XAgent
  mode: Pipeline
  pipeline:
    - step: render-resources
      functionRef:
        name: function-python
      input:
        apiVersion: python.fn.crossplane.io/v1beta1
        kind: Script
        script: |
          from crossplane.function import resource

          STD_LABELS_TEMPLATE = {
              "app.kubernetes.io/name": None,
              "app.kubernetes.io/managed-by": "crossplane",
              "app.kubernetes.io/component": "agent",
              "backstage.io/kubernetes-id": None,
          }

          TERASKY_ANNOTATION_PREFIXES = ("terasky.backstage.io/", "backstage.io/", "platform.arigsela.com/")

          # Skill name -> agentregistry path mapping. Used to extract a short
          # skill name from the OCI ref so we can name resources predictably
          # and bind ClusterRoles by `agent-skill-<name>`.
          # OCI ref format: oci://agentregistry.agentregistry.svc/skills/<name>:<version>
          def parse_skill_name(ref: str) -> str:
              # ref like "oci://.../skills/kubernetes-mcp:v1"
              after_skills = ref.split("/skills/", 1)[-1]
              return after_skills.split(":", 1)[0]

          def std_labels(name):
              labels = dict(STD_LABELS_TEMPLATE)
              labels["app.kubernetes.io/name"] = name
              labels["backstage.io/kubernetes-id"] = name
              return labels

          def std_annotations(xr_annotations, resource_name):
              copied = {
                  k: v for k, v in (xr_annotations or {}).items()
                  if k.startswith(TERASKY_ANNOTATION_PREFIXES)
              }
              copied["crossplane.io/composition-resource-name"] = resource_name
              return copied

          # ---- kagent Agent CR ----
          # ADAPTATION: the exact spec shape depends on kagent v0.8.6's Agent CRD.
          # Verify field names against the CRD before relying on this in production.
          # The shape below is a reasonable default; adjust if the CRD differs.
          def make_kagent_agent(name, namespace, system_prompt, skill_names, annotations):
              return {
                  "apiVersion": "kagent.dev/v1alpha1",
                  "kind": "Agent",
                  "metadata": {
                      "name": name,
                      "namespace": namespace,
                      "labels": std_labels(name),
                      "annotations": annotations,
                  },
                  "spec": {
                      "description": "Managed by XAgent Composition",
                      "systemMessage": system_prompt,
                      "modelConfig": "anthropic",  # references the kagent provider config
                      "tools": [
                          {"toolServer": tname}
                          for tname in skill_names
                      ],
                      "serviceAccount": f"agent-{name}",
                  },
              }

          # ---- kagent ToolServer CR ----
          # ADAPTATION: per Phase 0 preflight Bucket. Below assumes Bucket A
          # (ToolServer accepts an OCI image directly via spec.config.image).
          # For Bucket B, this would render a Deployment + Service per skill
          # and the ToolServer would point to the Service URL via spec.config.url.
          def make_tool_server(skill_name, oci_ref, namespace, annotations):
              return {
                  "apiVersion": "kagent.dev/v1alpha1",
                  "kind": "ToolServer",
                  "metadata": {
                      "name": skill_name,
                      "namespace": namespace,
                      "labels": std_labels(skill_name),
                      "annotations": annotations,
                  },
                  "spec": {
                      "description": f"Skill {skill_name} from agentregistry",
                      "config": {
                          # Strip the "oci://" prefix; kagent expects an image ref.
                          "image": oci_ref.replace("oci://", ""),
                      },
                  },
              }

          # ---- Service + Ingress (always rendered) ----
          def make_service(name, namespace, annotations):
              return {
                  "apiVersion": "v1",
                  "kind": "Service",
                  "metadata": {
                      "name": name,
                      "namespace": namespace,
                      "labels": std_labels(name),
                      "annotations": annotations,
                  },
                  "spec": {
                      "type": "ClusterIP",
                      "selector": {"app.kubernetes.io/name": name},
                      "ports": [{"name": "http", "port": 80, "targetPort": 8080}],
                  },
              }

          # AGENTS_BASE_DOMAIN is set in the Composition's input below; falls
          # back to "agents.cluster.local" if missing. Replace with your real
          # domain at composition time (or override via XR annotation).
          DEFAULT_BASE_DOMAIN = "agents.cluster.local"

          def make_ingress(name, namespace, annotations, base_domain):
              host = f"{name}.{base_domain}"
              base_annotations = {
                  "cert-manager.io/cluster-issuer": "letsencrypt-prod",
                  "nginx.ingress.kubernetes.io/ssl-redirect": "true",
                  # POC: source-IP allowlist to cluster pod CIDR + Backstage pod IP.
                  # Replace with your actual ranges.
                  "nginx.ingress.kubernetes.io/whitelist-source-range": "10.0.0.0/8,192.168.0.0/16",
              }
              merged_annotations = {**base_annotations, **annotations}
              return {
                  "apiVersion": "networking.k8s.io/v1",
                  "kind": "Ingress",
                  "metadata": {
                      "name": name,
                      "namespace": namespace,
                      "labels": std_labels(name),
                      "annotations": merged_annotations,
                  },
                  "spec": {
                      "ingressClassName": "nginx",
                      "tls": [{"hosts": [host], "secretName": f"{name}-tls"}],
                      "rules": [{
                          "host": host,
                          "http": {"paths": [{
                              "path": "/", "pathType": "Prefix",
                              "backend": {"service": {"name": name, "port": {"number": 80}}},
                          }]},
                      }],
                  },
              }

          # ---- ServiceAccount + RoleBindings ----
          def make_service_account(name, namespace, annotations):
              return {
                  "apiVersion": "v1",
                  "kind": "ServiceAccount",
                  "metadata": {
                      "name": f"agent-{name}",
                      "namespace": namespace,
                      "labels": std_labels(name),
                      "annotations": annotations,
                  },
              }

          def make_cluster_role_binding(agent_name, namespace, skill_name, annotations):
              return {
                  "apiVersion": "rbac.authorization.k8s.io/v1",
                  "kind": "ClusterRoleBinding",
                  "metadata": {
                      "name": f"agent-{agent_name}-skill-{skill_name}",
                      "labels": std_labels(agent_name),
                      "annotations": annotations,
                  },
                  "subjects": [{
                      "kind": "ServiceAccount",
                      "name": f"agent-{agent_name}",
                      "namespace": namespace,
                  }],
                  "roleRef": {
                      "kind": "ClusterRole",
                      "name": f"agent-skill-{skill_name}",
                      "apiGroup": "rbac.authorization.k8s.io",
                  },
              }

          # ---- ExternalSecret (only when surface needs creds) ----
          def make_external_secret_for_surface(agent_name, namespace, surface, annotations):
              if surface == "slack":
                  return {
                      "apiVersion": "external-secrets.io/v1beta1",
                      "kind": "ExternalSecret",
                      "metadata": {
                          "name": f"{agent_name}-slack",
                          "namespace": namespace,
                          "labels": std_labels(agent_name),
                          "annotations": annotations,
                      },
                      "spec": {
                          "refreshInterval": "1h",
                          "secretStoreRef": {"name": "vault-backend", "kind": "SecretStore"},
                          "target": {"name": f"{agent_name}-slack", "creationPolicy": "Owner"},
                          "data": [{
                              "secretKey": "slack_bot_token",
                              "remoteRef": {
                                  "key": f"agents/{agent_name}",
                                  "property": "slack_bot_token",
                              },
                          }],
                      },
                  }
              if surface == "github-webhook":
                  return {
                      "apiVersion": "external-secrets.io/v1beta1",
                      "kind": "ExternalSecret",
                      "metadata": {
                          "name": f"{agent_name}-github",
                          "namespace": namespace,
                          "labels": std_labels(agent_name),
                          "annotations": annotations,
                      },
                      "spec": {
                          "refreshInterval": "1h",
                          "secretStoreRef": {"name": "vault-backend", "kind": "SecretStore"},
                          "target": {"name": f"{agent_name}-github", "creationPolicy": "Owner"},
                          "data": [
                              {"secretKey": "github_app_id",       "remoteRef": {"key": f"agents/{agent_name}", "property": "github_app_id"}},
                              {"secretKey": "github_installation_id", "remoteRef": {"key": f"agents/{agent_name}", "property": "github_installation_id"}},
                              {"secretKey": "github_private_key",  "remoteRef": {"key": f"agents/{agent_name}", "property": "github_private_key"}},
                          ],
                      },
                  }
              return None  # http, mcp — no per-agent secret

          # ---- compose() entry point ----
          def compose(req, rsp):
              xr = resource.struct_to_dict(req.observed.composite.resource)
              spec = xr["spec"]
              meta = xr["metadata"]
              name = meta["name"]
              namespace = meta["namespace"]
              xr_annotations = meta.get("annotations", {})
              base_domain = xr_annotations.get(
                  "platform.arigsela.com/base-domain",
                  DEFAULT_BASE_DOMAIN,
              )

              skills = spec["skills"]
              skill_names = [parse_skill_name(s["ref"]) for s in skills]
              # de-dup while preserving order
              seen = set()
              unique_skill_names = []
              for sn in skill_names:
                  if sn not in seen:
                      seen.add(sn)
                      unique_skill_names.append(sn)
              skill_ref_by_name = {parse_skill_name(s["ref"]): s["ref"] for s in skills}

              # 1) ServiceAccount
              resource.update(
                  rsp.desired.resources["serviceaccount"],
                  make_service_account(name, namespace,
                                       std_annotations(xr_annotations, "serviceaccount")),
              )

              # 2) ClusterRoleBindings (one per unique skill)
              for sn in unique_skill_names:
                  resource.update(
                      rsp.desired.resources[f"crb-{sn}"],
                      make_cluster_role_binding(name, namespace, sn,
                                                std_annotations(xr_annotations, f"crb-{sn}")),
                  )

              # 3) ToolServers (one per unique skill)
              for sn in unique_skill_names:
                  resource.update(
                      rsp.desired.resources[f"toolserver-{sn}"],
                      make_tool_server(sn, skill_ref_by_name[sn], namespace,
                                       std_annotations(xr_annotations, f"toolserver-{sn}")),
                  )

              # 4) kagent Agent CR
              resource.update(
                  rsp.desired.resources["agent"],
                  make_kagent_agent(name, namespace, spec["systemPrompt"],
                                    unique_skill_names,
                                    std_annotations(xr_annotations, "agent")),
              )

              # 5) Service (always)
              resource.update(
                  rsp.desired.resources["service"],
                  make_service(name, namespace,
                               std_annotations(xr_annotations, "service")),
              )

              # 6) Ingress (always)
              resource.update(
                  rsp.desired.resources["ingress"],
                  make_ingress(name, namespace,
                               std_annotations(xr_annotations, "ingress"),
                               base_domain),
              )

              # 7) ExternalSecret (only when surface needs creds)
              es = make_external_secret_for_surface(
                  name, namespace, spec["surface"],
                  std_annotations(xr_annotations, "externalsecret"),
              )
              if es is not None:
                  resource.update(
                      rsp.desired.resources["externalsecret"],
                      es,
                  )
````

- [ ] **Step 2: Validate the Composition renders correctly with `crossplane render`**

Create a fixture XAgent and a stub functions.yaml:

```bash
mkdir -p /tmp/xagent-render
cat > /tmp/xagent-render/xr.yaml <<'EOF'
apiVersion: platform.arigsela.com/v1alpha1
kind: XAgent
metadata:
  name: cluster-health
  namespace: agents
spec:
  description: "Reports cluster status"
  systemPrompt: "You are the Cluster Health Agent."
  skills:
    - ref: oci://agentregistry.agentregistry.svc/skills/kubernetes-mcp:v1
    - ref: oci://agentregistry.agentregistry.svc/skills/prometheus-mcp:v1
  surface: slack
EOF

cat > /tmp/xagent-render/functions.yaml <<'EOF'
apiVersion: pkg.crossplane.io/v1beta1
kind: Function
metadata:
  name: function-python
spec:
  package: ghcr.io/crossplane-contrib/function-python:v0.x.x  # match your installed version
EOF

crossplane render \
  /tmp/xagent-render/xr.yaml \
  base-apps/crossplane-compositions/composition-agent.yaml \
  /tmp/xagent-render/functions.yaml
```

Expected: rendered output containing ServiceAccount, two ClusterRoleBindings (kubernetes-mcp, prometheus-mcp), two ToolServers, one Agent, one Service, one Ingress, and one ExternalSecret (slack flavor).

If `crossplane render` errors on schema, fix the script and retry until clean output.

- [ ] **Step 3: Commit**

```bash
git add base-apps/crossplane-compositions/composition-agent.yaml
git commit -m "feat(crossplane): Composition for XAgent (function-python) (Phase 1)"
```

After ArgoCD sync: `kubectl get composition agent` should show as established.

---

## Task 1.5: Hand-write the Cluster Health XAgent

**Files:**
- Create: `base-apps/agents/cluster-health/agent.yaml`
- Create: `base-apps/agents/cluster-health/catalog-info.yaml`

The Cluster Health Agent is the first XAgent. Hand-written for Phase 1 (Backstage's Software Template that scaffolds these comes in Phase 3).

- [ ] **Step 1: Write the XAgent**

`base-apps/agents/cluster-health/agent.yaml`:
```yaml
apiVersion: platform.arigsela.com/v1alpha1
kind: XAgent
metadata:
  name: cluster-health
  namespace: agents
  annotations:
    platform.arigsela.com/base-domain: "<base-domain>"  # replace
spec:
  description: "Reports cluster/namespace status: pod readiness, events, recent deploys, top noisy pods. Read-only."
  systemPrompt: |
    You are the Cluster Health Agent. On request, gather for the named namespace
    (or cluster-wide if none specified):
      - Pod readiness counts
      - Events in the last hour, grouped by reason
      - Deployments in the last 24h
      - Top 5 pods by CPU and by memory
    Respond with brief, skimmable Markdown. Lead with anything actionable
    (CrashLoopBackOff, OOMKilled, ImagePullBackOff). If everything is normal,
    say so plainly. You are read-only — do not take any action.
  skills:
    - ref: oci://agentregistry.agentregistry.svc/skills/kubernetes-mcp:v1
      alias: k8s
    - ref: oci://agentregistry.agentregistry.svc/skills/prometheus-mcp:v1
      alias: prom
  surface: slack
```

(Phase 1 sets `surface: slack` so the right ExternalSecret is rendered, but the Slack adapter itself doesn't exist yet — Phase 2 wires that. The agent is invokable via curl in the meantime.)

- [ ] **Step 2: Pre-stage the (unused-yet) Slack bot token in Vault**

```bash
vault kv put k8s-secrets/agents/cluster-health \
  slack_bot_token="placeholder-set-in-phase-2"
```

(Phase 2 replaces the placeholder with the real token after the Slack app is configured.)

- [ ] **Step 3: Write the Backstage Component manifest (anticipates Phase 3)**

`base-apps/agents/cluster-health/catalog-info.yaml`:
```yaml
apiVersion: backstage.io/v1alpha1
kind: Component
metadata:
  name: cluster-health-agent
  description: "Reports cluster/namespace status — read-only."
  annotations:
    backstage.io/kubernetes-id: cluster-health
    kagent.dev/agent-name: cluster-health
    langfuse.platform.arigsela.com/project: golden-poc
    agent.platform.arigsela.com/try-url: https://cluster-health.<base-domain>/v1/messages
spec:
  type: agent
  lifecycle: experimental
  owner: platform-team
```

- [ ] **Step 4: Commit**

```bash
git add base-apps/agents/cluster-health/
git commit -m "feat(agent): cluster-health XAgent — first agent end-to-end (Phase 1)"
```

After ArgoCD sync: `kubectl get xagent cluster-health -n agents` should show as Synced/Ready.

- [ ] **Step 5: Verify all child resources rendered**

```bash
kubectl get sa,toolserver,agent,service,ingress,externalsecret,clusterrolebinding -n agents \
  -l app.kubernetes.io/name=cluster-health
```

Expected: `agent-cluster-health` ServiceAccount; two ToolServers (kubernetes-mcp, prometheus-mcp); kagent Agent named `cluster-health`; Service `cluster-health`; Ingress `cluster-health`; ExternalSecret `cluster-health-slack`.

For ClusterRoleBindings (cluster-scoped, no namespace flag):
```bash
kubectl get clusterrolebinding | grep "agent-cluster-health-skill-"
# Expected: two bindings (kubernetes-mcp, prometheus-mcp)
```

---

## Task 1.6: Smoke-test the Cluster Health Agent via HTTP

**Files:** None (verification-only)

- [ ] **Step 1: Wait for the kagent Agent pod to be running**

```bash
kubectl get pods -n agents -l kagent.dev/agent-name=cluster-health
# Expected: pod Running and Ready
```

If the pod is not running: `kubectl describe pod -n agents -l kagent.dev/agent-name=cluster-health` and look for image pull errors (likely an issue with the ToolServer OCI ref → adjust per Phase 0 preflight bucket).

- [ ] **Step 2: Invoke via the agent's Service (in-cluster)**

```bash
kubectl run curl-test --rm -i --image=curlimages/curl --restart=Never --quiet -- \
  curl -sS -X POST http://cluster-health.agents.svc.cluster.local/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role":"user","content":"What is the status of the agents namespace?"}]
  }' | head -100
```

Expected: a JSON response with the agent's structured Markdown analysis of the `agents` namespace (running pods, events, etc.).

If the request times out: kagent runtime probably can't reach the ToolServers. Check kagent controller logs: `kubectl logs -n kagent deploy/kagent-controller --tail=100 | grep -i toolserver`.

- [ ] **Step 3: Verify the LLM call traversed agentgateway**

```bash
kubectl logs -n agentgateway -l app=agentgateway --tail=10 | grep -E "claude-|messages"
# Expected: a recent log line for the message we just sent
```

- [ ] **Step 4: Verify the trace appears in Langfuse**

Open `https://langfuse.<base-domain>` → project `golden-poc` → Traces. Expected: a new trace within 30 seconds, tagged with `cluster-health` (or with the agent name visible in the trace metadata).

If no trace: check kagent controller logs for OTLP export errors; revisit Phase 0 Task 0.13.

---

## Task 1.7: Phase 1 acceptance + handoff to Phase 2

**Files:**
- Modify: `docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md` (append Phase 1 status)

- [ ] **Step 1: Run all Phase 1 verification one more time**

```bash
kubectl get xagent cluster-health -n agents -o jsonpath='{.status.conditions}' | jq
# Expected: Ready=True, Synced=True
```

Plus: invoke the agent via curl one more time and confirm a clean Markdown response.

- [ ] **Step 2: Append status to preflight-results.md**

```markdown
## Phase 1 — Status

- [x] `agents` namespace + SecretStore created.
- [x] Per-skill ClusterRoles defined (`agent-skill-kubernetes-mcp`, `agent-skill-prometheus-mcp`, `agent-skill-github-mcp`, `agent-skill-k8s-yaml-lint`).
- [x] XAgent XRD established (`xagents.platform.arigsela.com`).
- [x] Composition `agent` established. Renders 7 resource types; verified via `crossplane render`.
- [x] Cluster Health XAgent reconciles all 7 child resources.
- [x] curl smoke test against `https://cluster-health.<base-domain>` returns LLM response.
- [x] Trace visible in Langfuse for the smoke test.

**Phase 2 ready to start.**
```

- [ ] **Step 3: Final commit**

```bash
git add docs/superpowers/plans/2026-05-03-phase-0-preflight-results.md
git commit -m "docs(golden-poc): Phase 1 verification complete"
```

Phase 1 complete. The platform spine works end-to-end without Backstage or surface adapters. Phase 2 layers on Slack and GitHub webhook adapters plus the PR Review agent.
