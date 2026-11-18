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


# --- The training-sequence assistant -------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from toolving.playground.agent import (  # noqa: E402
    REPLY_SCHEMA,
    SequenceAgent,
    SequenceError,
    load_instructions,
    validate_sequence,
)


def model_reply(reply, sequence=None, stop_reason="end_turn"):
    text = json.dumps({"reply": reply, "sequence": sequence})
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])


@pytest.fixture
def assistant():
    """A playground whose assistant calls a stand-in for the Anthropic API."""
    calls, replies = [], []

    def create(**request):
        calls.append(request)
        return replies.pop(0)

    agent = SequenceAgent(create, model="test-model", instructions="INSTRUCTIONS")
    client = LearnerClient(api_key="slk_abc123_secret", http_client=httpx.Client(
        base_url="https://learner.test", transport=httpx.MockTransport(lambda request: httpx.Response(500))))
    port = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(client, TOKEN, port, agent))
    port["port"] = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port['port']}", calls, replies
    server.shutdown()
    server.server_close()


def test_instructions_are_the_packaged_file_and_cover_the_sequence_format():
    text = load_instructions()
    assert "one question line followed by one answer line" in text
    assert "single-line" in text and "`sequence`" in text
    assert SequenceAgent(lambda **_: None).instructions == text


def test_validation_accepts_the_training_sequence_format():
    sequence = validate_sequence([
        {"question": "Where do Acme invoices go?", "answer": "Finance / vendors / Acme"},
        {"question": " Where do Globex invoices go? ", "answer": "Finance / vendors / Globex"},
    ])
    assert sequence.text == ("Where do Acme invoices go?\nFinance / vendors / Acme\n\n"
                             "Where do Globex invoices go?\nFinance / vendors / Globex\n")
    assert sequence.to_json()["pairs"][1] == {"question": "Where do Globex invoices go?", "answer": "Finance / vendors / Globex"}


@pytest.mark.parametrize("items, message", [
    ([], "at least one teaching"),
    (None, "at least one teaching"),
    (["Where?"], "Teaching 1 is not a question and answer"),
    ([{"question": "Where?", "answer": " "}], "Teaching 1 has no answer"),
    ([{"question": "q", "answer": "a"}, {"answer": "a"}], "Teaching 2 has no question"),
    ([{"question": "Where?\nAnd why?", "answer": "a"}], "question must be a single line"),
    ([{"question": "Where?", "answer": "line\r\nbreak"}], "answer must be a single line"),
])
def test_validation_rejects_what_the_format_cannot_hold(items, message):
    with pytest.raises(SequenceError, match=message):
        validate_sequence(items)


def test_assistant_needs_the_token(assistant):
    base, calls, _ = assistant
    assert call(base, "POST", "/api/agent/chat", {"messages": [{"role": "user", "content": "hi"}]}, token="")[0] == 403
    assert calls == []


def test_session_reports_whether_the_assistant_is_available(assistant, playground):
    assert call(assistant[0], "GET", "/api/session")[1]["assistant"] == {"available": True, "model": "test-model"}
    off = call(playground[0], "GET", "/api/session")[1]["assistant"]
    assert off["available"] is False and off["reason"]
    status, error = call(playground[0], "POST", "/api/agent/chat", {"messages": [{"role": "user", "content": "hi"}]})
    assert status == 503 and error["error"]["code"] == "AGENT_UNAVAILABLE"


def test_conversation_asks_then_proposes_a_valid_sequence(assistant):
    base, calls, replies = assistant
    replies.append(model_reply("What questions will Conceiver be asked?"))
    first = [{"role": "user", "content": "I want Conceiver to file invoices."}]
    status, turn = call(base, "POST", "/api/agent/chat", {"messages": first})
    assert status == 200 and turn["reply"] == "What questions will Conceiver be asked?"
    assert turn["sequence"] is None and turn["sequence_error"] is None

    pairs = [{"question": "Where do Acme invoices go?", "answer": "Finance / vendors / Acme"}]
    replies.append(model_reply("Here is a sequence.", pairs))
    second = first + [{"role": "assistant", "content": turn["raw"]},
                      {"role": "user", "content": "Where do Acme invoices go? Finance / vendors / Acme."}]
    status, turn = call(base, "POST", "/api/agent/chat", {"messages": second})
    assert status == 200 and turn["sequence"] == {"pairs": pairs, "text": "Where do Acme invoices go?\nFinance / vendors / Acme\n"}

    request = calls[-1]
    assert request["model"] == "test-model" and request["system"] == "INSTRUCTIONS"
    assert request["messages"] == second
    assert request["output_config"]["format"] == {"type": "json_schema", "schema": REPLY_SCHEMA}


def test_an_invalid_proposal_is_reported_not_offered(assistant):
    base, _, replies = assistant
    replies.append(model_reply("Here.", [{"question": "Two\nlines", "answer": "a"}]))
    status, turn = call(base, "POST", "/api/agent/chat", {"messages": [{"role": "user", "content": "go"}]})
    assert status == 200 and turn["sequence"] is None and "single line" in turn["sequence_error"]


@pytest.mark.parametrize("messages", [
    None,
    [],
    [{"role": "assistant", "content": "hi"}],
    [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}],
    [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
    [{"role": "user", "content": "  "}],
])
def test_malformed_conversations_are_refused_before_the_model_is_called(assistant, messages):
    base, calls, _ = assistant
    status, error = call(base, "POST", "/api/agent/chat", {"messages": messages})
    assert status == 400 and error["error"]["code"] == "INVALID_REQUEST"
    assert calls == []


@pytest.mark.parametrize("reply, code", [
    (model_reply("", stop_reason="refusal"), "AGENT_REFUSED"),
    (model_reply("cut", stop_reason="max_tokens"), "AGENT_TRUNCATED"),
    (SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="not json")]), "AGENT_BAD_OUTPUT"),
])
def test_model_failures_come_back_as_errors(assistant, reply, code):
    base, _, replies = assistant
    replies.append(reply)
    status, error = call(base, "POST", "/api/agent/chat", {"messages": [{"role": "user", "content": "go"}]})
    assert status == 502 and error["error"]["code"] == code


def test_ui_has_the_assistant_panel(playground):
    with urllib.request.urlopen(playground[0] + "/", timeout=5) as response:
        page = response.read().decode()
    assert 'id="assistant"' in page and 'id="assistant-toggle"' in page
