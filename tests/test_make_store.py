"""Backend selection/resolution in make_store - flag args win over env vars,
which win over defaults. Infra-free: only the memory/local backends and the
pre-construction validation paths are exercised (no cloud calls)."""
from __future__ import annotations

import pytest

from dbt_state_oss.store import InMemoryStore, LocalFileStore, make_store

_ENV_VARS = [
    "STATE_STORE",
    "DBTSTATE_LOCAL_DIR",
    "DBTSTATE_S3_BUCKET",
    "DBTSTATE_S3_PREFIX",
    "DBTSTATE_AZURE_ACCOUNT",
    "DBTSTATE_AZURE_CONTAINER",
    "DBTSTATE_AZURE_PREFIX",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_to_local(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    store = make_store()
    assert isinstance(store, LocalFileStore)


def test_memory_backend(monkeypatch):
    monkeypatch.setenv("STATE_STORE", "memory")
    assert isinstance(make_store(), InMemoryStore)


def test_store_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("STATE_STORE", "local")
    assert isinstance(make_store(store="memory"), InMemoryStore)


def test_local_dir_from_env(monkeypatch, tmp_path):
    d = tmp_path / "from_env"
    monkeypatch.setenv("DBTSTATE_LOCAL_DIR", str(d))
    make_store(store="local")
    assert d.exists()


def test_local_dir_arg_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DBTSTATE_LOCAL_DIR", str(tmp_path / "env_dir"))
    chosen = tmp_path / "arg_dir"
    make_store(store="local", dir=str(chosen))
    assert chosen.exists()
    assert not (tmp_path / "env_dir").exists()


def test_s3_requires_bucket():
    with pytest.raises(ValueError, match="bucket"):
        make_store(store="s3")


def test_azure_requires_account():
    with pytest.raises(ValueError, match="account"):
        make_store(store="azure")


def test_unknown_store_is_rejected():
    with pytest.raises(ValueError, match="azure_blob|Unknown"):
        make_store(store="azure_blob")  # old name no longer valid


def test_unknown_store_message_lists_choices():
    with pytest.raises(ValueError, match="local|s3|azure|memory"):
        make_store(store="nope")
