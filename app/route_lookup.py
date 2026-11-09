"""Route lookup and status management service for Small Cloud applications.

This module provides synchronous route lookups for the reverse proxy (T1.15)
and status/port management for runtimes and schedulers.

Architecture & Design Principles:
    * **Immutable Snapshots**: Returned ``AppRoute`` instances are frozen dataclasses
      containing plain, detached Python types (never live SQLAlchemy ORM instances).
      They are safe to pass across threads and cache.
    * **Synchronous API**: All functions in this module are synchronous. Async callers
      such as the reverse proxy must dispatch them via ``asyncio.to_thread`` or
      Starlette's ``run_in_threadpool``, never directly on the event loop.
    * **No Caching**: This service does not cache lookups. Caching policies (TTL, LRU,
      invalidation) are decided by callers (e.g. the reverse proxy).
    * **State Transitions**: Status transitions are not enforced at this layer yet.
      TODO(B3.3): The lifecycle state machine will enforce valid transition rules.
    * **Untrusted Slugs**: Slugs are derived from HTTP Host headers and are untrusted.
      Malformed or non-string slugs are rejected before touching the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_session_factory
from app.models import App, AppStatus, User
from app.slugs import is_valid_slug

# ---------------------------------------------------------------------------
# Sentinel & Constants
# ---------------------------------------------------------------------------


class _Unset:
    """Private sentinel indicating an omitted argument."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()
"""Sentinel object representing an omitted optional parameter."""

_ALLOWED_STATUSES: frozenset[str] = frozenset(s.value for s in AppStatus)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AppRoute:
    """Immutable snapshot of an application's route and container state.

    Attributes:
        app_id: Primary key ID of the application.
        slug: Unique DNS subdomain routing slug.
        kind: Application runtime kind (e.g. 'streamlit', 'fastapi').
        status: Current lifecycle state (e.g. 'running', 'stopped').
        host_port: Container port mapped on 127.0.0.1, or None if not running.
        owner_email: Account email of the application owner.
    """

    app_id: int
    slug: str
    kind: str
    status: str
    host_port: int | None
    owner_email: str

    @property
    def upstream_url(self) -> str | None:
        """Upstream container URL on 127.0.0.1, or None if host_port is None."""
        if self.host_port is not None:
            return f"http://127.0.0.1:{self.host_port}"
        return None


# ---------------------------------------------------------------------------
# Core Operations
# ---------------------------------------------------------------------------


def get_app_route(session: Session, slug: str) -> AppRoute | None:
    """Look up an application route snapshot by slug.

    Args:
        session: Active SQLAlchemy database session.
        slug: The untrusted subdomain routing slug from the Host header.

    Returns:
        An immutable AppRoute snapshot if found, or None.
    """
    if type(slug) is not str or not is_valid_slug(slug):
        return None

    stmt = (
        select(
            App.id,
            App.slug,
            App.kind,
            App.status,
            App.host_port,
            User.email,
        )
        .join(User, App.owner_id == User.id)
        .where(App.slug == slug)
    )
    row = session.execute(stmt).one_or_none()
    if row is None:
        return None

    return AppRoute(
        app_id=row[0],
        slug=row[1],
        kind=row[2],
        status=row[3],
        host_port=row[4],
        owner_email=row[5],
    )


def set_status(
    session: Session,
    slug: str,
    status: str,
    *,
    host_port: int | None | Any = UNSET,
) -> AppRoute | None:
    """Update an application's lifecycle status and container port in the database.

    Validation is performed before touching the database. This function does not
    commit; the caller owns the transaction.

    Args:
        session: Active SQLAlchemy database session.
        slug: The unique subdomain routing slug.
        status: The target status string matching an AppStatus value.
        host_port: Local host port (1..65535), None, or UNSET to leave unchanged.

    Returns:
        Updated AppRoute snapshot if updated, or None if slug is malformed/unknown.

    Raises:
        ValueError: If status or host_port violate validation rules.
    """
    if type(status) is not str or status not in _ALLOWED_STATUSES:
        raise ValueError("Unknown app status.")

    if host_port is not UNSET and host_port is not None:
        if (
            type(host_port) is not int
            or isinstance(host_port, bool)
            or not (1 <= host_port <= 65535)
        ):
            raise ValueError("Port must be a whole number from 1 to 65535.")

    if status == AppStatus.running.value:
        if host_port is UNSET or host_port is None:
            raise ValueError("A running app needs a port.")
        target_port: int | None | Any = host_port
    elif status in {
        AppStatus.stopped.value,
        AppStatus.crashed.value,
        AppStatus.archived.value,
    }:
        if host_port is not UNSET and host_port is not None:
            raise ValueError("This status cannot have a port.")
        target_port = None
    else:
        # paused, waking: leave unchanged unless host_port was explicitly passed
        target_port = host_port

    if type(slug) is not str or not is_valid_slug(slug):
        return None

    values_dict: dict[str, Any] = {"status": status}
    if target_port is not UNSET:
        values_dict["host_port"] = target_port

    stmt = (
        update(App)
        .where(App.slug == slug)
        .values(**values_dict)
        .execution_options(synchronize_session=False)
    )
    result = session.execute(stmt)
    if result.rowcount == 0:
        return None

    return get_app_route(session, slug)


# ---------------------------------------------------------------------------
# Convenience Wrappers
# ---------------------------------------------------------------------------


def lookup_route(slug: str) -> AppRoute | None:
    """Convenience function for the reverse proxy to look up an app route.

    Opens a session from get_session_factory, looks up the route, closes
    the session in a finally block, and returns the snapshot.
    """
    factory = get_session_factory()
    session = factory()
    try:
        return get_app_route(session, slug)
    finally:
        session.close()


def change_status(
    slug: str,
    status: str,
    *,
    host_port: int | None | Any = UNSET,
) -> AppRoute | None:
    """Convenience function for runtimes and schedulers to update status and port.

    Opens a session from get_session_factory, calls set_status, commits on success,
    rolls back on failure or exception, and closes the session.
    """
    factory = get_session_factory()
    session = factory()
    try:
        route = set_status(session, slug, status, host_port=host_port)
        if route is not None:
            session.commit()
        else:
            session.rollback()
        return route
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
