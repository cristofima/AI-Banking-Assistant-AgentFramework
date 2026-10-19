"""Explicit service configuration; no credential-file loading."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", hide_input_in_errors=True)
    database_url: SecretStr
    jwt_secret_key: SecretStr = Field(min_length=32)
    jwt_issuer: str = Field(min_length=1)
    jwt_audience: str = Field(min_length=1)
    auth_internal_secret: SecretStr = Field(min_length=32)
    access_token_minutes: int = Field(default=60, ge=1, le=60)
    oauth_issuer: str = ""
    oauth_frontend_authorize_url: str = ""
    oauth_audience: str = ""
    oauth_signing_backend: Literal["pem", "key_vault"] = "pem"
    oauth_signing_key_uri: str = ""
    oauth_signing_private_key: SecretStr = SecretStr("")
    oauth_signing_key_id: str = ""
    applicationinsights_connection_string: str | None = None
