---
type: "Kubernetes App Guide"
title: "Agent Substrate"
description: "Agent Substrate 0.5.0-alpha2: the gVisor actor runtime kagent 1.0 runs agents on (four Argo apps, Vault-backed CA pools, rustfs snapshots)"
app: substrate
catalog_entity: substrate
kind: docs
namespace: ate-system
last_reviewed: 2026-10-11
status: draft
tags: [substrate, kagent, gvisor, actors, sandbox]
sources:
  - base-apps/substrate-crds.yaml
  - base-apps/substrate-config.yaml
  - base-apps/substrate-podcert.yaml
  - base-apps/substrate.yaml
  - base-apps/substrate/external-secret-ca-pools.yaml
  - base-apps/substrate/external-secret-postgres.yaml
  - base-apps/substrate/configmap-ate-api-authentication.yaml
  - base-apps/substrate/networkpolicy-rustfs.yaml
  - base-apps/substrate/rustfs-pvc-placement.yaml
  - base-apps/substrate/ca-expiry-cronjob.yaml
  - base-apps/postgresql/init-substrate-db.yaml
  - base-apps/postgresql/init-kagent-v1-db.yaml
  - scripts/provision-kagent-1-0-vault.sh
  - docs/plans/kagent-1-0-on-k8s-1-37-implementation-plan.md
  - id: kagent-install
    resource: https://github.com/kagent-dev/kagent/blob/v1.0.0-alpha11/go/core/cli/internal/commands/substrate_init.go
    title: kagent CLI Substrate prerequisites (substrate_init.go)
  - id: substrate-postgres
    resource: https://github.com/kagent-dev/substrate/blob/v0.5.0-alpha2/docs/postgres.md
    title: Substrate database configuration
---

# Agent Substrate

## What it is
Agent Substrate (kagent-dev's fork of agent-substrate, `0.5.0-alpha2`) is the runtime kagent
1.0 runs its agents on. Each agent session is an **actor**: a process in a gVisor sandbox inside
a pre-warmed **worker** pod. Idle actors are snapshotted and suspended, freeing the worker; the
next request restores the snapshot, possibly on another node. kagent 1.0 cannot run without it.
It is installed on its own first (plan Phase 4, a go/no-go) while kagent 0.10.3 is still running;
kagent 1.0 starts using it at the cutover (plan Phase 5).

## Architecture & data flow
Four Argo apps, created in this order by the master app (sync waves), mirroring
`kagent install`:

| App | Wave | Holds |
|---|---|---|
| `substrate-crds` | -4 | CRDs `ate.dev`: WorkerPool, SandboxConfig, CSIDriverConfig |
| `substrate-config` | -3 | this directory: both namespaces, the seven CA/JWT pool Secrets and two Postgres connection Secrets (ESO from Vault), `ate-api-authentication`, the rustfs NetworkPolicy and placement policy, the CA-expiry check |
| `substrate-podcert` | -2 | `podcertificate-controller` in `podcertificate-controller-system`: signs PodCertificateRequests for `servicedns`, `podidentity` and `postgres` `.podcert.ate.dev` and publishes their ClusterTrustBundles |
| `substrate` | -1 | release `substrate` in `ate-system`: ate-api-server (2 replicas), ate-controller, the atelet DaemonSet, atenet-router, atenet-egress, k8s-credential-provider, rustfs, the `gvisor-default` SandboxConfig |

Waves only order Application creation; each app converges on its own (a pod whose Secret is not
there yet waits in ContainerCreating).

Request path once kagent 1.0 runs: the kagent controller calls ate-api-server at
`dns:///api.ate-system.svc:443` (gRPC, TLS with pod certificates) to create ActorTemplates and
actors, and reaches running actors through `http://atenet-router.ate-system.svc:80`. These names
exist only because the release is called `substrate`. kagent's WorkerPool `kagent-default` (kagent
chart, namespace `kagent`) makes ate-controller run a Deployment of worker pods
(`ghcr.io/kagent-dev/substrate/ateom-gvisor`) in `kagent`. The atelet on each node restores
snapshots into those workers and uploads them on suspend. Egress from a sandbox goes through
atenet-egress, which injects credentials (model API keys referenced as `ate-secret://`) via the
k8s credential provider. The provider is default-deny per atespace: `credentialProvider.namespacePolicies`
in `base-apps/substrate.yaml` lets atespace `kagent` read Secrets in namespace `kagent`, or every
model call returns 403.

State:
- **Postgres**: database `substrate` on the shared pgvector Postgres (`postgresql.postgresql.svc`),
  created by `base-apps/postgresql/init-substrate-db.yaml` with upstream's own setup SQL: NOLOGIN
  roles `substrate_owner` / `substrate_readwrite`, schema `substrate`, logins
  `substrate_owner_user` / `substrate_readwrite_user`, password auth (no TLS on that Postgres).[^substrate-postgres]
  ateapi runs its own migrations through the owner connection.
- **Snapshots**: the chart's bundled rustfs, an S3 API at `http://rustfs.ate-system.svc:9000`
  (path-style, region us-east-1), bucket `ate-snapshots` (created by the chart's
  `rustfs-bucket-init` Job). A Harness's `substrate.snapshotPolicy.location` must be
  `s3://ate-snapshots/<prefix>/`; kagent's agents use `s3://ate-snapshots/kagent/`. The scheme is not
  interpreted (the bucket is the URI host), but `s3://` matches the backend. Volume: 1Gi PVC
  `rustfs-data` on k3s-worker-02. Sessions are disposable (kagent's memory lives in Postgres);
  AWS S3 replaces this once ate-api-server can take AWS credentials (kagent-dev/substrate#64).

## Where config lives
- `base-apps/substrate{-crds,-config,-podcert,}.yaml`: the four Applications; chart values are in
  `substrate.yaml` and `substrate-podcert.yaml`.
- Vault, all written by `scripts/provision-kagent-1-0-vault.sh` (run it before the first sync):

  | Vault key | Properties | Read by |
  |---|---|---|
  | `k8s-secrets/substrate-cas` | `<pool>`, `<pool>-crt`, `<pool>-key` for actor-id-ca-pool, egress-mitm-ca-pool, service-dns-ca-pool, pod-identity-ca-pool, postgres-ca-pool; `actor-id-jwt-pool` | role `substrate` (SA `eso-substrate`) |
  | `k8s-secrets/substrate-db` | `db-name`, `owner-user`, `owner-password`, `readwrite-user`, `readwrite-password` | role `substrate` |
  | `k8s-secrets/kagent-db` | kagent 1.0's `v1-db-name`, `v1-user`, `v1-password` next to 0.10's properties | role `kagent-db` (unchanged) |

  Role `substrate` is bound to ServiceAccount `eso-substrate` in `ate-system`,
  `podcertificate-controller-system` and `postgresql`; its policy reads only those two keys.
- The CA pool Secrets are rebuilt by `external-secret-ca-pools.yaml` in exactly the shape
  `kagent install` creates (key names, Secret types, key algorithms).[^kagent-install] The script
  generates them with openssl because neither `kagent install` nor `kubectl ate admin make-ca-pool`
  can print them instead of writing them to a cluster; its output was checked by parsing it with
  Substrate's own `internal/localca` and `internal/localjwtauthority` code.
- `configmap-ate-api-authentication.yaml`: ate-api-server accepts ServiceAccount tokens for the
  audience `api.ate-system.svc` from the cluster's issuer, `https://kubernetes.default.svc.cluster.local`.

## Security profile
Nothing runs privileged, but Substrate is not a hardened workload:
- atelet (DaemonSet) runs as root with `DAC_OVERRIDE`, mounts
  `/var/lib/ate`, `/var/lib/kubelet/{plugins,device-plugins}` and `/dev`, and binds hostPorts
  8085 (gRPC) and 9090 (metrics).
- Worker pods (in `kagent`) run as UID 0 with 13 capabilities and seccomp and AppArmor Unconfined:
  gVisor's own sandbox is the isolation boundary.
- k8s-credential-provider has cluster-wide `get` on Secrets, limited by its namespace policy.
- The repo's Deny admission policies match only `agents` and `externalsecrets`, so nothing blocks
  this. The audit-only hygiene policies exclude `ate-system` and
  `podcertificate-controller-system`; Falco's API-contact clause excludes them too. The chart's own
  ValidatingAdmissionPolicy `sandboxconfig-assets` (Deny, SandboxConfig shape) carries no Kyverno
  reporting label; it is a schema check, not a hygiene audit.
- rustfs runs with the chart's default credentials (the chart cannot read them from a Secret, and
  this repo is public); `networkpolicy-rustfs.yaml` admits only atelet, ate-api-server and the
  bucket-init Job.

## Gotchas & tribal knowledge
- **CAs expire after 365 days and nothing renews them** (substrate#1931). The CronJob
  `substrate-ca-expiry` and the Grafana rule `substrate-ca-expiry` warn 30 days ahead (and when the
  check stops reporting). Rotation needs restarts: `runbook.md`.
- **Version lockstep.** kagent 1.0.0-alpha11 is built against Substrate v0.5.0-alpha2. Renovate
  groups the five charts and waits for a Dependency Dashboard tick; every alpha bump so far has
  needed clean databases.
- **Images.** podcertcontroller, ateom-gvisor and the third-party images are pinned by digest. The
  four Substrate components (ateapi, atecontroller, atelet, kubernetes-secrets) run by the tag
  `v0.5.0-alpha2`: the chart builds all four from one shared `image.tag`. Their digests as of
  2026-10-11 are in the header of `base-apps/substrate.yaml`.
- **`prune: false`** on the `substrate` app protects the rustfs PVC (SPEC §V.39). An object a
  chart bump stops rendering stays until deleted by hand.
- **rustfs placement.** The chart cannot pin the PVC, so the MutatingAdmissionPolicy in
  `rustfs-pvc-placement.yaml` pre-selects k3s-worker-02 (most free disk) when the claim is
  created. local-path does not enforce the 1Gi size.
- **Two nodes, not three.** atelet tolerates only `ate.dev/sandboxClass` taints, so it does not
  run on k3s-control-01 (`node-role.kubernetes.io/control-plane:NoSchedule`), and neither do
  worker pods. Actors run on k3s-worker-01 and k3s-worker-02 only; the chart has no value to add a
  toleration, and none is wanted on the single control plane.
- **Reboots.** A worker pod that survives a reboot keeps its name but gets a new IP its actors
  never learn (substrate#1837); `ansible/playbooks/patch.yml` deletes a rebooted node's worker
  pods. A drain evicts workers and may crash an agent turn in flight (substrate#1526). Resume is
  tied to a node with a ~6 s deadline (substrate#1761), and a snapshot only restores on a node with
  the same CPU flags (substrate#1657): `patch.yml` refuses to run if the k3s VMs' flags differ. Single-replica components (ate-controller, atenet-router,
  podcertificate-controller, rustfs) make agents briefly unavailable while their node drains.
- **Connections.** The read/write string carries `pool_max_conns=5`: two ate-api-server replicas
  share a Postgres (`max_connections` 100) with n8n and kagent.
- **No feature gates.** ClusterTrustBundle, ClusterTrustBundleProjection and PodCertificateRequest
  are GA and on by default on 1.37 (SPEC §R.43); setting their gates would lock in at 1.38.

[^substrate-postgres]: Substrate database configuration
[^kagent-install]: kagent CLI Substrate prerequisites (substrate_init.go)
