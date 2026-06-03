"""Semantic fingerprint of a model execution.

The skip decision compares the incoming fingerprint against stored ones, so the
fingerprint only needs to be self-consistent (same input -> same hash).

Folds in:
  - execution_type (FULL vs MERGE etc. must not collide)
  - the model's own SQL, normalized via sqlglot (whitespace/comments/formatting
    do not change the hash)
  - each dependency SQL passed in query_dependencies, normalized

The fingerprint covers only a model's own SQL. Invalidation when an upstream
changes is handled separately by the freshness check in servicers._is_fresh.
"""
from __future__ import annotations

import hashlib
from typing import Iterable

from sqlglot import parse_one


def normalize_sql(sql: str | None, dialect: str | None) -> str:
    if not sql:
        return ""
    try:
        return parse_one(sql, read=dialect or None).sql(
            dialect=dialect or None, normalize=True, comments=False, pretty=False
        )
    except Exception:
        # Unparseable SQL: fall back to a trimmed raw string so we still hash something stable.
        return sql.strip()


def compute_fingerprint(
    sql: str | None,
    dialect: str | None,
    execution_type: str,
    dependency_queries: Iterable[str],
) -> str:
    h = hashlib.sha256()
    h.update((execution_type or "").encode())
    h.update(b"\x00")
    h.update(normalize_sql(sql, dialect).encode())
    for dep in sorted(normalize_sql(q, dialect) for q in dependency_queries):
        h.update(b"\x00")
        h.update(dep.encode())
    return h.hexdigest()
