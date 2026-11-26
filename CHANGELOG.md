# Toolving Python SDK

Release notes for the `toolving` package. Each section is shown on the
Releases page (`/releases`) for its version.

## 0.16.1 (2026-11-26)

- Agent skill and README: the hosted models are `language` and `numbers` (the skill said `language` and `json`). They now describe the numbers model and what it gets wrong, `spin_down()` / `set_spin_down()`, when an answer stays a `hypothesis`, exact-word matching, and that `pip --user` puts `toolving-skill` in a folder that is often not on `PATH` (`python -m toolving.skill` works either way). No code changes.

## 0.16.0 (2026-11-26)

- Conceiver: a ready-made numbers model, a basic calculator. `create(model="numbers")` starts an instance that already answers `1234 plus 4321`, `-5 + 3`, `2.5 * 4`, `10 / 4`, `17 % 5`, `1,000 - 2,500`, `300 compared to 299` (greater / less / same) and `12 and 10` (also or, xor): words or symbols, negatives, up to six decimals, whole parts up to about a trillion. It declines what it cannot answer, such as a division by zero; chained expressions like `1 + 2 * 3` are not handled. Teach it more like any instance.
- Conceiver: `spin_down()` reads and `set_spin_down(save)` sets whether an instance's spin-down (an idle retirement or a reap) saves the model as a stored artifact. On by default.

## 0.15.1 (2026-11-26)

- Conceiver: set a language instance's word delimiters with `create(delimiters={"split": " ", "keep": ".(),"})`. `split` characters separate words and are dropped; `keep` characters separate words and are words themselves. Without it the instance keeps the owner's default (split at the space; keep `. , ; : ! ? ( ) [ ] { } " /`).

## 0.15.0 (2026-11-25)

- Agent skill: the SDK ships the `toolving-conceiver` skill, which teaches a coding agent how to teach, query and correct Conceiver. Install it with `python -m toolving.skill install` (or `toolving-skill install`) for Claude Code, Codex, Pi and any host that reads `~/.agents/skills`; `--dir` installs it into any other harness's skills folder, `print` prints it.
- Predictions accept the confidence `ambiguous`: an answer whose evidence for and against is level.

## 0.14.0 (2026-11-12)

- Conceiver 2.0: predictions accept the confidence `highly confident`. `high` is still accepted for Conceiver 1.x results.

## 0.13.0 (2026-10-28)

- Conceiver: create an instance from a retired artifact with `create(artifact_id=...)`; new instances otherwise start empty.
- Conceiver: list stored artifacts and pin or unpin them (`artifacts()`, `pin_artifact()`, `unpin_artifact()`); pinned artifacts are never overwritten when storage is full.
- Conceiver: retirement results carry `purpose`, `rotated_sequence_ids` and `skipped` (`"NOTHING_TAUGHT"` when nothing was kept).
- Conceiver: a create whose key had already created an instance that has since retired returns a new instance with `replaced`, `replaced_instance_id` and `replaced_operation_id`.
- This repository is the single source of the SDK; toolving-api ships a copy of this release.
- Discover available hosted models and explicitly select language or compatibility-scoped JSON instances.
- Read account storage availability and bounded usage from the agent API.
- List saved training sequences, optionally filtered by model.
- Assemble and copy current construction-derived training text without server-side storage, including on Free accounts.
- Download stored construction-derived training sequences as plain UTF-8 text.
- Ingest canonical question-and-answer line pairs through the normal teaching path.

## 0.12.1 (2026-10-19)

- Expose answered and unresolved Markdown decision evidence as `TextPrediction.evidence`.
- Add `query(..., with_memory=True)` and complete relevant `EvidenceEpisode` values.
- Expose native prediction confidence as `hypothesis`, `confident`, `high`, or `None` when a result has no confidence field.
- Return `Retirement` and optional checksum-verified `Artifact` metadata from `reap()`.
- Preserve confidence and evidence on recovered operation results without changing prediction policy.

## 0.12.0 (2026-10-17)

- Ship `ConceiverClient`, `ConceiverInstance`, `ConceiverAPIError` and `TextPrediction` for the native language API.
- Create, teach, query, resolve, save, reap and restore with explicit operation identities.
- Preserve the existing `LearnerClient` and local playground for earlier deployments.
- The previously distributed 0.11.0 wheel remains unchanged.

## 0.11.0 (2026-10-03)

- Wait for durable service operations without an overall execution deadline.
- Resume accepted work with `learner.wait_for_operation(client.last_operation_id)`.
- Retain an optional correctness assessment separately when recording an outcome.

## 0.10.0 (2026-10-02)

- Configuration from `TOOLVING_API_URL` and `TOOLVING_API_KEY`.
- Install with the release installer: `curl -fsSL <your API URL>/install.sh | sh`.

## 0.9.0 (2026-10-02)

- First release as `toolving` (`pip install toolving`, `import toolving`).
- `toolving-playground`: a local browser UI for trying a learner.
