# Toolving Python SDK

Release notes for the `toolving` package. Each section is shown on the
Releases page (`/releases`) for its version.

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
