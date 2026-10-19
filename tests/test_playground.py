"""The playground's local proxy: token and Host gates, allowlist, error passthrough."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import httpx
import pytest

from toolving import LearnerClient
from toolving.playground import build_handler

TOKEN = "t" * 32


@pytest.fixture
def playground():
    seen = []

    def upstream(request):
        seen.append((request.method, request.url.path, json.loads(request.content or b"null"), request.headers))
        if request.url.path == "/v1/learners" and request.method == "GET":
            return httpx.Response(200, json={"learners": [{"learner": "demo", "mode": "LEARNING", "state_version": 2}]})
        if request.url.path.endswith("/predict"):
            return httpx.Response(200, json={"prediction_id": "op_1", "status": "CHOSEN", "output": "red",
                                             "alternatives": ["blue", "red"], "state_version": 3, "reason": None,
                                             "evidence": None, "replayed": False})
        return httpx.Response(404, json={"error": {"code": "LEARNER_NOT_FOUND", "message": "Learner not found.",
                                                   "retryable": False, "request_id": "req_x"}})

    client = LearnerClient(api_key="slk_abc123_secret", http_client=httpx.Client(
        base_url="https://learner.test", transport=httpx.MockTransport(upstream)))
    port = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(client, TOKEN, port))
    port["port"] = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port['port']}", seen
    server.shutdown()
    server.server_close()


def call(base, method, path, body=None, *, token=TOKEN, host=None):
    request = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode())
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("X-Playground-Token", token)
    if host:
        request.add_header("Host", host)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as failure:
        return failure.code, json.loads(failure.read())


def test_ui_assets_are_served(playground):
    base, _ = playground
    for path in ("/", "/app.js", "/style.css"):
        with urllib.request.urlopen(base + path, timeout=5) as response:
            assert response.status == 200


def test_calls_need_the_token_and_a_loopback_host(playground):
    base, seen = playground
    assert call(base, "GET", "/api/learners", token="")[0] == 403
    assert call(base, "GET", "/api/learners", token="wrong")[0] == 403
    assert call(base, "GET", "/api/learners", host="evil.example")[0] == 403
    assert seen == []  # nothing reached the API


def test_allowlisted_calls_are_forwarded_with_auth_and_idempotency(playground):
    base, seen = playground
    status, session = call(base, "GET", "/api/session")
    assert status == 200 and session["key_id"] == "abc123" and "api_key" not in session
    assert call(base, "GET", "/api/learners")[1]["learners"][0]["learner"] == "demo"
    status, prediction = call(base, "POST", "/api/learners/demo/predict", {"input": "red blue"})
    assert status == 200 and prediction["status"] == "CHOSEN"
    method, path, body, headers = seen[-1]
    assert (method, path, body) == ("POST", "/v1/learners/demo/predict", {"input": "red blue"})
    assert headers["authorization"] == "Bearer slk_abc123_secret" and headers["idempotency-key"]


def test_unlisted_calls_are_refused_and_errors_pass_through(playground):
    base, seen = playground
    assert call(base, "POST", "/api/learners/demo/bootstrap", {"input": "x"})[0] == 404
    assert call(base, "GET", "/api/../v1/learners")[0] in (403, 404)
    assert not any(path.endswith("/bootstrap") for _, path, _, _ in seen)
    status, error = call(base, "GET", "/api/learners/missing")
    assert status == 404 and error["error"]["code"] == "LEARNER_NOT_FOUND" and error["error"]["request_id"] == "req_x"
