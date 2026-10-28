# tests/n8n_bug_investigator/test_clients.py
"""The GitHub client, the MCP client, countResults and the n8n http adapter (§T.152, Task 3)."""
from __future__ import annotations

import json

import pytest

from conftest import JsError

API = "https://api.github.com"
TOKEN = "tok-abcdefgh-12345"  # matches no scrub pattern: only the extra-secret path can redact it
MCP = "http://telemetry.test/mcp"


def http(routes, throws=None):
    return {"fake": "fakeHttp", "spec": {"routes": routes, "throws": throws or {}}}


# ------------------------------------------------------------------------------------- GitHub


def test_get_all_follows_link_next(js):
    base = f"{API}/repos/o/r/issues?state=open&per_page=100"
    routes = {
        f"GET {base}": {"body": [1, 2], "headers": {"link": f'<{base}&page=2>; rel="next", <{base}&page=3>; rel="last"'}},
        f"GET {base}&page=2": {"body": [3], "headers": {"link": f'<{base}&page=3>; rel="next"'}},
        f"GET {base}&page=3": {"body": [4]},
    }
    reply = js("github", "ghCall", TOKEN, "getAll", "/repos/o/r/issues", {"state": "open"}, ctx=http(routes), full=True)
    assert reply["value"] == {"value": [1, 2, 3, 4], "requests": 3}
    assert [c["url"] for c in reply["calls"]] == [base, f"{base}&page=2", f"{base}&page=3"]
    assert all(c["headers"]["authorization"] == f"Bearer {TOKEN}" for c in reply["calls"])


def test_get_all_stops_at_max_pages(js):
    base = f"{API}/repos/o/r/commits?per_page=100"
    routes = {f"GET {base}": {"body": [1], "headers": {"link": f'<{base}>; rel="next"'}}}
    value = js("github", "ghCall", TOKEN, "getAll", "/repos/o/r/commits", {}, 2, ctx=http(routes))
    assert value == {"value": [1, 1], "requests": 2}


def test_errors_never_carry_the_token(js):
    routes = {f"GET {API}/user": {"status": 401, "body": {"message": f"Bad credentials for {TOKEN}"}}}
    with pytest.raises(JsError) as e:
        js("github", "ghCall", TOKEN, "get", "/user", ctx=http(routes))
    assert e.value.name == "GitHubError" and e.value.extra["status"] == 401 and e.value.extra["path"] == "/user"
    assert "[redacted]" in e.value.message and TOKEN not in e.value.message

    throws = {f"GET {API}/user": f"connect ECONNREFUSED while sending {TOKEN}"}
    with pytest.raises(JsError) as e:
        js("github", "ghCall", TOKEN, "get", "/user", ctx=http({}, throws))
    assert e.value.name == "GitHubError" and "[redacted]" in e.value.message and TOKEN not in e.value.message


def test_request_returns_status_without_throwing(js):
    routes = {f"POST {API}/repos/o/r/issues/7/reactions": {"status": 200, "body": {"id": 9, "content": "eyes"}}}
    reply = js("github", "ghCall", TOKEN, "request", "POST", "/repos/o/r/issues/7/reactions", {"content": "eyes"},
               ctx=http(routes), full=True)
    assert reply["value"]["value"] == {"status": 200, "body": {"id": 9, "content": "eyes"}}
    assert reply["calls"][0]["body"] == {"content": "eyes"}


def test_graphql_returns_data_and_raises_on_errors(js):
    ok = {f"POST {API}/graphql": {"body": {"data": {"repository": {"id": "R1"}}}}}
    reply = js("github", "ghCall", TOKEN, "graphql", "query($n: Int!) { x }", {"n": 1}, ctx=http(ok), full=True)
    assert reply["value"]["value"] == {"repository": {"id": "R1"}}
    assert reply["calls"][0]["body"] == {"query": "query($n: Int!) { x }", "variables": {"n": 1}}

    bad = {f"POST {API}/graphql": {"body": {"data": None, "errors": [{"message": "Field 'x' doesn't exist"}]}}}
    with pytest.raises(JsError) as e:
        js("github", "ghCall", TOKEN, "graphql", "{ x }", {}, ctx=http(bad))
    assert e.value.name == "GitHubError" and "Field 'x' doesn't exist" in e.value.message


def test_login_is_cached(js):
    routes = {f"GET {API}/user": {"body": {"login": "ari"}}}
    # login() twice on one client: one request.
    reply = js("github", "ghLoginTwice", TOKEN, ctx=http(routes), full=True)
    assert reply["value"] == ["ari", "ari"] and len(reply["calls"]) == 1


# ---------------------------------------------------------------------------------------- MCP


def _init(session="s-1", sse=False):
    msg = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "t"}}}
    return _reply(msg, sse, {"mcp-session-id": session})


def _reply(msg, sse=False, headers=None):
    h = dict(headers or {})
    if sse:
        h["content-type"] = "text/event-stream"
        return {"headers": h, "body": f"event: message\ndata: {json.dumps(msg)}\n\n"}
    h["content-type"] = "application/json"
    return {"headers": h, "body": msg}


def _tool_reply(rid, text, is_error=False, sse=False):
    return _reply({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}], "isError": is_error}}, sse)


@pytest.mark.parametrize("sse", [False, True], ids=["json", "sse"])
def test_call_tool_reply(js, sse):
    routes = {f"POST {MCP}": [_init(sse=sse), {"status": 202}, _tool_reply(2, "hello", sse=sse)], f"DELETE {MCP}": {"status": 202}}
    reply = js("mcp", "callTool", MCP, "query_prometheus", {"expr": "up"}, ctx=http(routes), full=True)
    assert reply["value"] == {"isError": False, "text": "hello"}
    methods = [(c["method"], (c["body"] or {}).get("method")) for c in reply["calls"]]
    assert methods == [("POST", "initialize"), ("POST", "notifications/initialized"), ("POST", "tools/call"), ("DELETE", None)]
    assert "mcp-session-id" not in reply["calls"][0]["headers"]
    assert all(c["headers"]["mcp-session-id"] == "s-1" for c in reply["calls"][1:])
    assert reply["calls"][2]["body"]["params"] == {"name": "query_prometheus", "arguments": {"expr": "up"}}


def test_session_is_reused_and_closed(js):
    routes = {f"POST {MCP}": [_init(), {"status": 202}, _tool_reply(2, "a"), _tool_reply(3, "b")], f"DELETE {MCP}": {"status": 202}}
    reply = js("mcp", "mcpSession", MCP, [["query_loki_logs", {}], ["query_prometheus", {}]], ctx=http(routes), full=True)
    assert [r["text"] for r in reply["value"]] == ["a", "b"]
    bodies = [(c["body"] or {}).get("method") for c in reply["calls"]]
    assert bodies.count("initialize") == 1 and bodies.count("tools/call") == 2
    assert [c["method"] for c in reply["calls"]][-1] == "DELETE"


def test_call_tool_error_is_not_thrown(js):
    rpc_error = _reply({"jsonrpc": "2.0", "id": 2, "error": {"code": -32602, "message": "unknown tool"}})
    routes = {f"POST {MCP}": [_init(), {"status": 202}, rpc_error], f"DELETE {MCP}": {"status": 202}}
    assert js("mcp", "callTool", MCP, "nope", {}, ctx=http(routes)) == {"isError": True, "text": "unknown tool"}
    refused = _tool_reply(2, "refused: needs a matcher", is_error=True)
    routes = {f"POST {MCP}": [_init(), {"status": 202}, refused], f"DELETE {MCP}": {"status": 202}}
    assert js("mcp", "callTool", MCP, "list_loki_label_names", {}, ctx=http(routes)) == {"isError": True, "text": "refused: needs a matcher"}


def test_initialize_without_a_session_throws(js):
    routes = {f"POST {MCP}": {"status": 503, "body": "upstream down"}}
    with pytest.raises(JsError) as e:
        js("mcp", "callTool", MCP, "query_prometheus", {}, ctx=http(routes))
    assert "initialize" in e.value.message and "503" in e.value.message


def test_count_results(js):
    logs = json.dumps([{"timestamp": "t", "labels": {}, "line": f"l{i}"} for i in range(3)])
    series = json.dumps({"resultType": "matrix", "series": [{"labels": {}, "values": [
        {"time": "t", "value": "0"}, {"time": "t", "value": "NaN"}, {"time": "t", "value": "1.5"}, {"time": "t", "value": "+Inf"}]}]})
    series_list = json.dumps([{"labels": {}, "values": [{"time": "t", "value": "2"}, {"time": "t", "value": "-Inf"}]}])
    wrapped_logs = json.dumps({"truncated": True, "returned": 2, "total": 5, "items": [{"line": "a"}, {"line": "b"}]})
    wrapped_series_empty = json.dumps({"truncated": True, "returned": 0, "total": 4, "items": []})
    wrapped_series = json.dumps({"truncated": True, "returned": 1, "total": 9, "items": [{"labels": {}, "values": [{"time": "t", "value": "3"}]}]})
    cut = '{"resultType":"matrix","series":[{"labels":{},"values":[{"time":"t","value":"1"}' + " ...[truncated]"
    cases = [(logs, 3), (series, 2), (series_list, 2), (wrapped_logs, 5), (wrapped_series_empty, None),
             (wrapped_series, 1), (cut, None), ("oops", None), ("[]", 0), (json.dumps({"resultType": "vector", "series": []}), 0)]
    for text, want in cases:
        assert js("mcp", "countResults", text) == want, text[:80]


# ---------------------------------------------------------------------------------------- n8n


def test_http_from_helpers_maps_full_response(js):
    ctx = {"fake": "fakeHelpers", "spec": {"reply": {"statusCode": 404, "statusMessage": "Not Found",
                                                     "headers": {"Content-Type": "application/json", "X-Foo": "a"}, "body": {"message": "x"}}}}
    reply = js("n8n", "httpVia", {"method": "GET", "url": f"{API}/x", "headers": {"Accept": "a"}}, ctx=ctx, full=True)
    assert reply["value"] == {"status": 404, "headers": {"content-type": "application/json", "x-foo": "a"}, "body": {"message": "x"}}
    opts = reply["calls"][0]
    assert opts["json"] is True and opts["returnFullResponse"] is True and opts["ignoreHttpStatusErrors"] is True
    assert opts["timeout"] == 20000 and opts["url"] == f"{API}/x" and opts["method"] == "GET"

    reply = js("n8n", "httpVia", {"method": "POST", "url": f"{API}/y", "body": {"a": 1}, "timeoutMs": 5000}, ctx=ctx, full=True)
    assert reply["calls"][0]["timeout"] == 5000 and reply["calls"][0]["body"] == {"a": 1}
