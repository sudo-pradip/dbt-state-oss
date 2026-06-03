"""Run the dbt-state-oss decision server.

    python -m server --port 50051

Point the dbt-state client at it with:
    API_URL=localhost:50051 API_SECURE=false RUN_CACHE_OAUTH_CLIENT_SECRET=dev
"""
from __future__ import annotations

import argparse
from concurrent import futures

import grpc

from query_cache_protobuf.query_cache.services import (
    client_validation_service_pb2_grpc as val_grpc,
    execution_service_pb2_grpc as exec_grpc,
    health_service_pb2_grpc as health_grpc,
    sql_service_pb2_grpc as sql_grpc,
)

from .servicers import (
    ClientValidationServicer,
    ExecutionServicer,
    HealthServicer,
    SQLServicer,
    _Pending,
    _log,
)
from .store import make_store


def serve(port: int) -> None:
    store = make_store()
    pending = _Pending()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    sql_grpc.add_SQLServicer_to_server(SQLServicer(store, pending), server)
    exec_grpc.add_ExecutionServicer_to_server(ExecutionServicer(store, pending), server)
    val_grpc.add_ClientValidationServicer_to_server(ClientValidationServicer(), server)
    health_grpc.add_HealthServicer_to_server(HealthServicer(), server)

    server.add_insecure_port(f"[::]:{port}")
    server.start()
    _log(f"listening on :{port} (insecure)  store={type(store).__name__}")
    server.wait_for_termination()


def main() -> None:
    ap = argparse.ArgumentParser(prog="server")
    ap.add_argument("--port", type=int, default=50051)
    args = ap.parse_args()
    serve(args.port)


if __name__ == "__main__":
    main()
