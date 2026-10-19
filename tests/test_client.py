from __future__ import annotations

import json

import httpx
import pytest

from toolving import (
    LearnerClient,
    NotFound,
    ServiceUnavailable,
    VersionConflict,
)

PREDICTION = {
    "prediction_id": "op_1",
    "status": "PREDICTED",
    "state_version": 3,
    "output": "done",
    "alternatives": [],
    "reason": None,
    "evidence": {"format": "text/markdown", "markdown": "## Evidence\n", "memory_ids": []},
    "replayed": False,
}


def client_with(handler, **kwargs):
    http = httpx.Client(base_url="https://learner.test", transport=httpx.MockTransport(handler))
    return LearnerClient(api_key="slk_a_b", http_client=http, **kwargs)


def test_abstained_conflict_exposes_alternatives_without_an_answer():
    payload = {**PREDICTION, "status": "ABSTAINED", "output": None,
               "alternatives": ["amber", "violet"], "reason": "CONFLICTING_CONSTRUCTION_OUTPUTS"}
    client = client_with(lambda request: httpx.Response(200, json=payload))
    prediction = client.learner("ops").predict("lookup nup")
    assert prediction.abstained and prediction.ambiguous
    assert not prediction.predicted and not prediction.chosen and not prediction.unsupported
    assert prediction.output is None
    assert prediction.alternatives == ("amber", "violet")


def test_predict_and_handle_methods_carry_ids_privately():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content or b"null"), request.headers))
        if request.url.path.endswith("/predict"):
            return httpx.Response(200, json=PREDICTION)
        if request.url.path.endswith("/teach"):
            return httpx.Response(200, json={"status": "LEARNED"})
        return httpx.Response(200, json={"status": "RECORDED"})

    learner = client_with(handler).learner("ops")
    prediction = learner.predict("do it")
    assert prediction.predicted and prediction.output == "done"
    prediction.outcome({"ok": True}, assessment={"evidence": "caller assessment"})
    assert prediction.teach("better").learned
    paths = [item[1] for item in seen]
    assert paths == [
        "/v1/learners/ops/predict",
        "/v1/learners/ops/predictions/op_1/outcome",
        "/v1/learners/ops/predictions/op_1/teach",
    ]
    assert seen[0][2] == {"input": "do it", "format": "text", "evidence": "narrow"}
    assert seen[1][2] == {"outcome": {"ok": True}, "assessment": {"evidence": "caller assessment"}}
    keys = [item[3]["Idempotency-Key"] for item in seen]
    assert len(set(keys)) == 3  # a fresh key per logical operation
    assert all(item[3]["Authorization"] == "Bearer slk_a_b" for item in seen)


def test_delete_learner_uses_admin_endpoint():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.headers, request.content))
        return httpx.Response(200, json={"status": "DELETED", "learner": "ops"})

    client_with(handler).delete_learner("ops")
    assert seen[0][0:2] == ("DELETE", "/v1/learners/ops")
    assert seen[0][2]["Idempotency-Key"]
    assert seen[0][3] == b""


def test_json_inputs_are_sent_as_json_format():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=PREDICTION)

    client_with(handler).learner("ops").predict({"a": 1}, evidence="none")
    assert bodies == [{"input": {"a": 1}, "format": "json", "evidence": "none"}]


def test_transport_failures_retry_with_the_same_idempotency_key():
    keys = []

    def handler(request):
        keys.append(request.headers["Idempotency-Key"])
        if len(keys) < 3:
            raise httpx.ConnectError("reset")
        return httpx.Response(200, json=PREDICTION)

    client_with(handler).learner("ops").predict("x")
    assert len(keys) == 3 and len(set(keys)) == 1


def test_error_responses_are_never_retried_and_are_typed():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(
            503,
            json={"error": {"code": "POLICY_PROVIDER_FAILED", "message": "down", "retryable": True,
                            "operation_id": "op_9", "request_id": "req_1"}},
        )

    with pytest.raises(ServiceUnavailable) as failure:
        client_with(handler).learner("ops").teach("a", "b")
    assert len(calls) == 1
    assert failure.value.retryable and failure.value.operation_id == "op_9"


@pytest.mark.parametrize(
    "status,code,expected",
    [(409, "VERSION_CONFLICT", VersionConflict), (404, "LEARNER_NOT_FOUND", NotFound)],
)
def test_error_mapping(status, code, expected):
    def handler(request):
        return httpx.Response(status, json={"error": {"code": code, "message": "m", "retryable": False}})

    with pytest.raises(expected):
        client_with(handler).learner("ops").remember("x")


def test_recall_requires_exactly_one_selector():
    learner = client_with(lambda request: httpx.Response(500)).learner("ops")
    with pytest.raises(ValueError):
        learner.recall()
    with pytest.raises(ValueError):
        learner.recall("cue", memory_id="memory-v1-x")


def test_evidence_none_is_an_empty_bundle():
    def handler(request):
        return httpx.Response(200, json={**PREDICTION, "evidence": None})

    prediction = client_with(handler).learner("ops").predict("x", evidence="none")
    assert prediction.evidence.markdown == "" and prediction.evidence.memory_ids == ()
    assert not prediction.evidence.found


def test_error_reason_is_exposed():
    def handler(request):
        return httpx.Response(503, json={"error": {"code": "POLICY_PROVIDER_FAILED", "message": "m",
                                                   "retryable": True, "reason": "upstream decision provider failed (HTTP 529 x3) after 3 attempt(s)"}})

    with pytest.raises(ServiceUnavailable) as failure:
        client_with(handler).learner("ops").remember("x")
    assert failure.value.reason.startswith("upstream decision provider failed")
    assert "HTTP 529" in str(failure.value)


def test_rate_limits_wait_retry_after_with_the_same_key_then_succeed():
    keys, sleeps = [], []

    def handler(request):
        keys.append(request.headers["Idempotency-Key"])
        if len(keys) < 3:
            return httpx.Response(429, headers={"Retry-After": "2"},
                                  json={"error": {"code": "RATE_LIMITED", "message": "m", "retryable": True}})
        return httpx.Response(200, json=PREDICTION)

    client = client_with(handler, sleep=sleeps.append)
    assert client.learner("ops").predict("x").predicted
    assert sleeps == [2.0, 2.0] and len(set(keys)) == 1


def test_rate_limit_surfaces_after_the_retry_budget():
    from toolving import RateLimited

    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "1"},
                              json={"error": {"code": "RATE_LIMITED", "message": "m", "retryable": True,
                                              "reason": "limit of 5 requests per minute reached"}})

    with pytest.raises(RateLimited) as failure:
        client_with(handler, sleep=lambda s: None, rate_limit_retries=2).learner("ops").remember("x")
    assert failure.value.reason == "limit of 5 requests per minute reached"


def test_maintain_memory_posts_to_the_maintenance_route():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content or b"null"),
                     request.headers.get("Idempotency-Key")))
        return httpx.Response(200, json={"operation_id": "op_9", "status": "MAINTAINED", "retested": 3,
                                         "routed": 1, "state_version": 4, "replayed": False})

    result = client_with(handler).learner("ops").maintain_memory()
    assert result["routed"] == 1 and result["retested"] == 3
    method, path, body, key = seen[0]
    assert (method, path, body) == ("POST", "/v1/learners/ops/memory/maintain", {})
    assert key  # every mutating call carries an idempotency key


def test_teach_prediction_by_stored_id():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content or b"null")))
        if request.url.path.endswith("/predict"):
            return httpx.Response(200, json=PREDICTION)
        return httpx.Response(200, json={"status": "RECORDED"})

    client = client_with(handler)
    prediction = client.learner("ops").predict("x")
    assert prediction.id == "op_1"
    taught = client.learner("ops").teach_prediction(prediction.id, "y")
    assert taught.learned is False
    assert seen[-1] == ("POST", "/v1/learners/ops/predictions/op_1/teach", {"output": "y"})


def test_waits_for_durable_operation_without_resubmitting_mutation():
    requests = []
    polls = iter([
        httpx.Response(202, json={"operation_id": "op_accepted", "status": "IN_PROGRESS"}, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"operation_id": "op_accepted", "status": "SUCCEEDED", "result": PREDICTION}),
    ])

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            assert request.headers["Prefer"] == "respond-async"
            return httpx.Response(202, json={"operation_id": "op_accepted", "status": "PENDING",
                                            "status_url": "/v1/learners/ops/operations/op_accepted"})
        return next(polls)

    client = client_with(handler)
    assert client.learner("ops").predict("input").output == PREDICTION["output"]
    assert client.last_operation_id == "op_accepted"
    assert [request.method for request in requests] == ["POST", "GET", "GET"]


def test_resume_operation_surfaces_terminal_failure():
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(422, json={"error": {"code": "INDUCTION_FAILED", "message": "failed",
                                                   "operation_id": "op_failed", "retryable": False}})

    with pytest.raises(Exception) as error:
        client_with(handler).learner("ops").wait_for_operation("op_failed")
    assert error.value.code == "INDUCTION_FAILED"


def test_polling_resumes_after_transport_loss():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls <= 4:
            raise httpx.ConnectError("service restarting")
        return httpx.Response(200, json={"status": "SUCCEEDED", "result": {"status": "TRAINED"}})

    client = client_with(handler, sleep=lambda _: None)
    assert client.learner("ops").wait_for_operation("op_saved") == {"status": "TRAINED"}
    assert calls == 5
