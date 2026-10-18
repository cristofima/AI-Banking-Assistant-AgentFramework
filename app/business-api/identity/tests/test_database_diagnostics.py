"""Database failures retain safe HTTP responses and bounded diagnostics."""
from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from identity.services.service import IdentityService


@pytest.mark.parametrize("sqlstate, expected", [
    ("42P01", "42P01"),
    (None, "unknown"),
    ("unsafe-driver-detail", "unknown"),
])
def test_database_failure_logs_only_safe_metadata(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
    sqlstate: str | None, expected: str,
) -> None:
    class DriverError(RuntimeError):
        def __init__(self, code: str | None) -> None:
            super().__init__("sensitive driver message")
            self.sqlstate = code

    original = DriverError(sqlstate)
    error = OperationalError("sensitive SQL", {"password": "sensitive value"}, original)
    logger = Mock()
    monkeypatch.setattr("identity.main.logger", logger)

    def fail_login(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(IdentityService, "login", fail_login)
    response = client.post("/auth/login", json={
        "email": "synthetic@invalid", "password": "synthetic-password",
    })

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "AUTH_UNAVAILABLE"}}
    logger.error.assert_called_once_with(
        "Identity database request failed: exception=%s sqlstate=%s",
        "OperationalError", expected,
    )
