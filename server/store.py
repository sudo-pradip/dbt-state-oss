"""State store: the prior-run history the skip decision is made against.

A pluggable interface (get_records / add_record) over a small structured record
per target table - not table data. Backends: InMemoryStore, LocalFileStore,
AzureBlobStore, S3Store.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol


@dataclass
class ExecutionRecord:
    """One confirmed prior execution of a target table."""

    target_table: str
    fingerprint: str
    execution_type: str
    last_modified_epoch: Optional[int] = None
    table_type: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> "ExecutionRecord":
        return cls(**json.loads(line))


class StateStore(Protocol):
    def get_records(self, target_table: str) -> list[ExecutionRecord]: ...
    def add_record(self, record: ExecutionRecord) -> None: ...


class InMemoryStore:
    """Process-local store. Fine for local dev / proving the mechanics.

    dbt runs models on multiple threads, so the gRPC server handles concurrent
    calls - guard the dict with a lock.
    """

    def __init__(self) -> None:
        self._by_table: dict[str, list[ExecutionRecord]] = {}
        self._lock = threading.Lock()

    def get_records(self, target_table: str) -> list[ExecutionRecord]:
        with self._lock:
            return list(self._by_table.get(target_table, []))

    def add_record(self, record: ExecutionRecord) -> None:
        with self._lock:
            self._by_table.setdefault(record.target_table, []).append(record)


class LocalFileStore:
    """Persistent store on the local filesystem.

    One append-only JSONL file per target table (filename = hash of the target
    name, so quoting/case can't collide on disk). Survives server restarts, needs
    no credentials, and exercises the exact same interface the cloud backends use.

    Object-per-target keeps parallel writes from different models from contending,
    which matters under dbt's multi-threaded runs.
    """

    def __init__(self, directory: str) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, target_table: str) -> Path:
        key = hashlib.sha256(target_table.encode()).hexdigest()[:16]
        return self._dir / f"{key}.jsonl"

    def get_records(self, target_table: str) -> list[ExecutionRecord]:
        path = self._path(target_table)
        if not path.exists():
            return []
        with self._lock:
            lines = path.read_text().splitlines()
        return [ExecutionRecord.from_json(ln) for ln in lines if ln.strip()]

    def add_record(self, record: ExecutionRecord) -> None:
        path = self._path(record.target_table)
        with self._lock:
            with path.open("a") as f:
                f.write(record.to_json() + "\n")


class AzureBlobStore:
    """Persistent store on Azure Blob Storage.

    One append blob per target table (`<prefix><hash>.jsonl`). Auth is via
    DefaultAzureCredential - so `az login` locally, OIDC/workload-identity in CI,
    or a managed identity on Azure compute all work with no key on disk. The
    identity needs the 'Storage Blob Data Contributor' role on the account.

    Credentials are never read from the repo - only the account/container names
    come from env (DBTSTATE_BLOB_ACCOUNT, DBTSTATE_BLOB_CONTAINER).
    """

    def __init__(self, account: str, container: str, prefix: str = "state/") -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        self._svc = BlobServiceClient(
            f"https://{account}.blob.core.windows.net", credential=DefaultAzureCredential()
        )
        self._container = self._svc.get_container_client(container)
        try:
            self._container.create_container()
        except Exception:
            pass  # already exists (or no create permission - reads/writes may still work)
        self._prefix = prefix

    def _blob(self, target_table: str):
        key = hashlib.sha256(target_table.encode()).hexdigest()[:16]
        return self._container.get_blob_client(f"{self._prefix}{key}.jsonl")

    def get_records(self, target_table: str) -> list[ExecutionRecord]:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            data = self._blob(target_table).download_blob().readall().decode()
        except ResourceNotFoundError:
            return []
        return [ExecutionRecord.from_json(ln) for ln in data.splitlines() if ln.strip()]

    def add_record(self, record: ExecutionRecord) -> None:
        from azure.core.exceptions import ResourceExistsError

        blob = self._blob(record.target_table)
        try:
            blob.create_append_blob()
        except ResourceExistsError:
            pass
        blob.append_block((record.to_json() + "\n").encode())


class S3Store:
    """Persistent store on AWS S3.

    S3 objects are immutable (no append), so this writes one object per record
    at `<prefix><target-hash>/<record-hash>.json` rather than appending to one
    file per target like the local/azure backends. add_record is a plain PUT and
    get_records lists+fetches the target's prefix - no read-modify-write.

    Credentials, region, and endpoint all come from boto3's standard
    configuration (default credential chain); none are read from this repo. The
    identity needs read/write on the bucket.
    """

    def __init__(self, bucket: str, prefix: str = "state/") -> None:
        import boto3

        self._s3 = boto3.client("s3")
        self._bucket = bucket
        self._prefix = prefix
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        # Best-effort: an existing bucket or one we can't create still serves reads/writes.
        region = self._s3.meta.region_name
        kwargs = {"Bucket": self._bucket}
        if region and region != "us-east-1":
            # us-east-1 rejects an explicit LocationConstraint; every other region requires it.
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        try:
            self._s3.create_bucket(**kwargs)
        except Exception:
            pass

    @staticmethod
    def _target_prefix(prefix: str, target_table: str) -> str:
        key = hashlib.sha256(target_table.encode()).hexdigest()[:16]
        return f"{prefix}{key}/"

    def _record_key(self, record: ExecutionRecord) -> str:
        body = record.to_json()
        digest = hashlib.sha256(body.encode()).hexdigest()[:16]
        return f"{self._target_prefix(self._prefix, record.target_table)}{digest}.json"

    def get_records(self, target_table: str) -> list[ExecutionRecord]:
        prefix = self._target_prefix(self._prefix, target_table)
        records: list[ExecutionRecord] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                body = self._s3.get_object(Bucket=self._bucket, Key=obj["Key"])["Body"].read()
                records.append(ExecutionRecord.from_json(body.decode()))
        return records

    def add_record(self, record: ExecutionRecord) -> None:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=self._record_key(record),
            Body=record.to_json().encode(),
        )


def make_store() -> StateStore:
    """Build the StateStore selected by STATE_STORE (default "local").

    local:       STATE_DIR (default ./.state_data)
    azure_blob:  DBTSTATE_BLOB_ACCOUNT, DBTSTATE_BLOB_CONTAINER (default "dbt-state"),
                 DBTSTATE_BLOB_PREFIX (default "state/"); auth via DefaultAzureCredential.
    s3:          DBTSTATE_S3_BUCKET, DBTSTATE_S3_PREFIX (default "state/"); auth,
                 region, and endpoint via boto3's standard configuration.
    memory:      no config.
    """
    kind = os.environ.get("STATE_STORE", "local").lower()
    if kind == "memory":
        return InMemoryStore()
    if kind == "local":
        return LocalFileStore(os.environ.get("STATE_DIR", ".state_data"))
    if kind in ("azure_blob", "blob"):
        account = os.environ.get("DBTSTATE_BLOB_ACCOUNT")
        if not account:
            raise ValueError("STATE_STORE=azure_blob requires DBTSTATE_BLOB_ACCOUNT")
        return AzureBlobStore(
            account=account,
            container=os.environ.get("DBTSTATE_BLOB_CONTAINER", "dbt-state"),
            prefix=os.environ.get("DBTSTATE_BLOB_PREFIX", "state/"),
        )
    if kind == "s3":
        bucket = os.environ.get("DBTSTATE_S3_BUCKET")
        if not bucket:
            raise ValueError("STATE_STORE=s3 requires DBTSTATE_S3_BUCKET")
        return S3Store(bucket=bucket, prefix=os.environ.get("DBTSTATE_S3_PREFIX", "state/"))
    raise ValueError(f"Unknown STATE_STORE={kind!r} (expected memory|local|azure_blob|s3)")
