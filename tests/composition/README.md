# Composition tests

Local regression suite for the `Application` Composition (XRD: `XApplication`,
`base-apps/crossplane-compositions/`). The Composition is deployed but dormant: nothing
instantiates an `XApplication` today (see `templates/new-app/README.md` for the path in use).

**Not run in CI.** The goldens are stale: they predate the Composition's later commits (e.g. the
2026-08-03 ESO migration), so `expected-*.yaml` still say `external-secrets.io/v1beta1` where the
Composition now emits `v1`. Expect a diff on `xr-with-db` and `xr-with-s3` until the goldens are
regenerated. All three cases also still expect the nginx `Ingress` + `letsencrypt-prod` that the
Composition emits (SPEC T76).

## Cases

| Case | Emits | Golden |
|---|---|---|
| `xr-minimal` | Deployment + Service + Ingress | `expected-xr-minimal.yaml` |
| `xr-with-db` | `dbNeeded: true`: + CNPG `Cluster`, ExternalSecret, PushSecret, SecretStore | `expected-xr-with-db.yaml` |
| `xr-with-s3` | `s3Needed: true`: + the AWS-creds Vault round-trip (ExternalSecret, PushSecret, SecretStore) | `expected-xr-with-s3.yaml` |

## Run

```bash
./tests/composition/render.sh xr-minimal
./tests/composition/render.sh xr-with-db
./tests/composition/render.sh xr-with-s3
```

Exit code 0 = output matches `expected-<case>.yaml`. Non-zero = diff printed; investigate.
`render.sh` normalizes both sides before diffing (sorted keys, no comments, no `status`,
`ownerReferences` or composite label).

## Update goldens (when Composition intentionally changes)

Use the same render and normalization as `render.sh` (keep `NORMALIZE` in sync with it):

```bash
CASE=xr-minimal   # or xr-with-db, xr-with-s3
NORMALIZE='[.] | sort_by(.kind) | .[] | (... comments="") | del(.status) | del(.metadata.ownerReferences) | del(.metadata.labels."crossplane.io/composite") | sort_keys(..)'
crossplane render -x \
  tests/composition/$CASE.yaml \
  base-apps/crossplane-compositions/composition-application.yaml \
  tests/composition/functions.yaml \
  --extra-resources base-apps/crossplane-compositions/xrd-application.yaml \
  | yq ea -P "$NORMALIZE" \
  > tests/composition/expected-$CASE.yaml
```

## Requires

- `crossplane` CLI
- Docker daemon running (function-python pulls + runs as OCI image)
- `yq` v4
