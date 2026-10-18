"""Configuration checks for the Microsoft Foundry model client."""

from pathlib import Path

import pytest

from pydantic import ValidationError

from app.config.settings import Settings


def test_agent_runtime_defaults_to_foundry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_RUNTIME", raising=False)

    configured = Settings(_env_file=None)

    assert configured.AGENT_RUNTIME == "foundry"


def test_local_dotenv_overrides_foundry_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("AGENT_RUNTIME", raising=False)
    dotenv = tmp_path / "runtime.env"
    dotenv.write_text("AGENT_RUNTIME=local\n", encoding="utf-8")

    configured = Settings(_env_file=dotenv)

    assert configured.AGENT_RUNTIME == "local"


@pytest.mark.parametrize("runtime", ["local", "foundry"])
def test_agent_runtime_loads_from_environment(
    monkeypatch: pytest.MonkeyPatch, runtime: str,
) -> None:
    monkeypatch.setenv("AGENT_RUNTIME", runtime)

    configured = Settings(_env_file=None)

    assert configured.AGENT_RUNTIME == runtime


@pytest.mark.parametrize("runtime", ["", "dev", "production", "FOUNDry"])
def test_agent_runtime_rejects_unsupported_values(
    monkeypatch: pytest.MonkeyPatch, runtime: str,
) -> None:
    monkeypatch.setenv("AGENT_RUNTIME", runtime)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_foundry_model_configuration_is_loaded() -> None:
    settings = Settings(
        FOUNDRY_PROJECT_ENDPOINT="https://example.services.ai.azure.com/api/projects/example",
        MODEL_DEPLOYMENT_NAME="gpt-4.1-mini",
        _env_file=None,
    )

    assert settings.FOUNDRY_PROJECT_ENDPOINT == (
        "https://example.services.ai.azure.com/api/projects/example"
    )
    assert settings.MODEL_DEPLOYMENT_NAME == "gpt-4.1-mini"


@pytest.mark.parametrize(
    "overrides",
    [(None, None, None), ("", "", ""), ("model-router", "gpt-5.4", "gpt-5.4")],
)
def test_per_agent_deployments_are_optional(
    overrides: tuple[str | None, str | None, str | None],
) -> None:
    configured = Settings(
        MODEL_DEPLOYMENT_NAME="gpt-4.1-mini",
        TRIAGE_MODEL_DEPLOYMENT_NAME=overrides[0],
        ACCOUNT_MODEL_DEPLOYMENT_NAME=overrides[1],
        TRANSACTION_MODEL_DEPLOYMENT_NAME=overrides[2],
        _env_file=None,
    )

    assert configured.MODEL_DEPLOYMENT_NAME == "gpt-4.1-mini"
    assert (
        configured.TRIAGE_MODEL_DEPLOYMENT_NAME,
        configured.ACCOUNT_MODEL_DEPLOYMENT_NAME,
        configured.TRANSACTION_MODEL_DEPLOYMENT_NAME,
    ) == overrides


def test_per_agent_deployments_default_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TRIAGE_MODEL_DEPLOYMENT_NAME", "ACCOUNT_MODEL_DEPLOYMENT_NAME",
        "TRANSACTION_MODEL_DEPLOYMENT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    configured = Settings(_env_file=None)

    assert configured.TRIAGE_MODEL_DEPLOYMENT_NAME is None
    assert configured.ACCOUNT_MODEL_DEPLOYMENT_NAME is None
    assert configured.TRANSACTION_MODEL_DEPLOYMENT_NAME is None


def test_per_agent_deployments_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")
    monkeypatch.setenv("TRIAGE_MODEL_DEPLOYMENT_NAME", "model-router")
    monkeypatch.setenv("ACCOUNT_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
    monkeypatch.setenv("TRANSACTION_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
    configured = Settings(_env_file=None)

    assert configured.MODEL_DEPLOYMENT_NAME == "gpt-4.1-mini"
    assert configured.TRIAGE_MODEL_DEPLOYMENT_NAME == "model-router"
    assert configured.ACCOUNT_MODEL_DEPLOYMENT_NAME == "gpt-5.4"
    assert configured.TRANSACTION_MODEL_DEPLOYMENT_NAME == "gpt-5.4"