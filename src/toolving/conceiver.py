"""Client for authenticated Conceiver instances and replayable event sequences."""

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, urlencode
from uuid import uuid4

import httpx


class ConceiverAPIError(Exception):
    def __init__(self, status: int, error: dict[str, Any]):
        self.status = status
        self.code = error["code"]
        self.operation_id = error.get("operation_id")
        self.retryable = error.get("retryable", False)
        super().__init__(error["message"])


@dataclass(frozen=True)
class EvidenceEpisode:
    input: str
    output: str


@dataclass(frozen=True)
class PredictionEvidence:
    format: Literal["text/markdown"]
    markdown: str
    episodes: tuple[EvidenceEpisode, ...] | None = None


@dataclass(frozen=True)
class TextPrediction:
    prediction_id: str
    status: str
    text: str | None = None
    output: Any = None
    confidence: Literal["hypothesis", "confident", "high"] | None = None
    evidence: PredictionEvidence | None = None


@dataclass(frozen=True)
class Artifact:
    format: str
    sha256: str
    bytes: int


@dataclass(frozen=True)
class Retirement:
    instance_id: str
    status: Literal["REAPED"]
    persisted: bool
    retired: bool
    sequence_id: str | None
    artifact: Artifact | None


class ConceiverClient:
    def __init__(self, base_url: str, api_key: str, *, http_client: Any = None):
        self._owned = http_client is None
        self._http = (
            http_client
            if http_client is not None
            else httpx.Client(
                timeout=None,
                trust_env=False,
                limits=httpx.Limits(
                    max_connections=None,
                    max_keepalive_connections=None,
                    keepalive_expiry=None,
                ),
            )
        )
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": "Bearer " + api_key}

    def close(self) -> None:
        if self._owned:
            self._http.close()

    def __enter__(self) -> "ConceiverClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _request(
        self, method: str, path: str, body: Any = None, key: str | None = None
    ) -> dict[str, Any]:
        headers = dict(self._headers)
        if method != "GET":
            headers["Idempotency-Key"] = key if key is not None else uuid4().hex
        response = self._http.request(
            method, self.base_url + path, headers=headers, json=body
        )
        if response.is_error:
            response_body = response.json()
            if "error" in response_body:
                raise ConceiverAPIError(response.status_code, response_body["error"])
            response.raise_for_status()
        return response.json()

    def models(self) -> dict[str, Any]:
        return self._request("GET", "/v1/models")

    def storage(self) -> dict[str, Any]:
        return self._request("GET", "/v1/storage")

    def create(
        self,
        *,
        sequence_id: str | None = None,
        model: Literal["language", "json"] | None = None,
        client_host: Literal["pi", "codex", "claude-code"] | None = None,
        compatibility_scope: str | None = None,
        key: str | None = None,
    ) -> "ConceiverInstance":
        body: dict[str, Any] = {"sequence_id": sequence_id}
        if model == "json":
            if client_host is None or compatibility_scope is None:
                raise ValueError("JSON model selection requires client_host and compatibility_scope")
            body.update(
                {
                    "model": "json",
                    "contract": "toolving-conceiver-json-v1",
                    "client": {
                        "kind": "agent-harness",
                        "host": client_host,
                        "compatibilityScope": compatibility_scope,
                    },
                }
            )
        elif model == "language":
            if client_host is not None or compatibility_scope is not None:
                raise ValueError("language model selection does not accept a JSON client scope")
            body["model"] = "language"
        elif client_host is not None or compatibility_scope is not None:
            raise ValueError("select the JSON model before supplying its client scope")
        receipt = self._request("POST", "/v1/instances", body, key)
        return self.instance(receipt["result"]["instance_id"])

    def instance(self, identity: str) -> "ConceiverInstance":
        return ConceiverInstance(self, identity)

    def operation(self, identity: str) -> dict[str, Any]:
        return self._request("GET", "/v1/operations/" + quote(identity, safe=""))

    def sequences(
        self, *, limit: int = 50, offset: int = 0, model: str | None = None
    ) -> dict[str, Any]:
        query = urlencode(
            {
                "limit": limit,
                "offset": offset,
                **({"model": model} if model is not None else {}),
            }
        )
        return self._request("GET", "/v1/sequences?" + query)

    def sequence(self, identity: str) -> dict[str, Any]:
        return self._request("GET", "/v1/sequences/" + quote(identity, safe=""))

    def training_sequence_text(self, identity: str) -> str:
        response = self._http.get(
            self.base_url + "/v1/sequences/" + quote(identity, safe="") + "/file",
            headers=self._headers,
        )
        if response.is_error:
            response_body = response.json()
            if "error" in response_body:
                raise ConceiverAPIError(response.status_code, response_body["error"])
            response.raise_for_status()
        return response.text


@dataclass(frozen=True)
class ConceiverInstance:
    client: ConceiverClient
    instance_id: str

    @property
    def _path(self) -> str:
        return "/v1/instances/" + quote(self.instance_id, safe="")

    def status(self) -> dict[str, Any]:
        return self.client._request("GET", self._path)

    def teach(
        self, question: str, answer: str, *, key: str | None = None
    ) -> dict[str, Any]:
        return self.client._request(
            "POST", self._path + "/teach", {"question": question, "answer": answer}, key
        )

    def query(
        self,
        question: str,
        *,
        with_memory: bool = False,
        key: str | None = None,
    ) -> TextPrediction:
        body: dict[str, Any] = {"question": question}
        if with_memory:
            body["withMemory"] = True
        receipt = self.client._request(
            "POST", self._path + "/query", body, key
        )
        result = receipt["result"]
        evidence = result.get("evidence")
        parsed_evidence = None
        if evidence is not None:
            episodes = evidence.get("episodes")
            parsed_evidence = PredictionEvidence(
                evidence["format"],
                evidence["markdown"],
                None
                if episodes is None
                else tuple(EvidenceEpisode(item["input"], item["output"]) for item in episodes),
            )
        return TextPrediction(
            receipt["operation_id"],
            result["status"],
            result.get("text"),
            result.get("output"),
            result.get("confidence"),
            parsed_evidence,
        )

    def resolve(
        self, prediction_id: str, answer: str, *, key: str | None = None
    ) -> dict[str, Any]:
        return self.client._request(
            "POST",
            self._path + "/resolve",
            {"prediction_id": prediction_id, "answer": answer},
            key,
        )

    def ingest_training_sequence(self, text: str) -> dict[str, Any]:
        return self.client._request(
            "POST", self._path + "/training-sequences", {"text": text}
        )

    def save(self, *, key: str | None = None) -> str:
        return self.client._request("POST", self._path + "/sequences", key=key)[
            "result"
        ]["sequence_id"]

    def reap(self, *, key: str | None = None) -> Retirement:
        result = self.client._request("DELETE", self._path, key=key)["result"]
        artifact = result.get("artifact")
        return Retirement(
            result["instance_id"],
            result["status"],
            result.get("persisted", result.get("sequence_id") is not None),
            result.get("retired", False),
            result.get("sequence_id"),
            None
            if artifact is None
            else Artifact(artifact["format"], artifact["sha256"], artifact["bytes"]),
        )
