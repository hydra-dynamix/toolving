# Toolving Python SDK

Python client for the hosted Toolving API. It depends only on `httpx`.

See the [customer changelog](CHANGELOG.md) for release updates.

## Install

Python 3.10 or later is required. The installer finds the newest release, checks its SHA-256, and installs it with the Python tooling you already use: the active virtualenv or conda env, the Poetry, PDM or uv project in the current directory, then `uv tool`, `pipx`, or `pip --user` where your Python accepts installs. It never creates an environment or installs a package manager; if nothing applies, it stops and says why.

Linux, macOS and WSL:

```sh
curl -fsSL https://toolving.com/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://toolving.com/install.ps1 | iex
```

Or download the wheel from the [Toolving releases page](https://github.com/hydra-dynamix/toolving/releases), verify the published SHA-256 checksum, and install it with your own tool:

```sh
python -m pip install toolving-0.14.0-py3-none-any.whl
```

Set `TOOLVING_API_URL` (`https://toolving.com`) and `TOOLVING_API_KEY` in your environment. Step-by-step setup for each system and shell, written so a coding agent can follow it, is at [toolving.com/agents.md](https://toolving.com/agents.md).

## Teach and query Conceiver

```python
from toolving import ConceiverClient

with ConceiverClient(api_url, account_api_key) as client:
    print(client.models())
    print(client.storage())
    instance = client.create(model="language", key="create-workflow")
    instance.teach(
        "Where do Acme invoices go?",
        "Finance / vendors / Acme",
        key="teach-acme",
    )

    prediction = instance.query(
        "Where do Acme invoices go?",
        key="query-acme",
    )
    if prediction.status == "ANSWER":
        # A best guess plus a flag: hypothesis, confident or highly confident.
        print(prediction.output, prediction.confidence)
    else:
        print("UNRESOLVED")
    print(prediction.evidence.markdown)

    current_text = instance.training_sequence()["text"]  # available without storage
    sequence_id = instance.save(key="save-training")      # requires account storage
    training_text = client.training_sequence_text(sequence_id)
    print(training_text)

    imported = client.create(key="create-import-target")
    imported.ingest_training_sequence(training_text)

    remembered = instance.query(
        "Where do Acme invoices go?",
        with_memory=True,
        key="query-acme-with-memory",
    )
    print(remembered.evidence.episodes)

    retirement = instance.reap(key="retire-workflow")
    print(retirement.persisted, retirement.artifact)
```

`client.models()` lists selectable hosted models. JSON selection also requires `client_host` and a 64-character hexadecimal `compatibility_scope`. `client.storage()` reports whether durable account storage is enabled and its bounded usage.

`instance.training_sequence()` assembles current training text without storing it, so Free accounts can copy and manage sequences locally. `save()` requires account storage. Saved training sequences are UTF-8 `.txt` files. Each teaching uses one question line and one answer line; one empty line separates teachings. `ingest_training_sequence()` validates this format and sends each pair through normal teaching. A standard query costs 1 credit. `with_memory=True` requests complete relevant teaching episodes and costs 2 credits. Teaching costs 5 credits. Conceiver returns `hypothesis`, `confident`, or `highly confident` as evidence levels (Conceiver 1.x said `high`), not probability percentages. One example already gives a best guess for a new input, flagged `hypothesis`; you decide whether to use it. When nothing taught fits, the guess is the input itself, flagged `hypothesis`, and its evidence says there is no evidence for it. `UNRESOLVED`, with no answer at all, is rare. A second, contrasting example is how a pattern gains evidence and its flag rises. Always check `status` before using an answer.

Final retirement on an account with storage returns checksum-verified artifact metadata. Final retirement without storage returns `persisted=False` and discards live memory. Reaping one of several active instances does not retire the shared account state.

## Recovery

An API error raises `ConceiverAPIError` with `status`, `code`, `retryable` and `operation_id`. `409 INSTANCE_INACTIVE` means the instance has retired: create a new one. `422 RUNTIME_OPERATION_REJECTED` means Conceiver could not carry out that operation; it has failed for good, nothing is charged, and repeating it returns the same error, so do not retry it.

Every mutation has an idempotency key. The SDK creates one when `key` is omitted. Supply and retain your own key when a request may need recovery. Repeating the same request with the same key returns the original operation result without repeating learning or charging. Creates are the one exception: a repeated create returns the same instance while it is live, but once that instance has retired the service starts a new one, and the returned instance has `replaced=True` with `replaced_instance_id` and `replaced_operation_id`. Use a new key for each create you mean to be new. `client.operation(operation_id)` reads an accepted operation after response loss.

Questions and answers are single-line strings. Raw JSON syntax support within Conceiver's taught language is in active development. A JSON-encoded answer can still be carried as text and decoded by the SDK.

## Earlier API compatibility

`LearnerClient` remains in this wheel for deployments using the earlier API. New Conceiver integrations should use `ConceiverClient`.

## Local playground and inspector

`toolving-playground` is a local browser UI for writing training sequences and teaching them to a local Conceiver build (the Rust `conceiver` crate). It needs `cargo` and `rustc`; it compiles a small driver against the crate when it starts.

```sh
toolving-playground --conceiver path/to/conceiver/conceiver   # or set TOOLVING_CONCEIVER
toolving-inspector --conceiver path/to/conceiver/conceiver    # the same server, opening the inspector
```

The playground teaches a sequence line by line: each line is asked first, then its answer is taught, and every answer shows its steps and evidence. Its assistant panel is a chat that asks about your problem and proposes a sequence. It runs Claude Code through [agentctl](https://github.com/hydra-dynamix/agentctl); the page's **Assistant settings** show whether agentctl and Claude Code are installed and signed in, give the install commands for your platform, and set the agentctl path, model and timeout. Settings and the agentctl config the playground writes for its agent are kept in `~/.toolving/playground/` (or `TOOLVING_PLAYGROUND_HOME`); your own agentctl config is not changed. Everything else works without agentctl. The inspector, at `/inspect` on the same server and so on the same trie, asks single lines, teaches corrections, and lists what the trie holds. Both listen on loopback only.
