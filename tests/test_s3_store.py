"""Integration tests for S3Store, run against a local S3-compatible endpoint.

These tests exercise the real boto3 S3 path. They reach the endpoint named by
the standard ``AWS_ENDPOINT_URL`` env var (boto3 reads it natively) - the store
itself has no knowledge of any emulator. If no endpoint is configured, or it is
unreachable, the whole module is skipped so the suite stays green without it.

To run them, point AWS_ENDPOINT_URL at a local S3-compatible server, e.g.:

    docker run -d -p 4566:4566 floci/floci:latest
    AWS_ENDPOINT_URL=http://localhost:4566 \
    AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test \
    AWS_DEFAULT_REGION=us-east-1 pytest tests/test_s3_store.py
"""
from __future__ import annotations

import os
import socket
import urllib.parse

import pytest

from server.store import ExecutionRecord, S3Store

_ENDPOINT = os.environ.get("AWS_ENDPOINT_URL")


def _endpoint_reachable(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _ENDPOINT or not _endpoint_reachable(_ENDPOINT),
    reason="no reachable S3 endpoint (set AWS_ENDPOINT_URL to a running S3-compatible server)",
)


def _record(target: str, fingerprint: str, **kw) -> ExecutionRecord:
    return ExecutionRecord(
        target_table=target,
        fingerprint=fingerprint,
        execution_type=kw.pop("execution_type", "table"),
        **kw,
    )


@pytest.fixture
def store():
    # A unique bucket per test run keeps repeated runs from contending.
    bucket = "dbtstate-test-" + os.urandom(6).hex()
    return S3Store(bucket=bucket, prefix="state/")


def test_bucket_is_created_and_empty_target_returns_nothing(store):
    # A freshly created bucket has no records for any target.
    assert store.get_records("prod.never_seen") == []


def test_add_then_get_round_trips_a_record(store):
    rec = _record("prod.fct_orders", "fp1", last_modified_epoch=1700000000)
    store.add_record(rec)

    got = store.get_records("prod.fct_orders")

    assert got == [rec]


def test_multiple_records_for_one_target_are_all_returned(store):
    r1 = _record("prod.fct_orders", "fp1", created_at=1.0)
    r2 = _record("prod.fct_orders", "fp2", created_at=2.0)
    store.add_record(r1)
    store.add_record(r2)

    got = store.get_records("prod.fct_orders")

    assert sorted(g.fingerprint for g in got) == ["fp1", "fp2"]


def test_targets_are_isolated_from_each_other(store):
    store.add_record(_record("prod.model_a", "fpa"))
    store.add_record(_record("prod.model_b", "fpb"))

    a = store.get_records("prod.model_a")
    b = store.get_records("prod.model_b")

    assert [r.fingerprint for r in a] == ["fpa"]
    assert [r.fingerprint for r in b] == ["fpb"]


def test_identical_record_written_twice_is_idempotent(store):
    rec = _record("prod.fct_orders", "fp1", created_at=5.0)
    store.add_record(rec)
    store.add_record(rec)

    got = store.get_records("prod.fct_orders")

    assert got == [rec]
