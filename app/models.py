"""Core database models for Small Cloud.

This module defines the relational persistence schema for users, apps, and
deployments using SQLAlchemy 2.0 mapped columns and declarative dataclass-like
typing.

Importing this module has **no side effects**: no database engine is initialized,
no configuration settings are evaluated, and no filesystem operations occur.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

import sqlalchemy
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db import Base

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AppKind(enum.StrEnum):
    """Supported application runtime kinds."""

    static = "static"
    streamlit = "streamlit"
    flask = "flask"
    fastapi = "fastapi"
    script = "script"


class AppStatus(enum.StrEnum):
    """Lifecycle status states for an app container."""

    running = "running"
    paused = "paused"
    stopped = "stopped"
    waking = "waking"
    crashed = "crashed"
    archived = "archived"


_KIND_VALUES = ", ".join(f"'{k.value}'" for k in AppKind)
_STATUS_VALUES = ", ".join(f"'{s.value}'" for s in AppStatus)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class User(Base):
    """Registered user account and owner of deployed applications.

    Stores the user identity (uniquely identified by normalized lowercase email)
    and acts as the security principal for app ownership and access lists.
    """

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    # Relationships
    apps: Mapped[list[App]] = relationship(
        "App",
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @validates("email")
    def validate_email(self, key: str, value: str) -> str:
        """Strip whitespace and lower-case the email on assignment."""
        if value is None:
            raise ValueError("Email cannot be None")
        return value.strip().lower()


class App(Base):
    """User-created application service and route target.

    Stores the subdomain routing slug, deployment metadata, lifecycle state,
    and runtime binding (such as container host port).
    """

    __tablename__ = "apps"
    __table_args__ = (
        CheckConstraint("slug = lower(slug)", name="slug_lowercase"),
        CheckConstraint(f"kind IN ({_KIND_VALUES})", name="kind_valid"),
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status_valid"),
        CheckConstraint(
            "host_port IS NULL OR (host_port BETWEEN 1 AND 65535)",
            name="host_port_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=AppStatus.stopped.value,
        server_default=AppStatus.stopped.value,
        nullable=False,
    )
    host_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_request_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    # Relationships
    owner: Mapped[User] = relationship(
        "User",
        back_populates="apps",
    )
    deployments: Mapped[list[Deployment]] = relationship(
        "Deployment",
        back_populates="app",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Deployment(Base):
    """Immutable versioned code release for a specific application.

    Records the filesystem location of the application's source code version,
    dependency flag, and deployment timestamp.
    """

    __tablename__ = "deployments"
    __table_args__ = (
        UniqueConstraint("app_id", "version", name="uq_deployments_app_id_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    app_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    code_path: Mapped[str] = mapped_column(String(500), nullable=False)
    has_requirements: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=sqlalchemy.false(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    # Relationships
    app: Mapped[App] = relationship(
        "App",
        back_populates="deployments",
    )
