"""Hosted diagnostic transport contracts without Azure login or network calls."""

import hashlib
import hmac

import httpx
import pytest

from banking_evals.historical import diagnostic


def test_hosted_client_separates_partition_and_signed_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer = {"customer_id": "synthetic-a", "email": "synthetic@example.test", "locale": "es"}
    secret = "synthetic-test-signer-not-a-credential"
    captured: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"conversation": "synthetic-conversation"})

    client = httpx.Client(transport=httpx.MockTransport(upstream))
    monkeypatch.setattr(diagnostic, "_az_cli_access_token", lambda: "synthetic-aad-token")
    monkeypatch.setattr(diagnostic.httpx, "Client", lambda **kwargs: client)
    hosted = diagnostic.HostedAgentClient("https://synthetic.example.test/responses", secret)
    try:
        _, conversation = hosted.send_turn(customer, "synthetic input", None)
    finally:
        hosted.close()

    assert conversation == "synthetic-conversation"
    headers = captured[0].headers
    assert headers["x-ms-user-identity"] == "banking." + hmac.new(
        secret.encode(), b"foundry-user-v1:eval-synthetic-a", hashlib.sha256,
    ).hexdigest()
    assert len(headers["x-ms-user-identity"]) == 72
    assert headers["x-client-banking-identity"] == diagnostic._sign_internal_identity(
        secret, "eval-synthetic-a", customer["customer_id"], customer["email"], customer["locale"],
    )
    assert headers["authorization"] == "Bearer synthetic-aad-token"
