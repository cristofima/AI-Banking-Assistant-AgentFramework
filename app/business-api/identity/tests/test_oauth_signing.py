"""Offline native signing checks; no Azure connections or credentials."""
from __future__ import annotations

from base64 import urlsafe_b64decode
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock

from azure.core.exceptions import ServiceRequestError
from azure.keyvault.keys.crypto import SignatureAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, utils
from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import SecretStr
import pytest

from identity.services.oauth_signing import OAuthSigner, key_location
from identity.settings import Settings

NativeFixture = tuple[OAuthSigner, RSAKey, Mock, Mock, Mock]

URI = "https://synthetic-vault.vault.azure.net/keys/oauth-signing/0123456789abcdef"


@pytest.fixture
def native(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> tuple[OAuthSigner, RSAKey, Mock, Mock, Mock]:
    settings.oauth_signing_backend = "key_vault"
    settings.oauth_signing_key_uri = URI
    settings.oauth_signing_key_id = "synthetic-key"
    settings.oauth_signing_private_key = SecretStr("")
    key = RSAKey.generate_key(2048, private=True)
    public = key.as_dict(private=False)
    def raw(value: str) -> bytes:
        return urlsafe_b64decode(value + "=" * (-len(value) % 4))
    remote = SimpleNamespace(id=URI, properties=SimpleNamespace(enabled=True),
        key=SimpleNamespace(kty="RSA", key_ops=["sign", "verify"],
                            n=raw(public["n"]), e=raw(public["e"])))
    credential, client, crypto = Mock(), Mock(), Mock()
    client.get_key.return_value = remote
    def sign(algorithm: SignatureAlgorithm, digest: bytes) -> SimpleNamespace:
        assert algorithm == SignatureAlgorithm.rs256
        return SimpleNamespace(key_id=URI, signature=key.private_key.sign(
            digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256())))
    crypto.sign.side_effect = sign
    monkeypatch.setattr("identity.services.oauth_signing.ManagedIdentityCredential", lambda: credential)
    monkeypatch.setattr("identity.services.oauth_signing.KeyClient", lambda *args, **kwargs: client)
    monkeypatch.setattr("identity.services.oauth_signing.CryptographyClient", lambda *args, **kwargs: crypto)
    return OAuthSigner(settings), key, credential, client, crypto


def test_native_signs_sha256_digest_and_publishes_only_public_key(native: NativeFixture) -> None:
    signer, key, credential, client, crypto = native
    claims = {"sub": "synthetic-customer", "iat": 1, "exp": 3601}
    token = signer.encode(claims)
    assert jwt.decode(token, key, algorithms=["RS256"]).claims == claims
    signing_input = token.rsplit(".", 1)[0].encode("ascii")
    crypto.sign.assert_called_once_with(SignatureAlgorithm.rs256, sha256(signing_input).digest())
    client.get_key.assert_called_once_with("oauth-signing", "0123456789abcdef")
    assert set(signer.jwks["keys"][0]) == {"kty", "kid", "n", "e", "alg", "use"}
    assert jwt.decode(token, RSAKey.import_key(signer.jwks["keys"][0]), algorithms=["RS256"]).claims == claims
    signer.close()
    for resource in (credential, client, crypto):
        resource.close.assert_called_once()


@pytest.mark.parametrize("change", ["disabled", "wrong_version", "ec", "no_sign", "weak"])
def test_native_rejects_invalid_public_key(native: NativeFixture, change: str) -> None:
    signer, _, _, client, crypto = native
    remote = client.get_key.return_value
    if change == "disabled":
        remote.properties.enabled = False
    elif change == "wrong_version":
        remote.id = URI + "other"
    elif change == "ec":
        remote.key.kty = "EC"
    elif change == "no_sign":
        remote.key.key_ops = ["verify"]
    else:
        remote.key.n = b"\x01" * 128
    with pytest.raises(ValueError):
        signer.encode({"exp": 1})
    crypto.sign.assert_not_called()


def test_native_failure_never_falls_back_to_pem(native: NativeFixture) -> None:
    signer, _, _, client, crypto = native
    client.get_key.side_effect = ServiceRequestError("synthetic failure")
    with pytest.raises(ServiceRequestError):
        signer.encode({"exp": 1})
    crypto.sign.assert_not_called()
    signer.settings.oauth_signing_private_key = SecretStr("synthetic-unusable-pem")
    with pytest.raises(ValueError, match="cannot receive"):
        signer.validate()


def test_native_rejects_wrong_signature_key_version(native: NativeFixture) -> None:
    signer, _, _, _, crypto = native
    crypto.sign.side_effect = None
    crypto.sign.return_value = SimpleNamespace(key_id=URI + "other", signature=b"synthetic")
    with pytest.raises(ValueError, match="unexpected key version"):
        signer.encode({"exp": 1})


@pytest.mark.parametrize("uri", ["", "http://synthetic-vault.vault.azure.net/keys/key/version",
    "https://synthetic-vault.vault.azure.net/keys/key", "https://evil.example/keys/key/version",
    URI + "?x=1", URI + "#x", "https://user@synthetic-vault.vault.azure.net/keys/key/version"])
def test_key_uri_requires_pinned_trusted_vault(uri: str) -> None:
    with pytest.raises(ValueError):
        key_location(uri)
