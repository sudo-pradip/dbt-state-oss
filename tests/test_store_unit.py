"""Unit tests for the state store that need no infrastructure."""
from __future__ import annotations

from dbt_state_oss.store import ExecutionRecord, S3Store


def test_execution_record_json_round_trip():
    rec = ExecutionRecord(
        target_table="prod_analytics.fct_orders",
        fingerprint="abc123",
        execution_type="table",
        last_modified_epoch=1700000000,
        table_type="table",
        created_at=1700000001.5,
    )
    restored = ExecutionRecord.from_json(rec.to_json())
    assert restored == rec


def test_s3_target_prefix_is_stable_for_same_target():
    a = S3Store._target_prefix("state/", "prod.model_x")
    b = S3Store._target_prefix("state/", "prod.model_x")
    assert a == b


def test_s3_target_prefix_differs_per_target():
    a = S3Store._target_prefix("state/", "prod.model_x")
    b = S3Store._target_prefix("state/", "prod.model_y")
    assert a != b


def test_s3_target_prefix_honors_prefix_and_is_a_directory():
    p = S3Store._target_prefix("custom/", "prod.model_x")
    assert p.startswith("custom/")
    assert p.endswith("/")
