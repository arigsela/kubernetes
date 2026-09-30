# Backup and disaster recovery

This file covers the state the cluster holds, what protects each piece, how to take a restore
point before risky work, and how to get each piece back. The procedures live in the scripts
and runbooks linked below. This file says which one to use, and in what order.

Velero was removed on 2026-02-08 (`8ced214`) and nothing here uses it. Backups come from the
scripts in `scripts/` (drilled in CI by `tests/k3s-upgrade/`), CNPG's barman backups, and the
AWS KMS key that seals Vault. `SPEC.md` holds the invariants those scripts enforce (§V.1,
§V.15, §V.16, §V.21, §V.28, §V.35).

## State and what protects it

| State | Where it lives | Protected by | Off-host copy |
|---|---|---|---|
| Manifests, Argo CD Applications, Terraform, Ansible | this repo | GitHub | yes |
| Every Kubernetes API object, including every `Secret` | k3s SQLite datastore in `/var/lib/rancher/k3s/server/` on `k3s-control-01`. There's no etcd: the server runs without `--cluster-init` | `scripts/k3s-backup.sh`, by hand | only if you copy it off |
| Vault: every secret, policy and auth method | `file` storage at `/vault/data`, 1Gi local-path volume on `k3s-worker-01` (`vault-0` is pinned there by `nodeSelector`) | `scripts/vault-backup.sh`, by hand | only if you copy it off (ciphertext) |
| The key Vault's storage is encrypted with | AWS KMS `alias/vault-auto-unseal`, us-east-2 (`terraform/roots/asela-cluster/vault-kms.tf`) | `prevent_destroy`, 30-day deletion window | lives in AWS |
| CNPG `postgresql-cluster`: `donetick`, plus `n8n` and the orphaned `chores_tracker` | 2 instances, one per worker, 20Gi local-path each | barman: daily base backup at 02:00 UTC plus WAL archiving to `s3://mysql-backups-asela-cluster/postgresql/`, 30d retention. `scripts/pg-backup.sh` by hand | yes (S3) |
| Plain `postgresql` Deployment (pgvector): root DB, `kagent`, `homelab_agent`, Backstage, and n8n per its docs (n8n's DB host comes from Vault, so git can't confirm which instance it uses) | `postgresql-pvc`, 10Gi local-path | **nothing** | no |
| Other local-path volumes (table below) | the node that first mounted each one | **nothing** | no |
| Loki log chunks and index | S3 `asela-chores-loki-logs-20251017` (us-east-1). Loki keeps 30 days, and the pod's `/loki` is an `emptyDir` | S3 is the store | yes |
| Agent action record (redacted) | S3 `asela-agent-audit-record`, exported daily (`30 1 * * *`) from kagent's DB by the `agent-audit-export` CronJob | S3 is the store. You can't restore kagent from it | yes |
| Terraform state | S3 `asela-terraform-states` (us-east-2) | S3 | yes |
| AWS resources | KMS key and IAM in `terraform/roots/asela-cluster/`; S3 buckets and their IAM in `base-apps/*-aws-infrastructure/` (Crossplane) | git | n/a |
| VM disks (all three VMs) | the hypervisor `asela-k8s`, 10.0.1.101 | **nothing** | no |

### local-path volumes

`local-path` is the only StorageClass. Each volume is a directory under
`/var/lib/rancher/k3s/storage/` on one node, the reclaim policy is `Delete`, and a pod can only
run on the node that holds its volume. The Node column comes from the 1.36 hop's outage table
(2026-09-24, `docs/plans/k3s-1.36-upgrade-plan.md`). Check the live placement with the command
below the table.

| PVC (namespace) | Size | Node | Holds | Backup |
|---|---|---|---|---|
| `vault-data-vault-0` (vault) | 1Gi | worker-01 | Vault storage | `vault-backup.sh` |
| CNPG instance volumes (postgresql) | 20Gi each | one per worker | CNPG data | barman, `pg-backup.sh` |
| `postgresql-pvc` (postgresql) | 10Gi | worker-01 | plain Postgres | none |
| `storage-prometheus-0` (logging) | 50Gi | worker-01 | metrics | none |
| `grafana-storage` (logging) | 10Gi | worker-02 | Grafana's DB: user accounts and their GitHub OAuth links, plus anything made in the UI. Dashboards, datasources and alerting are provisioned from git | none |
| `n8n-pvc` (n8n) | 5Gi | worker-02 | n8n's local state dir. Workflows live in Postgres | none |
| `jupyter-pvc` (jupyter) | 20Gi | worker-02 | notebooks | none |
| `incident-memory-pvc` (oncall-agent) | 1Gi | worker-01 | LanceDB incident memory | none |
| Coroot, ClickHouse (coroot, created by the operator) | 10Gi, 20Gi | worker-01, worker-02 | telemetry, 7-day TTL | none |
| `atlantis-data` (atlantis) | 5Gi | control-01 | Atlantis working dirs and locks | none. `k3s-backup.sh` skips `storage/` |

```bash
kubectl get pv -o custom-columns='NS:.spec.claimRef.namespace,PVC:.spec.claimRef.name,SIZE:.spec.capacity.storage,NODE:.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values[0]'
```

## Gaps

1. **Nothing backs up the k3s datastore or Vault on a schedule.** An artifact exists only
   when someone runs the scripts. The last recorded full set is from the 1.36 hop on
   2026-09-24 (`SPEC.md` §T.21).
2. **The plain `postgresql` Deployment has no backup of any kind.** That covers the root DB,
   `kagent` (agent history, and the source for the agent audit), `homelab_agent`, Backstage,
   and n8n if its docs are right. `pg-backup.sh` only targets the CNPG cluster: its default
   exec looks up the CNPG primary by label. Pointing it here with `--exec-cmd` is untested,
   and it dumps as role `postgres`, which this instance may not have.
3. **No other local-path volume has a backup**: Grafana, n8n's state dir, Jupyter,
   oncall-agent's memory, Prometheus, Coroot, Atlantis.
4. **Everything runs on one physical host.** All three VM disks sit on the hypervisor, and
   nothing backs them up. If that disk dies, every local-path volume goes with it, along with
   the k3s datastore and any artifact left only on a node. The only off-host copies are in S3
   (barman, Loki, whatever you uploaded) and on your laptop.
5. **A Vault backup costs an outage and depends on AWS.** The `file` backend can't take a
   consistent snapshot while Vault runs, so a good backup stops Vault. The artifact is
   ciphertext that only the KMS key can decrypt. Putting it back into `vault-0`'s volume is a
   manual step.
6. **The backup bucket isn't declared anywhere in this repo.** Neither Terraform nor
   Crossplane declares `s3://mysql-backups-asela-cluster/`, so you can't see its region,
   versioning or lifecycle from git. (The region barman uses is in Vault key
   `postgresql-backup`.)
7. **A k3s artifact is as sensitive as the cluster.** It's the whole `server/` directory:
   every Kubernetes Secret (including the AWS keys in `vault-kms-credentials`), the cluster CA
   and the join token. Store it like a secret.

## Taking backups before risky work

Take a set before a k3s upgrade, a CNPG operator upgrade, a Vault upgrade, or a planned
hypervisor reboot. Every script refuses a destination inside cluster storage (§V.21) and
writes a `.sha256` beside the artifact. Each one also accepts `--dest s3://bucket/prefix` when
the aws CLI and credentials are on the machine running it. `scripts/hop-verify.sh gate` looks
for all three artifacts in `~/k3s-upgrade-artifacts` on your laptop and fails any that are
older than 24 hours.

```bash
# laptop, with kubectl pointed at the cluster
scripts/pg-backup.sh    --dest ~/k3s-upgrade-artifacts --all              # pg_dumpall of the CNPG primary
scripts/vault-backup.sh --dest ~/k3s-upgrade-artifacts --argo-app vault   # Vault is DOWN during the copy

# k3s-control-01: the script runs on the server node, as root
scp -i ~/.ssh/ari_sela_key scripts/k3s-backup.sh asela@10.0.1.50:
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.50
sudo ./k3s-backup.sh --mode cold --dest /home/asela/k3s-backups   # stops k3s: the API is down from here
sudo systemctl start k3s                                          # the script never restarts k3s
exit

# laptop: bring the k3s artifact home, then check the whole set
scp -i ~/.ssh/ari_sela_key 'asela@10.0.1.50:k3s-backups/k3s-backup-*' ~/k3s-upgrade-artifacts/
scripts/hop-verify.sh gate
```

- **k3s.** `--mode cold` gives a guaranteed point-in-time copy, and the upgrade runbook uses
  it. In an upgrade the installer starts k3s again, so you don't need the `systemctl start`
  there. `--mode online` keeps k3s running and snapshots the database with `sqlite3 .backup`,
  but `sqlite3` must be installed on the node.
- **Vault.** Cold mode suspends the `vault` Argo app, scales the StatefulSet to 0, tars
  `/vault/data` from a helper pod on the volume's node, scales back and waits for the unseal.
  It only suspends the child app, so `master-app` can turn auto-sync back on during the copy
  (§V.33, §B.4). For full protection run `scripts/argo-sync-window.sh pause` first and
  `scripts/argo-sync-window.sh resume` afterwards. `--mode online` needs `--allow-inconsistent`,
  and the artifact name is marked `INCONSISTENT`.
- **Off the laptop too.** Upload each artifact and its `.sha256`. So far the prefixes have
  been `k3s/`, `vault/` and `pg/` under `s3://mysql-backups-asela-cluster/` (§T.28). Barman
  owns `postgresql/`.
  ```bash
  aws s3 cp ~/k3s-upgrade-artifacts/<file> s3://mysql-backups-asela-cluster/<k3s|vault|pg>/
  ```
- **CNPG backs itself up.** Only check that the last run worked:
  `kubectl -n postgresql get backups --sort-by=.metadata.creationTimestamp`.

An untested artifact doesn't count as a backup (§V.16, §V.28). CI drills every restore
against real k3s, Vault and Postgres in Docker (`tests/k3s-upgrade/`, job
`k3s-upgrade-scripts`).

## Restoring

### k3s datastore

Use this after a failed k3s upgrade (the only rollback, because k3s migrates SQLite one way),
a corrupted datastore, or a rebuilt control-plane VM. Run it on `k3s-control-01`, with the
artifact and its `.sha256` in the same directory:

```bash
# same version (corrupt datastore): starts the installed binary
sudo ./k3s-restore.sh --artifact k3s-backups/<file>.tar.gz --expect-version v1.36.4+k3s1

# rolling back an upgrade: reinstall the old version too. The installer rewrites the unit, so pass the flags again
sudo INSTALL_K3S_VERSION=<prior> INSTALL_K3S_EXEC="server --write-kubeconfig-mode=644" \
  ./k3s-restore.sh --artifact k3s-backups/<file>.tar.gz --expect-version <prior>
```

- The script refuses a checksum mismatch. It then stops k3s, moves the **whole**
  `/var/lib/rancher/k3s` aside to `/var/lib/rancher/k3s.pre-restore.<UTC stamp>`, extracts
  `server/`, and fails unless the API answers at `--expect-version`.
- The artifact holds only `server/`. Everything else from the old tree, such as Atlantis's
  volume under `storage/` and containerd's images, stays in the moved-aside copy.
- Every API object goes back to the state it was in when the artifact was taken. Argo
  re-applies git on top, but anything created after the artifact that isn't in git is gone.
- Afterwards, handle the node as if it had just been upgraded. Do the istio-cni step in
  `docs/plans/k3s-1.36-upgrade-plan.md` ("The moment k3s returns on a node, before anything
  else"), then run `scripts/hop-verify.sh watch --since <epoch>` and `scripts/hop-verify.sh gate`.
- CI drills this restore (`test_restore_drill_docker_k3s`). Nobody has run it on the real
  cluster yet.

### Vault

You need the artifact and its `.sha256`, the KMS key, and the `vault-kms-credentials` Secret.
Terraform manages that Secret; if it's missing, see `base-apps/vault/runbook.md`.

1. Run `scripts/vault-restore.sh --artifact <file> --data-dir <local dir> --verify-cmd '<cmd>'`.
   It checks the checksum, refuses an `INCONSISTENT` artifact unless you add
   `--accept-inconsistent`, and extracts into a **local directory**. `--verify-cmd` must show
   that Vault unseals and a known secret reads back. `--no-verify` skips that check and leaves
   §V.28 unproven.
2. Copying the tree into `vault-0`'s volume is manual: see "Restore Vault" in
   `base-apps/vault/runbook.md`. If the volume itself is gone, follow the order in `SPEC.md`
   §V.40, which is how Vault moved to worker-01 on 2026-07-28 (§T.36). If worker-01 itself is
   gone, first change the `nodeSelector` in `base-apps/vault/statefulsets.yaml` to the new
   node and let Argo sync.
   1. Pause Argo (`scripts/argo-sync-window.sh pause`) and scale Vault to 0.
   2. Delete the old `vault-data-vault-0` claim if it still exists. Create a new one through a
      helper pod pinned to Vault's node, and restore into it.
   3. Delete the helper and scale Vault to 1, so the StatefulSet adopts the volume.
   4. Check `vault status` and a secret read, then run `scripts/argo-sync-window.sh resume`.

   Don't let Vault create the new volume itself. With `WaitForFirstConsumer`, that starts Vault
   on an empty directory before the restore.
3. CI drills this with a Shamir seal, so the drill doesn't exercise awskms. The awskms path
   has been done once, for real, in §T.36.

If you have no Vault artifact at all, you have to re-enter every secret under `k8s-secrets/`
by hand. ESO keeps the last Secret it synced when a sync fails, so running workloads keep
their credentials while you do that.

### CNPG

- **One instance lost, the other healthy.** The operator promotes the survivor. For failover,
  rebuilding a replica, and the async-replication loss window, see the CNPG section of
  `base-apps/postgresql/runbook.md`.
- **Both instances lost, or bad data replicated to both.**
  - **barman (S3):** recover into a new Cluster with `bootstrap.recovery`. The spec that worked
    (§T.34, healthy in about 2 minutes) is under "Barman restore" in
    `docs/plans/k3s-1.36-upgrade-plan.md`. Set `serverName: postgresql-cluster` explicitly.
    This needs a working operator and the `postgresql-backup-credentials` Secret (from Vault key
    `postgresql-backup`), so restore Vault first. Switching production to a recovered cluster
    (changing the bootstrap in `cnpg-cluster.yaml`) has never been drilled.
  - **pg_dumpall artifact:** this path doesn't need the operator, so use it when the operator
    itself is broken. Find the primary with
    `kubectl -n postgresql get pods -l cnpg.io/cluster=postgresql-cluster,cnpg.io/instanceRole=primary`,
    then run
    `gunzip -c <file>.sql.gz | kubectl exec -i -n postgresql <primary> -c postgres -- psql -U postgres`.
- CNPG 1.31 removes `barmanObjectStore`. Before any 1.31 bump, move to the Barman Cloud Plugin
  and prove the restore again (§T.79).

### Plain Postgres and the other local-path volumes

There's no backup to restore from. If a volume's node is gone for good, the data is gone
with it. The pod stays `Pending` because the PV's node affinity names the dead node.
Recreating the PVC gives the app an empty volume somewhere else. That's a deliberate delete
(§V.36), so do it only once you're sure the disk isn't coming back. After that:

- **Grafana** gets its dashboards, datasources and alerting back from git, but not its user
  accounts. With sign-up closed, your GitHub login is refused until you re-link it. See
  "Login: GitHub OAuth only" in `base-apps/logging/docs.md` (§V.69, §B.10).
- **Plain Postgres:** see `base-apps/postgresql/runbook.md` for re-running the init Jobs that
  create roles and databases.
- **Atlantis:** run `atlantis plan` again on open PRs.

### Rebuild from nothing (never drilled)

Nobody has tried this. What matters is the order:

1. **VMs.** VM creation isn't automated; `ansible/` only lays down the OS baseline.
2. **k3s server.** Install the version the artifact came from, then run `k3s-restore.sh`. That
   brings back every API object, including Argo CD, `master-app` and `vault-kms-credentials`.
   With no k3s artifact:
   1. Run `terraform apply` in `terraform/roots/asela-cluster` from your laptop, since Atlantis
      isn't running yet. That creates Argo CD and `vault-kms-credentials`.
   2. Run `kubectl apply -f base-apps/master-app.yaml`, because Terraform no longer creates the
      root app.
3. **Vault**, before anything that needs a secret.
4. **CNPG**, from barman. Everything else syncs from git and starts empty.

## Vault and the KMS key

- `vault-0` seals with awskms (`base-apps/vault/configmaps.yaml`), using key
  `alias/vault-auto-unseal` in us-east-2. It reaches the key with IAM user `vault-kms-user`,
  whose access keys are in the `vault-kms-credentials` Secret. Terraform creates that Secret,
  not ESO, because ESO depends on Vault.
- **The key is part of the backup** (§V.35, and the comment in `vault-kms.tf`). Vault's
  storage is ciphertext under this key, so every artifact from `vault-backup.sh` is useless
  without it. If the key is deleted or the AWS account is lost, every Vault backup is
  unreadable for good. Three things protect it:
  - `prevent_destroy` stops a `terraform destroy` or a replacement.
  - The 30-day deletion window stops a hasty `ScheduleKeyDeletion`.
  - Automatic rotation keeps the old key material, so old artifacts still decrypt.
- The Shamir recovery keys don't decrypt storage. They're only for generating a root token or
  migrating the seal. They aren't in git: `.gitignore` reserves `recovery/vault-credentials.txt`
  for them, but this checkout doesn't have that file, so make sure you know where your copy is.
- At boot, Vault needs to reach AWS KMS. If the internet or AWS is down, Vault stays sealed.
  The Kubernetes Secrets already in the cluster keep apps running, but nothing refreshes.
  Check `kubectl -n vault logs vault-0 | grep -i kms`, then `base-apps/vault/runbook.md`.

## After a power loss

This section also applies after an unplanned hypervisor reboot. For a planned one, use
`ansible-playbook playbooks/patch-hypervisor.yml -e confirm=yes` (see `ansible/README.md`),
which shuts the VMs down cleanly first. Take backups before you run it.

### What comes back on its own

- **Hypervisor:** whether it powers on when power returns is a BIOS setting, and this repo
  doesn't manage it. If it stays off, power it on.
- **VMs:** libvirt autostart (set by `ansible/roles/hypervisor`) starts all three. There's no
  boot order, and none is needed: the agents keep retrying until the server answers.
- **k3s and pods:** systemd starts k3s, and each pod comes back on the node that holds its
  local-path volume.
- **Vault:** unseals itself through KMS within seconds, as long as AWS is reachable.
- **Secrets:** Kubernetes Secrets live in the datastore, so apps start without waiting for
  Vault. ESO starts refreshing again once Vault is unsealed.
- **CNPG:** the operator restarts both instances and keeps or elects a primary.
- **Argo CD:** carries on auto-syncing from git.
- **DNS and the allow-list:** if the ISP gave out a new WAN address, `wan-ip-monitor` (every
  12 hours) updates Route 53 and opens a PR for the Istio allow-list. Until that PR merges,
  IP-restricted hosts refuse you. To run it sooner, see "Run a job by hand" in
  `base-apps/wan-ip-monitor/runbook.md`.

### What to check

```bash
ssh -i ~/.ssh/ari_sela_key asela@10.0.1.101 virsh -c qemu:///system list --all   # 3 VMs running
kubectl get nodes                                          # 3 Ready, v1.36.4+k3s1
kubectl get pods -A | grep -v -e Running -e Completed      # anything stuck?
kubectl -n vault exec vault-0 -- vault status              # Sealed false, Seal Type awskms
kubectl -n postgresql get cluster postgresql-cluster       # healthy, 2 instances ready
kubectl get externalsecret -A | grep -v True               # the gate tolerates 3 known failures
kubectl -n argo-cd get applications | grep -v 'Synced.*Healthy'
```

`scripts/hop-verify.sh gate` runs the same checks and more (Istio dataplane, CNI, ingress,
admission). Its artifact-age failures are expected when you haven't just taken a backup.
`scripts/hop-verify.sh watch --since <epoch>` waits until every node is Ready and every app is
green.

Then confirm CNPG is archiving WAL again: `last_archived_time` should be later than the
restart.

```bash
kubectl -n postgresql exec <primary> -c postgres -- \
  psql -U postgres -tAc 'select last_archived_time, last_failed_time from pg_stat_archiver'
```

### If something is stuck

- **Pods stuck in `ContainerCreating` on one node** (`FailedCreatePodSandBox`):
  1. Restart that node's istio-cni pod:
     `kubectl -n istio-system delete pod -l k8s-app=istio-cni-node --field-selector spec.nodeName=<node>`.
     This is a long shot: §V.50's orphaned-binary failure needs a k3s version change, not just a
     reboot, and §T.46 moved the binary somewhere stable.
  2. If sandbox creation failed repeatedly, audit the node's IP reservations (§V.51). Compare
     `sudo ls /var/lib/cni/networks/cbr0/ | grep -c '^10\.'` with the node's running pods, and
     prune following the upgrade plan: tar the directory first, then delete only reservations
     that match neither a live pod IP nor a live container ID.

  **Never wipe `/var/lib/cni/networks/cbr0`**, as the Velero-era recovery scripts did. Live pods
  hold addresses in there and would collide.
- **Vault sealed:** either KMS is unreachable or `vault-kms-credentials` is wrong. See
  `base-apps/vault/runbook.md`.
- **`vault-0` Pending:** worker-01 isn't back yet. Vault is pinned there on purpose. Don't
  delete the PVC.
- **CNPG degraded, or one instance Pending:** that instance's node isn't back. See the CNPG
  section of `base-apps/postgresql/runbook.md`.
- **ExternalSecrets failing while Vault is unsealed:** see `base-apps/vault/runbook.md`.

## Hosts

From `ansible/inventory/`. All three nodes run k3s `v1.36.4+k3s1`.

| Host | IP | Role |
|---|---|---|
| `asela-k8s` | 10.0.1.101 | hypervisor (libvirt) |
| `k3s-control-01` | 10.0.1.50 | k3s server and SQLite datastore, API at `https://10.0.1.50:6443`, taint `node-role.kubernetes.io/control-plane:NoSchedule` |
| `k3s-worker-01` | 10.0.1.5 | agent: Vault, one CNPG instance, plain Postgres |
| `k3s-worker-02` | 10.0.1.108 | agent: one CNPG instance |

SSH: `ssh -i ~/.ssh/ari_sela_key asela@<ip>`.
