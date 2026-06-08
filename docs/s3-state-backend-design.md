# S3 state-store backend — design

Date: 2026-06-08

## Goal

Add an `s3` backend to the pluggable `StateStore` interface so the decision
server can persist run records to AWS S3, alongside the existing `local`,
`azure_blob`, and `memory` backends. Production targets **real AWS S3**.

## Principles

- **Production code targets real AWS S3 only.** No code path, env var, or branch
  in `server/` exists to accommodate any local emulator. The store calls
  `boto3.client("s3")` and lets boto3's standard configuration resolve
  credentials, region, and (where set) endpoint.
- **Secret-free, mirroring `AzureBlobStore`.** We never read or store an access
  key/secret in our code. boto3's default credential chain provides them: IAM
  role, instance profile, SSO, `AWS_*` env vars, or `~/.aws/credentials`. We only
  read non-secret config (bucket, prefix) from our own env vars.
- **S3 objects are immutable** — there is no append. The record layout is
  one immutable object per record, not the append-style single file the
  `local`/`azure_blob` backends use.

## Backend: `S3Store` (in `server/store.py`)

Self-contained class in the style of `AzureBlobStore`, with a lazy
`import boto3` inside `__init__` so the core install stays SDK-free.

Constructor signature: `S3Store(bucket: str, prefix: str = "state/")`.

```python
import boto3
self._s3 = boto3.client("s3")   # creds, region, endpoint all resolved by boto3
self._bucket = bucket
self._prefix = prefix
```

### Config (env), matching the `DBTSTATE_*` convention

| env | meaning | default |
|---|---|---|
| `DBTSTATE_S3_BUCKET` | bucket name (**required**) | — |
| `DBTSTATE_S3_PREFIX` | key prefix | `state/` |

Region, credentials, and endpoint are **not** our env vars — they come from
boto3's standard resolution (`AWS_DEFAULT_REGION`/profile for region,
the default credential chain for creds, `AWS_ENDPOINT_URL` for endpoint).

### Layout — one immutable object per record

- **Key for a target:** `target_hash = sha256(target_table.encode())[:16]`
  (identical hashing to the other backends).
- **`add_record(record)`** → `PUT <prefix><target_hash>/<record_hash>.json`,
  where `record_hash = sha256(record.to_json().encode())[:16]`. Hashing the full
  serialized record (including `created_at`) keeps records unique; an identical
  re-record harmlessly collapses onto the same key (idempotent).
- **`get_records(target_table)`** → `list_objects_v2` under
  `<prefix><target_hash>/`, `GET` each object, parse via
  `ExecutionRecord.from_json`. A missing/empty prefix returns `[]`.

No read-modify-write, so writes are concurrency-safe (S3 is strongly
consistent) — including across multiple server processes.

### Bucket bootstrap

Best-effort, mirroring Azure's swallow-on-exists `create_container`:

- Attempt `create_bucket`. Read the region from `self._s3.meta.region_name`;
  if it is set and not `us-east-1`, pass
  `CreateBucketConfiguration={"LocationConstraint": region}` (required by AWS
  for non-default regions). Swallow all exceptions — a pre-existing bucket or a
  caller without create permission still works for read/write.

## `make_store()` wiring

Add an `s3` branch:

```python
if kind == "s3":
    bucket = os.environ.get("DBTSTATE_S3_BUCKET")
    if not bucket:
        raise ValueError("STATE_STORE=s3 requires DBTSTATE_S3_BUCKET")
    return S3Store(bucket=bucket, prefix=os.environ.get("DBTSTATE_S3_PREFIX", "state/"))
```

Update the function docstring and the final `ValueError` to include `s3`.

## Packaging (`pyproject.toml`)

- New extra: `s3 = ["boto3>=1.28"]`. The `>=1.28` floor guarantees boto3 honors
  the standard `AWS_ENDPOINT_URL` env var (needed by the test path; harmless in
  production).
- Add `pytest` to the `dev` extra.

## Tests

floci (a LocalStack-style AWS emulator) is used **only** by the tests as a
stand-in S3 endpoint. It is reached purely through standard AWS env vars that
boto3 reads natively — the production store has no knowledge of it.

### `tests/test_s3_store.py` — integration vs floci

- A fixture sets standard AWS env vars (`AWS_ENDPOINT_URL=http://localhost:4566`,
  `AWS_ACCESS_KEY_ID=test`, `AWS_SECRET_ACCESS_KEY=test`,
  `AWS_DEFAULT_REGION=us-east-1`) and constructs `S3Store(bucket, prefix)`.
- If floci is not reachable (endpoint unset or connection fails), the module
  **skips with a clear message**, so the suite stays green without docker.
- Cases:
  - round-trip: `add_record` then `get_records` returns the record;
  - empty target → `[]`;
  - multiple records for one target are all returned;
  - target isolation: two targets do not bleed into each other;
  - idempotent re-record: writing the same record twice yields one object;
  - bucket auto-create against a fresh bucket name.

### `tests/test_store_unit.py` — no infra

- `ExecutionRecord` JSON round-trip (`to_json`/`from_json`).
- Key-hashing helper stability (same target → same key).

Run with `pytest`. To run the floci integration tests:

```bash
docker run -p 4566:4566 floci/floci:latest
```

## Docs

- **README:** move S3 from the Roadmap to a supported backend row; add an S3 auth
  note (boto3 default credential chain; identity needs S3 read/write on the
  bucket) and the `STATE_STORE=s3 DBTSTATE_S3_BUCKET=...` invocation. No mention
  of any local emulator or testing stand-in — this is a public repo.
- **HANDOFF.md (gitignored):** record the floci dev loop, the no-append /
  object-per-record rationale, and that endpoint redirection is done via the
  standard `AWS_ENDPOINT_URL` so production stays emulator-agnostic.

## Out of scope

- Azure/local/memory backends are untouched.
- No GCS or OneLake backend in this change.
- No changes to the gRPC servicers, fingerprint, or auth.
