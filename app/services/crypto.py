"""Symmetric encryption for third-party channel tokens at rest.

A ChannelConnection holds credentials that can post publicly as the customer's
organization, so they are never stored in the clear: everything written to
`access_token_enc` / `refresh_token_enc` goes through encrypt() here, and only
the live adapters decrypt them, in-process, at the moment of an API call.

The key comes from TOKEN_ENCRYPTION_KEY when set (a Fernet key, or any
passphrase - a non-Fernet value is stretched into one). With nothing set it is
derived from JWT_SECRET, so a deployment doesn't silently fall back to
plaintext. The trade-off of that fallback: rotating JWT_SECRET makes existing
channel tokens undecryptable, which surfaces as a channel needing to be
reconnected (decrypt() returns None rather than raising), never as a crash.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_DERIVATION_LABEL = b"engage-ai:channel-token-encryption:v1"


def _fernet() -> Fernet:
    configured = (settings.token_encryption_key or "").strip()
    if configured:
        try:
            return Fernet(configured.encode())
        except (ValueError, TypeError):
            # Not a valid 32-byte urlsafe-base64 Fernet key - treat whatever
            # was set as a passphrase and stretch it into one, so an operator
            # pasting a random string still gets real encryption.
            secret = configured
    else:
        secret = settings.jwt_secret

    digest = hashlib.sha256(_DERIVATION_LABEL + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str | None) -> str | None:
    """Encrypt a token for storage. None/empty in, None out."""
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    """Decrypt a stored token, or None if it's missing or no longer readable
    with the current key (rotated secret, restored-from-elsewhere row). Callers
    treat None as "this connection needs reconnecting"."""
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return None


def generate_key() -> str:
    """A fresh Fernet key, for operators setting TOKEN_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode()
