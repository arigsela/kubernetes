# homelab-agent — Vault provisioning & rollout runbook

One-time operator actions (run with a Vault root/admin token) to back the
ESO manifests in `base-apps/postgresql/` and `base-apps/kagent/`. Nothing here
is committed with real values; the DB password is generated inline.

## 0. Recommended: the provisioning script

`scripts/provision-homelab-agent-vault.sh` does everything in sections 1–3
idempotently (generates the DB password once and preserves it on re-run;
reads the app tokens from env vars so they never hit shell history):

```sh
kubectl -n vault cp scripts/provision-homelab-agent-vault.sh vault-0:/tmp/prov.sh
kubectl -n vault exec -it vault-0 -- sh
export VAULT_TOKEN=<root-or-admin-token>
export ANTHROPIC_API_KEY=<key>          # first run only (or to rotate)
export BACKSTAGE_MCP_TOKEN=<token>       # first run only (or to rotate)
sh /tmp/prov.sh
unset ANTHROPIC_API_KEY BACKSTAGE_MCP_TOKEN VAULT_TOKEN
rm /tmp/prov.sh
exit
```

Then jump to section 4 to roll out and verify. The manual commands below
(sections 1–3) are the equivalent reference if you'd rather run them by hand.

## 1. Vault secrets

```bash
# DB password — URL-safe hex so the DSN needs no escaping. Never printed.
vault kv put k8s-secrets/homelab-agent-db password="$(openssl rand -hex 32)"

# App tokens — replace the placeholders with the real values.
#  - anthropic-api-key: a dedicated Anthropic key for this agent (NOT the shared kagent one)
#  - backstage-token:   the Backstage MCP bearer token value (same one backstage-catalog-mcp uses)
vault kv put k8s-secrets/homelab-agent \
  anthropic-api-key='REPLACE_WITH_ANTHROPIC_KEY' \
  backstage-token='REPLACE_WITH_BACKSTAGE_MCP_TOKEN'
```

## 2. Vault policies (least privilege — each reads only its one path)

```bash
vault policy write homelab-agent - <<'EOF'
path "k8s-secrets/data/homelab-agent" { capabilities = ["read"] }
EOF

vault policy write homelab-agent-db - <<'EOF'
path "k8s-secrets/data/homelab-agent-db" { capabilities = ["read"] }
EOF
```

## 3. Kubernetes-auth roles

```bash
# App-secrets: eso-homelab-agent in kagent only.
vault write auth/kubernetes/role/homelab-agent \
  bound_service_account_names=eso-homelab-agent \
  bound_service_account_namespaces=kagent \
  policies=homelab-agent ttl=1h

# DB password/DSN: eso-homelab-agent-db in BOTH kagent (agent DSN) and
# postgresql (init job). One role, two namespace bounds.
vault write auth/kubernetes/role/homelab-agent-db \
  bound_service_account_names=eso-homelab-agent-db \
  bound_service_account_namespaces=kagent,postgresql \
  policies=homelab-agent-db ttl=1h
```

## 4. Roll out (merge the manifests, let Argo sync) and verify

```bash
# ESO materialized the three secrets:
kubectl -n postgresql get externalsecret homelab-agent-db-credentials   # STATUS SecretSynced
kubectl -n kagent     get externalsecret homelab-agent-secrets          # SecretSynced
kubectl -n kagent     get externalsecret homelab-agent-db               # SecretSynced

# DB provisioned (idempotent hook):
kubectl -n postgresql logs job/init-homelab-agent-db   # "...initialized successfully with pgvector extension"

# Agent up, serving A2A + memory:
kubectl -n kagent get pods -l app.kubernetes.io/name=homelab-agent
kubectl -n kagent port-forward svc/homelab-agent 18080:8080 &
curl -s localhost:18080/health                          # {"status":"healthy","agent":"homelab-agent"}
curl -s localhost:18080/.well-known/agent.json | jq '.skills[].id'   # the three skill ids
```

## 5. Parity & cutover (later, separate)

Compare `homelab-agent` vs `homelab-knowledge` over A2A on the golden
questions (the skill examples). Once parity holds, retire the Declarative
`homelab-knowledge` CR in a follow-up PR. Reversible at each step.

## Rotation

`vault kv put k8s-secrets/homelab-agent-db password=...` then re-run the init
job (ALTER ROLE is idempotent) and restart the agent pod so ESO re-renders the
DSN. Same for `homelab-agent` app tokens (ESO refresh is hourly, or delete the
target Secret to force an immediate resync).
