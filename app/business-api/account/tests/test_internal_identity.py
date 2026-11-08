"""Verification tests for Account internal principals."""

import time

import pytest

from banking_shared.internal_identity import get_customer_id
from banking_shared.testing import INTERNAL_IDENTITY_SECRET, internal_authorization


TEST_SECRET = INTERNAL_IDENTITY_SECRET
_authorization = internal_authorization


def test_valid_principal_returns_customer_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", TEST_SECRET)

    customer_id = get_customer_id(
        {"authorization": _authorization(int(time.time()) + 60)},
    )

    assert customer_id == "local-customer"


@pytest.mark.parametrize(
    ("authorization", "message"),
    [
        ("", "required"),
        ("Bearer v1.invalid.signature", "Invalid"),
        (_authorization(0), "expired"),
    ],
)
def test_invalid_principal_is_denied(
    monkeypatch: pytest.MonkeyPatch,
    authorization: str,
    message: str,
) -> None:
    monkeypatch.setenv("INTERNAL_IDENTITY_SECRET", TEST_SECRET)

    with pytest.raises(PermissionError, match=message):
        get_customer_id({"authorization": authorization})