# dbt-state-oss

An open-source, self-hosted decision server for the Apache-2.0
[`dbt-state`](https://github.com/dbt-labs/dbt-state) client, with the state
store backed by **ADLS / S3** instead of dbt Labs' hosted, metered service.

## Why

`dbt-state` skips redundant model executions ("NO-OP" on a second run) and
auto-defers to prod, without a manifest. But the **decision engine is a hosted,
metered gRPC service** (`api.state.dbt.com`); the pip package is only a client.
With no auth, the client silently disables itself and dbt runs vanilla.

The client, the protobuf protocol, and the shared libs are all **Apache-2.0**.
Only the server is closed. This project builds an open replacement server that:

- speaks the same gRPC protocol (reuses the client's `*Servicer` stubs),
- keeps all state in **your own storage** (ADLS Gen2 first, S3 later),
- needs **no dbt Labs account** (insecure channel for dev; your own OAuth/Entra ID for prod).

## How the client/server split works (verified against the wheel)

- **Client (unchanged, Apache-2.0):** compiles model SQL, extracts deps + table
  refs (sqlglot), reads each input's `last_modified` from the warehouse via an
  adapter extension, hashes seed files, ships **raw SQL + metadata** over gRPC,
  acts on the verdict, and reports outcomes back.
- **Server (this repo):** computes a semantic fingerprint, matches it against
  stored history for the target table, checks freshness + execution_type, and
  returns **skip / clone / execute**. Persists run records to ADLS/S3.

Our fingerprint algorithm only has to be **self-consistent** between
"record a run" and "check a run" - it does not need to match dbt Labs'.

## Auth

- **Dev / trusted network:** `API_URL=localhost:50051` (non-:443) or `API_SECURE=false`
  -> insecure channel, zero OAuth. In CI/non-interactive, set
  `RUN_CACHE_OAUTH_CLIENT_SECRET=<dummy>` to pass the client's disable-gate
  (presence-checked only; never used on an insecure channel).
- **Production:** TLS + override `AUTH_URL`/`TOKEN_URL` to your own IdP
  (e.g. Azure Entra ID, same identity that guards ADLS). Client does OAuth2 and
  attaches a bearer token; the server validates the JWT.

## Layout

```
server/          the gRPC decision server (the engine)
example_project/ a tiny dbt-postgres project (seed -> staging -> mart) for local testing
docs/            PROTOCOL.md (the reverse-engineered contract), FINDINGS.md (the eval)
reference/       local copy of dbt-labs' Apache-2.0 client source (gitignored, not committed)
```

## Status

**v1 works (postgres, local, in-memory store).** Verified end-to-end against our own
server with zero dbt Labs:

| scenario | result |
|---|---|
| second run, nothing changed | all models **NO-OP** (reused, no SQL run) |
| comment / whitespace-only edit | **NO-OP** (semantic fingerprint) |
| real SQL change to a model | that model rebuilds |
| real change upstream | downstream rebuilds too (freshness check, cache stays safe) |
| seed file unchanged | seed **NO-OP** (via values_hash) |

Requires postgres `track_commit_timestamp=on` (the client reads freshness from
`pg_xact_commit_timestamp`); the local docker postgres sets it.

### State backends (pluggable via `STATE_STORE` env)

**Initial version supports `local` and `azure_blob` only.** (`memory` exists for
dev/tests.) All backends implement the same two-method `StateStore` interface, so
the roadmap entries are additive.

| backend | status | env |
|---|---|---|
| `local` | supported | `STATE_DIR` |
| `azure_blob` | supported | `DBTSTATE_BLOB_ACCOUNT`, `DBTSTATE_BLOB_CONTAINER`, `DBTSTATE_BLOB_PREFIX` |
| `memory` | dev/test only | - |

**Roadmap (not yet implemented):**
- S3 (AWS)
- Google Cloud Storage
- Fabric OneLake files

**Azure Blob auth:** `DefaultAzureCredential` (`az login` locally, OIDC/workload-identity
in CI, managed identity on Azure). The identity needs the **Storage Blob Data
Contributor** role on the account (control-plane Owner/Contributor is NOT enough):
```bash
az role assignment create --assignee-object-id <your-oid> --assignee-principal-type User \
  --role "Storage Blob Data Contributor" \
  --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<acct>
```

Next milestones: S3 / GCS / OneLake backends -> fabricspark adapter extension -> clone + prod auth.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[azure,dev]"

# terminal 1: the server (local file store)
STATE_STORE=local .venv/bin/dbt-state-oss --port 50051

# terminal 2: dbt, pointed at our server
cd example_project
export DBT_PROFILES_DIR=$PWD
export RUN_CACHE_API_URL=localhost:50051 RUN_CACHE_API_SECURE=false RUN_CACHE_OAUTH_CLIENT_SECRET=dev
../.venv/bin/dbt build --target prod    # run 1: builds
../.venv/bin/dbt build --target prod    # run 2: NO-OP
```

Client env vars use the `RUN_CACHE_` prefix. `RUN_CACHE_API_SECURE=false` selects an
insecure channel (no OAuth); `RUN_CACHE_OAUTH_CLIENT_SECRET` need only be present to
pass the client's enable-gate in non-interactive runs.

To use Azure Blob instead of local: `az login`, then start the server with
`STATE_STORE=azure_blob DBTSTATE_BLOB_ACCOUNT=<acct> .venv/bin/dbt-state-oss`.
