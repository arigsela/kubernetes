# Composition tests

Local regression suite for `Application` Composition (XRD: `XApplication`).

## Run

```bash
./tests/composition/render.sh xr-minimal
./tests/composition/render.sh xr-with-db
```

Exit code 0 = output matches `expected-<case>.yaml`. Non-zero = diff printed; investigate.

## Update goldens (when Composition intentionally changes)

```bash
# Capture new output as the golden:
crossplane render \
  tests/composition/xr-minimal.yaml \
  base-apps/crossplane-compositions/composition-application.yaml \
  tests/composition/functions.yaml \
  --extra-resources base-apps/crossplane-compositions/xrd-application.yaml \
  | yq -P 'sort_keys(..)' \
  > tests/composition/expected-minimal.yaml
```

## Requires

- `crossplane` CLI
- Docker daemon running (function-python pulls + runs as OCI image)
- `yq` v4
