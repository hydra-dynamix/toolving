---
name: toolving-conceiver
description: Teach, query and correct Toolving's Conceiver through the toolving Python SDK (ConceiverClient). Use when a task involves Conceiver or Toolving, teaching a program input -> output examples instead of writing rules, reading an answer's confidence (hypothesis, confident, highly confident, ambiguous) or evidence, recording what actually happened for a prediction, or saving and restoring what an instance learned.
---

<!-- toolving-conceiver skill, installed by `python -m toolving.skill install` -->

# Using Conceiver through the Toolving SDK

Conceiver learns from examples. You teach it an input line and the output line it should
give; asked a new input, it returns its best guess, a confidence level, and the evidence
behind it, or `UNRESOLVED` when nothing it was taught fits. It is not an LLM: it does not
know anything it was not taught, and it never invents an answer to fill a gap.

## Before you start

The SDK reads `TOOLVING_API_URL` and `TOOLVING_API_KEY` from the environment. Never print
the key, log it or commit it. Check the setup without teaching anything:

```python
import os
from toolving import ConceiverClient

with ConceiverClient(os.environ["TOOLVING_API_URL"], os.environ["TOOLVING_API_KEY"]) as client:
    print([m["id"] for m in client.models()["models"]])   # ['language', 'json']
```

If `toolving` is not installed, follow `{TOOLVING_API_URL}/agents.md` (by default
`https://toolving.com/agents.md`).

## The loop: create, teach, query, resolve, reap

```python
with ConceiverClient(os.environ["TOOLVING_API_URL"], os.environ["TOOLVING_API_KEY"]) as client:
    instance = client.create()                      # a fresh, empty memory
    instance.teach("create account alice", "createAccount(alice)")
    p = instance.query("create account bob")
    if p.status == "ANSWER":
        print(p.text, p.confidence)                 # createAccount(bob) hypothesis
        print(p.evidence.markdown)                  # why it answered that
    else:
        print("UNRESOLVED")                         # nothing taught fits: ask an LLM or a person
    instance.resolve(p.prediction_id, "createAccount(bob)")   # what actually happened
    instance.reap()                                 # release the instance
```

- `teach(question, answer)`: one example. Both are single lines.
- `query(question)`: returns a prediction with `status` (`ANSWER` or `UNRESOLVED`), `text`,
  `output` (decoded JSON when `text` is JSON), `confidence`, `evidence.markdown` and
  `prediction_id`. `query(..., with_memory=True)` also returns the full past examples in
  `evidence.episodes` (2 credits instead of 1).
- `resolve(prediction_id, answer)`: records what the right answer turned out to be. Use it
  for every prediction whose real outcome you learn: it is how a correction is made.
- Always check `status` before using `text`.

## Confidence

Each answer carries a level counted from its evidence: how many times it was given or
confirmed (for), minus how many times it was corrected away (against).

| `confidence` | Evidence | What to do with it |
|---|---|---|
| `hypothesis` | One more for than against, or the input only fits a pattern in a new way (a new word in a slot, a repeated word, a different number of words) | A best guess. Use it where a wrong answer is cheap, or have it checked. |
| `confident` | Two more for than against | Usually safe to act on. |
| `highly confident` | Three or more | Act on it. |
| `ambiguous` | For and against are level | Do not act on it; ask, then resolve. |

These are evidence counts, not probabilities. Decide in your program which level is enough
for each action.

## How to teach different things

Every example below was run against Conceiver; the arrows show what it answered.

**Recall an exact answer.** One example is enough to recall it, as a hypothesis; teach it
again to raise the level.

```python
instance.teach("support queue", "Customer care")
# "support queue" -> Customer care (hypothesis)
# "sales queue"   -> UNRESOLVED (no taught input fits)
```

**Copy a value into a template (slots).** A word of the input that also appears in the
answer becomes a slot: any word can fill it.

```python
instance.teach("create account alice", "createAccount(alice)")
# "create account bob" -> createAccount(bob) (hypothesis)
for name in ("bob", "carol"):
    instance.teach(f"create account {name}", f"createAccount({name})")
# "create account zorb" -> createAccount(zorb) (highly confident)
# "make account zorb"   -> UNRESOLVED (a fixed word differs)
```

**Reorder or combine values, including into JSON.** Teach the answer as one line; compact
JSON works (`json.dumps(value, separators=(",", ":"), sort_keys=True)`), and `output` comes
back decoded.

```python
instance.teach("swap alice bob", "bob alice")
instance.teach("swap jane doug", "doug jane")
# "swap red blue" -> blue red (confident)

instance.teach("add alice to sales", '{"team":"sales","user":"alice"}')
instance.teach("add bob to support", '{"team":"support","user":"bob"}')
# "add carol to finance" -> {"team":"finance","user":"carol"} (confident)
```

**A fixed answer for a shape of input.** When the answer shares no word with the input, the
input's varying words still become slots and the answer is the same for all of them. Teach
the contrasting case explicitly: a word repeated in two slots is a different shape.

```python
for a, b in (("alice", "bob"), ("jane", "doug")):
    instance.teach(f"isEqual {a} {b}", "different")
for a in ("alice", "bob"):
    instance.teach(f"isEqual {a} {a}", "same")
# "isEqual zorb plonk" -> different
# "isEqual zorb zorb"  -> same
```

**Operations inside operations.** Once an operation is taught, it can stand where a value
goes: the inner one is answered first.

```python
instance.teach("keepFirst alice bob", "alice")
instance.teach("keepFirst jane doug", "jane")
# "isEqual zorb keepFirst zorb plonk"  -> same
# "isEqual plonk keepFirst zorb plonk" -> different
```

**A request that expands into steps.** Teach the parts and the whole the same way.

```python
for name in ("bob", "alice"):
    instance.teach(f"create account {name}", f"create_account({name})")
    instance.teach(f"add to team {name}", f"add_to_team({name})")
    instance.teach(f"onboard {name}", f"create account {name}; add to team {name}")
# "onboard carol" -> create account carol; add to team carol (confident)
```

**Correct a wrong answer.** Resolve the prediction with the right answer. The correction
counts against the old answer and for the new one, so the new one leads at once.

```python
instance.teach("file invoice from acme", "/finance/vendors/acme")
p = instance.query("file invoice from acme")          # /finance/vendors/acme
instance.resolve(p.prediction_id, "/legal/disputes/acme")
# "file invoice from acme" -> /legal/disputes/acme (hypothesis)
# resolved the same way once more                -> /legal/disputes/acme (confident)
```

**Tell two cases apart with a word.** Teach one example of each case with the word that
distinguishes them.

```python
instance.teach("file disputed invoice from acme", "/legal/disputes/acme")
instance.teach("file paid invoice from acme", "/finance/vendors/acme")
# "file disputed invoice from globex" -> /legal/disputes/globex
# "file paid invoice from globex"     -> /finance/vendors/globex
```

## Words and delimiters

Conceiver works on words. By default a language instance splits at the space and keeps
each of `. , ; : ! ? ( ) [ ] { } " /` as a word of its own, so `createAccount(alice)` is
the words `createAccount ( alice )` and `alice` can be a slot. Set other delimiters when
the instance is created:

```python
instance = client.create(model="language", delimiters={"split": " ", "keep": ".(),"})
```

`split` characters separate words and are dropped; `keep` characters separate words and are
words themselves. Use ASCII, at least one `split` character, each character once.

## What it does not do (yet)

- **Change part of a word.** `walk past tense -> walked` does not teach `talk -> talked`:
  `walked` is one word. Put the varying part in its own word, or use a delimiter.
- **Fill a slot with several words.** Taught `turn on kitchen lights -> lights.on(kitchen)`,
  it answers `turn on dining room lights` wrongly (`lights.on dining(room)`), as a
  hypothesis. Join a multi-word value into one word (`dining-room`).
- **Classify by meaning.** Inputs that share no words with anything taught come back
  `UNRESOLVED` (`my card was declined -> billing` teaches nothing about
  `my payment failed`). Send those to an LLM or a person, then teach the answer.
- **Know which words matter.** Any input word that also appears in the answer becomes a
  slot. After `turn on kitchen lights -> lights.on(kitchen)` (twice),
  `turn off kitchen lights` answers `lights.off(kitchen)` (confident). If that is wrong,
  resolve it with the right answer once: resolved with `lights.dim(kitchen)`, `turn off hall
  lights` then answers `lights.dim(hall)`, and the pattern's other answers drop to
  `hypothesis` until they gain evidence again.

## Keeping what it learned

- `instance.training_sequence()["text"]`: the teachings that still support what it learned,
  as text (question line, answer line, blank line). Free.
- `instance.save()`: stores that text as an account file (needs storage). Restore with
  `client.create(sequence_id=...)`, or teach it into another instance with
  `instance.ingest_training_sequence(text)`.
- `instance.reap()`: releases the instance. Without storage its memory is discarded; with
  storage it is saved as an artifact a later instance loads.

## Errors

Every API error raises `toolving.ConceiverAPIError` with `status`, `code`, `retryable` and
`operation_id`. Repeat a call only when `retryable` is true, with the same `key`.

| Code | Do this |
|---|---|
| `UNAUTHORIZED` (401) | Ask the user for a new key. |
| `INSTANCE_INACTIVE` (409) | The instance retired: `client.create()` a new one. |
| `RUNTIME_OPERATION_REJECTED` (422) | Failed for good, nothing charged: change the input or skip it. |
| `INVALID_REQUEST` (422) | Fix the input (questions and answers are single lines). |

Full reference: `{TOOLVING_API_URL}/docs/sdk`, teaching guide: `{TOOLVING_API_URL}/docs/teaching`.
