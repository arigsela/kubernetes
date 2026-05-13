# smoke-test-workload chart

Minimal Helm chart consumed by the `XSmokeTestApp` Crossplane Composition.
Templates a hello-world Deployment + Service + Ingress.

| Value | Type | Default | Description |
|---|---|---|---|
| `image` | string | _required_ | Container image |
| `port` | integer | 80 | Container port |
| `host` | string | _required_ | Ingress hostname (full FQDN) |
| `replicas` | integer | 1 | Pod replicas |

Not for direct user consumption — the Composition fills these values from the `SmokeTestApp` claim.

See:
- `docs/superpowers/specs/2026-05-13-smoke-test-app-design.md`
- `docs/plans/smoke-test-app-implementation-plan.md`
