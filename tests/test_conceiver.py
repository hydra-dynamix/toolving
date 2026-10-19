from __future__ import annotations

import json

import httpx

from toolving import ConceiverClient


def _client(handler) -> ConceiverClient:
    http = httpx.Client(
        base_url="https://toolving.test",
        transport=httpx.MockTransport(handler),
    )
    return ConceiverClient("https://toolving.test", "account-key", http_client=http)


def test_query_transports_markdown_evidence_and_optional_complete_episodes():
    requests = []

    def handler(request):
        requests.append((request.url.path, json.loads(request.content)))
        with_memory = requests[-1][1].get("withMemory", False)
        evidence = {
            "format": "text/markdown",
            "markdown": "## Decision\n\nTwo examples support this answer.",
        }
        if with_memory:
            evidence["episodes"] = [
                {"input": "question one", "output": "answer one"},
                {"input": "question two", "output": "answer two"},
            ]
        return httpx.Response(
            200,
            json={
                "operation_id": "op_prediction",
                "status": "SUCCEEDED",
                "result": {
                    "status": "ANSWER",
                    "text": "answer",
                    "output": "answer",
                    "confidence": "confident",
                    "evidence": evidence,
                },
            },
        )

    instance = _client(handler).instance("instance")
    ordinary = instance.query("question", key="ordinary")
    remembered = instance.query("question", with_memory=True, key="memory")

    assert ordinary.confidence == "confident"
    assert ordinary.evidence is not None and ordinary.evidence.episodes is None
    assert remembered.evidence is not None
    assert [(item.input, item.output) for item in remembered.evidence.episodes or ()] == [
        ("question one", "answer one"),
        ("question two", "answer two"),
    ]
    assert requests == [
        ("/v1/instances/instance/query", {"question": "question"}),
        (
            "/v1/instances/instance/query",
            {"question": "question", "withMemory": True},
        ),
    ]


def test_reap_returns_persistence_and_verified_artifact_identity():
    digest = "a" * 64

    def handler(request):
        assert request.method == "DELETE"
        return httpx.Response(
            200,
            json={
                "operation_id": "op_retire",
                "status": "SUCCEEDED",
                "result": {
                    "instance_id": "instance",
                    "status": "REAPED",
                    "persisted": True,
                    "retired": True,
                    "sequence_id": "sequence",
                    "artifact": {
                        "format": "conceiver-storage-v1",
                        "sha256": digest,
                        "bytes": 1234,
                    },
                },
            },
        )

    retirement = _client(handler).instance("instance").reap(key="retire")
    assert retirement.persisted and retirement.retired
    assert retirement.sequence_id == "sequence"
    assert retirement.artifact is not None
    assert (retirement.artifact.sha256, retirement.artifact.bytes) == (digest, 1234)


def test_reap_preserves_compatibility_with_an_earlier_sequence_result():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "operation_id": "op_reap",
                "status": "SUCCEEDED",
                "result": {
                    "instance_id": "instance",
                    "status": "REAPED",
                    "sequence_id": "sequence",
                    "event_count": 2,
                },
            },
        )

    retirement = _client(handler).instance("instance").reap()
    assert retirement.persisted is True
    assert retirement.retired is False
    assert retirement.sequence_id == "sequence" and retirement.artifact is None
