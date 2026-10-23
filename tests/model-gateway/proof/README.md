# Model gateway: local proof

`run-proof.sh` runs, in Docker:
- Postgres 16;
- a mock of the Anthropic Messages API (`mock_anthropic.py`);
- two gateways from the image the cluster runs, both on the config `scripts/gen-model-budgets.py`
  generates.

The mock stands in for Anthropic and the provider key is a made-up test value, so nothing leaves
the machine. The proof then checks each behaviour the design relies on (spec §7 step 3):
- keys are required, and the upstream sees only the provider key;
- the request body arrives unchanged;
- each key is limited to its group's models;
- prices match the collector's, for Sonnet with cache reads and for Haiku at both tiers;
- a budget refusal is answered 429 `budget_exceeded` with `x-should-retry: false`, while
  Anthropic's own 429 keeps its retries;
- token counting is routed;
- the Anthropic Go SDK at kagent's version (v1.80.0) fails at once on a refusal, instead of
  sleeping for its Retry-After.

Whether two gateways share a count (check 7) is recorded, not gated. In agentgateway 1.6.0 it
failed in about one run in three, which is why the gateway runs one replica (spec §3.1a). Check it
again after an upgrade before going back to two.

```bash
aws ecr get-login-password --region us-east-2 \
  | docker login --username AWS --password-stdin 852893458518.dkr.ecr.us-east-2.amazonaws.com
tests/model-gateway/proof/run-proof.sh          # --no-go skips the Go SDK check, --keep keeps the containers
```

You need about 1.5 GB of free Docker disk (Postgres, the mock, the gateway image, golang:1.25-alpine). A full Docker VM makes Postgres crash at first boot.

Re-run the proof after any agentgateway, Substrate or kagent upgrade, or any change to
`gateway_config()`.
