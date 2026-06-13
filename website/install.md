# Install & run

## Install

```bash
pip install dbt-state-oss          # add [s3] or [azure] for those backends
```

## Run the server

```bash
dbt-state-oss --store local --port 50051
```

Pick the backend with `--store` (`local`, `s3`, `azure`, or `memory` for tests);
see [State backends](backends.md) for each backend's flags and auth.

## Point dbt at it

The `dbt-state` client is configured with `RUN_CACHE_`-prefixed environment
variables:

```bash
export RUN_CACHE_API_URL=localhost:50051 RUN_CACHE_API_SECURE=false RUN_CACHE_OAUTH_CLIENT_SECRET=dev
dbt build      # in your dbt project; run twice and the second run NO-OPs
```

- `RUN_CACHE_API_SECURE=false` selects an insecure channel (no OAuth) — for local
  dev / trusted networks.
- `RUN_CACHE_OAUTH_CLIENT_SECRET` only needs to be *present* to pass the client's
  enable-gate in non-interactive runs (it's never used on an insecure channel).

!!! info "Native engines (dbt Core 1.12+, Fusion)"
    The 1.7–1.11 plugin auto-engages once `RUN_CACHE_API_URL` is set. On the
    native engines you turn state on explicitly with
    `DBT_ENGINE_MANAGE_STATE=true` (Fusion also accepts `--manage-state`). See
    [Compatibility](compatibility.md).

## Production auth

For a shared/production server, use TLS and point the client's
`RUN_CACHE_AUTH_URL` / `RUN_CACHE_TOKEN_URL` at your own IdP (e.g. Azure Entra
ID). The client does OAuth2 and attaches a bearer token; the server validates the
JWT.

## Try the end-to-end demo

A runnable seed → staging → mart project that NO-OPs on the second run lives in
[`example_project/`](https://github.com/sudo-pradip/dbt-state-oss/tree/main/example_project)
in the repo (it ships only in the repo, not the pip package). It uses postgres
with `track_commit_timestamp=on` — the client reads freshness from
`pg_xact_commit_timestamp`. Clone the repo, install with the `dev` extra, start
the server (`--store local`), then `dbt build --target prod` twice.
