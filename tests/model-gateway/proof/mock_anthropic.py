#!/usr/bin/env python3
"""A stand-in for api.anthropic.com for the model gateway's local proof (stdlib only).

POST /v1/messages answers like the Messages API, streamed or not. The usage it reports comes from
the request's metadata.user_id, `usage:in=<n>,out=<n>,cr=<n>,cw=<n>` (a real Messages API field, so
the gateway forwards it untouched). A message containing MOCK429 gets Anthropic's own 429 with
retry-after: 3. POST /v1/messages/count_tokens answers {"input_tokens": 42}.
GET /_log returns every request it saw: path, headers (lowercased), body.
"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEEN = []


def usage_of(body):
    spec = ((body.get("metadata") or {}).get("user_id") or "")
    vals = dict(re.findall(r"(in|out|cr|cw)=(\d+)", spec))
    return {"input_tokens": int(vals.get("in", 10)), "output_tokens": int(vals.get("out", 5)),
            "cache_read_input_tokens": int(vals.get("cr", 0)),
            "cache_creation_input_tokens": int(vals.get("cw", 0))}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, code, obj, headers=None):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("request-id", "req_mock")  # every Anthropic response carries one
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/_log":
            return self._json(200, SEEN)
        self._json(404, {"error": "no"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", 0)))
        body = json.loads(raw or b"{}")
        SEEN.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                     "body": body})
        if self.path.endswith("/v1/messages/count_tokens"):
            return self._json(200, {"input_tokens": 42})
        if not self.path.endswith("/v1/messages"):
            return self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": self.path}})
        if "MOCK429" in json.dumps(body.get("messages")):
            return self._json(429, {"type": "error", "error": {"type": "rate_limit_error", "message": "mock limit"}},
                              {"retry-after": "3"})
        usage = usage_of(body)
        model = body.get("model", "claude-sonnet-5-5")
        if not body.get("stream"):
            return self._json(200, {"id": "msg_mock", "type": "message", "role": "assistant", "model": model,
                                    "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
                                    "stop_sequence": None, "usage": usage})
        start = dict(usage, output_tokens=1)
        events = [
            ("message_start", {"type": "message_start", "message": {
                "id": "msg_mock", "type": "message", "role": "assistant", "model": model, "content": [],
                "stop_reason": None, "stop_sequence": None, "usage": start}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": "ok"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": usage["output_tokens"]}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        data = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()
        self.send_response(200)
        self.send_header("request-id", "req_mock")
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
