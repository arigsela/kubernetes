"""base-apps/model-gateway/configmap.yaml: the gateway's config, generated from budgets.yaml
(spec §3.1a, §3.2). The local proof (tests/model-gateway/proof/) ran these exact shapes; these
tests keep the committed config to them."""
import re

import pytest
import yaml

import model_budgets as mb
from conftest import REPO
from test_alerts import _gen

CM = REPO / "base-apps/model-gateway/configmap.yaml"


@pytest.fixture(scope="module")
def cfg():
    doc = yaml.safe_load(CM.read_text())
    assert doc["kind"] == "ConfigMap" and doc["metadata"] == {
        "name": "model-gateway-config", "namespace": "model-gateway",
        "labels": {"app.kubernetes.io/name": "model-gateway", "app.kubernetes.io/part-of": "model-gateway"}}
    return yaml.safe_load(doc["data"]["config.yaml"])


def test_one_key_per_group_with_its_daily_budget(cfg):
    table = mb.load()
    keys = cfg["llm"]["policies"]["apiKey"]["keys"]
    assert [k["metadata"]["name"] for k in keys] == list(table["groups"])
    for k in keys:
        g = k["metadata"]["name"]
        spec = table["groups"][g]
        assert k["key"] == "$" + mb.env_name(g), "keys come from the environment, never the file"
        assert k["metadata"] == {"name": g, "group": g}
        assert k["allowedModels"] == spec["models"]
        assert k["budgets"] == [{"name": "daily", "limit": {"unit": "USD", "amount": spec["daily_usd"]},
                                 "window": {"rolling": "24h"}, "onBudgetExceeded": "Block"}]


def test_api_key_is_read_from_x_api_key_strictly(cfg):
    pol = cfg["llm"]["policies"]["apiKey"]
    assert pol["mode"] == "strict" and pol["location"] == {"header": {"name": "x-api-key"}}


def test_models_are_anthropic_passthrough_with_the_provider_key_file(cfg):
    used = sorted({m for g in mb.load()["groups"].values() for m in g["models"]})
    models = cfg["llm"]["models"]
    assert [m["name"] for m in models] == used
    for m in models:
        assert m["provider"] == "anthropic" and m["passthrough"] == "detect"
        assert m["params"] == {"apiKey": {"file": "/var/run/model-gateway/anthropic/api-key"}}, \
            "no baseUrl: the provider's own https://api.anthropic.com"


def test_catalog_prices_equal_the_collector(cfg):
    prices = mb.collector_prices()
    catalog = cfg["config"]["modelCatalog"][0]["inline"]["providers"]["anthropic"]["models"]
    used = {m for g in mb.load()["groups"].values() for m in g["models"]}
    assert set(catalog) == used

    def rates(t):
        return {"input": str(t["input"]), "output": str(t["output"]),
                "cacheRead": str(t["cache_read"]), "cacheWrite": str(t["cache_write"])}

    for model, entry in catalog.items():
        tiers = prices[model]
        assert entry["rates"] == rates(tiers[0])
        assert entry.get("tiers", []) == [{"contextOver": t["over"], "rates": rates(t)} for t in tiers[1:]]
    assert catalog["claude-haiku-5-5"]["tiers"][0]["contextOver"] == 100000


def test_budget_refusal_is_not_retried(cfg):
    """The front listener (8080) proxies to the llm listener (127.0.0.1:8081) and marks the
    gateway's own 429 (no Anthropic request-id) x-should-retry: false (proof checks 5b, 6, 11)."""
    gen = _gen()
    (front,) = cfg["binds"]
    assert front["port"] == 8080
    (listener,) = front["listeners"]
    (route,) = listener["routes"]
    assert route["backends"] == [{"host": "127.0.0.1:8081", "weight": 100}]
    rule = route["policies"]["transformations"]["response"]["set"]["x-should-retry"]
    assert rule == gen.NO_RETRY
    assert 'response.code == 429' in rule and '"request-id" in response.headers' in rule and '"false"' in rule
    assert cfg["gateways"] == {"llm": {"port": 8081, "bindAddress": "127.0.0.1"}}
    assert cfg["llm"]["gateways"] == "llm"


def test_database_metrics_and_addresses(cfg):
    c = cfg["config"]
    assert c["database"] == {"url": "$MODEL_GATEWAY_DB_URL"}
    assert c["metrics"]["fields"]["add"]["group"] == 'default(apiKey.group, "")'
    assert c["statsAddr"] == "0.0.0.0:15020" and c["readinessAddr"] == "0.0.0.0:15021"
    assert "adminAddr" not in c, "the admin API (/api/budgets/status) stays on 127.0.0.1: port-forward to read it"


def test_tracing_to_the_collector(cfg):
    assert cfg["frontendPolicies"]["tracing"] == {
        "host": "otel-collector.tracing.svc.cluster.local:4317", "protocol": "grpc",
        "randomSampling": "true", "resources": {"service.name": '"model-gateway"'}}


def test_every_dollar_is_an_expected_env_reference():
    """agentgateway shell-expands the whole file: a stray $ would fail the start or leak an env value."""
    text = yaml.safe_load(CM.read_text())["data"]["config.yaml"]
    want = {mb.env_name(g) for g in mb.load()["groups"]} | {"MODEL_GATEWAY_DB_URL"}
    assert set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", text)) == want
    assert text.count("$") == len(want)
