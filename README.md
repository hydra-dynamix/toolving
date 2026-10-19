# Toolving Python SDK

Python client for the hosted Toolving API. It depends only on `httpx`.

## Install

Download the wheel from the [Toolving releases page](https://github.com/hydra-dynamix/toolving/releases), verify the published SHA-256 checksum, then install it:

```sh
python -m pip install toolving-0.12.1-py3-none-any.whl
```

Python 3.10 or later is required.

## Teach and query Conceiver

```python
from toolving import ConceiverClient

with ConceiverClient(api_url, account_api_key) as client:
    instance = client.create(key="create-workflow")
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

    remembered = instance.query(
        "Where do Acme invoices go?",
        with_memory=True,
        key="query-acme-with-memory",
    )
    print(remembered.evidence.episodes)

    retirement = instance.reap(key="retire-workflow")
    print(retirement.persisted, retirement.artifact)
```

A standard query costs 1 credit. `with_memory=True` requests complete relevant teaching episodes and costs 2 credits. Teaching costs 5 credits. Conceiver returns `hypothesis`, `confident`, or `high` as evidence levels, not probability percentages. Always check `status` before using an answer.

Final retirement on an account with storage returns checksum-verified artifact metadata. Final retirement without storage returns `persisted=False` and discards live memory. Reaping one of several active instances does not retire the shared account state.

## Recovery

Every mutation has an idempotency key. The SDK creates one when `key` is omitted. Supply and retain your own key when a request may need recovery. Repeating the same request with the same key returns the original operation result without repeating learning or charging. `client.operation(operation_id)` reads an accepted operation after response loss.

Questions and answers are single-line strings. Raw JSON syntax support within Conceiver's taught language is in active development. A JSON-encoded answer can still be carried as text and decoded by the SDK.

## Earlier API compatibility

`LearnerClient` and the local playground remain in this wheel for deployments using the earlier API. New Conceiver integrations should use `ConceiverClient`.
