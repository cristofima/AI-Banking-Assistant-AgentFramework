from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded by Pydantic Settings.

    Sources are listed from highest to lowest priority:
    1. Explicit constructor arguments.
    2. Environment variables.
    3. Configured dotenv files; later files override earlier ones.
    4. Configured secrets-directory files.
    5. Class defaults.

    AGENT_RUNTIME selects local or Foundry execution.
    Dotenv loading follows model_config.env_file.
    """

    # app-level
    APP_NAME: str = "Home Banking Multi-Agent Assistant"
    AGENT_RUNTIME: Literal["local", "foundry"] = Field(default="foundry")

    #Logging and monitoring
    APPLICATIONINSIGHTS_CONNECTION_STRING: str | None = Field(default=None)
    ENABLE_OTEL : bool = Field(default=True)


    # Foundry hosted agent configuration
    FOUNDRY_PROJECT_ENDPOINT: str | None = Field(default=None)
    MODEL_DEPLOYMENT_NAME: str | None = Field(default=None)
    TRIAGE_MODEL_DEPLOYMENT_NAME: str | None = Field(default=None)
    ACCOUNT_MODEL_DEPLOYMENT_NAME: str | None = Field(default=None)
    TRANSACTION_MODEL_DEPLOYMENT_NAME: str | None = Field(default=None)
    INTERNAL_IDENTITY_SECRET: str | None = Field(default=None, min_length=32)

    #MCP servers
    ACCOUNT_MCP_URL: str | None= Field(default=None,description="MCP server URL (required)", min_length=1)
    TRANSACTION_MCP_URL: str | None= Field(default=None,description="MCP server URL (required)", min_length=1)

    # Support for User Assigned Managed Identity: empty means system-managed
    AZURE_CLIENT_ID: str | None = Field(default=None)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()