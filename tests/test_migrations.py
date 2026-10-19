"""Tests for Alembic migrations (T1.3).

Verifies schema creation, rollback, idempotence, metadata drift, and naming conventions
using a temporary SQLite database without network or Docker dependencies.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.config import get_settings
from app.db import Base, dispose_engine, make_engine


@pytest.fixture()
def migration_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up temporary database environment and return configured Alembic Config and db file."""
    db_file = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_file}"

    # Set environment variables
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-migrations")
    monkeypatch.setenv("DATABASE_URL", db_url)

    # Clear caches and dispose any active engine before test
    get_settings.cache_clear()
    dispose_engine()

    repo_root = Path(__file__).resolve().parent.parent
    alembic_ini_path = repo_root / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))
    alembic_cfg.set_main_option("script_location", str(repo_root / "migrations"))

    yield alembic_cfg, db_file, db_url

    # Cleanup after test
    dispose_engine()
    get_settings.cache_clear()


def test_upgrade_head_creates_expected_tables(migration_env):
    """1. upgrade head on an empty database creates exactly: users, apps, deployments, alembic_version."""
    alembic_cfg, db_file, db_url = migration_env

    # Run upgrade head
    command.upgrade(alembic_cfg, "head")

    engine = make_engine(db_url)
    try:
        inspector = inspect(engine)
        tables = sorted(inspector.get_table_names())
        expected = sorted(["users", "apps", "deployments", "alembic_version"])
        assert tables == expected
    finally:
        engine.dispose()


def test_downgrade_base_removes_application_tables(migration_env):
    """2. downgrade base removes users, apps, deployments (alembic_version may remain)."""
    alembic_cfg, db_file, db_url = migration_env

    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")

    engine = make_engine(db_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "users" not in tables
        assert "apps" not in tables
        assert "deployments" not in tables
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade_cycle(migration_env):
    """3. upgrade -> downgrade -> upgrade succeeds."""
    alembic_cfg, db_file, db_url = migration_env

    # 1st upgrade
    command.upgrade(alembic_cfg, "head")
    # Downgrade
    command.downgrade(alembic_cfg, "base")
    # 2nd upgrade
    command.upgrade(alembic_cfg, "head")

    engine = make_engine(db_url)
    try:
        inspector = inspect(engine)
        tables = sorted(inspector.get_table_names())
        expected = sorted(["users", "apps", "deployments", "alembic_version"])
        assert tables == expected
    finally:
        engine.dispose()


def test_no_metadata_drift(migration_env):
    """4. No drift: compare_metadata between migrated database and Base.metadata returns empty list."""
    alembic_cfg, db_file, db_url = migration_env

    command.upgrade(alembic_cfg, "head")

    engine = make_engine(db_url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            diff = compare_metadata(ctx, Base.metadata)
            assert diff == []
    finally:
        engine.dispose()


def test_migration_files_contain_no_name_none():
    """5. The migration file text contains no 'name=None'."""
    repo_root = Path(__file__).resolve().parent.parent
    versions_dir = repo_root / "migrations" / "versions"
    migration_files = list(versions_dir.glob("*.py"))
    assert len(migration_files) > 0, "At least one migration file should exist"

    for mf in migration_files:
        content = mf.read_text(encoding="utf-8")
        assert "name=None" not in content, f"File {mf.name} contains 'name=None'"
