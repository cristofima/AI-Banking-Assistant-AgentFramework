"""Signed identity envelope for trusted internal service calls."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from bff.identity.models import AuthenticatedUser
from bff.identity.oauth_client import OAuthGrant


def create_internal_identity(
    user: AuthenticatedUser, secret: str, grant: OAuthGrant | None = None,
) -> str:
    """Sign verified banking identity and an optional server-owned OAuth grant."""
    claims = {
        "customer_id": user.customer_id, "sub": user.sub,
        "email": user.email, "locale": user.locale,
    }
    if grant is not None:
        claims.update(oauth_access_token=grant.access_token, oauth_expires_at=grant.expires_at)
    payload = json.dumps(
        claims,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    encoded_payload = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    signature = hmac.new(secret.encode(), encoded_payload.encode(), hashlib.sha256).hexdigest()
    return f"v1.{encoded_payload}.{signature}"