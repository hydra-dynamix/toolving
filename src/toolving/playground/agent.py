"""The playground's training-sequence assistant: an LLM chat that asks about the
user's problem and proposes a training sequence for Conceiver.

The assistant's instructions live in agent_instructions.md, next to this file,
so they can be edited for a new model without touching code. The model is
called through the Anthropic API (the optional `anthropic` package, installed
with `toolving[agent]`), with ANTHROPIC_API_KEY from the environment.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib import resources
from typing import Any, Callable, Mapping

DEFAULT_MODEL = "claude-opus-5-5"
MAX_MESSAGES = 60
MAX_MESSAGE_CHARS = 20_000

# The model's reply is constrained to this shape (structured outputs).
REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "sequence": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"question": {"type": "string"}, "answer": {"type": "string"}},
                        "required": ["question", "answer"],
                        "additionalProperties": False,
                    },
                },
            ]
        },
    },
    "required": ["reply", "sequence"],
    "additionalProperties": False,
}


class AgentError(Exception):
    """The assistant could not produce a reply. `code` is shown in the page."""

    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class SequenceError(ValueError):
    """A proposed sequence breaks the training-sequence format."""


def load_instructions() -> str:
    return resources.files(__package__).joinpath("agent_instructions.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class TrainingSequence:
    pairs: tuple[tuple[str, str], ...]

    @property
    def text(self) -> str:
        """The saved-file form: question line, answer line, one empty line between teachings."""
        return "\n\n".join(f"{question}\n{answer}" for question, answer in self.pairs) + "\n"

    def to_json(self) -> dict[str, Any]:
        return {"pairs": [{"question": q, "answer": a} for q, a in self.pairs], "text": self.text}


def validate_sequence(items: Any) -> TrainingSequence:
    """Check a proposed sequence against the training-sequence format: at least one
    teaching, each a non-empty single-line question and a non-empty single-line answer."""
    if not isinstance(items, list) or not items:
        raise SequenceError("A training sequence needs at least one teaching.")
    pairs = []
    for number, item in enumerate(items, 1):
        if not isinstance(item, Mapping):
            raise SequenceError(f"Teaching {number} is not a question and answer.")
        pair = []
        for field in ("question", "answer"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise SequenceError(f"Teaching {number} has no {field}.")
            if "\n" in value or "\r" in value:
                raise SequenceError(f"Teaching {number}: the {field} must be a single line.")
            pair.append(value.strip())
        pairs.append((pair[0], pair[1]))
    return TrainingSequence(tuple(pairs))


def _check_messages(messages: Any) -> list[dict[str, str]]:
    if not isinstance(messages, list) or not messages:
        raise AgentError("INVALID_REQUEST", "messages must be a non-empty list.", 400)
    if len(messages) > MAX_MESSAGES:
        raise AgentError("INVALID_REQUEST", f"The conversation is limited to {MAX_MESSAGES} messages; start a new one.", 400)
    checked = []
    for index, message in enumerate(messages):
        expected = "user" if index % 2 == 0 else "assistant"
        if not isinstance(message, Mapping) or message.get("role") != expected:
            raise AgentError("INVALID_REQUEST", f"Message {index + 1} must have role {expected!r}; turns alternate, starting with the user.", 400)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_MESSAGE_CHARS:
            raise AgentError("INVALID_REQUEST", f"Message {index + 1} must be non-empty text of at most {MAX_MESSAGE_CHARS} characters.", 400)
        checked.append({"role": expected, "content": content})
    if checked[-1]["role"] != "user":
        raise AgentError("INVALID_REQUEST", "The last message must be the user's.", 400)
    return checked


class SequenceAgent:
    """One conversation turn per call. The page holds the conversation and sends it
    whole; assistant turns are sent back as the JSON the model returned."""

    def __init__(self, create: Callable[..., Any], *, model: str = DEFAULT_MODEL, instructions: str | None = None):
        self._create = create  # anthropic's client.beta.messages.create, or a stand-in in tests
        self.model = model
        self.instructions = load_instructions() if instructions is None else instructions

    def reply(self, messages: Any) -> dict[str, Any]:
        conversation = _check_messages(messages)
        try:
            response = self._create(
                model=self.model,
                max_tokens=16000,
                system=self.instructions,
                messages=conversation,
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": REPLY_SCHEMA}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except Exception as failure:  # network, auth, rate limit: shown in the page
            raise AgentError("AGENT_UNREACHABLE", f"{type(failure).__name__}: {failure}") from failure
        if response.stop_reason == "refusal":
            raise AgentError("AGENT_REFUSED", "The model declined to answer this conversation.")
        if response.stop_reason == "max_tokens":
            raise AgentError("AGENT_TRUNCATED", "The model's reply was cut off; ask for a shorter sequence.")
        raw = next((block.text for block in response.content if block.type == "text"), "")
        try:
            output = json.loads(raw)
            reply = output["reply"]
        except (ValueError, KeyError, TypeError) as failure:
            raise AgentError("AGENT_BAD_OUTPUT", "The model's reply was not in the expected form.") from failure
        result: dict[str, Any] = {"reply": reply, "raw": raw, "sequence": None, "sequence_error": None}
        if output.get("sequence") is not None:
            try:
                result["sequence"] = validate_sequence(output["sequence"]).to_json()
            except SequenceError as failure:
                result["sequence_error"] = str(failure)
        return result


def agent_from_env(model: str | None = None) -> tuple[SequenceAgent | None, str]:
    """The assistant, or None and the reason it is unavailable."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, "Set ANTHROPIC_API_KEY before starting toolving-playground to use the assistant."
    try:
        import anthropic  # type: ignore[import-not-found]  # optional: toolving[agent]
    except ImportError:
        return None, "Install the assistant's dependency with `pip install 'toolving[agent]'`."
    client = anthropic.Anthropic()
    chosen = model or os.environ.get("TOOLVING_AGENT_MODEL") or DEFAULT_MODEL
    return SequenceAgent(client.beta.messages.create, model=chosen), ""
