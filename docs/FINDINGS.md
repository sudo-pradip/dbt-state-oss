# Evaluation findings: dbt-state for an OSS / ADLS-backed setup

Investigation of `dbt-state==2.22.8` (wheel source + live test on local postgres).

## License
- The GitHub repo `dbt-labs/dbt-state` is **Apache-2.0** (client + libs + `.proto`).
- The published **wheel omits the LICENSE file** and leaves the metadata `License:`
  field blank - a packaging artifact, not a license status. The code is open.
- The **server (decision engine) is not in the repo** and is not open. It is the
  hosted, metered service at `api.state.dbt.com`.

## Adapter support
- dbt-state officially supports **snowflake, bigquery, databricks, redshift**
  (same four as dbt Fusion / dbt-core 2.0). A `postgres` client extension exists in
  code but is unadvertised (their test target).
- **Fabric / fabricspark is unsupported.** Adding it needs a client-side
  `BaseAdapterExtension` subclass (the gap), independent of the server.
- Gotcha: `pip install dbt-postgres dbt-state` pulls **dbt-core 2.0 alpha (Fusion)**,
  which has no postgres adapter. Pin `dbt-core<2.0`.

## NO-OP requires a server (proven)
Ran `dbt build` x2 on postgres with dbt-state installed but unauthenticated:
```
State adapter: dbt-state v2.22.8 is enabled
State adapter: dbt State disabled: not authenticated and no OAuth client credentials configured.
... PASS=3 NO-OP=0   (both runs rebuilt everything)
```
With no auth the client **disables itself and falls back to vanilla dbt**. There is
no offline/local mode. The skip decision is server-side (`SkipExecutionResponse`
from `SQLService.SubmitEnrichedSQL`).

## Feasibility of a self-hosted, ADLS-backed replacement: YES
- `API_URL`, `AUTH_URL`, `TOKEN_URL` are all env-overridable (`grpc/client.py:123`).
- `secure = address endswith :443` (or `API_SECURE`); when false ->
  `grpc.insecure_channel`, **no OAuth** (`client.py:~130`).
- Servicer base classes ship in `query_cache_protobuf` -> subclass + serve.
- State is small structured metadata (per target table), so ADLS/S3 is a fine store.
- The only novel work is the **fingerprint + decision logic**, where we have full
  design freedom (must be self-consistent between record and check).

## The two build components
1. **Server (this repo):** the engine + ADLS/S3 state store. The big new thing.
2. **fabricspark client adapter extension:** needed for the stock client to run on
   fabricspark at all; contributable upstream to the Apache-2.0 client repo.

## Data egress note (hosted version only)
With dbt Labs' server, `SubmitEnrichedSQLRequest.sql` ships the **full raw model SQL**
plus upstream dependency SQL. Self-hosting keeps that inside your tenant.
