# dbt-state-oss

An open-source, self-hosted decision server for the Apache-2.0
[`dbt-state`](https://github.com/dbt-labs/dbt-state) client, keeping the state
store in **your own storage** (local disk, S3, or Azure Blob) instead of dbt
Labs' hosted, metered service.

## Why

`dbt-state` skips redundant model executions ("NO-OP" on a second run) and
auto-defers to prod, without a manifest. But the **decision engine is a hosted,
metered gRPC service** (`api.state.dbt.com`); the pip package is only a client.
With no auth, the client silently disables itself and dbt runs vanilla.

The client, the protobuf protocol, and the shared libs are all **Apache-2.0**.
Only the server is closed. This project builds an open replacement server that:

- speaks the same gRPC protocol (reuses the client's `*Servicer` stubs),
- keeps all state in **your own storage** (local disk, S3, or Azure Blob),
- needs **no dbt Labs account** (insecure channel for dev; your own OAuth/Entra ID for prod).

## How the client/server split works (verified against the wheel)

- **Client (unchanged, Apache-2.0):** compiles model SQL, extracts deps + table
  refs (sqlglot), reads each input's `last_modified` from the warehouse via an
  adapter extension, hashes seed files, ships **raw SQL + metadata** over gRPC,
  acts on the verdict, and reports outcomes back.
- **Server (this repo):** computes a semantic fingerprint, matches it against
  stored history for the target table, checks freshness + execution_type, and
  returns **skip / clone / execute**. Persists run records to your chosen
  backend (local, S3, or Azure Blob).

Our fingerprint algorithm only has to be **self-consistent** between
"record a run" and "check a run" - it does not need to match dbt Labs'.

## Auth

- **Dev / trusted network:** `RUN_CACHE_API_URL=localhost:50051` (non-:443) or
  `RUN_CACHE_API_SECURE=false` -> insecure channel, zero OAuth. In CI/non-interactive,
  set `RUN_CACHE_OAUTH_CLIENT_SECRET=<dummy>` to pass the client's disable-gate
  (presence-checked only; never used on an insecure channel).
- **Production:** TLS + override `RUN_CACHE_AUTH_URL`/`RUN_CACHE_TOKEN_URL` to your
  own IdP (e.g. Azure Entra ID, same identity that guards your storage). Client does
  OAuth2 and attaches a bearer token; the server validates the JWT.

## Repo layout

(The pip package ships only `dbt_state_oss/`; the rest is for development.)

```
dbt_state_oss/   the gRPC decision server (the engine)
example_project/ a tiny dbt-postgres project (seed -> staging -> mart) for local testing
tests/           unit + S3 integration tests
docs/            PROTOCOL.md (the reverse-engineered contract), FINDINGS.md (the eval)
reference/       local copy of dbt-labs' Apache-2.0 client source (gitignored, not committed)
```

## Status

`local`,
`s3`, and `azure` state stores are tested and supported.

Should work on **postgres, snowflake, databricks, redshift, and bigquery**.

**postgres** and **snowflake** are tested; the others are untested (feel free to try, test and raise issues).

Behaviors verified end-to-end:

| scenario | result |
|---|---|
| second run, nothing changed | all models **NO-OP** (reused, no SQL run) |
| comment / whitespace-only edit | **NO-OP** (semantic fingerprint) |
| real SQL change to a model | that model rebuilds |
| real change upstream | downstream rebuilds too (freshness check, cache stays safe) |
| seed file unchanged | seed **NO-OP** (via values_hash) |
| dev run, model not built in dev | reads its upstream from prod (defer-to-prod) |


### State backends

Pick the backend with `--store` (or the `STATE_STORE` env var). Each backend's
config takes a CLI flag that falls back to its env var. All backends implement
the same two-method `StateStore` interface, so the roadmap entries are additive.

| backend | status | flags | env |
|---|---|---|---|
| `local` | supported | `--dir` | `DBTSTATE_LOCAL_DIR` |
| `s3` | supported | `--bucket`, `--prefix` | `DBTSTATE_S3_BUCKET`, `DBTSTATE_S3_PREFIX` |
| `azure` | supported | `--account`, `--container`, `--prefix` | `DBTSTATE_AZURE_ACCOUNT`, `DBTSTATE_AZURE_CONTAINER`, `DBTSTATE_AZURE_PREFIX` |
| `memory` | dev/test only | - | - |

```bash
dbt-state-oss --store s3    --bucket my-bucket
dbt-state-oss --store azure --account acct --container dbt-state
dbt-state-oss --store local --dir ./.state_data
```

**Roadmap (not yet implemented):**
- Google Cloud Storage (`gcs`)
- Fabric OneLake files

**Azure auth:** `DefaultAzureCredential` (`az login` locally, OIDC/workload-identity
in CI, managed identity on Azure). The identity needs the **Storage Blob Data
Contributor** role on the account (control-plane Owner/Contributor is NOT enough):
```bash
az role assignment create --assignee-object-id <your-oid> --assignee-principal-type User \
  --role "Storage Blob Data Contributor" \
  --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<acct>
```

**S3 auth:** the boto3 default credential chain (IAM role, instance profile, SSO,
`AWS_*` env vars, or `~/.aws/credentials`). No keys are read from this repo. The
identity needs read/write on the bucket; region comes from your standard AWS
configuration. After `pip install "dbt-state-oss[s3]"`, start the server with
`dbt-state-oss --store s3 --bucket <bucket>`.

Next milestones: GCS / OneLake backends -> fabricspark adapter extension -> clone + prod auth.

## Install & run

```bash
pip install dbt-state-oss          # add [s3] or [azure] for those backends
dbt-state-oss --store local --port 50051
```

Then point your dbt-state client at the server (client env vars use the
`RUN_CACHE_` prefix):

```bash
export RUN_CACHE_API_URL=localhost:50051 RUN_CACHE_API_SECURE=false RUN_CACHE_OAUTH_CLIENT_SECRET=dev
dbt build      # in your dbt project; run twice and the second run NO-OPs
```

`RUN_CACHE_API_SECURE=false` selects an insecure channel (no OAuth);
`RUN_CACHE_OAUTH_CLIENT_SECRET` only needs to be *present* to pass the client's
enable-gate in non-interactive runs. Switch backends with `--store` (see the
table above), e.g. `dbt-state-oss --store azure --account <acct>` after `az login`.

## The NO-OP demo (from a clone)

A runnable seed -> staging -> mart project that NO-OPs on the second run lives in
`example_project/`. It ships only in the repo (not the pip package) and needs a
postgres with `track_commit_timestamp=on` — the client reads freshness from
`pg_xact_commit_timestamp`. The example profile expects postgres on `:5433`,
database `dbt_oss`. Clone the repo, install with the `dev` extra, start the
server (`--store local`), then `dbt build --target prod` twice from
`example_project/`.
