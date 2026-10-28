# Toolving Python SDK

Python client for the hosted Toolving API. It depends only on `httpx`.

See the [customer changelog](CHANGELOG.md) for release updates.

## Install

Download the wheel from the [Toolving releases page](https://github.com/hydra-dynamix/toolving/releases), verify the published SHA-256 checksum, then install it:

```sh
python -m pip install toolving-0.13.0-py3-none-any.whl
```

Python 3.10 or later is required.

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
        print(prediction.output)
    else:
        print("UNRESOLVED")
    print(prediction.confidence)
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

`instance.training_sequence()` assembles current training text without storing it, so Free accounts can copy and manage sequences locally. `save()` requires account storage. Saved training sequences are UTF-8 `.txt` files. Each teaching uses one question line and one answer line; one empty line separates teachings. `ingest_training_sequence()` validates this format and sends each pair through normal teaching. A standard query costs 1 credit. `with_memory=True` requests complete relevant teaching episodes and costs 2 credits. Teaching costs 5 credits. Conceiver returns `hypothesis`, `confident`, or `high` as evidence levels, not probability percentages. Always check `status` before using an answer.

Final retirement on an account with storage returns checksum-verified artifact metadata. Final retirement without storage returns `persisted=False` and discards live memory. Reaping one of several active instances does not retire the shared account state.

## Recovery

Every mutation has an idempotency key. The SDK creates one when `key` is omitted. Supply and retain your own key when a request may need recovery. Repeating the same request with the same key returns the original operation result without repeating learning or charging. Creates are the one exception: a repeated create returns the same instance while it is live, but once that instance has retired the service starts a new one, and the returned instance has `replaced=True` with `replaced_instance_id` and `replaced_operation_id`. Use a new key for each create you mean to be new. `client.operation(operation_id)` reads an accepted operation after response loss.

Questions and answers are single-line strings. Raw JSON syntax support within Conceiver's taught language is in active development. A JSON-encoded answer can still be carried as text and decoded by the SDK.

## Earlier API compatibility

`LearnerClient` and the local playground remain in this wheel for deployments using the earlier API. New Conceiver integrations should use `ConceiverClient`.
