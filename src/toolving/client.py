"""Agent-facing client.

    client = LearnerClient()                      # TOOLVING_API_URL / TOOLVING_API_KEY
    learner = client.learner("my-learner")

    prediction = learner.predict(task)
    if prediction.predicted:
        prediction.outcome(execute(prediction.output))
    elif prediction.unsupported or prediction.ambiguous:
        result = reason(task, alternatives=prediction.alternatives,
                        context=prediction.evidence.markdown)
        if authoritative:
            prediction.teach(result)

Idempotency keys, operation/prediction IDs, and state versions are handled
internally. A network failure is retried with the *same* idempotency key (so
the service replays the stored result instead of executing twice); an error
response from the service is never retried automatically.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence, cast

import httpx

from .errors import LearnerAPIError, error_from_response

EvidenceMode = Literal["none", "narrow", "broad"]
RecallMode = Literal["narrow", "broad"]
Mode = Literal["LEARNING", "PREDICTION_ONLY"]


@dataclass(frozen=True)
class Evidence:
    """Past observations rendered as Markdown. Context for reasoning, not an answer."""

    markdown: str
    memory_ids: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return bool(self.memory_ids)

    def __str__(self) -> str:
        return self.markdown

    @classmethod
    def _from(cls, value: Mapping[str, Any] | None) -> "Evidence":
        # evidence="none" arrives as null; expose an empty bundle so
        # `prediction.evidence.markdown` always works.
        if value is None:
            return cls("", ())
        return cls(value["markdown"], tuple(value["memory_ids"]))


@dataclass(frozen=True)
class Memory:
    """A remembered observation and its opaque, tenant-scoped memory ID."""

    memory_id: str
    evidence: Evidence


@dataclass(frozen=True)
class Taught:
    """Result of teaching. `learned` is False when the example was recorded but
    nothing reusable came of it yet (it is still kept in memory)."""

    learned: bool
    operation_id: str | None = None


class Prediction:
    """A prediction handle. Use `outcome()` or `teach()` without managing IDs."""

    def __init__(self, learner: "Learner", payload: Mapping[str, Any]) -> None:
        self._learner = learner
        self._id: str = payload["prediction_id"]
        self.status: str = payload["status"]
        self.output: Any = payload.get("output")
        self.alternatives: tuple[Any, ...] = tuple(payload.get("alternatives") or ())
        self.reason: str | None = payload.get("reason")
        self.evidence: Evidence = Evidence._from(payload.get("evidence"))

    @property
    def id(self) -> str:
        """The prediction's id: store it to teach this prediction later
        (`Learner.teach_prediction`)."""
        return self._id

    @property
    def predicted(self) -> bool:
        return self.status == "PREDICTED"

    @property
    def chosen(self) -> bool:
        """The learner's memories gave different answers and it picked `output` from
        `alternatives`. Teaching the observed output confirms or refutes the pick."""
        return self.status == "CHOSEN"

    @property
    def ambiguous(self) -> bool:
        """The learner has competing outputs and needs downstream evidence."""
        return self.status == "AMBIGUOUS" or (
            self.status == "ABSTAINED" and bool(self.alternatives)
        )

    @property
    def unsupported(self) -> bool:
        return self.status == "ABSTAINED" and not self.alternatives

    @property
    def abstained(self) -> bool:
        """No resolved answer; inspect alternatives and evidence for inference."""
        return self.status == "ABSTAINED"

    def outcome(self, result: Any, *, assessment: Any = None) -> None:
        """Record what happened; optionally retain a separate correctness assessment."""
        payload = {"outcome": result}
        if assessment is not None:
            payload["assessment"] = assessment
        self._learner._post(f"predictions/{self._id}/outcome", payload)

    def teach(self, correct_output: Any) -> Taught:
        """Teach the correct output for this prediction's input (no need to resend it)."""
        payload = self._learner._post(f"predictions/{self._id}/teach", {"output": correct_output})
        return Taught(learned=payload["status"] == "LEARNED", operation_id=payload.get("operation_id"))

    def __repr__(self) -> str:
        if self.predicted:
            return f"Prediction(PREDICTED, output={self.output!r})"
        if self.chosen:
            return f"Prediction(CHOSEN, output={self.output!r}, alternatives={list(self.alternatives)!r})"
        if self.ambiguous:
            return f"Prediction(AMBIGUOUS, alternatives={list(self.alternatives)!r})"
        return f"Prediction(ABSTAINED, reason={self.reason!r})"


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(0.0, float(response.headers.get("Retry-After", "1")))
    except ValueError:
        return 1.0


def _format(value: Any, declared: str | None) -> str:
    return declared or ("text" if isinstance(value, str) else "json")


@dataclass(frozen=True)
class Example:
    """One explicitly labeled training example for `Learner.train`."""

    input: Any
    output: Any
    role: Literal["target", "distractor"] = "target"


class Learner:
    """Handle for one learner in your tenant."""

    def __init__(self, client: "LearnerClient", name: str) -> None:
        self._client = client
        self.name = name

    def _post(self, path: str, body: Mapping[str, Any], *, method: str = "POST") -> dict[str, Any]:
        return self._client._request(method, f"/v1/learners/{self.name}/{path}", body)

    # -- agent operations -------------------------------------------------

    def predict(
        self, input: Any, *, evidence: EvidenceMode = "narrow", format: str | None = None
    ) -> Prediction:
        """Apply previously learned behavior. Does not learn from the request."""
        payload = self._post(
            "predict", {"input": input, "format": _format(input, format), "evidence": evidence}
        )
        return Prediction(self, payload)

    def teach(self, input: Any, output: Any, *, format: str | None = None) -> None:
        """Submit an authoritative input/output example."""
        self._post("teach", {"input": input, "output": output, "format": _format(input, format)})

    def remember(self, event: Any) -> Memory:
        """Store an observation in memory without teaching an answer."""
        payload = self._post("remember", {"event": event})
        return Memory(payload["memory_id"], Evidence._from(payload["evidence"]))

    def recall(
        self,
        cue: Any = None,
        *,
        mode: RecallMode = "narrow",
        memory_id: str | None = None,
        format: str | None = None,
    ) -> Evidence:
        """Retrieve related memories for a cue, or one memory by ID. Not a prediction."""
        if (cue is None) == (memory_id is None):
            raise ValueError("recall takes exactly one of cue or memory_id")
        body = (
            {"memory_id": memory_id}
            if memory_id is not None
            else {"cue": cue, "format": _format(cue, format), "mode": mode}
        )
        return Evidence._from(self._post("recall", body)["evidence"])

    def info(self) -> dict[str, Any]:
        return self._client._request("GET", f"/v1/learners/{self.name}", None)

    # -- administrative operations (admin-scoped keys) --------------------

    def fork(self, name: str, *, mode: Mode = "LEARNING") -> "Learner":
        self._post("forks", {"name": name, "mode": mode})
        return Learner(self._client, name)

    def set_mode(self, mode: Mode) -> None:
        self._post("mode", {"mode": mode}, method="PUT")

    def train(self, examples: Iterable[Example | Sequence[Any]]) -> None:
        """Atomically train on an ordered sequence; if any group yields nothing reusable, nothing is kept."""
        items = []
        for item in examples:
            if not isinstance(item, Example):
                item = Example(*item)
            items.append({"input": item.input, "output": item.output, "role": item.role})
        self._post("training", {"examples": items})

    def bootstrap(self, input: Any, *, format: str | None = None) -> dict[str, Any]:
        return self._post("bootstrap", {"input": input, "format": _format(input, format)})

    def teach_prediction(self, prediction_id: str, correct_output: Any) -> Taught:
        """Teach the correct output for an earlier prediction, by its id.

        The same as `Prediction.teach`, for callers that stored the
        `prediction_id` (e.g. a correction made later, in another process)."""
        payload = self._post(f"predictions/{prediction_id}/teach", {"output": correct_output})
        return Taught(learned=payload["status"] == "LEARNED", operation_id=payload.get("operation_id"))

    def wait_for_operation(self, operation_id: str) -> dict[str, Any]:
        """Wait for a previously accepted operation, including after reconnecting."""
        return self._client._request("GET", f"/v1/learners/{self.name}/operations/{operation_id}", None)

    def maintain_memory(self) -> dict[str, Any]:
        """Run memory maintenance. What the learner has learned does not change."""
        return self._post("memory/maintain", {})


@dataclass
class LearnerClient:
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    timeout: float = 120.0
    transport_retries: int | None = None
    # 429s are rejected before anything executes, so waiting Retry-After and
    # re-sending the same idempotency key is safe. Set 0 to surface them at once.
    rate_limit_retries: int | None = None
    sleep: Any = field(default=None, repr=False)
    http_client: httpx.Client | None = None
    last_operation_id: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.base_url = (self.base_url or os.environ.get("TOOLVING_API_URL", "")).rstrip("/")
        self.api_key = self.api_key or os.environ.get("TOOLVING_API_KEY")
        if not self.api_key:
            raise ValueError("an API key is required (api_key= or TOOLVING_API_KEY)")
        if self.http_client is None:
            if not self.base_url:
                raise ValueError("a base URL is required (base_url= or TOOLVING_API_URL)")
            self.http_client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
            self._owns_client = True
        else:
            self._owns_client = False

    def learner(self, name: str) -> Learner:
        return Learner(self, name)

    def create_learner(self, name: str, *, mode: Mode = "LEARNING") -> Learner:
        self._request("POST", "/v1/learners", {"name": name, "mode": mode})
        return Learner(self, name)

    def learners(self) -> list[dict[str, Any]]:
        """Every learner in this key's tenant: name, mode, and state version."""
        return self._request("GET", "/v1/learners", None)["learners"]

    def delete_learner(self, name: str) -> None:
        """Permanently delete a learner and its learner-scoped history (admin)."""
        self._request("DELETE", f"/v1/learners/{name}", None)

    def version(self) -> dict[str, Any]:
        return self._request("GET", "/v1/meta/version", None)

    def close(self) -> None:
        if self._owns_client:
            cast(httpx.Client, self.http_client).close()

    def __enter__(self) -> "LearnerClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None) -> dict[str, Any]:
        polling = method == "GET" and "/operations/" in path
        while True:
            payload, response = self._exchange(method, path, body)
            if response.status_code != 202:
                return payload["result"] if polling else payload
            self.last_operation_id = payload["operation_id"]
            status_url = payload.get("status_url", response.headers.get("Location", path))
            if not status_url.startswith("/v1/learners/"):
                raise LearnerAPIError("INVALID_RESPONSE", "Invalid operation status URL.", status=202)
            if polling:
                (self.sleep or time.sleep)(_retry_after(response))
            path, method, body, polling = status_url, "GET", None, True

    def _exchange(self, method: str, path: str, body: Mapping[str, Any] | None):
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if method != "GET":
            headers["Idempotency-Key"] = str(uuid.uuid4())
            headers["Prefer"] = "respond-async"
        attempt = 0
        limited = 0
        sleep = self.sleep or time.sleep
        while True:
            try:
                response = cast(httpx.Client, self.http_client).request(method, path, json=body, headers=headers)
            except httpx.TransportError:
                # Same idempotency key: the service replays rather than re-executes.
                if self.transport_retries is not None and attempt >= self.transport_retries:
                    raise
                attempt += 1
                sleep(1)
                continue
            if response.status_code == 429 and (self.rate_limit_retries is None or limited < self.rate_limit_retries):
                limited += 1
                sleep(_retry_after(response))
                continue
            break
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code >= 400:
            raise error_from_response(response.status_code, payload)
        if not isinstance(payload, dict):
            raise LearnerAPIError(
                "INVALID_RESPONSE", "service returned a non-object body", status=response.status_code
            )
        return payload, response
