"""Typed API errors. None of them trigger an automatic retry of a terminal failure."""

from __future__ import annotations


class LearnerAPIError(Exception):
    """An error response from the learner service.

    `retryable` is advisory metadata. Retrying is the caller's decision, and
    the SDK issues any retry as a new operation with a new idempotency key.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int,
        retryable: bool = False,
        request_id: str | None = None,
        operation_id: str | None = None,
        replayed: bool = False,
        reason: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}" + (f" ({reason})" if reason else ""))
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.request_id = request_id
        self.operation_id = operation_id
        self.replayed = replayed
        self.reason = reason
        """Why it failed, when the service can say safely (e.g. provider status, attempts)."""


class AuthenticationError(LearnerAPIError):
    """The API key is missing, invalid, revoked, or lacks a required scope."""


class NotFound(LearnerAPIError):
    """The learner (or linked prediction) does not exist in your tenant."""


class InvalidRequest(LearnerAPIError):
    """The request was malformed or is not a valid learner command."""


class Conflict(LearnerAPIError):
    """Base class for state/lifecycle conflicts."""


class VersionConflict(Conflict):
    """The learner changed concurrently; nothing was applied by this call."""


class LearningDisabled(Conflict):
    """The learner is in prediction-only mode."""


class LearnerExists(Conflict):
    """A learner with that name already exists."""


class IdempotencyConflict(Conflict):
    """An idempotency key was reused for a different request."""


class OperationInProgress(Conflict):
    """The same operation is still executing elsewhere."""


class TrainingFailed(LearnerAPIError):
    """Training produced nothing reusable; nothing was kept."""


class RateLimited(LearnerAPIError):
    """The tenant's request rate limit was reached (after the SDK's own waits)."""


class QuotaExceeded(LearnerAPIError):
    """A tenant quota (e.g. number of learners) is exhausted."""


class ServiceUnavailable(LearnerAPIError):
    """A provider, storage, or durability failure. Nothing was reported as done."""


_BY_CODE = {
    "VERSION_CONFLICT": VersionConflict,
    "LEARNING_DISABLED": LearningDisabled,
    "LEARNER_ALREADY_EXISTS": LearnerExists,
    "IDEMPOTENCY_CONFLICT": IdempotencyConflict,
    "OPERATION_IN_PROGRESS": OperationInProgress,
    "INDUCTION_FAILED": TrainingFailed,
    "RATE_LIMITED": RateLimited,
    "QUOTA_EXCEEDED": QuotaExceeded,
    "CURRICULUM_INVALID": TrainingFailed,
}
_BY_STATUS = {
    400: InvalidRequest,
    401: AuthenticationError,
    403: AuthenticationError,
    404: NotFound,
    409: Conflict,
    413: InvalidRequest,
    422: InvalidRequest,
    429: RateLimited,
    502: ServiceUnavailable,
    503: ServiceUnavailable,
}


def error_from_response(status: int, payload: object) -> LearnerAPIError:
    body = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(body, dict):
        body = {"code": "HTTP_ERROR", "message": f"HTTP {status}"}
    code = str(body.get("code", "HTTP_ERROR"))
    cls = _BY_CODE.get(code) or _BY_STATUS.get(status, LearnerAPIError)
    return cls(
        code,
        str(body.get("message", "")),
        status=status,
        retryable=bool(body.get("retryable", False)),
        request_id=body.get("request_id"),
        operation_id=body.get("operation_id"),
        replayed=bool(body.get("replayed", False)),
        reason=body.get("reason"),
    )
