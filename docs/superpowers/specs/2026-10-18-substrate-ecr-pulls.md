# Substrate ECR pulls (K2): spike and design

**Date:** 2026-10-18 (spike run 2026-10-19). **SPEC:** §T.129, §V.100. **For:** the docs-checker agent
(claude-agents `docs/superpowers/specs/2026-10-18-docs-checker-design.md` §3 and §11, row K2), whose
image lives in the private ECR repository `852893458518.dkr.ecr.us-east-2.amazonaws.com/docs-checker`.

**Problem.** The cluster pulls ECR images with the `ecr-registry` pull Secret (CronJob
`ecr-credentials-sync` in `kube-system`, injected by the MutatingAdmissionPolicy
`inject-ecr-pull-secret`). That only reaches the kubelet. Substrate's node agent, atelet, pulls actor
(sandbox) images itself, and Harness, SandboxTemplate, WorkerPool and SandboxConfig have no pull-secret
field, so a sandbox image in ECR cannot be pulled today.

**Verdict: a safe path exists, and this change implements it.** atelet runs kubelet credential
provider plugins from a host directory. The pinned `ecr-credential-provider` binary and a 0600 AWS
credentials file for a pull-only IAM user sit in that directory on the two workers, outside anything a
worker pod or sandbox mounts. One trap had to be designed around: the provider inherits atelet's
environment, which carries rustfs's `AWS_*` variables, and those beat the credentials file (Q2).

**Read for this spike (read-only):**
- Chart `oci://ghcr.io/kagent-dev/substrate/helm/substrate:0.5.0-alpha2` (`helm pull` digest
  `sha256:361504a126e02473b78a0e9e10b1c613b62a2ec7429419cf6a83e808b9808a34`). Paths below are
  `chart:<file>:<line>`.
- Source `kagent-dev/substrate` at tag `v0.5.0-alpha2` = commit `9c4b1fb52212028100fa02c8a7bc4526575ea37e`.
  Paths are `src:<file>:<line>`.
- `kubernetes/cloud-provider-aws` at `v1.37.0` (`cmd/ecr-credential-provider/main.go`, `go.mod`), and
  `aws/aws-sdk-go-v2` `config/v1.29.14` (the version that provider builds with).
- Go `go1.27.0` `src/os/exec/exec.go` (atelet is built with go 1.27.0).
- `helm template` of the chart with this repo's values, before and after the change.

## Q1. How is the provider binary run?

- **Wiring.** When either `atelet.imageCredentialProviderConfig` (a host path) or
  `atelet.imageCredentialProviderConfigYAML` (inline, rendered into a ConfigMap) is set, the chart adds
  `--image-credential-provider-config=/run/image-credential-provider/config.yaml` and
  `--image-credential-provider-bin-dir=/run/image-credential-provider/bin` to atelet
  (chart:templates/atelet.yaml:142-145). The two config values are mutually exclusive, and the bin dir
  is required with either (chart:templates/atelet.yaml:17-23).
- **Startup.** atelet reads the config once at startup (src:cmd/atelet/main.go:206-218). A config it
  cannot read or parse is **fatal**: `serverboot.Fatal` (main.go:213-216). The parse is
  `yaml.UnmarshalStrict` into the kubelet's `CredentialProviderConfig`, and it must declare
  `kubelet.config.k8s.io/v1` / `CredentialProviderConfig` (src:cmd/atelet/internal/credentialprovider/config.go:48-59).
  An unknown field, or an env entry with `valueFrom`, therefore crash-loops atelet on every node. A
  provider entry that fails validation is skipped with a warning instead (config.go:60-80).
- **Per-provider rules** (config.go:91-138): `name` is required and must be a bare file name, joined
  onto the bin dir (`filepath.Join(binDir, cfg.Name)`, config.go:97-99, 131). `matchImages` and
  `defaultCacheDuration` are required. `apiVersion` must be `credentialprovider.kubelet.k8s.io/v1`.
  `tokenAttributes` is refused, because actors have no Kubernetes service account (config.go:117-122).
  The binary's existence is **not** checked at startup.
- **Exec** (src:cmd/atelet/internal/credentialprovider/exec.go:42-84): this is the kubelet protocol, as a
  child process **of atelet, inside atelet's container**. It writes a `CredentialProviderRequest` JSON
  to stdin and reads a `CredentialProviderResponse` from stdout. Limits: one minute, and 1 MiB of
  stdout (exec.go:33-37). So the binary runs in atelet's root filesystem and mount namespace, not the
  host's. That rootfs is `gcr.io/distroless/base-debian13`: it has glibc and a CA bundle
  (src:.ko.yaml:23-25). The pinned provider is a static x86-64 ELF, so it would run on a static base
  too.
- **When it runs.** Only for images a provider claims through `matchImages` (src:cmd/atelet/internal/credentialprovider/credentialprovider.go:95-102).
  Matching works like the kubelet's: the same number of host segments, each matched as a glob; the
  ports equal; and the pattern's path a **string prefix** of the repository path
  (src:cmd/atelet/internal/credentialprovider/match.go:32-59). If a provider fails, atelet logs
  `Image credential provider failed; skipping it` and pulls anonymously, so the registry answers 401
  (credentialprovider.go:107-111). Success logs `Resolved image credentials from credential provider`
  with `cached=false|true` (credentialprovider.go:127-128). The response is cached in atelet's memory
  per its `cacheKeyType` and `cacheDuration` (credentialprovider.go:215-283). `ecr-credential-provider`
  answers `cacheKeyType: Registry` with half of the ECR token's remaining life, so about 6 h
  (cloud-provider-aws `main.go:288-318`). A restart of atelet drops that cache.
- **Where the token goes.** It is used in-process (`remote.WithAuth`,
  src:internal/imagecache/imagecache.go:818-838). It is never written to `/var/lib/ate` or handed to a
  worker pod.

## Q2. Can `env` point to a credentials file?

**Yes, but `env` takes literals only, and the provider inherits atelet's whole environment.**

- `ExecEnvVar` is `{name, value}`, both strings (src:vendor/k8s.io/kubelet/config/v1/types.go:186-189).
  There is no `valueFrom`, and strict parsing makes one fatal (Q1). So keys cannot come from a Secret
  through the config. A **path** can: `AWS_SHARED_CREDENTIALS_FILE=/run/image-credential-provider/bin/aws-credentials`.
- The provider's environment is `append(os.Environ(), p.env...)`, the union with atelet's own
  environment (exec.go:63-65). In Go, "if Env contains duplicate environment keys, only the last
  value in the slice for each duplicate key is used" (go1.27.0 `os/exec/exec.go:166-167`). So a
  config entry overrides an inherited variable.
- **Trap.** With `rustfs.enabled: true`, the chart gives atelet `AWS_REGION=us-east-1`,
  `AWS_ENDPOINT_URL=http://rustfs.ate-system.svc:9000`, `AWS_S3_USE_PATH_STYLE=true`, and the rustfs
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` (chart:templates/atelet.yaml:186-197; confirmed in the
  render). The provider builds its ECR client with `config.LoadDefaultConfig(WithRegion(<region parsed
  from the registry host>))` (cloud-provider-aws `main.go:74-92`). In aws-sdk-go-v2:
  - env keys **win** over the shared credentials file and over an `AWS_PROFILE` from the environment
    (`config/resolve_credentials.go:102-124`: only a programmatic profile ranks above env keys);
  - `AWS_ENDPOINT_URL` becomes the base endpoint of every service (`config/env_config.go:397`).

  So a config that only sets `AWS_SHARED_CREDENTIALS_FILE` would sign `GetAuthorizationToken` with
  rustfs's keys and send it to rustfs over plain HTTP. The pull fails, and the credentials file is
  never read. (Nothing leaks: the request carries rustfs's own keys to rustfs.)
- **Fix.** The provider config sets `AWS_ACCESS_KEY_ID=""`, `AWS_SECRET_ACCESS_KEY=""` and
  `AWS_ENDPOINT_URL=""`. The SDK treats an empty value as unset (`setStringFromEnvVal` only takes
  `len(v) > 0`, `config/env_config.go:724-731`, used at :323 and :397). It also sets
  `AWS_IGNORE_CONFIGURED_ENDPOINT_URLS=true`, which covers a future `AWS_ENDPOINT_URL_ECR`, and
  `AWS_EC2_METADATA_DISABLED=true`: the nodes are not EC2, so this fails fast instead of probing IMDS.
  The profile is named (`AWS_PROFILE=docs-checker-pull`), so a missing or mis-written file fails
  with "profile not found" instead of falling through the chain. The region comes from the image
  host (`us-east-2`) and overrides the inherited `AWS_REGION`. `tests/substrate/` pins all of this
  (§V.100).

## Q3. Can atelet see a host file, and can the sandboxes **not** see it?

**atelet can.** The bin dir is a `hostPath` of `type: Directory` mounted **read-only** at
`/run/image-credential-provider/bin` (chart:templates/atelet.yaml:225-235, 253-257). The whole
directory is mounted, so a file next to the binary is visible at
`/run/image-credential-provider/bin/<file>`. atelet runs as UID 0 with `DAC_OVERRIDE`
(chart:templates/atelet.yaml:152-159) and no user namespace, so a `root:root 0600` file is readable
(it is the owner). Because the mount type is `Directory`, **atelet does not start on a node where the
directory is missing.** The kubelet refuses the hostPath, the pod stays `ContainerCreating`, and the
node runs no actors. The directory must exist on every node atelet runs on (k3s-worker-01 and 02;
atelet does not tolerate the control plane's taint, `base-apps/substrate/docs.md`) **before** the
chart change syncs.

**Sandboxes cannot, as long as gVisor holds.** The boundary has three layers:

| layer | sees | source |
|---|---|---|
| atelet container | `/var/lib/ate` (rw), `/var/lib/kubelet/{plugins,device-plugins}`, `/dev` (ro), the bin dir (ro), the config | chart:templates/atelet.yaml:209-267 |
| ateom worker pod (runsc's host side, in `kagent`) | `/var/lib/ate` only (rw, `HostToContainer`), plus projected certs; a micro-VM pool would add `/dev/net/tun` | src:cmd/atecontroller/internal/controllers/workerpool_apply.go:169-204, 427-465 |
| the sandbox (gVisor guest) | its OCI rootfs plus bind mounts whose sources are all per-actor dirs under `/var/lib/ate/actors/<uid>/` (durable dir, CSI volume, system-info, image volume), plus `/etc/resolv.conf` read-only | src:internal/ocispec/ocispec.go:175-196, src:internal/ocispec/gvisor.go:64-80 |

- The actor API has no hostPath volume source. `Volume` is `durable_dir | external | system_info | image`
  (src:internal/proto/ateletpb/atelet.proto:339-362). System-info sources are only actor metadata and
  trust bundles, and external volumes are CSI mounts under the actor's directory
  (src:cmd/atelet/volumes.go:32-56).
- So the bin dir must **not** be under `/var/lib/ate`, which every worker pod mounts read-write. It
  must not be under `/var/lib/kubelet/plugins` either (shared with CSI drivers), or `/dev`. This
  change uses `/opt/ate-image-credential-provider`, and a test enforces that (§V.100).
- **Residual risk.** A gVisor escape lands in the ateom container as root with 13 capabilities
  including `SYS_ADMIN`, with seccomp and AppArmor `Unconfined` and no user namespace
  (workerpool_apply.go:342-416). From there a node escape is plausible. The credentials file is then
  readable, but so is everything else on the node (kubelet credentials, every pod's mounted Secrets).
  What the file adds is a key that can pull **one** repository, read-only. Today's `ecr-registry`
  Secret, readable in every namespace, pulls **all** of them.
- **Who can use the pull without reading the file.** Anyone who can make atelet pull an image that
  matches: the kagent controller, and anyone holding a ServiceAccount token for the audience
  `api.ate-system.svc`. ate-api-server authenticates those tokens but does no authorization yet
  (src:docs/authentication.md:26-30), so its callers already control every actor. They can run the
  docs-checker image. That image carries no secrets: the code is in a repository and the keys are
  injected at egress.

## The other questions in the brief

- **Config format.** The kubelet's `CredentialProviderConfig` (`kubelet.config.k8s.io/v1`), with
  `providers[]: {name, matchImages, defaultCacheDuration, apiVersion: credentialprovider.kubelet.k8s.io/v1, args?, env?}`.
  `tokenAttributes` is refused. Unknown fields are fatal (Q1).
- **How atelet sees the bin dir.** As a read-only hostPath of the whole directory (Q3). Provider names
  resolve inside it. The config comes either from a host file (hostPath `type: File`, which is also
  fatal-if-missing) or from the chart's ConfigMap mounted with `subPath`.
- **Env values.** Literals only. Union with atelet's environment, and the last value wins (Q2).
- **Isolation.** See Q3.

## Design (this change, §T.129)

| piece | where | what |
|---|---|---|
| IAM user | `terraform/roots/asela-cluster/docs-checker-pull-iam.tf` | `system/docs-checker-pull`: `ecr:GetAuthorizationToken` on `*` (the action has no resource-level permissions), `ecr:BatchGetImage` and `ecr:GetDownloadUrlForLayer` on the `docs-checker` repository ARN only. **No access key in Terraform**: it would land in the state bucket (the `crossplane-admin` precedent in `iam.tf`) |
| keys | `scripts/provision-docs-checker-pull.sh` | creates the access key with the AWS CLI and writes it to Vault `k8s-secrets/docs-checker-pull` (`aws-access-key-id`, `aws-secret-access-key`, `aws-region`, the property names `ecr-auth` uses). The values go through stdin and pipes only, never argv or output. If the Vault write fails, the new key is deleted. `--rotate` adds a second key, `--retire-old` deletes the other one, `--check` previews |
| host files | `ansible/playbooks/ate-image-credentials.yml`, run by the script | on `k3s_workers`: `/opt/ate-image-credential-provider/` root 0700, `ecr-credential-provider` 0755 (cloud-provider-aws **v1.37.0** linux/amd64, `get_url` with sha256 `842e0fd8159f5ed8df2f38e6521e1e4f0a1ee80cf1e1ac119bbf20bf9c20681c`, the value published at `<url>.sha256`; checked independently on 2026-10-19), and `aws-credentials` 0600 (profile `[docs-checker-pull]`, written with `no_log`). It then runs the provider on each worker with `env -i` and the config's variables, and checks that it returns credentials for the registry. The token goes through a pipe, never into Ansible's output |
| chart values | `base-apps/substrate.yaml` | `atelet.imageCredentialProviderBinDir: /opt/ate-image-credential-provider` and `atelet.imageCredentialProviderConfigYAML` (see the note below), with one provider: `matchImages: [852893458518.dkr.ecr.us-east-2.amazonaws.com/docs-checker]` and the Q2 env |
| guards | `tests/substrate/`, `tests/ansible/test_ate_image_credentials.py` | the config passes atelet's own rules (strict fields, required fields, literal env); the inherited `AWS_*` are blanked; the bin dir is outside the shared paths; values, playbook and script agree on dir, file and profile names; the script runs end to end against stub `vault`, `aws` and `ansible-playbook`, and never prints or passes a secret on argv; the playbook runs against the test node with a fake provider |

**Why `…ConfigYAML` and not `atelet.imageCredentialProviderConfig`** (the value the plan names): in
this chart, `imageCredentialProviderConfig` is a **host path** to a config file. That would put the
provider's `matchImages` and env on each node, out of review and free to drift, and a missing file
would also stop atelet. `imageCredentialProviderConfigYAML` keeps the whole config in git. The
chart's own comment gives it as the choice "for a provider environment that has to differ from the
kubelet's", which is Q2's case. Only the keys stay off git, in a file the config names by path.

**What this takes from `ecr-credentials-sync`:** the trust model (static keys of a dedicated IAM user,
because the nodes have no instance profile), Vault `k8s-secrets` as the source of truth, and its
property names. **What differs:** the consumer is a host file, not a Kubernetes Secret, so delivery is
Ansible (the repo's host layer) and Vault needs no role or policy. Only an operator token reads it.

**Rejected:**
- *Keys as literals in a host-file config* (`imageCredentialProviderConfig`). It works, but the
  config would leave git (see above).
- *ESO plus a DaemonSet that writes the host file.* The keys would become a Kubernetes Secret, which
  `k8s-credential-provider`'s cluster-wide `get` on Secrets can read. It would also need a pod that
  writes host paths.
- *`aws_iam_access_key` in Terraform.* The secret would be in state.
- *A public image* (GHCR public, ECR Public). This needs no credentials at all, and remains the
  fallback. Ari chose ECR with pull credentials (design §12).

## Rollout order (the deploy is gated on this; §T.129)

1. PR open. Atlantis plans and applies the IAM user (Terraform applies before merge).
2. `scripts/provision-docs-checker-pull.sh --check`, then `scripts/provision-docs-checker-pull.sh`.
   This needs Vault (`VAULT_ADDR`, a token), the AWS CLI as an IAM admin, and `ansible-playbook` (see
   `ansible/README.md`). The verify step must pass on both workers.
3. Merge. Argo syncs `substrate`: the ConfigMap `atelet-image-credential-provider` appears and the
   atelet DaemonSet rolls on both workers (new args and volumes). Merge in a quiet window: a
   restarting atelet briefly stops actor lifecycle calls on its node.
4. Acceptance below.

## Acceptance (documented here, run by Ari; C#48)

None of this has been run. The kubectl-ate steps follow the v0.5.0-alpha2 CLI docs
(`cmd/kubectl-ate/README.md`, `demos/counter/counter-template.yaml.tmpl`). If the golden build refuses
the probe template, the structural checks in C1 still stand.

**Probe image.** A throwaway image in the `docs-checker` repository, so the scoped credentials apply.
It also does not wait for the real docs-checker image.

```sh
mkdir -p /tmp/ecr-probe && cd /tmp/ecr-probe
cat > probe.sh <<'EOF'
#!/bin/sh
# K2 acceptance probe: from inside a Substrate sandbox, look for atelet's pull credentials.
mkdir -p /www && echo ok > /www/readyz
httpd -p 8080 -h /www
while true; do
  for p in /opt/ate-image-credential-provider /run/image-credential-provider; do
    if [ -e "$p" ]; then echo "PROBE FOUND $p"; else echo "PROBE absent $p"; fi
  done
  hits=$(find / -xdev -type f -size -1024k 2>/dev/null | grep -v '^/probe.sh$' | xargs grep -l 'docs-checker-pull' 2>/dev/null)
  echo "PROBE files naming the profile: ${hits:-none}"
  sleep 30
done
EOF
cat > Dockerfile <<'EOF'
FROM busybox:1.37.0@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e
ARG STAMP
LABEL org.opencontainers.image.description="Substrate ECR pull probe ${STAMP}"
COPY probe.sh /probe.sh
ENTRYPOINT ["/bin/sh", "/probe.sh"]
EOF
aws ecr get-login-password --region us-east-2 | docker login -u AWS --password-stdin 852893458518.dkr.ecr.us-east-2.amazonaws.com
docker buildx build --platform linux/amd64 --provenance=false --build-arg STAMP=$(date +%s) \
  -t 852893458518.dkr.ecr.us-east-2.amazonaws.com/docs-checker:pull-probe-1 --push .
```

A new `STAMP` gives a new digest, so the image is uncached on every node. Each probe tag counts toward
the repository's `keep_tagged = 10`, so delete them afterwards (Cleanup).

**kubectl-ate** (no release binary): `git clone --depth 1 --branch v0.5.0-alpha2 https://github.com/kagent-dev/substrate`
and `go build -o ~/.local/bin/kubectl-ate ./cmd/kubectl-ate` (Go ≥ 1.27). It authenticates with a
ServiceAccount token for the audience `api.ate-system.svc`:
`kubectl create token default -n default --audience api.ate-system.svc --duration 1h > /tmp/ate-token`,
then pass `--token-file /tmp/ate-token` to every call. Then:

```sh
kubectl ate create atespace ecr-probe --token-file /tmp/ate-token
# copy workerSelector, resources, sandboxConfig and snapshotConfig from a template kagent made:
kubectl ate get actor-templates -a kagent -o yaml --token-file /tmp/ate-token
```

`probe-template.yaml` (protojson `ActorTemplate`):

```yaml
metadata: {atespace: ecr-probe, name: probe-1}
workerSelector: <copied from kagent's template>
containers:
- name: probe
  image: 852893458518.dkr.ecr.us-east-2.amazonaws.com/docs-checker@<digest of pull-probe-1>
  wakeupProbe: {httpGet: {path: /readyz, port: 8080}}
resources: <copied>
sandboxConfig: {sandboxClass: SANDBOX_CLASS_GVISOR, configName: gvisor-default}
snapshotConfig: {onCommit: SNAPSHOT_CONTENT_SCOPE_FULL, storageLocation: "s3://ate-snapshots/ecr-probe/"}
```

**A. An uncached digest on a fresh worker.**
1. `kubectl ate create actor-template -f probe-template.yaml --token-file /tmp/ate-token`. The golden
   build pulls the new digest on some worker.
2. `kubectl -n ate-system logs -l app=atelet --since=15m --prefix | grep -E 'Resolved image credentials|Image credential provider failed'`
   must show `provider=ecr-credential-provider image=852893458518.dkr.ecr.us-east-2.amazonaws.com/docs-checker cached=false`
   on that node, and no failure. `kubectl ate get actor-templates -a ecr-probe` must show a `GOLDEN TAG`
   and no `ERROR`.
3. The golden build runs on whichever worker is free. To cover the other one, repeat with a new probe
   digest (new `STAMP`, template `probe-2`) until both workers have logged a `cached=false`
   resolution. Restarting atelet does not reset this: the image cache is on disk. Alternatively,
   accept one worker, since the playbook verified both.

**B. A pull after key rotation.**
1. `scripts/provision-docs-checker-pull.sh --rotate`. The new key goes to Vault and both workers, and
   the playbook's verify passes with it on each. The old key stays active.
2. `scripts/provision-docs-checker-pull.sh --retire-old`. Every key except Vault's is deleted.
3. `kubectl -n ate-system rollout restart ds/atelet && kubectl -n ate-system rollout status ds/atelet`.
   This drops the token cached from the old key (about 6 h otherwise).
4. Push a new probe digest (new `STAMP`, tag `pull-probe-2`), then create template `probe-2` with it.
   Expect the same log line, `cached=false`, and a golden tag.
5. `aws iam get-access-key-last-used --access-key-id <the new id, from vault kv get -field=aws-access-key-id k8s-secrets/docs-checker-pull>`
   must show `ServiceName: ecr` with a `LastUsedDate` after step 4.

**C. The credential file is unreadable from a sandbox.**
- *C1, structural (deterministic; run it even if C2 cannot run):*
  - on each worker: `sudo stat -c '%a %U:%G %n' /opt/ate-image-credential-provider /opt/ate-image-credential-provider/*`
    shows `700 root:root` for the directory, `755` for the binary and `600` for `aws-credentials`;
  - `kubectl -n kagent get pods -l ate.dev/worker-pool -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.volumes[*].hostPath.path}{"\n"}{end}'`
    lists only `/var/lib/ate` for every worker pod;
  - on each worker, every running actor's OCI mounts:
    `sudo find /var/lib/ate/actors -path '*/bundles/*/config.json' -exec python3 -c 'import json,sys; [print(m.get("source","")) for m in json.load(open(sys.argv[1])).get("mounts",[])]' {} \; | sort -u`
    shows only `proc`, `tmpfs`, `sysfs`, `/etc/resolv.conf` and paths under `/var/lib/ate/`.
- *C2, from inside a sandbox:* `kubectl ate create actor probe-1a --template probe-1 -a ecr-probe`, then
  `kubectl ate resume actor probe-1a -a ecr-probe` and `kubectl ate logs actors probe-1a -a ecr-probe`.
  Every round must print `PROBE absent /opt/ate-image-credential-provider`,
  `PROBE absent /run/image-credential-provider` and `PROBE files naming the profile: none`.

**Cleanup.** `kubectl ate delete actor probe-1a -a ecr-probe --any-state`,
`kubectl ate delete actor-template probe-1 -a ecr-probe` (and `probe-2`), then
`aws ecr batch-delete-image --repository-name docs-checker --region us-east-2 --image-ids imageTag=pull-probe-1 imageTag=pull-probe-2`
and `rm /tmp/ate-token`.

## Rotation (steady state)

This is the same sequence as acceptance B, steps 1 to 3. The playbook's verify proves the new key from
each worker before anything is deleted. A worker added or rebuilt later needs
`scripts/provision-docs-checker-pull.sh --limit <host>` before atelet can start there (Q3).
