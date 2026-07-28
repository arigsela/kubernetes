# SPEC

## §G GOAL
k3s cluster 1.33.5 → **`v1.35.6+k3s1`** (§R.18: 4 components cap @ 1.35, ingress-nginx TERMINAL ∴ 1.36 ⊥ reachable). 1.36 = FOLLOW-ON, gated on replacing ingress-nginx (§T.43). platform components @ matrix ceiling per minor, INTERLEAVED w/ walk where matrix demands (§V.13); ESO DEFERRED past the walk (§V.48); ⊥ data loss.

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
- Vault seal = `awskms` (`alias/vault-auto-unseal`, `terraform/roots/asela-cluster/vault-kms.tf`). `/vault/data` bytes = CIPHERTEXT ∴ ∀ fs backup undecryptable w/o KMS key. Shamir recovery keys govern recovery ops, ⊥ storage decryption.
- Istio = ambient (istiod + `ztunnel` DS + `istio-cni-node` DS) ∴ node-level dataplane, hop risk ≥ sidecar.
- k8s 1.33 EOL 2026-06-28 ∴ cluster currently unsupported.

## §I INTERFACES
- file: `base-apps/system-upgrade-controller.yaml` → Argo CD Application
- file: `base-apps/system-upgrade-controller/*.yaml` → SUC deploy + RBAC + CRD
- file: `base-apps/system-upgrade-controller/plan-agent.yaml` → Plan CR, workers ONLY
- file: ⊥ `plan-server.yaml`. control-plane hop = manual (§V.17) ∴ SUC ⊥ ever target `k3s-control-01`
- cmd: `scripts/vault-backup.sh --dest <dir\|s3://> [--mode cold\|online] [--argo-app NAME]` → `vault-0` `file` storage → sha256 → off-cluster. cold = suspend Argo app (§V.33) → scale sts 0 → helper pod w/ control-plane toleration (§V.34) → scale back → ! verify unseal. online ! `--allow-inconsistent` & brands artifact `INCONSISTENT`
- cmd: `scripts/vault-restore.sh --artifact <a> --data-dir <d> [--verify-cmd C] [--accept-inconsistent]` → checksum gate → refuse `INCONSISTENT` w/o ack → restore → ! prove unseal + secret read
- node-label: `k3s-upgrade=true` → gates Plan `nodeSelector`
- cmd: `scripts/k3s-backup.sh --mode cold\|online --dest <dir\|s3://>` → archives `server/` ONLY (⊥ `storage/` = local-path PV data, ⊥ `agent/`, ⊥ `kine.sock`) → sha256 → off-cluster. cold ! k3s stopped; online = `sqlite3 .backup` + excludes live db file set
- cmd: `scripts/k3s-restore.sh --artifact <a> --expect-version <v> [--skip-service] [--verify-cmd C]` → checksum gate → prior tree moved aside (⊥ destroy evidence) → promote snapshot → reinstall pinned `INSTALL_K3S_VERSION` → ! prove API @ expected version
- cmd: `scripts/pg-backup.sh --dest <dir\|s3://> --all \| --database <db>` → `pg_dumpall` (globals+roles) \| `pg_dump` via CNPG primary → sha256 → off-cluster. ⊥ default db name (cluster hosts `n8n`+`chores_tracker`). operator-independent ∴ survives §T.9
- backup: CNPG barman → `s3://mysql-backups-asela-cluster/postgresql/`, `ScheduledBackup postgresql-daily-backup` 02:00 UTC, 30d retention. healthy & archiving, restore ⊥ proven (§T.34)
- store: off-cluster artifact target. `s3://mysql-backups-asela-cluster/` ∃ already ∴ add `k3s/` + `pg/` prefixes. ⊥ new bucket needed
- test: `tests/k3s-upgrade/` = pytest oracle ∀ §V.1,6,10,15,16,21,28. drills boot real k3s + postgres + vault in docker. CI job `k3s-upgrade-scripts` ∈ `.github/workflows/validate.yaml`
- doc: `docs/plans/k3s-1.36-upgrade-plan.md` → runbook + outage comms
- doc: `docs/plans/k3s-1.36-api-scan.md` → §T.4 report. built-in APIs clean 1.34/1.35/1.36; pluto ⊥ see CRD removals ∴ ESO `v1beta1` = real gap

## §R RESEARCH
id|topic|finding|src
R1|ESO v1beta1 removal|`v0.17.0` stops serving `v1beta1`. ! migrate manifests → `v1` BEFORE `0.16`→`0.17`. change = drop `beta1` only|github.com/external-secrets/external-secrets/releases/tag/v0.17.0
R2|ESO last v1beta1 build|`v0.16.2` = last serving `v1beta1` — chartmuseum chart ONLY, ⊥ OCI variant. repo @ `https://charts.external-secrets.io` ∴ correct source already|github.com/external-secrets/external-secrets/issues/5478
R3|CRD storage version|live `externalsecrets`+`secretstores`+`clustersecretstores` ∀ `status.storedVersions=["v1beta1"]`, conversion=`Webhook`. k8s ⊥ drop version ∈ storedVersions ∴ ! rewrite ∀ stored object as `v1` + prune storedVersions before `0.17.0`. git ≠ etcd|kubectl, local truth 2026-07-27
R4|ESO ↔ k8s matrix|`≤0.13`:1.19-1.31 / `0.15-0.18`:1.32-1.33 / `0.19`:1.33 / `0.20`:1.34 / `1.0-1.3`:1.34 / `2.0-2.6`:1.34-1.35 / `2.7`:1.35. ⊥ version supports 1.36|external-secrets.io/latest/introduction/stability-support/
R5|ESO already out-of-matrix|`0.11.x` unsupported upstream, matrix = k8s ≤1.31, cluster @ 1.33 ∴ out-of-matrix now — same posture as Istio `1.24.0`|external-secrets.io/latest/introduction/stability-support/
R6|ESO latest|`v2.8.0`. k8s 1.36 support ⊥ stated in release notes ? — ! confirm before §T.21|github.com/external-secrets/external-secrets/releases
R7|Argo drift #5478|`0.16.x` webhook rewrites stored objects `v1beta1`→`v1` → drift vs git. CLOSED 2025-11-23, resolution = migrate manifests, ⊥ a code fix ∴ pause = migration window only|github.com/external-secrets/external-secrets/issues/5478
R8|CNPG rollout triggers|doc lists `imageName`, image-catalog, pg config needing restart, `spec.resources`, PVC resize (AKS), operator update. affinity ⊥ listed — BUT EMPIRICALLY DOES restart the primary: "Primary instance is being restarted without a switchover" observed on affinity patch 2026-07-28 (§B.5). doc trigger list ⊥ exhaustive ∴ absence of mention ≠ absence of behaviour|cloudnative-pg.io/documentation/1.24/rolling_update/ + observed, local truth 2026-07-28
R9|CNPG relocate procedure|documented: cordon host node → `instances` 1→2 → operator AUTO-switchover → scale→1 (drops original) → drain old node. NB procedure NEVER changes affinity — the CORDON is what redirects the new instance|cloudnative-pg.io/documentation/1.24/kubernetes_upgrade/
R10|CNPG PDB blocks drain|`enablePDB=true` live; PDB `postgresql-cluster-primary` ALLOWED DISRUPTIONS=0 ∴ drain of `k3s-control-01` BLOCKED while single instance there. gates §T.17|kubectl, local truth 2026-07-28
R11|CNPG live affinity|`nodeSelector: workload=infrastructure` + control-plane toleration + `podAntiAffinityType: preferred` ∴ 2 instances ? co-locate on 1 node (preferred ⊥ required)|kubectl, local truth 2026-07-28
R12|`kubectl cnpg` absent|plugin ⊥ installed ∴ ⊥ `cnpg promote` manual switchover. `primaryUpdateStrategy=unsupervised` ∴ auto-switchover expected. ! install as §T.37 fallback lever|local truth 2026-07-28
R13|ingress-nginx TERMINAL|`kubernetes/ingress-nginx` repo ARCHIVED (`archived:true`, last push 2026-03-23). final release `controller-v1.15.1` 2026-03-19 = EXACTLY the version in cluster. supports k8s 1.31-1.35 ∴ ⊥ 1.36 EVER. upstream: adopt Gateway API|github.com/kubernetes/ingress-nginx via gh api
R14|Gateway API ∃ already|4 GatewayClass live & Accepted: `istio`, `gloo-gateway-v2`, `agentgateway-enterprise`(+waypoint). `istio-waypoint` Gateways serving `chores-tracker`+frontend 217d ∴ migration path ∃, ⊥ greenfield|kubectl, local truth 2026-07-28
R15|Argo CD ⊥ 3.5 GA|`v3.5.0` = rc1/rc2/rc3 ONLY (rc3 2026-07-28). latest STABLE = `v3.4.5` 2026-07-09 ∴ §V.7 satisfiable only by DOWNGRADE → 3.4 line \| wait for 3.5.0 GA|github.com/argoproj/argo-cd/releases
R16|kyverno matrix|`v1.18` = k8s 1.33-1.35 ∴ ⊥ 1.36. cluster @ `v1.17.1`, older still. kyverno = admission webhook ∴ incompat blocks ∀ pod create|kyverno.io/docs/installation/releases/
R17|cert-manager matrix|`v1.20` = k8s 1.32-1.35; `v1.21` = 1.33-1.36 ∴ REACHES 1.36 via upgrade. ⊥ blocker|cert-manager.io/docs/releases/
R18|1.36 CEILING VERDICT|4 components cap @ 1.35: CNPG(§R.4-adjacent), ESO(§R.4), ingress-nginx(§R.13, TERMINAL), kyverno(§R.16). ∴ **1.35 = the real target**. 1.36 ⊥ reachable w/o replacing ingress-nginx entirely|synthesis §R.4,13,16
R19|SSA ⊥ the drift fix|5 of 8 drifting apps ALREADY have `ServerSideApply=true` (istio-base, kagent-secrets, kyverno, openshell, openshell-secrets) & drift regardless ∴ §T.42 premise FALSE. drift causes heterogeneous: istio = `caBundle` (1460B) injected by istiod ∉ git; others = SA, Job, StatefulSet, ExternalSecret, CRD. Argo docs: SSA "has the potential to be destructive and might lead to resources having to be recreated, which could cause an outage"|argo-cd.readthedocs.io/en/stable/user-guide/sync-options/ + kubectl 2026-07-28
R20|1.35 audit ≠ 1.36 audit|retarget → 1.35 REMOVES work. `ingress-nginx v1.15.1` (1.31-1.35) & `cert-manager v1.20.2` (1.32-1.35) ALREADY cover the target ∴ ⊥ upgrade needed. only CNPG + Istio + kyverno remain|synthesis §R.13,17 vs §G target
R21|chart `kubeVersion` ≠ published matrix|kyverno chart `3.7.1`(v1.17.1) declares `kubeVersion: '>=1.25.0-0'` — NO upper bound; crossplane & istio charts declare none. ∴ published matrices = TESTED-version statements, ⊥ install-time blocks. nothing refuses to install @ 1.35|helm show chart, local truth 2026-07-28
R22|CNPG ⊥ skip minors|docs: "upgrading through each version in sequential order... not skipping versions". ∴ §T.9 = 5 STEPS ⊥ 1: chart `0.23.2`→1.25.1, `0.25.0`→1.26.1, `0.26.1`→1.27.1, `0.27.1`→1.28.1, `0.28.3`→1.29.1. operator upgrade → instance rollout; `instances=1` ∴ ⊥ switchover possible, pod RESTARTS each step (~30-60s DB down ×5). `ENABLE_INSTANCE_MANAGER_INPLACE_UPDATES` would avoid it but breaks pod immutability ∴ ⊥ used|cloudnative-pg.io/docs/1.29/installation_upgrade/ + charts index.yaml
R23|Istio ambient upgrade|order = base(CRD) → istiod → cni → ztunnel → gateways/waypoints. istiod = revisioned + tags; `istio-cni` ⊥ canary-able ("Istio does not currently support canary upgrades of istio-cni") ∴ cni + ztunnel = IN-PLACE. "Upgrading ztunnel in-place will briefly disrupt all ambient mesh traffic on the node". CRITICAL: cni+ztunnel `1.x` compatible w/ control plane `1.x`..`1.x+1` ONLY ∴ 1-minor skew MAX ∴ `1.24`→`1.29` = 5 SEQUENTIAL steps × 4 components, ⊥ one jump|istio.io/latest/docs/ambient/upgrade/helm/
R24|Istio k8s matrix vs 1.33|`1.24` tested 1.28-1.31 ∴ ALREADY out of tested range on this cluster & working. `1.25` 1.29-1.32 (also out). `1.26`/`1.27` 1.29-1.33 ✓. `1.28` 1.30-1.34 ✓. `1.29` 1.31-1.35 ✓. ∴ passing THROUGH `1.25` ⊥ worse than status quo, but ⊥ skippable either (§R.23)|istio.io/latest/docs/releases/supported-releases/

## §V INVARIANTS
V1: ∀ hop → verified fs backup `/var/lib/rancher/k3s/` ∃ & restore drill passed before hop starts.
V2: ∀ hop → ∀ platform component ∈ its supported k8s matrix @ target minor, EXCEPT bounded transient per §V.27. ⊥ hop otherwise.
V3: ∀ hop → 1 minor step. ⊥ skip.
V4: control-plane hops before workers. ∀ worker hop after control-plane Ready.
V5: ∀ hop → post-gate: ∀ node Ready & ∀ Argo CD app Synced+Healthy before next hop.
V6: CNPG `postgresql-cluster` → pg_dump verified & restorable before ∀ hop.
V7: ⊥ rc/pre-release image, EXCEPT Argo CD `v3.5.0-rc2` — ACCEPTED 2026-07-28. ⊥ 3.5 GA ∃ (§R.15); `3.4.5` GA covers k8s ≤1.35 but a downgrade-across-minor carries its own risk, judged > the RC risk for a homelab. running 11d clean. ! REVISIT @ 3.5.0 GA, & if §T.42 kyverno drift traces to it.
V8: ∀ component upgrade → own commit, own sync, own rollback point. ⊥ batch.
V9: outage window announced & ≤ 15min per control-plane hop.
V10: ∀ hop → removed-API scan clean vs target minor.
V11: ∀ hop → ESO healthy & ∀ ExternalSecret `SecretSynced=True`.
V12: Istio ambient upgrade → revision/canary. ⊥ in-place istiod replace.
V13: remediation & walk interleave WHERE a component matrix demands it. ESO was the only such case (≥`0.20` ! k8s ≥1.34, §R.4) and is DEFERRED past §T.19 (§V.48) ∴ ⊥ forcing case remains: Istio `1.30` & cert-manager `1.21` both ∈ matrix @ 1.33. ∀ hop → ∀ component @ max version its matrix allows for CURRENT minor first.
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
V24: Argo auto-sync paused ∀ app w/ ExternalSecret during §T.31 migration window ONLY (⊥ ∀ ESO stage). `0.16.x` webhook rewrites stored objects → drift vs git ∴ `selfHeal` fights webhook. resume once manifests @ `v1` committed (§R.7: resolution = migrate, ⊥ code fix).
V25: barman restore ! proven (scratch Cluster ← `bootstrap.recovery`) BEFORE §T.9. barman restore ⊥ operator ∴ ⊥ verifiable after operator upgrade breaks.
V26: ⊥ ESO ≥`0.17.0` until `status.storedVersions == ["v1"]` ∀ of `externalsecrets`+`secretstores`+`clustersecretstores`. manifests @ `v1` ∈ git ≠ objects @ `v1` ∈ etcd (§R.3) ∴ k8s refuses to drop `v1beta1` & existing secrets become unreadable.
V27: §V.2 exception — component ? out-of-matrix ACROSS a hop boundary iff ALL: (a) in-matrix @ CURRENT minor pre-hop, (b) upgrade → in-matrix @ target ∈ SAME maintenance window, (c) rollback artifact ∃ & drill-proven. ⊥ open-ended skew. ESO `0.19`→`0.20` = the case (§R.4).
V28: ∀ hop → Vault data backed up off-cluster & restore-proven. `vault-0` storage=`file` @ local-path pinned `k3s-control-01` ∴ node loss = ∀ secret loss ∀ ns. KMS auto-unseal protects SEAL, ⊥ data.
V29: §T.27 = HARD gate. DECIDED 2026-07-27 = RELOCATE both → `k3s-worker-01`. ⊥ §T.13+ until §T.36 & §T.37 complete. relocate = PV migration ∴ ! before walk, ⊥ retrofit mid-walk.
V30: §V.18 pause ? be lifted to reach §V.5 green — paused app ⊥ self-heal ∴ ⊥ circular wait. order: pause → hop → MANUAL sync → verify → resume.
V31: ∀ hop task ! enumerate FULL gate set incl §V.6. ⊥ bare "gate →".
V32: Istio hop plan ! explicit per-minor & ∀ intermediate ∈ k8s matrix @ CURRENT minor. Istio `1.25` = k8s ≤1.32 ∴ ∉ 1.33 ∴ ⊥ naive 1-minor walk. `1.26`+ ∈ matrix @ 1.33.
V33: ∀ quiesce of GitOps-managed workload (scale→0 \| pod delete) → ! suspend its Argo app FIRST, resume after. `selfHeal: true` + replicas ∈ git ∴ Argo rescales mid-operation → torn artifact \| silent revert (§B.2). ALSO ! suspend PARENT app-of-apps (`master-app`) — child `syncPolicy` ∈ git ∴ parent re-sync silently restores `automated` mid-op (§B.4). ⊥ patch child alone. applies §T.35, §T.36, §T.37.
V34: ∀ pod mounting a node-pinned `local-path` PV → ! carry toleration ∀ taint on that node + explicit resource limits. `k3s-control-01` taint = `node-role.kubernetes.io/control-plane:NoSchedule`; kyverno `require-resource-limits` = Audit (warns, ⊥ blocks). ⊥ toleration → Pending forever, ⊥ 2 workers (∉ PV affinity). applies §T.35, §T.36, §T.37.
V35: Vault backup ⊇ KMS key. `alias/vault-auto-unseal` deleted \| AWS acct lost → ∀ vault artifact undecryptable ∀ time, however perfect the bytes. ∴ key ! carry deletion protection & its policy backed up ALONGSIDE artifact. ⊥ treat key as adjacent to the backup.
V36: ⊥ delete PVC w/ reclaim=`Delete` unless restore-proven backup ∃ & verified. deliberate delete = §V.19 hazard by another route. applies §T.36.
V37: post-§T.36+§T.37 → §V.14 scope MOVES: control-plane hop = API only; `k3s-worker-01` hop = Vault + CNPG outage. ∀ hop task ! re-cite before exec. §T.18 = highest-risk hop post-relocate.
V38: §T.37 scale-down gate — ! assert `postgresql-cluster-2` = primary BEFORE `instances` 2→1. CNPG drops highest-serial NON-primary & ⊥ targetable ∴ failed switchover → silent revert to control-plane.
V39: ∀ PVC ∉ Argo prune scope — `Prune=false` annotation \| app `prune: false` where chart ⊥ template annotations. enforced @ `tests/k3s-upgrade/test_prune_scope.py`. 10/21 WERE exposed 2026-07-27 ∴ §V.19 was false since inception.
V40: §T.36 order ! = commit `nodeSelector` → Argo sync → suspend → scale 0 → delete PVC → helper pod (nodeSelector + §V.34 toleration) provisions & receives restore → delete helper → scale 1 (STS adopts `vault-data-vault-0`). ⊥ start Vault on empty PVC — `WaitForFirstConsumer` ∴ scaling up to create the PV self-initialises Vault before restore.
V41: §T.39 runbook ! ∃ before §T.17. §V.9 "announced" ⊥ satisfiable w/o it.
V42: ∀ node drain w/ CNPG single-instance on it → BLOCKED by PDB (`enablePDB=true`, disruptionsAllowed=0, §R.10). PDB selects `instanceRole=primary` ∴ constraint FOLLOWS the primary, ⊥ eliminated by relocation. §T.37 unblocked §T.17 but MOVED the block to `k3s-worker-01` ∴ §T.18. mitigation @ hop time: scale→2 \| `enablePDB: false` \| `nodeMaintenanceWindow`.
V43: relocate CNPG across nodes = 3 PHASE. (a) BROADEN selector so CURRENT node still matches (nodeAffinity `In [old,new]` \| drop selector + cordon every node except target) → restart = no-op. (b) cordon old → `instances`+1 → auto-switchover → assert primary (§V.38) → scale back → uncordon. (c) NARROW to target host. ⊥ narrow before relocate — restarted primary ⊥ schedulable off its own PV (§B.5).
V44: ∀ live patch of a NESTED MAP (`nodeSelector`, labels, annotations) → `kubectl patch --type json` REPLACE. `--type merge` MERGES maps ∴ old keys survive → impossible conjunction (§B.5). ! verify the resulting object before proceeding.
V45: after ANY out-of-band `kubectl patch` on an Argo-managed resource, `kubectl.kubernetes.io/last-applied-configuration` goes STALE ∴ Argo client-side 3-way merge yields UNION of stale + desired, ⊥ replacement. before resuming auto-sync ! BOTH: (a) refresh & CONFIRM `status.sync.revision` == merged commit — Argo ? sync a CACHED older rev, (b) `kubectl apply -f <git manifest>` to repair last-applied \| set `ServerSideApply=true`. ⊥ resume on faith (§B.6).
V46: ⊥ hop → 1.36 while `ingress-nginx` ∈ cluster. repo archived, `v1.15.1` terminal, supports ≤1.35 (§R.13) ∴ ⊥ future version. 1.36 ! preceded by §T.43 (Gateway API migration). ∀ other 1.35-capped component ? ship 1.36 support later; this one ⊥ can.
V47: §V.5 ⊥ absolute. controller-mutated fields (webhook `caBundle`, SA tokens, defaulted spec) drift PERMANENTLY vs git ∴ "∀ app Synced" unreachable in a real cluster. REVISED gate: ⊥ UNEXPLAINED drift — ∀ OutOfSync app ! have a documented benign cause + `ignoreDifferences` covering it, else it blocks. ⊥ blanket `ServerSideApply` as the remedy (§R.19).
V48: ESO `v0.11.0` DEFERRED past §T.19 (decision 2026-07-28) ∴ runs ~4 minors out of matrix @ 1.35 (§R.4: `0.11` = k8s ≤1.31; already 2 out @ 1.33 & resolving 40/43). ACCEPTED: break = secret REFRESH stops, existing `Secret` objects PERSIST ∴ gradual degrade, ⊥ outage. §V.11 check @ ∀ hop = the early-warning signal; §T.18 (→1.34) = first real test. ⊥ proceed to §T.19 if §V.11 fails @ 1.34.
V49: Istio `1.24.0` DEFERRED past §T.19 (decision 2026-07-28). ALREADY out of tested range @ 1.33 (§R.24) & working; only 2 ns ∈ ambient (`chores-tracker`,`-frontend`). RISK ≠ ESO: `istio-cni` = CNI plugin chained into node networking ∴ failure ? break pod create on a node, ⊥ merely degrade. ∴ ∀ hop → CHECK `istio-cni-node` + `ztunnel` DS Ready ∀ node & both ambient ns still routing. ⊥ proceed to next hop if degraded.

## §T TASKS
id|status|task|cites
T1|x|write `scripts/k3s-backup.sh` — quiesce k3s, `sqlite3 .backup`, tar, checksum, ship off-cluster|V1,V15,V21,I.cmd
T2|x|write `scripts/k3s-restore.sh` + drill on throwaway VM: artifact → API up @ prior version|V1,V16,I.cmd
T3|x|`scripts/pg-backup.sh` pg_dump `postgresql-cluster` → off-cluster, verify restore|V6,V21,I.cmd
T4|x|deprecated/removed-API scan vs 1.34,1.35,1.36 (`pluto` \| `kubent`)|V10
T5|x|DECIDED 2026-07-28: KEEP `v3.5.0-rc2`. ⊥ 3.5 GA ∃; downgrade-to-3.4.5 risk judged > RC risk. §V.7 amended to record it. REVISIT @ 3.5.0 GA \| if §T.42 kyverno drift traces to the RC|V7
T6|.|DEFERRED past §T.19 (§V.48). ESO stage 1: `v0.11.0` → `v0.16.2` (serves `v1beta1`+`v1`)|V11,V8,V23,V24,V48
T7|.|verify ∀ ExternalSecret resync post-ESO, Vault k8s-auth roles intact|V11
T8|.|Vault `1.18.1` → current stable. NB StatefulSet `updateStrategy: OnDelete` ∴ ! manual `delete pod vault-0` after sync|V8
T9|x|DONE 2026-07-28: CNPG operator `1.24.1`→`1.29.1` in 5 sequential steps (§R.22, ⊥ skip minors). chart `0.22.1`→`0.23.2`→`0.25.0`→`0.26.1`→`0.27.1`→`0.28.3`. CVE-2026-44477 (CVSS 9.4) FIXED — patched in 1.29.1/1.28.3. ∀ step verified healthy + data unchanged (`n8n` 55 tbl, `chores_tracker` 7 tbl, `chores` 3 rows). restarts ≪ estimate: back healthy inside one 15s poll each. now ∈ matrix @ k8s 1.33-1.35|V2,V6,V8,V25,R.22
T10|.|DEFERRED past §T.19 (§V.49). Istio `1.24.0` → `1.29.x` = 5 SEQUENTIAL steps × 4 components (§R.23: cni+ztunnel skew ≤1 minor ∴ ⊥ jump). ztunnel step disrupts ambient traffic per node. istiod revisioned; cni+ztunnel in-place (⊥ canary-able)|V12,V20,V32,V49
T11|.|DEFERRED w/ §T.10. verify `ztunnel` + `istio-cni-node` DS healthy ∀ node post-Istio, then retire old revision|V12,V20,V49
T12|x|DONE 2026-07-28 matrix audit → §R.13-§R.18. blockers: ingress-nginx TERMINAL, kyverno ≤1.35, ArgoCD ⊥ 3.5 GA. clear: cert-manager→v1.21, Istio→1.30. RE-SCOPED to 1.35 (§R.20): ingress-nginx + cert-manager need NOTHING|V2,V46
T13|x|DONE 2026-07-28: `base-apps/system-upgrade-controller.yaml` Argo app. SUC `v0.20.1`, Synced/Healthy|V29,I.file
T14|x|DONE 2026-07-28: official v0.20.1 manifests + CRD, sync-wave -2/-1/0. `plan-agent.yaml` agents ONLY, ⊥ `plan-server.yaml`. `drain` ABSENT (local-path node-pinned), `cordon: true`, `concurrency: 1`, `version` pinned ⊥ channel. guards @ `tests/k3s-upgrade/test_suc_plan.py`|V3,V17,I.file
T15|~|`k3s-worker-02` labelled `k3s-upgrade=true` for §T.16. `k3s-worker-01` deliberately UNLABELLED until the real hop (hosts Vault+CNPG). `k3s-control-01` ⊥ labelled ∀ time (§V.17)|V17,I.node-label
T16|x|DONE 2026-07-28 dry run on `k3s-worker-02`: job created t+0s → cordon t+15s → Complete + uncordon t+31s. ⊥ NotReady ∀ moment. 21 workloads survived, other nodes untouched. NB job logged "Binary already been replaced" & exit 0 ∴ k3s never restarted — mechanism proven, real hop NotReady window still UNMEASURED|V4,V17
T17|.|gate §V.1,2,5,6,10,11,14,27,28 → MANUAL hop control-plane → `v1.34.9+k3s1`, console access ∃. ⊥ drainable until §T.37 done — PDB blocks it (§R.10, §V.42) + §V.49 istio-cni/ztunnel check|V1,V2,V3,V6,V14,V17,V27,V28,V31
T18|.|SUC hop workers → `v1.34.9+k3s1`. gate §V.1,5,6,11,14,28 — post-relocate THIS hop = Vault + CNPG outage (§V.37), ⊥ bare "verify §V.5". NB post-§T.37 the CNPG PDB blocks draining `k3s-worker-01` (§V.42) — scale→2 \| disable PDB for the hop + §V.49 istio-cni/ztunnel check|V4,V5,V6,V17,V31,V37,V42
T19|.|gate §V.1,2,5,6,10,11,14,27,28 → hop → `v1.35.6+k3s1` (control-plane manual, workers SUC) + §V.49 istio-cni/ztunnel check|V3,V5,V6,V17,V27,V28,V31
T20|x|RESOLVED 2026-07-28 (§R.18): 1.36 blocked by 4 components; `ingress-nginx` TERMINAL ∴ permanent. → target = `v1.35.6+k3s1`, §G retargeted|V2,V46
T21|.|FOLLOW-ON (⊥ this spec's target): hop → `v1.36.2+k3s1`. gated §T.43 + CNPG/ESO/kyverno shipping 1.36 support|V2,V3,V46
T22|.|confirm local kubectl v1.36.2 now in-skew|-
T23|.|update `index.md` + `docs/` topology w/ landed versions|-
T24|.|follow-on: spec 3-server embedded etcd HA conversion|-
T25|.|build pause/resume for Argo auto-sync ∀ PVC-bearing app across hop window|V18
T26|x|prune-scope audit: 10 PVC WERE prunable (§V.19 false since inception). fixed — `Prune=false` ∀ 9 manifest + `prune: false` @ atlantis app (chart 6.1.0 ⊥ template PVC annotations). `tests/k3s-upgrade/test_prune_scope.py` = regression guard. NB `lg-agents/orchestrator-data` orphaned (⊥ app) → §T.40|V19,V39
T27|x|DECIDED 2026-07-27: RELOCATE both → `k3s-worker-01` (97GB disk, ⊥ pressure). → §T.36 + §T.37. rationale: retires single failure domain, hops become API-only|V14,V29
T28|.|add `k3s/` + `pg/` + `vault/` prefixes → existing `s3://mysql-backups-asela-cluster/`. ⊥ new bucket (§I.store). + KMS key deletion protection (§V.35)|V21,V35,I.store
T29|.|verify `kube-system/ingress-nginx` helm-controller reconcile + ingress reachable post-∀-hop|V22
T30|.|define §V.9 measurement: start = k3s stop, end = ∀ Argo app Synced+Healthy|V9
T31|.|DEFERRED past §T.19. ESO stage 2: ∀ 56 file → `v1` (drop `beta1`) THEN rewrite ∀ stored object as `v1` + prune CRD `status.storedVersions` → `["v1"]`. git ≠ etcd (§R.3)|V23,V24,V26,V48
T32|.|DEFERRED past §T.19. ESO stage 3: → `0.17.0`+. gate `storedVersions==["v1"]`|V23,V26,V11,V48
T33|.|DEFERRED past §T.19. ESO stage 4: → `0.20.x` → `1.x` → `2.x`. ! k8s ≥1.34 (§R.4)|V23,V13,V11,V48
T34|x|DONE 2026-07-28: barman restore PROVEN. scratch ns + CNPG `bootstrap.recovery` ← `s3://mysql-backups-asela-cluster/postgresql/` (serverName `postgresql-cluster`) → healthy in ~2min. data verified vs source: sizes exact (`n8n` 290MB, `chores_tracker` 8260kB), `chores_tracker` rows 3/1/3/3 MATCH, `n8n.invalid_auth_token` 2/2 MATCH. torn down, PV reclaimed. §V.25 satisfied ∴ §T.9 unblocked|V6,V25
T35|x|write `scripts/vault-backup.sh` + drill: `vault-0` `file` storage → off-cluster, restore ! prove unseal + secret read. ⊥ backup ∃ today ∴ FIRST, before ∀ other `.` task|V28,V21,I.cmd
T36|x|DONE 2026-07-28: `vault-0` relocated `k3s-control-01` → `k3s-worker-01`. outage 2m24s (14:39:29-14:41:53Z). new PV `pvc-35030e9f` @ worker-01. unseal via awskms verified + live ESO read `refreshTime` 14:42:12Z `SecretSynced` (§V.28). ROLLBACK: old PV `pvc-0741ca81` = `Released`+`Retain` @ control-01 `/var/lib/rancher/k3s/storage/pvc-0741ca81-..._vault_vault-data-vault-0`; artifact `vault-backup-20260728T143928Z-T36.tar.gz`|V28,V29,V33,V34,V36,V40
T37|x|DONE 2026-07-28: `postgresql-cluster` relocated → `k3s-worker-01` via §V.43 3-phase. primary now `postgresql-cluster-2` @ worker-01, 1/1 healthy, `n8n` 55 tbl + `chores_tracker` 7 tbl verified. auto-switchover fired as §R.9 predicted. ⊥ outage beyond 2 expected restarts. ROLLBACK: PV `pvc-57ffc455` = `Released`+`Retain` @ control-01; dump `pg-...20260728T152040Z.sql.gz`|V6,V29,V33,V38,V42,V43,V44
T38|.|post-relocate: `k3s-control-01` ⊥ host stateful. amend §C pinning lines + §V.14 scope per §V.37 — control-plane hop now API-only, `k3s-worker-01` hop = Vault+DB outage|V14,V29,V37
T39|x|DONE 2026-07-28: `docs/plans/k3s-1.36-upgrade-plan.md` — runbook, outage comms table, per-hop gate + rollback, §B.1-§B.6 hard-won specifics. §V.9 "announced" now satisfiable|V9,V41,I.doc
T40|.|`lg-agents/orchestrator-data` PVC tracked by app `lg-agents` that ⊥ ∃. orphaned ∴ ⊥ prunable, but ∉ GitOps. decide: adopt \| delete \| document|V19
T41|.|install `kubectl cnpg` plugin — fallback manual switchover lever for §T.37 if auto-switchover ⊥ fire (§R.12). before §T.37|V38,R.12
T42|~|drift 8→2. FIXED: master-app (`recurse: false`), openshell-secrets (ESO webhook defaults), istio-base+istio-istiod (8 fields: istiod `caBundle`+`failurePolicy`, 6 API defaults), openshell (13 API defaults), argo-rollouts (`preserveUnknownFields`+`conversion`). REMAINING: `kagent-secrets` = operator copies Argo `tracking-id` onto a generated SA ∴ needs kagent fix ⊥ ignore rule; `kyverno` = chart renders `metadata.labels: {}` on 11/22 CRD (upstream bug) — rule applied & stable CLI shows ⊥ diff, but `v3.5.0-rc2` controller still flags. retest after §T.5|V5,V47
T43|.|FOLLOW-ON: migrate ingress off `ingress-nginx` → Gateway API. infra ∃ already (§R.14: istio/gloo/agentgateway GatewayClasses live). prerequisite for 1.36 (§V.46), ⊥ for 1.35|V46,R.13,R.14
T44|.|kyverno `v1.17.1` → `v1.18.x` (chart `3.8.2`). ADVISED ⊥ required: chart allows ≥1.25 (§R.21) but `v1.18` = the version TESTED @ 1.33-1.35 (§R.16). kyverno = admission webhook ∴ break blocks ∀ pod create|V2,V8

## §B BUGS
id|date|cause|fix
B1|2026-07-27|`k3s-backup.sh` online mode excluded `state.db` only. kine runs SQLite WAL ∴ live `-wal`/`-shm` shipped beside `.backup` snapshot → SQLite replays foreign WAL over restored db. found by drill, ⊥ by review|V15
B2|2026-07-27|`vault-backup.sh` cold mode scaled sts→0 to quiesce, but `replicas: 1` ∈ git (`base-apps/vault/statefulsets.yaml:18`) & Argo app `vault` `selfHeal: true` ∴ Argo rescales mid-copy → torn artifact BRANDED CONSISTENT. same class as §B.1. caught pre-exec by inspecting syncPolicy|V33
B3|2026-07-27|`vault-backup.sh` helper pod ⊥ toleration for `node-role.kubernetes.io/control-plane:NoSchedule` ∴ Pending forever — PV pinned to tainted control-plane & 2 workers ∉ PV affinity. backup aborted; §V.33 trap restored Argo + Vault safely ∴ ⊥ outage|V34
B4|2026-07-28|§V.33 suspend patched the CHILD Argo app only. `vault` app tracked by `master-app` & its `syncPolicy` ∈ git ∴ master-app re-synced @14:41:09Z MID-§T.36 & restored `automated` — suspension silently undone while the restore helper still held the volume. ⊥ harm (manual scale-up won the race) but `selfHeal` could have started Vault on half-restored data|V33
B5|2026-07-28|§T.37 patched `spec.affinity` on the live single-instance CNPG. §R.8 claimed affinity ⊥ rollout trigger — doc OMISSION read as absence, wrong: CNPG restarted the primary at once, its PVC pinned `k3s-control-01` ∴ Pending. compounded by `--type merge` MERGING the `nodeSelector` map → impossible `{hostname:worker-01, workload:infrastructure}`. ~5min outage `n8n`+`chores_tracker`, ⊥ data loss. NB a CORRECT patch fails identically — restarted primary ⊥ schedulable off its PV|V43,V44
B6|2026-07-28|resumed Argo post-§T.37 w/o confirming observed revision. Argo synced CACHED `d6f8ddd` (pre-T37, selector=`workload:infrastructure`); client-side apply merged it w/ live `hostname` → impossible union AGAIN → Postgres down ~4min. then FLAPPED ~60s cycles from stale `last-applied-configuration` until repaired by `kubectl apply` of the git manifest. 2nd outage of the day, same shape as §B.5, different actor|V45
