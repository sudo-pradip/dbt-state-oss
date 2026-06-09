# dbt-state gRPC protocol (reverse-engineered from `dbt-state==2.22.8`)

Source of truth: the Apache-2.0 `.proto` files in
[`dbt-labs/dbt-state/proto`](https://github.com/dbt-labs/dbt-state/tree/main/proto)
and the generated stubs in the installed `query_cache_protobuf` package. Field
shapes below were introspected from the compiled `_pb2` descriptors.

Client dials `API_URL` (default `api.state.dbt.com:443`). Server address and
`API_SECURE` decide TLS+OAuth vs insecure. All 7 services live under
`query_cache_protobuf.query_cache.services`. (These config keys are read from
env with a `RUN_CACHE_` prefix, e.g. `RUN_CACHE_API_URL` / `RUN_CACHE_API_SECURE`.)

## Services (only ~4 carry weight)

| Service | RPCs | We implement |
|---|---|---|
| `SQLService` | `SubmitEnrichedSQL`, `SubmitValues` | **yes - the verdict** |
| `ExecutionService` | `RecordExecutions`, `ConfirmExecution` | **yes - writes state** |
| `CloneService` | `Clone` | phase 4 |
| `ClientValidationService` | `ValidateClientVersion` | yes (return `is_supported=true`) |
| `HealthService` | `Check`, `Watch` | yes (return SERVING) |
| `ExplainService` | `GetExplainMessages` | optional (human text) |
| `ClientTelemetryService` | - | no-op (this is dbt Labs' metering) |

## The decision RPC: `SQLService.SubmitEnrichedSQL`

### Request `SubmitEnrichedSQLRequest`
```
target_table: str?            # rendered "db.schema.table" of the model being built
dialect: str                  # e.g. "postgres", "databricks", "spark"
default_catalog: str
execution_type: enum          # FULL|APPEND|MERGE|INSERT_OVERWRITE|DELETE_INSERT|
                              #   MICROBATCH|SNAPSHOT|DBT_DATA_TEST|VALUES|VIEW|DBT_CUSTOM
sql: str                      # RAW compiled model SQL  <-- server fingerprints this
tables: [{name, last_modified_epoch}]          # freshness of each input relation
query_dependencies: [{name, query, default_catalog, default_schema}]  # upstream SQL
semantic_extras: map<str,str>
freshness_tolerance_seconds: int
lenient_dependencies: set<str>
tolerate_nondeterminism: bool
labels: map<str,str>
clone_time_travel_limit, clone_table_properties, clone_chain_depth_limit, stale_upstream_policy
```

### Response `SubmitSQLResponse` (oneof `response`)
```
oneof response:
  ready_to_execute -> ReadyToExecuteResponse {
      request_id, explained_decision, transformed_nodes_by_query,
      query_hash_metadata_info{semantic_hash_match, data_hash_match}?, execution_decision_id? }
  skip_execution   -> SkipExecutionResponse  {          # <-- the NO-OP
      explained_decision, transformed_nodes_by_query,
      execution_results (Struct), execution_runtime_ms?, execution_decision_id? }
  ready_to_clone   -> ReadyToCloneResponse   {
      request_id, explained_decision, clone_sqls[], clone_source, clone_target,
      clone_required_last_modified_epoch?, ... }
```

### `ExplainedDecision`
```
decision: enum SubmitSQLResultType { SKIP_EXECUTION | READY_TO_EXECUTE | READY_TO_CLONE | UNKNOWN }
skip_rejection_reason: enum RejectionReason?
clone_rejection_reason: enum RejectionReason?
is_stale: bool
```
`RejectionReason`: NO_SUITABLE_MATCH_FOUND, TARGET_TABLE_MISMATCH/MATCH,
EXECUTION_TYPE_MISMATCH, EXECUTION_TYPE_NOT_FULL, TARGET_TABLE_DOES_NOT_EXIST,
FORCED_NOT_ELIGIBLE, LATEST_QUERY_HASH_NOT_MATCH, OUTSIDE_TIME_TRAVEL_WINDOW,
CLONE_CHAIN_LIMIT_EXCEEDED.

## Writing state: `ExecutionService`

After a real run the client calls `ConfirmExecution` (and `RecordExecutions` for
write-only / bypass paths) so future runs can be skipped:
```
ConfirmExecutionRequest  { request_id, last_modified_epoch?, failed_to_clone,
                           table_type?, execution_results(Struct), execution_runtime_ms?, labels }
ConfirmExecutionResponse { request_id, success }

RecordExecutionsRequest  { records: [ ExecutionRecord{
    outcome: ExecutionOutcome{last_modified_epoch?, table_type?, execution_results, execution_runtime_ms?},
    enriched_sql: SQLExecution?,    # mirror of SubmitEnrichedSQLRequest minus clone fields
    values: ValuesExecution? } ] }
RecordExecutionsResponse { records_stored: uint }
```

## Trivial services
```
ValidateClientVersionRequest { dbt_run_cache_version } -> { is_supported: bool }   # return true
HealthCheckRequest { service } -> { status: enum }                                  # return SERVING
```

## Client-side vs server-side (verified)
- Client computes only: table-name normalization + dependency extraction (sqlglot),
  and an **md5 of seed file contents** (`values_hash`, for SubmitValues). For models
  it sends **raw SQL**.
- Server owns: the **semantic fingerprint**, the **match against history**, and the
  **skip/clone/execute decision**. None of this is in the client.

## What the server must store (the "state")
Per `target_table`, an append log of:
```
{ semantic_hash, deps_hash, last_modified_epoch, execution_type,
  execution_results, table_type, created_at }
```
Decision = does the incoming fingerprint match a stored record, is the data fresh
enough (inputs not newer than the recorded run), is execution_type compatible
-> SKIP / CLONE / EXECUTE.
