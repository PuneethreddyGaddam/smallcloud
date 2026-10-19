"""Initial tables: users, apps, deployments.

Revision ID: 2747fa61dbb7
Revises:
Create Date: 2026-10-19 17:34:54.958338

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2747fa61dbb7"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Table: users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "email = lower(email)", name=op.f("ck_users_email_lowercase")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )

    # 2. Table: apps
    op.create_table(
        "apps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default="stopped", nullable=False
        ),
        sa.Column("host_port", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_request_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('static', 'streamlit', 'flask', 'fastapi', 'script')",
            name=op.f("ck_apps_kind_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'paused', 'stopped', 'waking', 'crashed', 'archived')",
            name=op.f("ck_apps_status_valid"),
        ),
        sa.CheckConstraint(
            "host_port IS NULL OR (host_port BETWEEN 1 AND 65535)",
            name=op.f("ck_apps_host_port_range"),
        ),
        sa.CheckConstraint("slug = lower(slug)", name=op.f("ck_apps_slug_lowercase")),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_apps_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_apps")),
        sa.UniqueConstraint("slug", name=op.f("uq_apps_slug")),
    )
    with op.batch_alter_table("apps", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_apps_owner_id"), ["owner_id"], unique=False
        )

    # 3. Table: deployments
    op.create_table(
        "deployments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("app_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("code_path", sa.String(length=500), nullable=False),
        sa.Column(
            "has_requirements",
            sa.Boolean(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["app_id"],
            ["apps.id"],
            name=op.f("fk_deployments_app_id_apps"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deployments")),
        sa.UniqueConstraint(
            "app_id", "version", name=op.f("uq_deployments_app_id_version")
        ),
    )
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_deployments_app_id"), ["app_id"], unique=False
        )


def downgrade() -> None:
    # Drop in exact reverse order
    with op.batch_alter_table("deployments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_deployments_app_id"))

    op.drop_table("deployments")

    with op.batch_alter_table("apps", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_apps_owner_id"))

    op.drop_table("apps")
    op.drop_table("users")
