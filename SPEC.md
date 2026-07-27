# SPEC

## §G GOAL
k3s cluster 1.33.5 → v1.36.2+k3s1; platform components current & in-matrix first; ⊥ data loss.

## §C CONSTRAINTS
- GitOps only. ∀ change → git commit → Argo CD sync. ⊥ direct `kubectl apply` (CLAUDE.md).
- cluster = 3 node k3s. `k3s-control-01` server, `k3s-worker-01` + `k3s-worker-02` agents. Ubuntu 24.04.3, containerd 2.1.4-k3s1.
- server args = `server --disable traefik --write-kubeconfig-mode 644`. ⊥ `--cluster-init` ∴ SQLite datastore, ⊥ embedded etcd.
- ⊥ `k3s etcd-snapshot` (SQLite ∉ etcd). backup = fs copy `/var/lib/rancher/k3s/`.
- k3s minor upgrade migrates DB one-way ∴ downgrade ⊥ supported. rollback = fs restore + reinstall @ prior `INSTALL_K3S_VERSION`.
- 1 server ∴ ∀ control-plane hop → outage of API + Vault + CNPG. accepted, bounded @ §V.9, §V.14.
- ⊥ HA conversion ∈ this spec. 3-server embedded etcd = follow-on §T.24.
- k8s skew ! ≤ ±1 minor ∴ walk 1.33 → 1.34 → 1.35 → 1.36. ⊥ skip minor.
- mechanism = system-upgrade-controller. Plan CRs ∈ `base-apps/`.
- 92 workloads ∈ 45 ns.
- CNPG `postgresql/postgresql-cluster` instances=1 ∴ ⊥ replica ∴ restart = data-path outage.
- `local-path` = only SC & default, reclaim=`Delete`, 21 PVC ∀ on it. PV `nodeAffinity` hard-pins pod → node ∴ ⊥ reschedule on drain.
- `vault-0` + `postgresql-cluster-1` ∈ `k3s-control-01`, PV pinned there ∴ control-plane hop = secrets + DB outage, ⊥ evacuable w/o PV migration.
- ∀ Argo app `prune: true` + `selfHeal: true` ∴ prune of PVC → PV Delete → data gone.
- `kube-system/ingress-nginx` = HelmChart CR (`base-apps/nginx-ingress/`) reconciled by k3s embedded helm-controller ∴ helm-controller upgraded by ∀ hop.
- Vault in-cluster ∴ ESO break → ∀ app secret resolution stops. ESO ! healthy before ∀ hop.
- Istio = ambient (istiod + `ztunnel` DS + `istio-cni-node` DS) ∴ node-level dataplane, hop risk ≥ sidecar.
- k8s 1.33 EOL 2026-06-28 ∴ cluster currently unsupported.

## §I INTERFACES
- file: `base-apps/system-upgrade-controller.yaml` → Argo CD Application
- file: `base-apps/system-upgrade-controller/*.yaml` → SUC deploy + RBAC + CRD
- file: `base-apps/system-upgrade-controller/plan-server.yaml` → Plan CR, control-plane
- file: `base-apps/system-upgrade-controller/plan-agent.yaml` → Plan CR, workers
- node-label: `k3s-upgrade=true` → gates Plan `nodeSelector`
- cmd: `scripts/k3s-backup.sh --mode cold\|online --dest <dir\|s3://>` → archives `server/` ONLY (⊥ `storage/` = local-path PV data, ⊥ `agent/`, ⊥ `kine.sock`) → sha256 → off-cluster. cold ! k3s stopped; online = `sqlite3 .backup` + excludes live db file set
- cmd: `scripts/k3s-restore.sh --artifact <a> --expect-version <v> [--skip-service] [--verify-cmd C]` → checksum gate → prior tree moved aside (⊥ destroy evidence) → promote snapshot → reinstall pinned `INSTALL_K3S_VERSION` → ! prove API @ expected version
- cmd: `scripts/pg-backup.sh --dest <dir\|s3://> --all \| --database <db>` → `pg_dumpall` (globals+roles) \| `pg_dump` via CNPG primary → sha256 → off-cluster. ⊥ default db name (cluster hosts `n8n`+`chores_tracker`). operator-independent ∴ survives §T.9
- backup: CNPG barman → `s3://mysql-backups-asela-cluster/postgresql/`, `ScheduledBackup postgresql-daily-backup` 02:00 UTC, 30d retention. healthy & archiving, restore ⊥ proven (§T.34)
- store: off-cluster artifact target. `s3://mysql-backups-asela-cluster/` ∃ already ∴ add `k3s/` + `pg/` prefixes. ⊥ new bucket needed
- test: `tests/k3s-upgrade/` = pytest oracle ∀ §V.1,6,10,15,16,21. drills boot real k3s + postgres in docker. CI job `k3s-upgrade-scripts` ∈ `.github/workflows/validate.yaml`
- doc: `docs/plans/k3s-1.36-upgrade-plan.md` → runbook + outage comms
- doc: `docs/plans/k3s-1.36-api-scan.md` → §T.4 report. built-in APIs clean 1.34/1.35/1.36; pluto ⊥ see CRD removals ∴ ESO `v1beta1` = real gap

## §V INVARIANTS
V1: ∀ hop → verified fs backup `/var/lib/rancher/k3s/` ∃ & restore drill passed before hop starts.
V2: ∀ hop → ∀ platform component ∈ its supported k8s matrix @ target minor. ⊥ hop otherwise.
V3: ∀ hop → 1 minor step. ⊥ skip.
V4: control-plane hops before workers. ∀ worker hop after control-plane Ready.
V5: ∀ hop → post-gate: ∀ node Ready & ∀ Argo CD app Synced+Healthy before next hop.
V6: CNPG `postgresql-cluster` → pg_dump verified & restorable before ∀ hop.
V7: ⊥ rc/pre-release image in cluster. ! GA tag.
V8: ∀ component upgrade → own commit, own sync, own rollback point. ⊥ batch.
V9: outage window announced & ≤ 15min per control-plane hop.
V10: ∀ hop → removed-API scan clean vs target minor.
V11: ∀ hop → ESO healthy & ∀ ExternalSecret `SecretSynced=True`.
V12: Istio ambient upgrade → revision/canary. ⊥ in-place istiod replace.
V13: ⊥ 2 phases concurrent. component remediation fully green before walk starts.
V14: ∀ control-plane hop → outage = API + Vault + CNPG. ⊥ model as API-only. all 3 restored & verified before hop declared done.
V15: k3s backup ! quiesced — stop k3s \| `sqlite3 .backup` before copy. ⊥ archive live datastore file SET: `state.db` + `state.db-wal` + `state.db-shm`. kine = WAL mode ∴ stale sidecar replays over snapshot → silent corruption.
V16: restore drill ! prove API returns @ prior version from artifact. unproven artifact ∉ backup.
V17: control-plane hop = manual, operator-driven, console access ∃. SUC scope = agents only.
V18: ∀ hop window → Argo auto-sync paused ∀ app w/ PVC. resume only after §V.5 green.
V19: ⊥ PVC ∈ prune scope, ∀ time. local-path reclaim=Delete ∴ prune = data loss.
V20: Istio upgrade → new Application per revision, old revision live until `ztunnel`+`istio-cni-node` green ∀ node. ⊥ mutate `targetRevision` in place.
V21: ∀ backup artifact (k3s, pg_dump, vault) ! stored ∉ cluster. artifact on `local-path` ∉ backup.
V22: ∀ hop → helm-controller reconcile of `kube-system/ingress-nginx` verified & ingress reachable post-hop.
V23: ESO upgrade ! staged. ⊥ jump `v0.11.0` → ≥`0.17.0` — `0.17.0` REMOVES `external-secrets.io/v1beta1` & 59 manifest + CRD storage version ∀ @ `v1beta1`. path: `0.16.2` (serves both) → ∀ manifest `v1` → storage migrated → ≥`0.17.0` → `2.x`. ∀ stage own gate. §T row order ≠ exec order ∴ ! follow `after §T.n` cites.
V24: ∀ ESO stage → Argo auto-sync paused ∀ app w/ ExternalSecret until manifests @ `v1` committed. `0.16.x` webhook auto-converts `v1beta1`→`v1` in-cluster → drift vs git ∴ `selfHeal` fights webhook.
V25: barman restore ! proven (scratch Cluster ← `bootstrap.recovery`) BEFORE §T.9. barman restore ⊥ operator ∴ ⊥ verifiable after operator upgrade breaks.

## §T TASKS
id|status|task|cites
T1|x|write `scripts/k3s-backup.sh` — quiesce k3s, `sqlite3 .backup`, tar, checksum, ship off-cluster|V1,V15,V21,I.cmd
T2|x|write `scripts/k3s-restore.sh` + drill on throwaway VM: artifact → API up @ prior version|V1,V16,I.cmd
T3|x|`scripts/pg-backup.sh` pg_dump `postgresql-cluster` → off-cluster, verify restore|V6,V21,I.cmd
T4|x|deprecated/removed-API scan vs 1.34,1.35,1.36 (`pluto` \| `kubent`)|V10
T5|.|Argo CD `v3.5.0-rc2` → v3.5.x GA|V7,V8
T6|.|ESO stage 1: `v0.11.0` → `v0.16.2` (serves `v1beta1`+`v1`). ⊥ proceed past w/o §T.31|V11,V8,V23,V24
T7|.|verify ∀ ExternalSecret resync post-ESO, Vault k8s-auth roles intact|V11
T8|.|Vault `1.18.1` → current stable. NB StatefulSet `updateStrategy: OnDelete` ∴ ! manual `delete pod vault-0` after sync|V8
T9|.|CNPG `1.24.1` → 1.29.x — CVE-2026-44477 CVSS 9.4 metrics exporter. gate §T.34 first|V6,V8,V25
T10|.|Istio `1.24.0` → 1.30.x — new Application per revision, ⊥ bump `targetRevision` in place|V12,V20,V8
T11|.|verify `ztunnel` + `istio-cni-node` DS healthy ∀ node post-Istio, then retire old revision|V12,V20
T12|.|matrix-audit rest vs 1.36: kyverno 1.17.1, argo-rollouts 1.8.3, argo-workflows 3.6.10, cert-manager 1.20.2, crossplane 2.2.1, ingress-nginx, falco, gloo|V2
T13|.|add `base-apps/system-upgrade-controller.yaml` Argo app|I.file
T14|.|add SUC manifests + RBAC + CRD (sync-wave: CRD before Plan). Plan scope = agents only|V17,I.file
T15|.|label `k3s-worker-01`,`k3s-worker-02` `k3s-upgrade=true`. ⊥ label `k3s-control-01`|V17,I.node-label
T16|.|dry-run SUC Plan on `k3s-worker-02` @ current version (no-op hop)|V4,V17
T17|.|gate §V.1,2,5,10,11,14 → MANUAL hop control-plane → `v1.34.9+k3s1`, console access ∃|V1,V2,V3,V14,V17
T18|.|SUC hop workers → `v1.34.9+k3s1`, verify §V.5|V4,V5,V17
T19|.|gate → hop → `v1.35.6+k3s1` (control-plane manual, workers SUC)|V3,V5,V17
T20|.|BLOCKER: CNPG 1.29.x supports k8s ≤1.35. ! confirm CNPG 1.30 ships 1.36 support, else hold @ 1.35|V2
T21|.|gate → hop → `v1.36.2+k3s1` (control-plane manual, workers SUC)|V2,V3,V5,V17
T22|.|confirm local kubectl v1.36.2 now in-skew|-
T23|.|update `index.md` + `docs/` topology w/ landed versions|-
T24|.|follow-on: spec 3-server embedded etcd HA conversion|-
T25|.|build pause/resume for Argo auto-sync ∀ PVC-bearing app across hop window|V18
T26|.|audit prune scope: assert ⊥ PVC prunable (Kyverno rule \| `Prune=false` annotation)|V19
T27|.|DECIDE: relocate `vault-0` + `postgresql-cluster-1` → worker before walk (PV migration) \| accept control-plane outage|V14
T28|.|provision off-cluster artifact store for k3s + pg + vault backups|V21,I.store
T29|.|verify `kube-system/ingress-nginx` helm-controller reconcile + ingress reachable post-∀-hop|V22
T30|.|define §V.9 measurement: start = k3s stop, end = ∀ Argo app Synced+Healthy|V9
T31|.|ESO stage 2: ∀ 59 manifest `external-secrets.io/v1beta1` → `v1` + CRD storage version migrated. after §T.6|V23,V24
T32|.|ESO stage 3: → ≥`0.17.0` (`v1beta1` removed upstream). after §T.31|V23,V11
T33|.|ESO stage 4: → `2.x`. after §T.32|V23,V11
T34|.|prove barman restore: scratch ns + CNPG Cluster ← `bootstrap.recovery` ← `s3://mysql-backups-asela-cluster/postgresql/`. BEFORE §T.9|V25,V6

## §B BUGS
id|date|cause|fix
B1|2026-07-27|`k3s-backup.sh` online mode excluded `state.db` only. kine runs SQLite WAL ∴ live `-wal`/`-shm` shipped beside `.backup` snapshot → SQLite replays foreign WAL over restored db. found by drill, ⊥ by review|V15
