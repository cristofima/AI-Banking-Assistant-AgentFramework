"""Local PEM or version-pinned Key Vault signing for the same RS256 token contract."""
from __future__ import annotations

from base64 import urlsafe_b64encode
from functools import cached_property
from threading import RLock
from hashlib import sha256
import json
from typing import Any
from urllib.parse import urlsplit

from azure.identity import ManagedIdentityCredential
from azure.keyvault.keys import KeyClient
from azure.keyvault.keys.crypto import CryptographyClient, SignatureAlgorithm
from joserfc import jwt
from joserfc.jwk import RSAKey

from banking_shared.oauth_jwt import public_jwks
from identity.settings import Settings


def encoded(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def key_location(uri: str) -> tuple[str, str, str]:
    parsed = urlsplit(uri)
    parts = parsed.path.split("/")
    if (parsed.scheme != "https" or not parsed.hostname
            or not parsed.hostname.endswith(".vault.azure.net")
            or parsed.username or parsed.password or parsed.port not in {None, 443}
            or parsed.query or parsed.fragment or len(parts) != 4
            or parts[1] != "keys" or not parts[2] or not parts[3]):
        raise ValueError("A version-pinned Azure Key Vault key URI is required")
    return f"https://{parsed.hostname}", parts[2], parts[3]


class OAuthSigner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = RLock()
        self._credential: ManagedIdentityCredential | None = None
        self._key_client: KeyClient | None = None
        self._crypto: CryptographyClient | None = None

    def validate(self) -> None:
        config = self.settings
        if not config.oauth_signing_key_id.strip() or len(config.oauth_signing_key_id) > 128:
            raise ValueError("OAuth signing key ID is required")
        if config.oauth_signing_backend == "pem":
            if config.oauth_signing_key_uri:
                raise ValueError("PEM signing cannot select a Key Vault key")
            public_jwks(config.oauth_signing_private_key.get_secret_value(),
                        config.oauth_signing_key_id)
        else:
            if config.oauth_signing_private_key.get_secret_value().strip():
                raise ValueError("Key Vault signing cannot receive a private key")
            key_location(config.oauth_signing_key_uri)

    @property
    def jwks(self) -> dict[str, Any]:
        with self._lock:
            return self._jwks

    @cached_property
    def _jwks(self) -> dict[str, Any]:
        self.validate()
        if self.settings.oauth_signing_backend == "pem":
            return public_jwks(self.settings.oauth_signing_private_key.get_secret_value(),
                               self.settings.oauth_signing_key_id)
        vault, name, version = key_location(self.settings.oauth_signing_key_uri)
        if self._credential is None:
            self._credential = ManagedIdentityCredential()
            self._key_client = KeyClient(vault, self._credential, logging_enable=False)
        if self._key_client is None:
            raise ValueError("Key Vault signer is unavailable")
        key = self._key_client.get_key(name, version)
        if (key.id != self.settings.oauth_signing_key_uri or key.key.kty != "RSA"
                or key.properties.enabled is not True or "sign" not in key.key.key_ops
                or not key.key.n or not key.key.e):
            raise ValueError("Key Vault requires an enabled RSA signing key")
        public = {"kty": "RSA", "kid": self.settings.oauth_signing_key_id,
                  "n": encoded(bytes(key.key.n)), "e": encoded(bytes(key.key.e)),
                  "alg": "RS256", "use": "sig"}
        if RSAKey.import_key(public).public_key.key_size < 2048:
            raise ValueError("OAuth RSA signing key must be at least 2048 bits")
        self._crypto = CryptographyClient(key, self._credential, logging_enable=False)
        return {"keys": [public]}

    def encode(self, claims: dict[str, object]) -> str:
        self.validate()
        header = {"alg": "RS256", "typ": "at+jwt", "kid": self.settings.oauth_signing_key_id}
        if self.settings.oauth_signing_backend == "pem":
            return jwt.encode(header, claims, RSAKey.import_key(
                self.settings.oauth_signing_private_key.get_secret_value()), algorithms=["RS256"])
        self.jwks
        if self._crypto is None:
            raise ValueError("Key Vault signer is unavailable")
        segments = [encoded(json.dumps(value, separators=(",", ":")).encode())
                    for value in (header, claims)]
        signing_input = ".".join(segments).encode("ascii")
        result = self._crypto.sign(SignatureAlgorithm.rs256, sha256(signing_input).digest())
        if result.key_id != self.settings.oauth_signing_key_uri:
            raise ValueError("Key Vault signed with an unexpected key version")
        return ".".join([*segments, encoded(result.signature)])

    def close(self) -> None:
        for client in (self._crypto, self._key_client, self._credential):
            if client is not None:
                client.close()
