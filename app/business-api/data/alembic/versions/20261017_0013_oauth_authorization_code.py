"""Separate short-lived OAuth authorization-code credentials from application JWTs."""
from alembic import op
import sqlalchemy as sa

revision = "20261017_0013"
down_revision = "20261005_0012"
branch_labels = None
depends_on = None


def credential_columns() -> list[sa.Column | sa.ForeignKeyConstraint]:
    return [
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("identity_version", sa.Integer(), nullable=False),
        sa.Column("profile_snapshot", sa.JSON(), nullable=False),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["oauth.clients.client_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["public.users.id"], ondelete="RESTRICT"),
    ]


def upgrade() -> None:
    op.execute(sa.schema.CreateSchema("oauth"))
    op.create_table(
        "clients",
        sa.Column("client_id", sa.String(128), primary_key=True),
        sa.Column("secret_hash", sa.String(500), nullable=False),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        schema="oauth",
    )
    op.create_table(
        "authorization_codes",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        *credential_columns(),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("code_challenge_method", sa.String(8), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        schema="oauth",
    )
    op.create_table(
        "access_tokens", sa.Column("token_hash", sa.String(64), primary_key=True),
        *credential_columns(), schema="oauth",
    )


def downgrade() -> None:
    raise RuntimeError("OAuth credential removal requires explicit retention and client review")
