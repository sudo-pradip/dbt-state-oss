"""gRPC servicers implementing the dbt-state decision protocol.

Subclass the generated *Servicer base classes from query_cache_protobuf.

Flow:
  SubmitEnrichedSQL  -> fingerprint, look up history:
                          match + fresh -> SkipExecutionResponse  (the NO-OP)
                          else           -> ReadyToExecuteResponse(request_id), held as pending
  SubmitValues       -> seeds: skip when values_hash matches, else execute
  ConfirmExecution   -> client ran the model; move the pending entry into the store
  RecordExecutions   -> write-only / bypass path; store directly from the enriched_sql
  ValidateClientVersion / Check -> trivial OK responses
"""
from __future__ import annotations

import threading
import uuid

from query_cache_protobuf.query_cache import shared_pb2
from query_cache_protobuf.query_cache.services import (
    client_validation_service_pb2 as val_pb2,
    client_validation_service_pb2_grpc as val_grpc,
    execution_service_pb2 as exec_pb2,
    execution_service_pb2_grpc as exec_grpc,
    health_service_pb2 as health_pb2,
    health_service_pb2_grpc as health_grpc,
    sql_service_pb2 as sql_pb2,
    sql_service_pb2_grpc as sql_grpc,
)

from .fingerprint import compute_fingerprint
from .store import ExecutionRecord, StateStore

_RESULT = shared_pb2.SubmitSQLResultType
_REASON = shared_pb2.RejectionReason
_EXEC_TYPE = shared_pb2.ModelExecutionType


def _log(msg: str) -> None:
    print(f"[dbt-state-oss] {msg}", flush=True)


class _Pending:
    """request_id -> (target_table, fingerprint, execution_type) between SubmitEnrichedSQL
    and the client's ConfirmExecution."""

    def __init__(self) -> None:
        self._d: dict[str, tuple[str, str, str]] = {}
        self._lock = threading.Lock()

    def put(self, rid: str, value: tuple[str, str, str]) -> None:
        with self._lock:
            self._d[rid] = value

    def pop(self, rid: str) -> tuple[str, str, str] | None:
        with self._lock:
            return self._d.pop(rid, None)


def _execute_response(pending: _Pending, target: str, fingerprint: str, etype: str):
    rid = uuid.uuid4().hex
    pending.put(rid, (target, fingerprint, etype))
    decision = shared_pb2.ExplainedDecision(
        decision=_RESULT.READY_TO_EXECUTE,
        skip_rejection_reason=_REASON.Value("NO_SUITABLE_MATCH_FOUND"),
    )
    return sql_pb2.SubmitSQLResponse(
        ready_to_execute=sql_pb2.ReadyToExecuteResponse(
            request_id=rid, explained_decision=decision
        )
    )


def _skip_response():
    decision = shared_pb2.ExplainedDecision(decision=_RESULT.SKIP_EXECUTION, is_stale=False)
    return sql_pb2.SubmitSQLResponse(
        skip_execution=sql_pb2.SkipExecutionResponse(explained_decision=decision)
    )


class SQLServicer(sql_grpc.SQLServicer):
    def __init__(self, store: StateStore, pending: _Pending) -> None:
        self._store = store
        self._pending = pending

    def SubmitEnrichedSQL(self, request, context):
        target = request.target_table or ""
        etype = _EXEC_TYPE.Name(request.execution_type)
        dep_queries = [d.query for d in request.query_dependencies]
        fingerprint = compute_fingerprint(request.sql, request.dialect, etype, dep_queries)

        match = None
        if target:
            match = next(
                (r for r in self._store.get_records(target) if r.fingerprint == fingerprint),
                None,
            )

        if match is not None and self._is_fresh(target, match, request.tables):
            _log(f"SKIP   {target}  (fingerprint match + fresh)")
            return _skip_response()

        reason = "no match" if match is None else "stale (an input is newer than last build)"
        _log(f"EXECUTE {target or '<unknown>'}  ({reason})")
        return _execute_response(self._pending, target, fingerprint, etype)

    @staticmethod
    def _is_fresh(target, match, tables) -> bool:
        """A skip is safe only if the target was built AFTER every input was last
        modified. dbt builds upstream first, so an upstream that changed this run
        carries a newer last_modified_epoch than the target's recorded build time.

        `tables` carries the target itself plus its inputs (commit timestamps read
        live from the warehouse). Compare inputs against the target's build time.
        """
        norm = lambda n: (n or "").replace('"', "").replace("`", "").strip()
        target_n = norm(target)
        by_name = {norm(t.name): t.last_modified_epoch for t in tables}

        target_lm = by_name.get(target_n)
        if target_lm is None:
            target_lm = match.last_modified_epoch
        if target_lm is None:
            return False  # can't prove freshness -> rebuild

        for name, lm in by_name.items():
            if name == target_n:
                continue
            if lm is not None and lm > target_lm:
                return False  # an input is newer than the target's last build -> stale
        return True

    def SubmitValues(self, request, context):
        # Seeds: skip when the file content (values_hash) is unchanged. Skipping keeps
        # the seed table's commit timestamp stable, so downstream freshness holds.
        target = request.target_table or ""
        fingerprint = f"values:{request.values_hash}"
        matched = target and any(
            r.fingerprint == fingerprint for r in self._store.get_records(target)
        )
        if matched:
            _log(f"SKIP   {target}  (seed values_hash match)")
            return _skip_response()
        _log(f"EXECUTE {target}  (seed - new/changed values)")
        return _execute_response(
            self._pending, target, fingerprint, _EXEC_TYPE.Name(_EXEC_TYPE.VALUES)
        )


class ExecutionServicer(exec_grpc.ExecutionServicer):
    def __init__(self, store: StateStore, pending: _Pending) -> None:
        self._store = store
        self._pending = pending

    def ConfirmExecution(self, request, context):
        entry = self._pending.pop(request.request_id)
        if entry and not request.failed_to_clone:
            target, fingerprint, etype = entry
            self._store.add_record(
                ExecutionRecord(
                    target_table=target,
                    fingerprint=fingerprint,
                    execution_type=etype,
                    last_modified_epoch=request.last_modified_epoch,
                    table_type=request.table_type or None,
                )
            )
            _log(f"RECORD {target}  (confirmed)")
        return exec_pb2.ConfirmExecutionResponse(request_id=request.request_id, success=True)

    def RecordExecutions(self, request, context):
        stored = 0
        for rec in request.records:
            if rec.HasField("enriched_sql"):
                es = rec.enriched_sql
                etype = _EXEC_TYPE.Name(es.execution_type)
                fp = compute_fingerprint(
                    es.sql, es.dialect, etype, [d.query for d in es.query_dependencies]
                )
                self._store.add_record(
                    ExecutionRecord(
                        target_table=es.target_table or "",
                        fingerprint=fp,
                        execution_type=etype,
                        last_modified_epoch=rec.outcome.last_modified_epoch,
                        table_type=rec.outcome.table_type or None,
                    )
                )
                stored += 1
        return exec_pb2.RecordExecutionsResponse(records_stored=stored)


class ClientValidationServicer(val_grpc.ClientValidationServicer):
    def ValidateClientVersion(self, request, context):
        return val_pb2.ValidateClientVersionResponse(is_supported=True)


class HealthServicer(health_grpc.HealthServicer):
    def Check(self, request, context):
        return health_pb2.HealthCheckResponse(status=health_pb2.HealthCheckResponse.SERVING)

    def Watch(self, request, context):
        yield health_pb2.HealthCheckResponse(status=health_pb2.HealthCheckResponse.SERVING)
