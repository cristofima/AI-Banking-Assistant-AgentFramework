"""Controlled remote diagnostics with no Azure or database access."""
from __future__ import annotations

import subprocess
from unittest.mock import Mock

from azure.core.exceptions import AzureError, ClientAuthenticationError, HttpResponseError
import pytest
from sqlalchemy.exc import SQLAlchemyError

from identity.services.oauth_remote import RemoteOperationError, main, operation_stage


def http_error(status: int) -> HttpResponseError:
    error = HttpResponseError("sensitive upstream detail")
    error.status_code = status
    return error


@pytest.mark.parametrize(("error", "code"), [
    (ClientAuthenticationError("sensitive upstream detail"), "AZURE_AUTHENTICATION_FAILED"),
    (http_error(403), "AZURE_ACCESS_DENIED"),
    (http_error(404), "AZURE_RESOURCE_NOT_FOUND"),
    (http_error(409), "AZURE_CONFLICT"),
    (http_error(429), "AZURE_THROTTLED"),
    (http_error(500), "AZURE_REQUEST_FAILED"),
    (AzureError("sensitive upstream detail"), "AZURE_REQUEST_FAILED"),
    (ValueError("sensitive upstream detail"), "VALIDATION_FAILED"),
    (SQLAlchemyError("sensitive upstream detail"), "DATABASE_OPERATION_FAILED"),
    (subprocess.CalledProcessError(1, "sensitive command"), "MIGRATION_PROCESS_FAILED"),
    (OSError("sensitive upstream detail"), "IO_FAILED"),
])
def test_stage_classifies_failure_without_upstream_content(error: Exception, code: str) -> None:
    with pytest.raises(RemoteOperationError) as failure:
        with operation_stage("jwks-read"):
            raise error
    assert str(failure.value) == f"stage=jwks-read; code={code}"
    assert failure.value.__suppress_context__ is True


def test_nested_stage_preserves_innermost_failure() -> None:
    with pytest.raises(RemoteOperationError) as failure:
        with operation_stage("configuration"), operation_stage("signing-key-read"):
            raise ValueError("sensitive upstream detail")
    assert failure.value.stage == "signing-key-read"


@pytest.mark.parametrize("failed_stage", [
    "signing-key-read", "jwks-publication", "client-secret-publication",
    "database-client-registration",
])
def test_main_prints_only_controlled_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failed_stage: str,
) -> None:
    monkeypatch.setattr("sys.argv", [
        "oauth_remote", "init-oauth", "--vault-url", "https://synthetic.vault.azure.net",
        "--expected-host", "synthetic.postgres.database.azure.com",
        "--expected-database", "banking", "--confirm",
    ])
    run = Mock(side_effect=RemoteOperationError(failed_stage, "AZURE_ACCESS_DENIED"))
    monkeypatch.setattr("identity.services.oauth_remote._run_operation", run)
    with pytest.raises(SystemExit) as failure:
        main()
    assert failure.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"Remote operation rejected; stage={failed_stage}; code=AZURE_ACCESS_DENIED. "
        "Check authorized configuration, access and schema.\n"
    )


def test_stage_success_preserves_operation() -> None:
    operation = Mock()
    with operation_stage("jwks-publication"):
        operation()
    operation.assert_called_once_with()
