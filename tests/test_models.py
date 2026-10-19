"""Tests for app.models – Core models and constraints (T1.3).

All tests use a temporary SQLite file engine with Base.metadata.create_all
so SQLite foreign keys and CheckConstraints are strictly enforced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base, make_engine, make_session_factory
from app.models import App, AppKind, AppStatus, Deployment, User


@pytest.fixture()
def db_session(tmp_path: Path):
    """Create a temporary database with all tables and yield a clean session."""
    db_file = tmp_path / "models_test.db"
    engine = make_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_user_email_stripped_and_lowercased():
    """1. User email is stripped and lower-cased on assignment."""
    user = User(email="  User.Name@Example.COM  ")
    assert user.email == "user.name@example.com"

    user.email = "  Another@DOMAIN.Org  "
    assert user.email == "another@domain.org"


def test_duplicate_email_case_insensitive_raises(db_session: Session):
    """2. Two users whose emails differ only by case raise IntegrityError."""
    user1 = User(email="user@example.com")
    db_session.add(user1)
    db_session.commit()

    user2 = User(email="USER@EXAMPLE.COM")
    db_session.add(user2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_duplicate_slug_raises(db_session: Session):
    """3. Two apps with the same slug raise IntegrityError."""
    user = User(email="owner@example.com")
    db_session.add(user)
    db_session.commit()

    app1 = App(
        owner_id=user.id,
        slug="unique-slug",
        name="App One",
        kind=AppKind.static.value,
    )
    db_session.add(app1)
    db_session.commit()

    app2 = App(
        owner_id=user.id,
        slug="unique-slug",
        name="App Two",
        kind=AppKind.flask.value,
    )
    db_session.add(app2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_invalid_kind_status_host_port_raise(db_session: Session):
    """4. Invalid kind, invalid status and host_port 0 or 70000 each raise IntegrityError."""
    user = User(email="dev@example.com")
    db_session.add(user)
    db_session.commit()

    # 4a. Invalid kind
    app_invalid_kind = App(
        owner_id=user.id,
        slug="app-invalid-kind",
        name="Bad Kind",
        kind="invalid_kind",
    )
    db_session.add(app_invalid_kind)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 4b. Invalid status
    app_invalid_status = App(
        owner_id=user.id,
        slug="app-invalid-status",
        name="Bad Status",
        kind=AppKind.static.value,
        status="invalid_status",
    )
    db_session.add(app_invalid_status)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 4c. Host port 0
    app_port_0 = App(
        owner_id=user.id,
        slug="app-port-zero",
        name="Port Zero",
        kind=AppKind.fastapi.value,
        host_port=0,
    )
    db_session.add(app_port_0)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 4d. Host port 70000
    app_port_70k = App(
        owner_id=user.id,
        slug="app-port-70k",
        name="Port 70k",
        kind=AppKind.fastapi.value,
        host_port=70000,
    )
    db_session.add(app_port_70k)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_foreign_key_enforced(db_session: Session):
    """5. An app whose owner_id does not exist raises IntegrityError."""
    orphan_app = App(
        owner_id=999999,
        slug="orphan-app",
        name="Orphan App",
        kind=AppKind.static.value,
    )
    db_session.add(orphan_app)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_cascade_delete_user_removes_apps_and_deployments(db_session: Session):
    """6. Deleting a user also deletes their apps and deployments."""
    user = User(email="delete-me@example.com")
    db_session.add(user)
    db_session.commit()

    app = App(
        owner_id=user.id,
        slug="app-to-delete",
        name="Delete Me App",
        kind=AppKind.script.value,
    )
    db_session.add(app)
    db_session.commit()

    deployment = Deployment(
        app_id=app.id,
        version=1,
        code_path="/data/apps/1/v1",
        has_requirements=True,
    )
    db_session.add(deployment)
    db_session.commit()

    # Verify rows exist
    assert db_session.query(User).count() == 1
    assert db_session.query(App).count() == 1
    assert db_session.query(Deployment).count() == 1

    # Delete the user
    db_session.delete(user)
    db_session.commit()

    # Verify cascaded removal
    assert db_session.query(User).count() == 0
    assert db_session.query(App).count() == 0
    assert db_session.query(Deployment).count() == 0


def test_duplicate_app_version_raises(db_session: Session):
    """7. A duplicate (app_id, version) raises IntegrityError."""
    user = User(email="deployer@example.com")
    db_session.add(user)
    db_session.commit()

    app = App(
        owner_id=user.id,
        slug="versioned-app",
        name="Versioned App",
        kind=AppKind.streamlit.value,
    )
    db_session.add(app)
    db_session.commit()

    dep1 = Deployment(
        app_id=app.id,
        version=1,
        code_path="/data/apps/1/v1",
    )
    db_session.add(dep1)
    db_session.commit()

    dep2 = Deployment(
        app_id=app.id,
        version=1,
        code_path="/data/apps/1/v1_dup",
    )
    db_session.add(dep2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_model_defaults(db_session: Session):
    """8. Defaults: a new App has status 'stopped' and created_at; Deployment has has_requirements False."""
    user = User(email="defaults@example.com")
    db_session.add(user)
    db_session.commit()

    app = App(
        owner_id=user.id,
        slug="defaults-app",
        name="Defaults App",
        kind=AppKind.static.value,
    )
    db_session.add(app)
    db_session.commit()
    db_session.refresh(app)

    assert app.status == AppStatus.stopped.value
    assert app.created_at is not None

    deployment = Deployment(
        app_id=app.id,
        version=1,
        code_path="/data/apps/1/v1",
    )
    db_session.add(deployment)
    db_session.commit()
    db_session.refresh(deployment)

    assert deployment.has_requirements is False
    assert deployment.created_at is not None
