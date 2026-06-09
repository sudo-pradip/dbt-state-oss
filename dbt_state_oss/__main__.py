"""Run the dbt-state-oss decision server.

    dbt-state-oss --store s3 --bucket my-bucket --port 50051

Point the dbt-state client at it with:
    RUN_CACHE_API_URL=localhost:50051 RUN_CACHE_API_SECURE=false RUN_CACHE_OAUTH_CLIENT_SECRET=dev
"""
from __future__ import annotations

import argparse
import os
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


def serve(port: int, store) -> None:
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
    ap = argparse.ArgumentParser(prog="dbt-state-oss")
    ap.add_argument("--store", choices=["local", "s3", "azure", "memory"],
                    help="state backend (env STATE_STORE; default local)")
    ap.add_argument("--port", type=int,
                    help="listen port (env DBTSTATE_PORT; default 50051)")
    ap.add_argument("--dir", help="[local] state directory (env DBTSTATE_LOCAL_DIR)")
    ap.add_argument("--bucket", help="[s3] bucket (env DBTSTATE_S3_BUCKET)")
    ap.add_argument("--account", help="[azure] storage account (env DBTSTATE_AZURE_ACCOUNT)")
    ap.add_argument("--container", help="[azure] container (env DBTSTATE_AZURE_CONTAINER)")
    ap.add_argument("--prefix", help="[s3|azure] key prefix (env DBTSTATE_S3_PREFIX/DBTSTATE_AZURE_PREFIX)")
    args = ap.parse_args()

    port = args.port or int(os.environ.get("DBTSTATE_PORT") or 50051)
    store = make_store(
        store=args.store, dir=args.dir, bucket=args.bucket,
        prefix=args.prefix, account=args.account, container=args.container,
    )
    serve(port, store)


if __name__ == "__main__":
    main()
