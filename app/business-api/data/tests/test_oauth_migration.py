"""Offline PostgreSQL DDL contract; does not connect to or migrate a database."""
from __future__ import annotations

from io import StringIO
from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from banking_shared.identity_models import User
from banking_shared.oauth_models import OAuthAccessToken, OAuthAuthorizationCode, OAuthClient


def test_oauth_migration_creates_isolated_credentials_with_public_user_bindings() -> None:
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output},
    )
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    revision = runpy.run_path(str(path / "20261017_0013_oauth_authorization_code.py"))
    with Operations.context(context):
        revision["upgrade"]()
    sql = output.getvalue()
    assert revision["revision"] == "20261017_0013"
    assert revision["down_revision"] == "20261005_0012"
    assert "CREATE SCHEMA oauth" in sql
    for table in ("clients", "authorization_codes", "access_tokens"):
        assert f"CREATE TABLE oauth.{table}" in sql
    assert sql.count("REFERENCES public.users (id) ON DELETE RESTRICT") == 2
    assert sql.count("REFERENCES oauth.clients (client_id) ON DELETE RESTRICT") == 2
    assert "expires_at TIMESTAMP WITH TIME ZONE NOT NULL" in sql
    assert "ALTER TABLE" not in sql
    assert "DROP TABLE" not in sql
    with pytest.raises(RuntimeError, match="retention"):
        revision["downgrade"]()


@pytest.mark.parametrize("model", [OAuthAuthorizationCode, OAuthAccessToken])
def test_oauth_orm_resolves_identity_and_preserves_restrictive_foreign_keys(
    model: type[OAuthAuthorizationCode] | type[OAuthAccessToken],
) -> None:
    foreign_keys = {key.parent.name: key for key in model.__table__.foreign_keys}
    assert foreign_keys["user_id"].column is User.__table__.c.id
    assert foreign_keys["client_id"].column is OAuthClient.__table__.c.client_id
    assert {key.ondelete for key in foreign_keys.values()} == {"RESTRICT"}
    assert model.__table__.schema == "oauth"
    # Default-schema Identity metadata resolves to public on the deployed PostgreSQL search path.
    assert User.__table__.schema is None
    sql = str(CreateTable(model.__table__).compile(dialect=dialect()))
    assert "REFERENCES users (id) ON DELETE RESTRICT" in sql
    assert "REFERENCES oauth.clients (client_id) ON DELETE RESTRICT" in sql
