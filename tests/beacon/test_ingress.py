# tests/beacon/test_ingress.py
"""beacon §T.16: beacon.arigsela.com on the shared Gateway, allow-listed (beacon §V.20)."""
from conftest import HOST, load, one


def listener():
    gw = one("base-apps/istio-ingress/gateway.yaml", "Gateway", "main")
    (l,) = [l for l in gw["spec"]["listeners"] if l.get("hostname") == HOST]
    return l


def test_listener_terminates_tls_with_beacons_certificate():
    l = listener()
    assert l["name"] == "https-beacon" and l["protocol"] == "HTTPS" and l["port"] == 443
    assert l["tls"]["certificateRefs"] == [{"kind": "Secret", "name": "beacon-tls", "namespace": "beacon"}]
    cert = one("base-apps/beacon/certificate.yaml", "Certificate", "beacon-tls")
    assert cert["spec"]["secretName"] == "beacon-tls" and cert["spec"]["dnsNames"] == [HOST]
    grant = one("base-apps/beacon/reference-grant.yaml", "ReferenceGrant", "gateway-to-beacon-tls")
    assert grant["spec"]["to"] == [{"group": "", "kind": "Secret", "name": "beacon-tls"}]
    assert grant["spec"]["from"][0]["namespace"] == "istio-ingress"


def test_route_attaches_to_this_listener():
    route = one("base-apps/beacon/httproute.yaml", "HTTPRoute", "beacon")
    assert route["spec"]["parentRefs"][0]["sectionName"] == listener()["name"]


def test_v20_host_is_allow_listed_never_open():
    ap = one("base-apps/istio-ingress/authorizationpolicy.yaml", "AuthorizationPolicy", "gateway-allow")
    rules = [r for r in ap["spec"]["rules"] for t in r.get("to", []) if HOST in t["operation"].get("hosts", [])]
    assert rules, "beacon.arigsela.com has no allow-list rule: the Gateway would refuse it"
    for r in rules:
        # Every rule naming the host restricts the source: no path is public until
        # beacon's WAF and rate-limit tests pass (beacon §V.20, §T.36).
        assert r.get("from"), "an allow-list rule for beacon without `from:` makes it public"
        assert f"{HOST}:*" in r["to"][0]["operation"]["hosts"]


def test_wan_ip_monitor_keeps_the_record_on_the_current_address():
    cj = one("base-apps/wan-ip-monitor/cronjob.yaml", "CronJob", "wan-ip-monitor")
    env = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"]
    raw = next(e["value"] for e in env if e["name"] == "MANAGED_HOSTNAMES")
    assert HOST in [h.strip() for h in raw.split(",")]
