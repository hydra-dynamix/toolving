# Toolving

**Give your agents learning and memory through an API.**

Teach your agent with questions and answers, and we make memories from them.
It handles familiar work from experience, and says so when something is new
instead of guessing.

## Releases

Toolving is distributed through this repository's
[Releases page](https://github.com/hydra-dynamix/toolving/releases). Each
release carries:

- **API client**: a thin client for the hosted Toolving API.
- **SDK**: the higher-level toolkit for teaching an instance, asking it, and
  saving and replaying training sequences.

## Status

Toolving is in active development. No release has been cut yet; the first
will follow the first end-to-end workflow: teach, save the training sequence,
restore it in a fresh instance, and show the same behaviour.
