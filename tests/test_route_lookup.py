"""Tests for app.route_lookup – Route lookup and status management (A1.1).

Covers all functional requirements, security boundaries, edge cases,
transaction isolation, and import safety.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import app.route_lookup
from app.db import Base, make_engine, make_session_factory
from app.models import App, AppKind, AppStatus, User
from app.route_lookup import (
    UNSET,
    AppRoute,
    change_status,
    get_app_route,
    lookup_route,
    set_status,
)

# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_engine(tmp_path: Path):
    """Create a temporary SQLite engine with all SmallCloud tables."""
    db_file = tmp_path / "route_lookup_test.db"
    engine = make_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    """Return a sessionmaker bound to the temporary SQLite engine."""
    return make_session_factory(db_engine)


@pytest.fixture()
def db_session(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    """Seed test users and applications with various statuses and yield a session."""
    monkeypatch.setattr(
        app.route_lookup, "get_session_factory", lambda: session_factory
    )

    session = session_factory()
    user1 = User(email="owner1@example.com")
    user2 = User(email="owner2@example.com")
    session.add_all([user1, user2])
    session.flush()

    apps = [
        App(
            owner_id=user1.id,
            slug="running-app-1234",
            name="Running App",
            kind=AppKind.streamlit.value,
            status=AppStatus.running.value,
            host_port=18001,
        ),
        App(
            owner_id=user1.id,
            slug="stopped-app-1234",
            name="Stopped App",
            kind=AppKind.flask.value,
            status=AppStatus.stopped.value,
            host_port=None,
        ),
        App(
            owner_id=user2.id,
            slug="paused-app-1234",
            name="Paused App",
            kind=AppKind.fastapi.value,
            status=AppStatus.paused.value,
            host_port=18002,
        ),
        App(
            owner_id=user2.id,
            slug="waking-app-1234",
            name="Waking App",
            kind=AppKind.script.value,
            status=AppStatus.waking.value,
            host_port=18003,
        ),
        App(
            owner_id=user1.id,
            slug="crashed-app-1234",
            name="Crashed App",
            kind=AppKind.streamlit.value,
            status=AppStatus.crashed.value,
            host_port=None,
        ),
        App(
            owner_id=user2.id,
            slug="archived-app-1234",
            name="Archived App",
            kind=AppKind.static.value,
            status=AppStatus.archived.value,
            host_port=None,
        ),
    ]
    session.add_all(apps)
    session.commit()

    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 1. Required Result
# ---------------------------------------------------------------------------


def test_required_result(db_session: Session) -> None:
    """1. get_app_route and lookup_route return accurate AppRoute snapshots for known slugs."""
    route = get_app_route(db_session, "running-app-1234")
    assert route is not None
    assert isinstance(route, AppRoute)
    assert isinstance(route.app_id, int)
    assert route.slug == "running-app-1234"
    assert route.kind == "streamlit"
    assert route.status == "running"
    assert route.host_port == 18001
    assert route.owner_email == "owner1@example.com"
    assert route.upstream_url == "http://127.0.0.1:18001"

    # Convenience wrapper returns identical snapshot
    convenience_route = lookup_route("running-app-1234")
    assert convenience_route == route

    # Non-existent slug returns None
    assert get_app_route(db_session, "missing-slug-9999") is None
    assert lookup_route("missing-slug-9999") is None


# ---------------------------------------------------------------------------
# 2. Malformed Inputs Execute Zero SQL
# ---------------------------------------------------------------------------


def test_malformed_inputs_zero_sql(db_engine: Engine, db_session: Session) -> None:
    """2. Malformed or invalid slugs execute 0 SQL queries; valid unknown slug executes query."""
    queries: list[str] = []

    def before_cursor_execute(
        conn: Any,  # noqa: ARG001
        cursor: Any,  # noqa: ARG001
        statement: str,
        parameters: Any,  # noqa: ARG001
        context: Any,  # noqa: ARG001
        executemany: bool,  # noqa: ARG001
    ) -> None:
        queries.append(statement)

    event.listen(db_engine, "before_cursor_execute", before_cursor_execute)
    try:
        malformed_inputs = [
            "",
            "A-b",
            "a b",
            "a;DROP TABLE apps",
            "a'--",
            "x" * 64,
            "www",
            "api",
            "\uff41-b-1234",
            "a\n",
            None,
            123,
            b"calm-otter-1234",
            [],
            {},
            True,
        ]
        for bad_slug in malformed_inputs:
            assert get_app_route(db_session, bad_slug) is None  # type: ignore[arg-type]

        assert len(queries) == 0, (
            f"Expected 0 queries for malformed inputs, got: {queries}"
        )

        # Valid slug syntax triggers exactly 1 query to database
        res = get_app_route(db_session, "valid-slug-9999")
        assert res is None
        assert len(queries) == 1, (
            f"Expected exactly 1 query for valid slug, got: {queries}"
        )
    finally:
        event.remove(db_engine, "before_cursor_execute", before_cursor_execute)


# ---------------------------------------------------------------------------
# 3. SQL Injection Attempt
# ---------------------------------------------------------------------------


def test_sql_injection_attempt(db_session: Session) -> None:
    """3. SQL injection patterns in slug return None and never modify/corrupt table data."""
    initial_count = len(db_session.execute(select(App.id)).all())
    injection_slugs = [
        "calm-otter-4821' OR '1'='1",
        "running-app-1234' OR 1=1 --",
        "'; DROP TABLE apps; --",
        '" OR ""="',
    ]
    for bad_slug in injection_slugs:
        assert get_app_route(db_session, bad_slug) is None
        assert lookup_route(bad_slug) is None
        assert set_status(db_session, bad_slug, "stopped") is None
        assert change_status(bad_slug, "stopped") is None

    current_count = len(db_session.execute(select(App.id)).all())
    assert current_count == initial_count


# ---------------------------------------------------------------------------
# 4. Snapshot Safety
# ---------------------------------------------------------------------------


def test_snapshot_safety(db_session: Session) -> None:
    """4. AppRoute snapshots are frozen dataclasses and decoupled from DB mutations."""
    route = get_app_route(db_session, "running-app-1234")
    assert route is not None

    # Immutable: cannot reassign or add attributes
    with pytest.raises(dataclasses.FrozenInstanceError):
        route.status = "stopped"  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        route.host_port = 9000  # type: ignore[misc]

    # Mutating database afterwards does not modify existing snapshot
    db_session.execute(
        update(App)
        .where(App.slug == "running-app-1234")
        .values(status=AppStatus.stopped.value, host_port=None)
    )
    db_session.commit()

    assert route.status == "running"
    assert route.host_port == 18001
    assert route.upstream_url == "http://127.0.0.1:18001"


# ---------------------------------------------------------------------------
# 5. Every Status Returned
# ---------------------------------------------------------------------------


def test_every_status_returned(db_session: Session) -> None:
    """5. Lookups for archived, crashed, waking, stopped return expected status & ports."""
    # Archived
    archived = get_app_route(db_session, "archived-app-1234")
    assert archived is not None
    assert archived.status == "archived"
    assert archived.kind == "static"
    assert archived.host_port is None
    assert archived.upstream_url is None

    # Crashed
    crashed = get_app_route(db_session, "crashed-app-1234")
    assert crashed is not None
    assert crashed.status == "crashed"
    assert crashed.kind == "streamlit"
    assert crashed.host_port is None
    assert crashed.upstream_url is None

    # Waking
    waking = get_app_route(db_session, "waking-app-1234")
    assert waking is not None
    assert waking.status == "waking"
    assert waking.kind == "script"
    assert waking.host_port == 18003
    assert waking.upstream_url == "http://127.0.0.1:18003"

    # Stopped
    stopped = get_app_route(db_session, "stopped-app-1234")
    assert stopped is not None
    assert stopped.status == "stopped"
    assert stopped.kind == "flask"
    assert stopped.host_port is None
    assert stopped.upstream_url is None

    # Paused
    paused = get_app_route(db_session, "paused-app-1234")
    assert paused is not None
    assert paused.status == "paused"
    assert paused.kind == "fastapi"
    assert paused.host_port == 18002
    assert paused.upstream_url == "http://127.0.0.1:18002"


# ---------------------------------------------------------------------------
# 6. Set Status Rules
# ---------------------------------------------------------------------------


def test_set_status_rules(db_session: Session) -> None:
    """6. Validation rules for running, stopped/crashed/archived, and paused/waking."""
    # 6a. Running requires host_port
    with pytest.raises(ValueError, match="^A running app needs a port\\.$"):
        set_status(db_session, "stopped-app-1234", "running")

    with pytest.raises(ValueError, match="^A running app needs a port\\.$"):
        set_status(db_session, "stopped-app-1234", "running", host_port=None)

    running_route = set_status(
        db_session, "stopped-app-1234", "running", host_port=18010
    )
    assert running_route is not None
    assert running_route.status == "running"
    assert running_route.host_port == 18010
    assert running_route.upstream_url == "http://127.0.0.1:18010"

    # 6b. Stopped / crashed / archived reject non-None host_port
    with pytest.raises(ValueError, match="^This status cannot have a port\\.$"):
        set_status(db_session, "running-app-1234", "stopped", host_port=18010)

    with pytest.raises(ValueError, match="^This status cannot have a port\\.$"):
        set_status(db_session, "running-app-1234", "crashed", host_port=18010)

    with pytest.raises(ValueError, match="^This status cannot have a port\\.$"):
        set_status(db_session, "running-app-1234", "archived", host_port=18010)

    # 6c. Stopped / crashed / archived clear existing port to None
    stopped_route = set_status(db_session, "running-app-1234", "stopped")
    assert stopped_route is not None
    assert stopped_route.status == "stopped"
    assert stopped_route.host_port is None
    assert stopped_route.upstream_url is None

    crashed_route = set_status(db_session, "waking-app-1234", "crashed", host_port=None)
    assert crashed_route is not None
    assert crashed_route.status == "crashed"
    assert crashed_route.host_port is None

    # 6d. Paused / waking: host_port omitted leaves port unchanged
    assert repr(UNSET) == "UNSET"
    paused_unchanged = set_status(
        db_session, "paused-app-1234", "paused", host_port=UNSET
    )
    assert paused_unchanged is not None
    assert paused_unchanged.status == "paused"
    assert paused_unchanged.host_port == 18002

    # Paused / waking: explicit host_port updates port
    paused_updated = set_status(
        db_session, "paused-app-1234", "paused", host_port=18025
    )
    assert paused_updated is not None
    assert paused_updated.status == "paused"
    assert paused_updated.host_port == 18025

    # Paused / waking: explicit host_port=None clears port
    paused_cleared = set_status(db_session, "paused-app-1234", "waking", host_port=None)
    assert paused_cleared is not None
    assert paused_cleared.status == "waking"
    assert paused_cleared.host_port is None


# ---------------------------------------------------------------------------
# 7. Invalid Values Raise ValueError
# ---------------------------------------------------------------------------


def test_invalid_values_raise_value_error(db_session: Session) -> None:
    """7. Invalid status or port raises ValueError without leaking slug or SQL."""
    target_slug = "running-app-1234"

    # Bad statuses
    bad_statuses = ["UNKNOWN", "starting", "", "RUNNING", None, 123, []]
    for bad_status in bad_statuses:
        with pytest.raises(ValueError) as exc_info:
            set_status(db_session, target_slug, bad_status)  # type: ignore[arg-type]
        msg = str(exc_info.value)
        assert msg == "Unknown app status."
        assert target_slug not in msg
        for sql_kw in [
            "SELECT",
            "UPDATE",
            "INSERT",
            "DELETE",
            "WHERE",
            "TABLE",
            "DROP",
        ]:
            assert sql_kw not in msg.upper()

    # Bad ports
    bad_ports = [0, 65536, -1, 70000, "8080", 80.5, True, False, []]
    for bad_port in bad_ports:
        with pytest.raises(ValueError) as exc_info:
            set_status(db_session, target_slug, "running", host_port=bad_port)
        msg = str(exc_info.value)
        assert msg == "Port must be a whole number from 1 to 65535."
        assert target_slug not in msg
        for sql_kw in [
            "SELECT",
            "UPDATE",
            "INSERT",
            "DELETE",
            "WHERE",
            "TABLE",
            "DROP",
        ]:
            assert sql_kw not in msg.upper()


# ---------------------------------------------------------------------------
# 8. Set Status Unknown Slug and Isolation
# ---------------------------------------------------------------------------


def test_set_status_unknown_slug_and_isolation(db_session: Session) -> None:
    """8. set_status returns None on unknown or malformed slug; other rows unchanged."""
    before_rows = db_session.execute(
        select(App.id, App.slug, App.status, App.host_port)
    ).all()

    assert set_status(db_session, "unknown-slug-9999", "stopped") is None
    assert set_status(db_session, "INVALID_SLUG!", "stopped") is None
    assert set_status(db_session, "", "stopped") is None  # type: ignore[arg-type]
    assert set_status(db_session, 12345, "stopped") is None  # type: ignore[arg-type]

    after_rows = db_session.execute(
        select(App.id, App.slug, App.status, App.host_port)
    ).all()
    assert before_rows == after_rows


# ---------------------------------------------------------------------------
# 9. Transaction Ownership
# ---------------------------------------------------------------------------


def test_transaction_ownership(
    db_session: Session,
    session_factory: sessionmaker[Session],
) -> None:
    """9. set_status does not commit (rollback reverts); change_status commits."""
    # 9a. set_status uncommitted
    route = set_status(db_session, "paused-app-1234", "waking", host_port=18090)
    assert route is not None
    assert route.status == "waking"
    assert route.host_port == 18090

    # Rollback reverts the change
    db_session.rollback()

    with session_factory() as verify_session:
        restored = get_app_route(verify_session, "paused-app-1234")
        assert restored is not None
        assert restored.status == "paused"
        assert restored.host_port == 18002

    # 9b. change_status auto-commits
    changed = change_status("paused-app-1234", "waking", host_port=18090)
    assert changed is not None
    assert changed.status == "waking"
    assert changed.host_port == 18090

    with session_factory() as verify_session:
        persisted = get_app_route(verify_session, "paused-app-1234")
        assert persisted is not None
        assert persisted.status == "waking"
        assert persisted.host_port == 18090


# ---------------------------------------------------------------------------
# 10. Change Status Unknown Slug and Error Handling
# ---------------------------------------------------------------------------


def test_change_status_unknown_and_error_handling(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,  # noqa: ARG001
) -> None:
    """10. change_status returns None on unknown slug, raises on bad input, closes session."""
    closed_sessions: list[MagicMock] = []

    def spy_factory() -> Session:
        sess = session_factory()
        spy = MagicMock(wraps=sess.close)
        sess.close = spy  # type: ignore[method-assign]
        closed_sessions.append(spy)
        return sess

    monkeypatch.setattr(app.route_lookup, "get_session_factory", lambda: spy_factory)

    # 1. Unknown slug
    res = change_status("unknown-slug-9999", "stopped")
    assert res is None
    assert len(closed_sessions) == 1
    closed_sessions[0].assert_called_once()

    # 2. Bad status raises and closes session
    with pytest.raises(ValueError, match="^Unknown app status\\.$"):
        change_status("running-app-1234", "invalid-status")
    assert len(closed_sessions) == 2
    closed_sessions[1].assert_called_once()

    # 3. Bad port raises and closes session
    with pytest.raises(ValueError, match="^Port must be a whole number"):
        change_status("running-app-1234", "running", host_port=99999)
    assert len(closed_sessions) == 3
    closed_sessions[2].assert_called_once()

    # 4. lookup_route also closes session
    route = lookup_route("running-app-1234")
    assert route is not None
    assert len(closed_sessions) == 4
    closed_sessions[3].assert_called_once()


# ---------------------------------------------------------------------------
# 11. Async Usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_usage(db_session: Session) -> None:  # noqa: ARG001
    """11. Calling synchronous lookup_route and change_status via asyncio.to_thread."""
    route = await asyncio.to_thread(lookup_route, "running-app-1234")
    assert route is not None
    assert route.status == "running"

    updated = await asyncio.to_thread(change_status, "running-app-1234", "paused")
    assert updated is not None
    assert updated.status == "paused"
    assert updated.host_port == 18001


# ---------------------------------------------------------------------------
# 12. Consistency
# ---------------------------------------------------------------------------


def test_consistency(db_session: Session) -> None:
    """12. Allowed statuses match AppStatus enum exactly."""
    enum_statuses = {s.value for s in AppStatus}
    assert app.route_lookup._ALLOWED_STATUSES == enum_statuses

    for status in enum_statuses:
        port = 18001 if status == "running" else None
        route = set_status(db_session, "running-app-1234", status, host_port=port)
        assert route is not None
        assert route.status == status


# ---------------------------------------------------------------------------
# 13. Import Safety
# ---------------------------------------------------------------------------


def test_import_safety() -> None:
    """13. Subprocess import without SECRET_KEY succeeds and does not instantiate engine."""
    env = os.environ.copy()
    env.pop("SECRET_KEY", None)

    code = (
        "import sys\n"
        "import app.route_lookup\n"
        "import app.db\n"
        "assert app.db.get_engine.cache_info().currsize == 0, 'engine was created on import'\n"
        "print('ok', app.db.get_engine.cache_info().currsize)\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"Subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "ok 0"
