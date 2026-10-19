"""Python client for the Toolving API."""

from .conceiver import (
    Artifact,
    ConceiverAPIError,
    ConceiverClient,
    ConceiverInstance,
    EvidenceEpisode,
    PredictionEvidence,
    Retirement,
    TextPrediction,
)
from .client import Evidence, Example, Learner, LearnerClient, Memory, Prediction, Taught
from .errors import (
    AuthenticationError,
    Conflict,
    IdempotencyConflict,
    InvalidRequest,
    LearnerAPIError,
    LearnerExists,
    LearningDisabled,
    NotFound,
    OperationInProgress,
    QuotaExceeded,
    RateLimited,
    ServiceUnavailable,
    TrainingFailed,
    VersionConflict,
)

__version__ = "0.12.1"

__all__ = [
    "ConceiverAPIError",
    "Artifact",
    "ConceiverClient",
    "ConceiverInstance",
    "EvidenceEpisode",
    "PredictionEvidence",
    "Retirement",
    "TextPrediction",
    "AuthenticationError",
    "Conflict",
    "Evidence",
    "Example",
    "IdempotencyConflict",
    "InvalidRequest",
    "Learner",
    "LearnerAPIError",
    "LearnerClient",
    "LearnerExists",
    "LearningDisabled",
    "Memory",
    "NotFound",
    "OperationInProgress",
    "Prediction",
    "QuotaExceeded",
    "RateLimited",
    "ServiceUnavailable",
    "Taught",
    "TrainingFailed",
    "VersionConflict",
]
