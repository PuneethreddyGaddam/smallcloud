"""Tests for app.db – database layer (T1.2).

Uses only tmp_path (no Docker, no network).  A local DeclarativeBase and a
small ``Note`` table are declared here so we never depend on the real app
models (which don't exist yet in T1.2).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Integer, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

import app.db
from app.config import Settings, get_settings
from app.db import (
    dispose_engine,
    get_engine,
    get_session,
    make_engine,
    make_session_factory,
    resolve_database_url,
)

# ---------------------------------------------------------------------------
# Local test-only ORM model (NOT the real app Base)
# ---------------------------------------------------------------------------


class _TestBase(DeclarativeBase):
    pass


class Note(_TestBase):
    """Minimal table used only inside this test module."""

    __tablename__ = "note"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(256), nullable=False)


# ---------------------------------------------------------------------------
# Cache-clearing fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def clear_caches() -> Iterator[None]:
    """Clear cached singletons before AND after each test that uses them."""
    get_settings.cache_clear()
    dispose_engine()
    yield
    dispose_engine()
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test 1: round-trip insert + read in a file database
# ---------------------------------------------------------------------------


def test_round_trip(tmp_path: Path) -> None:
    """Insert a Note via one session, read it back via a second session."""
    db_file = tmp_path / "rt.db"
    engine = make_engine(f"sqlite:///{db_file}")
    _TestBase.metadata.create_all(engine)
    factory = make_session_factory(engine)

    # Write
    with factory() as write_session:
        write_session.add(Note(text="hello world"))
        write_session.commit()

    # Read – fresh session
    with factory() as read_session:
        note = read_session.query(Note).one()

    assert note.text == "hello world"
    engine.dispose()


# ---------------------------------------------------------------------------
# Test 2: SQLite PRAGMAs are applied on a file database
# ---------------------------------------------------------------------------


def test_sqlite_pragmas(tmp_path: Path) -> None:
    """Verify foreign_keys, journal_mode and busy_timeout are set."""
    db_file = tmp_path / "pragma.db"
    engine = make_engine(f"sqlite:///{db_file}")

    with engine.connect() as conn:
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
        jm = conn.execute(text("PRAGMA journal_mode")).scalar()
        bt = conn.execute(text("PRAGMA busy_timeout")).scalar()

    assert fk == 1, f"expected foreign_keys=1, got {fk}"
    assert jm == "wal", f"expected journal_mode=wal, got {jm}"
    assert bt == 5000, f"expected busy_timeout=5000, got {bt}"
    engine.dispose()


# ---------------------------------------------------------------------------
# Test 3: resolve_database_url creates missing directories and returns SQLite URL
# ---------------------------------------------------------------------------


def test_resolve_database_url_creates_dir(tmp_path: Path) -> None:
    """resolve_database_url creates nested dirs and returns a smallcloud.db URL."""
    nested = tmp_path / "nested" / "dir"
    settings = Settings(_env_file=None, secret_key="x", data_dir=nested)  # type: ignore[call-arg]
    url = resolve_database_url(settings)

    assert nested.exists(), "nested directory should have been created"
    db_path = Path(url.database)
    assert db_path.name == "smallcloud.db"
    assert db_path.parent == nested.resolve()


# ---------------------------------------------------------------------------
# Test 4: resolve_database_url honours the database_url override
# ---------------------------------------------------------------------------


def test_resolve_database_url_override(tmp_path: Path) -> None:
    """When database_url is set, resolve_database_url returns that URL."""
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        secret_key="x",
        data_dir=tmp_path,
        database_url="sqlite:///:memory:",
    )
    url = resolve_database_url(settings)
    assert url.drivername == "sqlite"
    assert url.database == ":memory:"


# ---------------------------------------------------------------------------
# Test 5: get_session rolls back on error and closes the session
# ---------------------------------------------------------------------------


def test_get_session_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Driving the generator manually: error → rollback, normal → close."""
    db_file = tmp_path / "sess.db"
    engine = make_engine(f"sqlite:///{db_file}")
    _TestBase.metadata.create_all(engine)
    factory = make_session_factory(engine)

    monkeypatch.setattr(app.db, "get_session_factory", lambda: factory)

    # --- error path ---
    gen = get_session()
    session: Session = next(gen)
    close_spy = MagicMock(wraps=session.close)
    session.close = close_spy  # type: ignore[method-assign]

    session.add(Note(text="should-be-rolled-back"))
    # flush so the object is in the transaction but not committed
    session.flush()

    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("boom"))

    # The session should no longer be in an active transaction and close() was called
    assert not session.in_transaction()
    close_spy.assert_called_once()

    # A fresh session must find no rows
    with factory() as verify_session:
        count = verify_session.query(Note).count()
    assert count == 0, "rolled-back data must not be persisted"

    # --- normal path ---
    gen2 = get_session()
    session2: Session = next(gen2)
    close_spy2 = MagicMock(wraps=session2.close)
    session2.close = close_spy2  # type: ignore[method-assign]

    # Session has not yet been closed; it accepts operations.
    session2.execute(text("SELECT 1"))  # should not raise
    with pytest.raises(StopIteration):
        next(gen2)

    # Close was called on success
    close_spy2.assert_called_once()
    # After the generator is exhausted, close() has been called and the
    # internal transaction object is released.
    assert session2.get_transaction() is None

    engine.dispose()


# ---------------------------------------------------------------------------
# Test 6: get_engine uses DATA_DIR from the environment
# ---------------------------------------------------------------------------


def test_get_engine_uses_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clear_caches: None
) -> None:
    """get_engine() resolves the DB path from env-var DATA_DIR."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", "test-key")

    engine = get_engine()
    db_path = Path(engine.url.database)
    assert db_path.parent == tmp_path.resolve()
    dispose_engine()


# ---------------------------------------------------------------------------
# Test 7: dispose_engine is idempotent
# ---------------------------------------------------------------------------


def test_dispose_engine_idempotent(clear_caches: None) -> None:
    """dispose_engine is safe to call twice and when no engine exists."""
    # No engine created yet → must not raise
    dispose_engine()
    dispose_engine()
