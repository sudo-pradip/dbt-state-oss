# Hosting

`app.state.dbt.com` bundles two things: dbt Labs' cloud storage **and** a gRPC
decision server running 24/7. **dbt-state-oss decouples them** — you bring your
own storage (local / S3 / Azure) and you choose where the server runs.

The server is a small gRPC service. It doesn't have to run 24/7, but **a process
must be listening while dbt is talking to it** — dbt opens a gRPC channel and
makes several calls during the run. Your storage backend keeps the *data*; the
server just has to be reachable *during* the run.

## 1. Co-located sidecar (simplest)

Start the server next to dbt (on `localhost`) just for the run; state persists in
your backend between runs. No standing infrastructure.

| host | use case |
|---|---|
| developer's local machine | local dev / interactive runs |
| GitHub Actions | CI runs (server as a background step) |
| Snowflake notebook | run alongside dbt inside Snowflake |
| Databricks notebook | run alongside dbt inside Databricks |
| Databricks dbt job / workflow | scheduled jobs (server as a sidecar task) |

## 2. Central always-on

Your own `app.state.dbt.com`: one long-lived server the whole team/CI points at,
so NO-OP state is shared across runs. Add TLS + OAuth (see [Install & run](install.md#production-auth)).

| host | fit |
|---|---|
| container / VM — Cloud Run, Azure Container Apps, ECS/Fargate, Kubernetes, plain VM | ✅ a long-lived gRPC service fits naturally |
| serverless — AWS Lambda, Azure Functions | ❌ request/response only; can't host a persistent gRPC listener the client dials during a run |

!!! warning "Tested scope"
    Only the local sidecar is tested end-to-end. The other rows are deployment
    patterns, not yet verified here.
