import time
from unittest.mock import patch

import pytest
from azure.core.credentials import AccessTokenInfo
from azure.identity import AzureCliCredential

from app.config import azure_credential
from app.config.azure_credential import CachedAzureCliCredential


def test_cache_separates_tenant_and_failures_are_not_cached() -> None:
    token = AccessTokenInfo("t", int(time.time()) + 3600)
    credential = CachedAzureCliCredential()
    with patch.object(
        AzureCliCredential, "get_token_info", side_effect=[RuntimeError("boom"), token, token]
    ) as fetch:
        with pytest.raises(RuntimeError):
            credential.get_token_info("scope")
        credential.get_token_info("scope")
        credential.get_token_info("scope")
        credential.get_token_info("scope", options={"tenant_id": "other"})
    assert fetch.call_count == 3


def test_local_runtime_uses_cached_cli_credential() -> None:
    with patch.object(azure_credential.settings, "AGENT_RUNTIME", "local"):
        credential = azure_credential.get_azure_credential()
    assert isinstance(credential, CachedAzureCliCredential)


def test_cli_credential_reuses_unexpired_token_and_refreshes_near_expiry() -> None:
    fresh = AccessTokenInfo("fresh", int(time.time()) + 3600)
    stale = AccessTokenInfo("stale", int(time.time()) + 60)
    credential = CachedAzureCliCredential()
    with patch.object(AzureCliCredential, "get_token_info", side_effect=[stale, fresh]) as fetch:
        assert credential.get_token("scope").token == "stale"
        assert credential.get_token("scope").token == "fresh"
        assert credential.get_token("scope").token == "fresh"
    assert fetch.call_count == 2
