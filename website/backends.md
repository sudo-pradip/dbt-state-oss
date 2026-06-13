# State backends

The server keeps a small run record per target table (fingerprint + freshness
metadata — **not** table data) in a backend you choose. Pick it with `--store`
(or the `STATE_STORE` env var). Each backend's config takes a CLI flag that falls
back to its env var.

| backend | status | flags | env |
|---|---|---|---|
| `local` | supported | `--dir` | `DBTSTATE_LOCAL_DIR` |
| `s3` | supported | `--bucket`, `--prefix` | `DBTSTATE_S3_BUCKET`, `DBTSTATE_S3_PREFIX` |
| `azure` | supported | `--account`, `--container`, `--prefix` | `DBTSTATE_AZURE_ACCOUNT`, `DBTSTATE_AZURE_CONTAINER`, `DBTSTATE_AZURE_PREFIX` |
| `memory` | dev/test only | – | – |

```bash
dbt-state-oss --store s3    --bucket my-bucket
dbt-state-oss --store azure --account acct --container dbt-state
dbt-state-oss --store local --dir ./.state_data
```

## S3 auth

The boto3 default credential chain (IAM role, instance profile, SSO, `AWS_*` env
vars, or `~/.aws/credentials`). No keys are read from the repo. The identity needs
read/write on the bucket; region comes from your standard AWS configuration.

```bash
pip install "dbt-state-oss[s3]"
dbt-state-oss --store s3 --bucket <bucket>
```

## Azure auth

`DefaultAzureCredential` (`az login` locally, OIDC/workload-identity in CI,
managed identity on Azure). The identity needs the **Storage Blob Data
Contributor** role on the account (control-plane Owner/Contributor is not enough):

```bash
az role assignment create --assignee-object-id <your-oid> --assignee-principal-type User \
  --role "Storage Blob Data Contributor" \
  --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<acct>
```

## Roadmap

Not yet implemented: Google Cloud Storage (`gcs`), Snowflake stage files,
Databricks Unity Catalog volumes, Fabric OneLake files. All backends implement
the same small two-method interface, so these are additive.
