"""The playground's training-sequence assistant: an LLM chat that asks about the
user's problem and proposes a training sequence for Conceiver.

The assistant's instructions live in agent_instructions.md, next to this file,
so they can be edited for a new model without touching code. The model runs
through agentctl (assistant_config.py sets which agent), which uses the
machine's existing Claude sign-in; no API key is read here.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Mapping

MAX_MESSAGES = 60
MAX_MESSAGE_CHARS = 20_000

# The shape the model is asked to reply in; the reply is checked against it.
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
            if "\t" in value:
                raise SequenceError(f"Teaching {number}: the {field} cannot hold a tab.")
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


def compose_prompt(instructions: str, conversation: list[dict[str, str]]) -> str:
    """One prompt holding the instructions, the conversation so far and the reply format."""
    turns = "\n\n".join(f"<{m['role']}>\n{m['content']}\n</{m['role']}>" for m in conversation)
    return (
        f"{instructions}\n\n---\n\nThe conversation so far, oldest first. Your earlier turns are the JSON you "
        f"returned.\n\n{turns}\n\nReply to the user's last message. Output only one JSON object that matches "
        f"this JSON Schema, with no code fence and no other text:\n{json.dumps(REPLY_SCHEMA)}\n"
    )


def _json_object(text: str) -> Any:
    """The JSON object in a model's reply, allowing for a code fence or words around it."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object")
    return json.loads(text[start:end + 1])


class SequenceAgent:
    """One conversation turn per call. The page holds the conversation and sends it
    whole; assistant turns are sent back as the JSON the model returned.

    `complete` takes the prompt and returns the model's text (agentctl_complete,
    or a stand-in in tests)."""

    def __init__(self, complete: Callable[[str], str], *, label: str, instructions: str | None = None):
        self._complete = complete
        self.label = label
        self.instructions = load_instructions() if instructions is None else instructions

    def reply(self, messages: Any) -> dict[str, Any]:
        conversation = _check_messages(messages)
        raw = self._complete(compose_prompt(self.instructions, conversation))
        try:
            output = _json_object(raw)
            reply = output["reply"]
            if not isinstance(reply, str):
                raise TypeError("reply is not text")
        except (ValueError, KeyError, TypeError) as failure:
            raise AgentError("AGENT_BAD_OUTPUT", f"The model's reply was not in the expected form: {raw[:300]!r}") from failure
        result: dict[str, Any] = {"reply": reply, "raw": json.dumps(output, ensure_ascii=False),
                                  "sequence": None, "sequence_error": None}
        if output.get("sequence") is not None:
            try:
                result["sequence"] = validate_sequence(output["sequence"]).to_json()
            except SequenceError as failure:
                result["sequence_error"] = str(failure)
        return result


def _stdout_of(log: str) -> str:
    """agentctl's raw log is "stdout:\\n<text>\\n\\nstderr:\\n<text>"; the reply is the first part."""
    body = log.removeprefix("stdout:\n")
    return body.rsplit("\nstderr:\n", 1)[0].strip()


def agentctl_complete(executable: str, agent: str, timeout: float = 600, *,
                      config_dir: Path | None = None) -> Callable[[str], str]:
    """Run each prompt through `agentctl run <agent>` and return what the agent printed.
    agentctl runs in `config_dir`, so an agentctl.toml there defines the agent; the
    agent itself runs in an empty folder."""

    def complete(prompt: str) -> str:
        with tempfile.TemporaryDirectory(prefix="toolving-agent-") as work:
            prompt_file = Path(work) / "prompt.txt"
            prompt_file.write_text(prompt, encoding="utf-8")
            try:
                done = subprocess.run(
                    [executable, "run", agent, "--prompt-file", str(prompt_file), "--json", "--cwd", work],
                    capture_output=True, text=True, encoding="utf-8", timeout=timeout, cwd=config_dir,
                )
                summary = json.loads(done.stdout)[-1]["summary"]
                log = Path(summary["raw_log_path"]).read_text(encoding="utf-8")
            except subprocess.TimeoutExpired as failure:
                raise AgentError("AGENT_TIMEOUT", f"agentctl gave no reply within {timeout:.0f} s.") from failure
            except (OSError, ValueError, KeyError, IndexError, TypeError) as failure:
                raise AgentError("AGENT_UNREACHABLE", f"agentctl failed: {failure}") from failure
        if summary.get("exit_code") != 0:
            raise AgentError("AGENT_UNREACHABLE", f"agentctl's {agent} exited with {summary.get('exit_code')}: "
                                                  f"{' '.join(summary.get('preview_lines') or [])[:500]}")
        return _stdout_of(log)

    return complete
