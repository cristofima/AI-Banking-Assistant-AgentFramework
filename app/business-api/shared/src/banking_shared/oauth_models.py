"""Identity-owned OAuth persistence, separate from application JWT credentials."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, JSON
from sqlmodel import Field, SQLModel


class OAuthClient(SQLModel, table=True):
    __tablename__ = "clients"
    __table_args__ = {"schema": "oauth"}

    client_id: str = Field(primary_key=True, max_length=128)
    secret_hash: str = Field(max_length=500, repr=False)
    redirect_uris: list[str] = Field(sa_column=Column(JSON, nullable=False))
    active: bool = Field(default=True)


class OAuthAuthorizationCode(SQLModel, table=True):
    __tablename__ = "authorization_codes"
    __table_args__ = {"schema": "oauth"}

    code_hash: str = Field(primary_key=True, max_length=64, repr=False)
    client_id: str = Field(foreign_key="oauth.clients.client_id", ondelete="RESTRICT", max_length=128)
    user_id: str = Field(foreign_key="users.id", ondelete="RESTRICT", max_length=36)
    identity_version: int
    profile_snapshot: dict[str, object] = Field(sa_column=Column(JSON, nullable=False))
    redirect_uri: str = Field(max_length=2048)
    scope: str = Field(max_length=128)
    code_challenge: str = Field(max_length=128)
    code_challenge_method: str = Field(default="S256", max_length=8)
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    consumed: bool = Field(default=False)

    def get_redirect_uri(self) -> str:
        return self.redirect_uri

    def get_scope(self) -> str:
        return self.scope


class OAuthAccessToken(SQLModel, table=True):
    __tablename__ = "access_tokens"
    __table_args__ = {"schema": "oauth"}

    token_hash: str = Field(primary_key=True, max_length=64, repr=False)
    client_id: str = Field(foreign_key="oauth.clients.client_id", ondelete="RESTRICT", max_length=128)
    user_id: str = Field(foreign_key="users.id", ondelete="RESTRICT", max_length=36)
    identity_version: int
    profile_snapshot: dict[str, object] = Field(sa_column=Column(JSON, nullable=False))
    scope: str = Field(max_length=128)
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
