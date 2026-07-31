"""Short-lived signed URLs for generated media.

Instagram and Google Business Profile don't accept uploaded bytes on the
publishing call - they fetch the media themselves from a URL, which rules out
the normal owner-scoped `GET /content/asset/{id}` (they have no bearer token).
So a publish to those channels mints a URL that carries its own proof: the
asset id, an expiry, and an HMAC over both. It grants read access to exactly
one asset for a few minutes and nothing else.
"""

import hmac
import time
from hashlib import sha256

from app.config import settings


def _key() -> bytes:
    return (settings.media_url_signing_key or settings.jwt_secret).encode()


def _signature(asset_id: int, expires: int) -> str:
    return hmac.new(_key(), f"{asset_id}:{expires}".encode(), sha256).hexdigest()


def sign_media_url(asset_id: int, ttl_seconds: int | None = None) -> str:
    """Absolute, publicly fetchable URL for one asset, valid for a few minutes.
    Absolute because the provider fetches it from its own servers."""
    expires = int(time.time()) + (ttl_seconds or settings.media_url_ttl_seconds)
    signature = _signature(asset_id, expires)
    base = settings.api_base_url.rstrip("/")
    return f"{base}/channels/media/{asset_id}?expires={expires}&signature={signature}"


def verify_media_url(asset_id: int, expires: int, signature: str) -> bool:
    if expires < int(time.time()):
        return False
    return hmac.compare_digest(_signature(asset_id, expires), signature or "")
