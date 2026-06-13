# Compatibility

## Warehouses

The decision server is warehouse-independent — the `dbt-state` client reads each
relation's `last_modified` and ships the SQL; the server just fingerprints and
checks freshness.

| warehouse | status |
|---|---|
| postgres, snowflake | supported (tested) |
| databricks, redshift, bigquery | supported (untested — please [open an issue](https://github.com/sudo-pradip/dbt-state-oss/issues) if you hit problems) |

!!! note
    postgres needs `track_commit_timestamp=on` (the client reads freshness from
    `pg_xact_commit_timestamp`). Snowflake needs nothing extra.

## dbt engines

| engine | status | how to enable |
|---|---|---|
| dbt Core 1.7–1.11 (`dbt-state` plugin) | supported | auto-engages once `RUN_CACHE_API_URL` is set |
| dbt Core 1.12+ (native, beta) | supported | `DBT_ENGINE_MANAGE_STATE=true` |
| dbt Fusion (preview) | supported | `DBT_ENGINE_MANAGE_STATE=true` or `--manage-state` |

In 1.7–1.11, `dbt-state` is a separately-installed plugin that hooks the run, so
it turns on automatically. From 1.12+ it's bundled into the engine (and Fusion
reimplements it natively), so you switch state on explicitly.

All three speak the same `com.fivetran.query_cache` gRPC protocol, which is why
the same self-hosted server works across them.
