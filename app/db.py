"""Database layer for Small Cloud.

This module provides a single, shared way to open the database and hand out
SQLAlchemy sessions.  It uses the **synchronous** SQLAlchemy 2.x API so that
routes remain simple and testable without an async runtime.

Typical FastAPI usage::

    from sqlalchemy.orm import Session
    from fastapi import Depends
    from app.db import get_session

    @app.post("/items")
    def create_item(session: Session = Depends(get_session)) -> dict:
        ...
        session.commit()
        return {"ok": True}

Importing this module has **no side effects**: no engine is created, no
settings are read, and the filesystem is not touched.  Cached resources are
built lazily on first use.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from typing import Any

import sqlalchemy
from sqlalchemy import MetaData, event
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Naming convention
# ---------------------------------------------------------------------------

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""Standard SQLAlchemy constraint-naming convention.

Passed to :class:`sqlalchemy.MetaData` so that Alembic can generate
deterministic, readable migration names for every index, unique constraint,
check constraint, foreign key, and primary key.
"""

# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Project-wide SQLAlchemy declarative base.

    All ORM model classes must inherit from this base so that they share the
    same :class:`~sqlalchemy.MetaData` (and therefore the naming convention).
    No tables are defined here; models live in ``app/models.py`` and feature
    sub-packages.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ---------------------------------------------------------------------------
# URL resolution
# ---------------------------------------------------------------------------


def resolve_database_url(settings: Settings) -> URL:
    """Return the SQLAlchemy :class:`~sqlalchemy.engine.URL` to use.

    Decision logic:

    * If ``settings.database_url`` is not *None*, parse and return it as-is.
      This lets callers inject an in-memory SQLite URL for tests or a
      PostgreSQL URL in production.
    * Otherwise create ``settings.data_dir`` (and any missing parents) and
      return a ``sqlite+pysqlite:///`` URL pointing at
      ``<data_dir_absolute>/smallcloud.db``.

    The URL is always built with :meth:`sqlalchemy.engine.URL.create` (never
    string concatenation) so the result is properly escaped.

    Args:
        settings: The :class:`~app.config.Settings` instance to read from.

    Returns:
        A fully-parsed :class:`sqlalchemy.engine.URL`.
    """
    if settings.database_url is not None:
        return sqlalchemy.engine.make_url(settings.database_url)

    # Ensure the directory exists before handing back the URL.
    db_dir = settings.data_dir.resolve()
    db_dir.mkdir(parents=True, exist_ok=True)

    return URL.create("sqlite", database=str(db_dir / "smallcloud.db"))


# ---------------------------------------------------------------------------
# Engine factory
# ---------------------------------------------------------------------------


def make_engine(url: str | URL, *, echo: bool = False) -> Engine:
    """Create and return a configured :class:`~sqlalchemy.engine.Engine`.

    SQLite databases receive extra tuning via connection-level ``PRAGMA``
    statements:

    * ``foreign_keys = ON`` – enforce referential integrity.
    * ``busy_timeout = 5000`` – wait up to 5 s when the database is locked.
    * ``journal_mode = WAL`` – enable Write-Ahead Logging for better
      concurrency (skipped for in-memory databases because WAL is not
      supported there).

    For any non-SQLite backend, :func:`~sqlalchemy.create_engine` is called
    with ``pool_pre_ping=True`` to detect stale connections before use.

    Args:
        url:  The database URL as a string or a
              :class:`~sqlalchemy.engine.URL` object.
        echo: If *True*, SQLAlchemy will log every SQL statement it emits
              (useful during development / debugging).

    Returns:
        A ready-to-use :class:`~sqlalchemy.engine.Engine`.
    """
    parsed: URL = sqlalchemy.engine.make_url(url)
    is_sqlite = parsed.get_backend_name() == "sqlite"

    if is_sqlite:
        engine = sqlalchemy.create_engine(
            parsed,
            echo=echo,
            connect_args={"check_same_thread": False},
        )

        # Detect whether this is an in-memory database.
        db_path: str = parsed.database or ""
        is_memory = db_path in ("", ":memory:") or "memory" in db_path

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(
            dbapi_connection: Any,
            connection_record: Any,  # noqa: ARG001
        ) -> None:
            """Apply recommended SQLite PRAGMAs on every new connection."""
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            if not is_memory:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    else:
        engine = sqlalchemy.create_engine(parsed, echo=echo, pool_pre_ping=True)

    return engine


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a :class:`~sqlalchemy.orm.sessionmaker` bound to *engine*.

    Sessions are created with ``expire_on_commit=False`` so that ORM objects
    remain usable after a commit without triggering lazy loads.  ``autoflush``
    is left at its SQLAlchemy default (``True``).

    Args:
        engine: The engine that sessions will use for database access.

    Returns:
        A configured :class:`~sqlalchemy.orm.sessionmaker` factory.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Cached singletons
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the application-wide :class:`~sqlalchemy.engine.Engine` singleton.

    The engine is created lazily on the first call and then cached for the
    lifetime of the process.  Settings are read from the environment via
    :func:`~app.config.get_settings`.

    Call :func:`dispose_engine` (e.g. in the app lifespan's shutdown hook) to
    release connections and clear this cache.

    Returns:
        The cached :class:`~sqlalchemy.engine.Engine`.
    """
    settings = get_settings()
    url = resolve_database_url(settings)
    return make_engine(url)


@functools.lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Return the application-wide :class:`~sqlalchemy.orm.sessionmaker` singleton.

    Built from :func:`get_engine` and cached.  Cleared by
    :func:`dispose_engine`.

    Returns:
        The cached :class:`~sqlalchemy.orm.sessionmaker`.
    """
    return make_session_factory(get_engine())


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def get_session() -> Iterator[Session]:
    """Yield one database :class:`~sqlalchemy.orm.Session` per request.

    Intended for use as a FastAPI dependency::

        from app.db import get_session
        from sqlalchemy.orm import Session
        from fastapi import Depends

        @router.get("/items")
        def list_items(session: Session = Depends(get_session)):
            ...

    Behaviour:

    * The session is **not** committed automatically; callers must call
      ``session.commit()`` explicitly when they want to persist changes.
    * If the caller raises an exception the session is rolled back and the
      exception is re-raised.
    * The session is **always** closed in a ``finally`` block.

    Yields:
        An open :class:`~sqlalchemy.orm.Session`.
    """
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Shutdown helper
# ---------------------------------------------------------------------------


def dispose_engine() -> None:
    """Dispose the cached engine and clear all related caches.

    Calls :meth:`~sqlalchemy.engine.Engine.dispose` on the cached engine (if
    one was ever created) so that all pooled connections are closed gracefully.
    Then clears the :func:`get_engine` and :func:`get_session_factory` caches
    so the next call to either function creates fresh instances.

    Safe to call multiple times and when no engine has ever been created.
    """
    # Only dispose if the cache actually holds an engine.
    if get_engine.cache_info().currsize:
        get_engine().dispose()

    get_engine.cache_clear()
    get_session_factory.cache_clear()
