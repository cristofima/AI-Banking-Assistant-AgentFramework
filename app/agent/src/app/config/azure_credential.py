import threading
import time
from typing import Any

from azure.core.credentials import AccessToken, AccessTokenInfo
from azure.identity import ManagedIdentityCredential, AzureCliCredential
from app.config.settings import settings

_REFRESH_MARGIN_SECONDS = 300
_CLI_PROCESS_TIMEOUT_SECONDS = 30
_TOKEN_OPTION_KEYS = ("claims", "tenant_id", "enable_cae")


class CachedAzureCliCredential(AzureCliCredential):
    """AzureCliCredential that reuses tokens, so per-request clients do not spawn `az` each time."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("process_timeout", _CLI_PROCESS_TIMEOUT_SECONDS)
        super().__init__(**kwargs)
        self._token_cache: dict[tuple[Any, ...], AccessTokenInfo] = {}
        self._cache_lock = threading.Lock()

    def get_token_info(self, *scopes: str, options: Any = None) -> AccessTokenInfo:
        key = (scopes, repr(sorted((options or {}).items())))
        with self._cache_lock:
            cached = self._token_cache.get(key)
            if cached and cached.expires_on - time.time() > _REFRESH_MARGIN_SECONDS:
                return cached
            token = super().get_token_info(*scopes, options=options)
            self._token_cache[key] = token
            return token

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        options = {name: kwargs[name] for name in _TOKEN_OPTION_KEYS if name in kwargs}
        info = self.get_token_info(*scopes, options=options)
        return AccessToken(info.token, info.expires_on)


def get_azure_credential() -> AzureCliCredential | ManagedIdentityCredential:
    """
    Returns an Azure credential based on the application environment.

    If the environment is 'local', it uses a token-caching AzureCliCredential.
    Otherwise, it uses ManagedIdentityCredential.
    """
    if settings.AGENT_RUNTIME == 'local':
        return CachedAzureCliCredential()
    else:
        return ManagedIdentityCredential(client_id=settings.AZURE_CLIENT_ID)
