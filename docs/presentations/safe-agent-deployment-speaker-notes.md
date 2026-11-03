# How I deploy agents safely

Five illustrated notebook infographics for a technical friend who is new to this homelab. Prepared 3 November 2026 from local repository revision `0a1beba0f8f2df3e329eec35a5deac94242103da` (28 October). This is a configuration review, not a live health or security assessment. The original reference PDFs are unchanged.

## 1. The deployment path

**Text alternative:** Reviewed agent configuration flows through CI, Argo CD, Kubernetes admission checks, an agent session and scoped tools. A session runs in a gVisor sandbox on Agent Substrate. Caller verification, human decisions and action records surround this path.

**Say:** I deploy the agent as a platform resource: its prompt, model, explicit tool list and runtime are files. Git review and CI check those files; Argo CD reconciles them. Admission checks the resources at the Kubernetes API boundary when Argo CD submits them. kagent's controller is outside the session sandbox; the agent's execution is inside it.

**Evidence:** [Git-only definitions policy](https://github.com/arigsela/kubernetes/blob/0a1beba0f8f2df3e329eec35a5deac94242103da/base-apps/admission-policies/agent-definitions-from-git.yaml), [kagent configuration guide](https://github.com/arigsela/kubernetes/blob/0a1beba0f8f2df3e329eec35a5deac94242103da/base-apps/kagent/docs.md), [Substrate guide](https://github.com/arigsela/kubernetes/blob/0a1beba0f8f2df3e329eec35a5deac94242103da/base-apps/substrate/docs.md).

**Scope:** The Git-only policy covers creates and definition-changing updates of Agent, AgentTemplate, Harness and RemoteMCPServer, with Argo CD and a documented break-glass group exempt. It is not a claim that every cluster resource or deletion is impossible outside Git. Runtime images are digest-pinned and constrained by the capability taxonomy. Both kagent and Substrate remain alpha.

## 2. Three levels of autonomy

**Text alternative:** Read agents inspect information; the implementer can create branches and pull requests; mutating operational tool bindings pause for approval. Named-tool allowlists and capability checks constrain the available calls.

**Say:** An instruction to be careful is useful, but the tool surface is the enforceable boundary. The implementer has read-only Kubernetes tools and proposal tools for GitHub; its server does not expose merge tools. Branch/PR creation is intentionally ungated. A human merge is the intended deployment gate. Operational write/destructive bindings require a human decision before execution.

**Evidence:** `base-apps/kagent/agent-templates/{implementer,restarter,k8s-reader}.yaml`, `base-apps/kagent/implementer-mcp.yaml`, `base-apps/admission-policies/agent-capability.yaml`, `base-apps/admission-policies/agent-capability-taxonomy.yaml`, `base-apps/cluster-rbac/kagent-tools.yaml`.

**Scope:** The three lanes are a simplified teaching model; the actual taxonomy also distinguishes write/admin classes. The shared Kubernetes tool server is still broadly privileged, with direct Kubernetes Secret API access excluded. This does not make logs, ConfigMaps, exec or other indirect paths incapable of exposing sensitive information. Prompt restrictions on branch names, file counts, target resources and allowed files are not equivalent to independent authorization checks. Read agents can still leak data they can read.

## 3. A fix has two different gates

**Text alternative:** A resource alert reaches the dispatcher and implementer. A limit-too-low verdict produces a fix PR for human merge, followed by GitOps sync. For configured leak or rollout-pending verdicts, the dispatcher starts a separate restarter session; its deletion call waits for human approval. A workload controller recreates the named pod. Inconclusive/contention verdicts do not produce a fix PR.

**Say:** These are two different gates and two different actions. Approving a PR and approving a runtime tool call are not interchangeable. The dispatcher never supplies the approval. The restarter is instructed to inspect ownership and delete exactly the named pod, but its underlying tool is the general `k8s_delete_resource`; the human must inspect the actual requested arguments.

**Evidence:** `base-apps/dispatcher/dispatch-table.yaml`, `base-apps/kagent/agent-templates/implementer.yaml`, `base-apps/kagent/agent-templates/restarter.yaml`.

**Scope:** The flow is illustrative configured behavior, not an incident replay. Not every alert takes this route. CPU-starved routes remediation only for rollout-pending; the memory-limit route includes leak and rollout-pending. Dispatcher configuration limits investigations to four/hour, repeat interval to six hours, open proposals to three and restart requests to two/day. These counts are kept out of the image to preserve readability.

## 4. Boundaries around a session

**Text alternative:** API callers reach an Istio waypoint that verifies Dex or Kubernetes service-account tokens and supplies a verified identity header. Sessions run in gVisor. Model calls use egress-injected credentials and a gateway with per-group daily budgets. Tool servers have their own scoped credentials.

**Say:** Caller identity, process isolation and credential placement address different risks. Egress injects a group's virtual model key; the model gateway exchanges it for the upstream provider key. The model key is not stored in the agent sandbox. Tool-server credentials are separate and sourced through Vault and External Secrets.

**Evidence:** `base-apps/kagent/request-authentication.yaml`, `authorization-policy.yaml`, `network-policy-controller.yaml`, `base-apps/substrate/docs.md`, `base-apps/model-gateway/docs.md`, `base-apps/model-gateway/budgets.yaml`.

**Limits that matter:** The UI still uses a shared controller identity. The controller is deliberately in insecure mode behind the mesh. The docs record an encoded query-parameter identity override gap and direct controller reachability from permitted in-cluster namespaces; there is no mesh-wide STRICT peer authentication. These controls therefore do not establish complete per-user isolation. Substrate's host components also have substantial host access. A sandbox is a boundary, not proof of immunity to escape.

**Budget scope:** The checked budget configuration enables the gateway for all listed groups. Calls are checked before running and charged afterward; in-flight calls can overshoot a budget. This is not an exact invoice cap. The gateway refuses subsequent model calls for the exhausted group until the daily window resets at 00:00 UTC.

## 5. Evaluate, observe and stop automation

**Text alternative:** After an agent definition changes, a PostSync evaluation checks expected and forbidden tool calls, adversarial refusals and fake-secret leakage. A regression hold blocks new dispatcher and Slack sessions. Action records support audit and alerts; model budgets constrain ongoing consumption.

**Say:** I test what the agent tries to do, not just whether its answer sounds right. The evaluation runner rejects pending approval requests rather than authorizing them. Canary values are deliberately fake credentials; emitting one is a hard failure. Audit compares recorded mutating calls with approval decisions, and missing audit data is configured to alert.

**Evidence:** `base-apps/kagent/docs.md` (Evaluation gate; Observability and audit), `scripts/eval_gate.py`, `scripts/eval_release.py`, `scripts/agent-audit.py`, `base-apps/kagent/eval-gate-job.yaml`, `tests/eval-corpus/`.

**Scope:** Evaluation runs after sync, not before deployment. Regression holds affect new sessions through the dispatcher and Slack bridge; CLI/UI remain available, existing sessions are not revoked, and a missing/unreadable hold file is unknown rather than a hold. A regression does not roll back the deployment. A clean evaluation cannot prove that every future prompt is safe. Audit redaction is best-effort and depends on working collection.

## What changed since the supplied decks

The 12 October agents-as-a-service deck described unauthenticated caller-supplied API identity. The repository now configures token verification at the waypoint, while retaining the specific residual gaps above. The whole-picture deck already introduced verified callers, evaluations and proposals on 14 October. This update adds a clearer separation of proposal versus operational approval, Git-only agent definitions, tool-server allowlists, per-group budget enforcement, and the newer read-only bug-investigator example. It avoids inheriting outdated counts, agent names and architecture claims.

## Delivery and provenance

The PDF consists of raster infographic pages, not editable diagrams or a tagged/selectable-text PDF. These notes provide reusable text, qualifications and source references. Source paths without links are relative to the pinned repository revision above. No cluster changes, agent sessions, deployment tests or live approval actions were performed for this presentation.

Image delivery: five PNGs at 1672 x 941 pixels each, placed without cropping or stretching on 16:9 PDF pages.
