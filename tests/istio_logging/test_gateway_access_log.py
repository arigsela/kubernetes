"""The ingress gateway's access log must not record query strings.

The gateway logs through Istio's built-in `envoy` provider
(base-apps/istio-ingress/telemetry.yaml). With no LogFormat of its own, that
provider formats lines from meshConfig.accessLogFormat (Istio 1.30.3,
pilot/pkg/model/telemetry_logging.go: telemetryAccessLog -> FileAccessLogFromMeshConfig).
Istio's default path field, %REQ(X-ENVOY-ORIGINAL-PATH?:PATH)%, includes the query
string, so search text (agent-audit-web's ?q=), OAuth ?code=/&state= callbacks and
anything else carried in a URL landed in Loki for 30 days.

The mesh format is Istio's own default with exactly one field changed.
"""
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Istio 1.30.3 EnvoyTextLogFormat, verbatim (pilot/pkg/model/telemetry_logging.go).
ISTIO_DEFAULT = (
    "[%START_TIME%] \"%REQ(:METHOD)% %REQ(X-ENVOY-ORIGINAL-PATH?:PATH)% "
    "%PROTOCOL%\" %RESPONSE_CODE% %RESPONSE_FLAGS% "
    "%RESPONSE_CODE_DETAILS% %CONNECTION_TERMINATION_DETAILS% "
    "\"%UPSTREAM_TRANSPORT_FAILURE_REASON%\" %BYTES_RECEIVED% %BYTES_SENT% "
    "%DURATION% %RESP(X-ENVOY-UPSTREAM-SERVICE-TIME)% \"%REQ(X-FORWARDED-FOR)%\" "
    "\"%REQ(USER-AGENT)%\" \"%REQ(X-REQUEST-ID)%\" \"%REQ(:AUTHORITY)%\" \"%UPSTREAM_HOST%\" "
    "%UPSTREAM_CLUSTER_RAW% %UPSTREAM_LOCAL_ADDRESS% %DOWNSTREAM_LOCAL_ADDRESS% "
    "%DOWNSTREAM_REMOTE_ADDRESS% %REQUESTED_SERVER_NAME% %ROUTE_NAME%\n"
)
PATH_WITH_QUERY = "%REQ(X-ENVOY-ORIGINAL-PATH?:PATH)%"
PATH_WITHOUT_QUERY = "%REQ_WITHOUT_QUERY(X-ENVOY-ORIGINAL-PATH?:PATH)%"


def _mesh_log_format() -> str:
    app = yaml.safe_load((ROOT / "base-apps/istio-istiod.yaml").read_text())
    values = yaml.safe_load(app["spec"]["source"]["helm"]["values"])
    return (values.get("meshConfig") or {}).get("accessLogFormat", "")


def test_gateway_log_format_strips_query_strings():
    fmt = _mesh_log_format()
    assert PATH_WITHOUT_QUERY in fmt
    assert PATH_WITH_QUERY not in fmt and "%REQ(:PATH)%" not in fmt


def test_format_is_istio_default_except_the_path_field():
    """Anything else changed would silently alter every gateway log line."""
    expected = ISTIO_DEFAULT.replace(PATH_WITH_QUERY, PATH_WITHOUT_QUERY)
    assert _mesh_log_format().rstrip("\n") == expected.rstrip("\n")


def test_gateway_uses_the_builtin_provider_that_reads_the_mesh_format():
    """A custom provider (or a LogFormat on it) would bypass meshConfig entirely."""
    telemetry = yaml.safe_load((ROOT / "base-apps/istio-ingress/telemetry.yaml").read_text())
    providers = [p["name"] for rule in telemetry["spec"]["accessLogging"] for p in rule["providers"]]
    assert providers == ["envoy"]
