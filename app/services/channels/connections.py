"""Storage and lifecycle for authenticated channel connections.

Everything that reads or writes a ChannelConnection's credentials goes through
here, so there is exactly one place that decrypts a token, one place that
decides a token is stale enough to refresh, and one place that marks a
connection broken. The routers deal in status; the adapters deal in a live
access token; neither touches the ciphertext.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.entities import ChannelConnection
from app.services.crypto import decrypt, encrypt

from .providers import (
    AUTHENTICATABLE_CHANNELS,
    ChannelProvider,
    ProviderError,
    get_provider,
    refresh_access_token,
    resolve_account,
)

# Refresh this far ahead of expiry so a post never races the clock.
REFRESH_MARGIN = timedelta(minutes=5)


def get_connection(db: Session, org_id: int, channel: str) -> ChannelConnection | None:
    return (
        db.query(ChannelConnection)
        .filter(
            ChannelConnection.organization_id == org_id,
            ChannelConnection.channel == channel,
        )
        .order_by(ChannelConnection.id.desc())
        .first()
    )


def list_connections(db: Session, org_id: int) -> dict[str, ChannelConnection]:
    rows = (
        db.query(ChannelConnection)
        .filter(ChannelConnection.organization_id == org_id)
        .order_by(ChannelConnection.id.asc())
        .all()
    )
    # Later rows win, so a reconnect supersedes an older revoked row.
    return {row.channel: row for row in rows}


def save_connection(
    db: Session,
    *,
    org_id: int,
    channel: str,
    user_id: int | None,
    auth_method: str,
    token_payload: dict,
    account: dict,
) -> ChannelConnection:
    """Create or replace the org's connection for `channel`.

    `token_payload` is the provider's token response; `account` is what
    resolve_account() worked out, including the access token that should
    actually be stored (Meta swaps the user token for the Page token).
    Reconnecting reuses the same row so history like `auto_post` survives -
    an admin who re-authorizes to fix an expired token doesn't silently lose
    the posting setting they had already chosen.
    """
    provider = get_provider(channel)
    access_token = account.get("access_token") or token_payload.get("access_token")
    expires_in = token_payload.get("expires_in")

    connection = get_connection(db, org_id, channel)
    if connection is None:
        connection = ChannelConnection(organization_id=org_id, channel=channel)
        db.add(connection)

    connection.provider = provider.provider
    connection.status = "connected"
    connection.auth_method = auth_method
    connection.account_id = account.get("account_id")
    connection.account_name = account.get("account_name")
    connection.account_url = account.get("account_url")
    connection.scopes = _scopes(token_payload, provider)
    connection.access_token_enc = encrypt(access_token)
    connection.refresh_token_enc = encrypt(token_payload.get("refresh_token"))
    connection.token_expires_at = (
        datetime.utcnow() + timedelta(seconds=int(expires_in)) if expires_in else None
    )
    connection.target = account.get("target")
    connection.connected_by_user_id = user_id
    connection.connected_at = datetime.utcnow()
    connection.last_error = None

    db.commit()
    db.refresh(connection)
    return connection


def disconnect(db: Session, connection: ChannelConnection) -> ChannelConnection:
    """Drop the credentials and mark the connection revoked.

    The row is kept (rather than deleted) so past Publications still have an
    account to point at, and so the operator dashboard can show that a channel
    *was* connected and no longer is."""
    connection.status = "revoked"
    connection.access_token_enc = None
    connection.refresh_token_enc = None
    connection.token_expires_at = None
    connection.auto_post = False
    db.commit()
    db.refresh(connection)
    return connection


def mark_error(db: Session, connection: ChannelConnection, message: str, status: str = "error") -> None:
    connection.status = status
    connection.last_error = message[:1000]
    db.commit()


def mark_used(db: Session, connection: ChannelConnection) -> None:
    connection.last_used_at = datetime.utcnow()
    connection.last_error = None
    db.commit()


def access_token(db: Session, connection: ChannelConnection) -> str:
    """A usable access token for this connection, refreshed if it's about to
    expire. Raises ProviderError (with an admin-readable reason) when the
    connection can't be used, after marking it so the UI shows why."""
    if connection.status == "revoked":
        raise ProviderError(
            f"{connection.channel} is disconnected. Reconnect it to post."
        )

    token = decrypt(connection.access_token_enc)
    if not token:
        mark_error(
            db,
            connection,
            "The stored credentials could not be read (the encryption key changed). "
            "Reconnect this channel.",
        )
        raise ProviderError(
            f"{connection.channel}'s stored credentials are unreadable. Reconnect it."
        )

    if not _expiring(connection):
        return token

    provider = get_provider(connection.channel)
    refresh = decrypt(connection.refresh_token_enc)
    if not refresh:
        mark_error(
            db,
            connection,
            "The access token expired and this provider gave no refresh token. "
            "Reconnect this channel.",
            status="expired",
        )
        raise ProviderError(
            f"{connection.channel}'s access has expired. Reconnect it to post again."
        )

    try:
        payload = refresh_access_token(provider, refresh)
    except ProviderError as exc:
        mark_error(db, connection, str(exc), status="expired")
        raise

    return _store_refreshed(db, connection, payload)


def _store_refreshed(db: Session, connection: ChannelConnection, payload: dict) -> str:
    token = payload["access_token"]
    connection.access_token_enc = encrypt(token)
    # Rotating providers (X) return a new refresh token each time; keep the old
    # one when they don't.
    if payload.get("refresh_token"):
        connection.refresh_token_enc = encrypt(payload["refresh_token"])
    expires_in = payload.get("expires_in")
    connection.token_expires_at = (
        datetime.utcnow() + timedelta(seconds=int(expires_in)) if expires_in else None
    )
    connection.status = "connected"
    connection.last_error = None
    db.commit()
    return token


def verify(db: Session, connection: ChannelConnection) -> ChannelConnection:
    """Re-check a connection against the provider and update its status.

    This is the difference between "we have a token on file" and "we can
    actually post": it re-resolves the account, which also picks up a Page
    that was renamed or an Instagram account that was unlinked since connect.
    """
    provider = get_provider(connection.channel)
    try:
        token = access_token(db, connection)
        account = resolve_account(provider, token)
    except ProviderError as exc:
        mark_error(db, connection, str(exc))
        db.refresh(connection)
        return connection

    connection.account_id = account.get("account_id")
    connection.account_name = account.get("account_name")
    connection.account_url = account.get("account_url")
    connection.target = account.get("target")
    if account.get("access_token"):
        connection.access_token_enc = encrypt(account["access_token"])
    connection.status = "connected"
    connection.last_error = None
    db.commit()
    db.refresh(connection)
    return connection


def can_post(connection: ChannelConnection | None) -> bool:
    return bool(connection and connection.status == "connected" and connection.access_token_enc)


def describe(channel: str, connection: ChannelConnection | None) -> dict:
    """The per-channel status the plugin and dashboard render. Deliberately
    contains no token material - only whether posting is possible, as whom,
    and what to do about it if not."""
    provider = get_provider(channel)
    return {
        "channel": channel,
        "label": provider.label,
        "provider": provider.provider,
        "oauth_available": provider.configured,
        "manual_token_hint": provider.manual_token_hint,
        "supports_media": provider.supports_media,
        "scopes_requested": provider.scopes,
        "connected": can_post(connection),
        "status": connection.status if connection else "not_connected",
        "auth_method": connection.auth_method if connection else None,
        "account_name": connection.account_name if connection else None,
        "account_url": connection.account_url if connection else None,
        "auto_post": bool(connection.auto_post) if connection else False,
        "expires_at": (
            connection.token_expires_at.isoformat()
            if connection and connection.token_expires_at
            else None
        ),
        "connected_at": (
            connection.connected_at.isoformat() if connection and connection.connected_at else None
        ),
        "last_used_at": (
            connection.last_used_at.isoformat() if connection and connection.last_used_at else None
        ),
        "last_error": connection.last_error if connection else None,
    }


def describe_all(db: Session, org_id: int) -> list[dict]:
    existing = list_connections(db, org_id)
    return [describe(channel, existing.get(channel)) for channel in AUTHENTICATABLE_CHANNELS]


def _expiring(connection: ChannelConnection) -> bool:
    if not connection.token_expires_at:
        return False
    return connection.token_expires_at <= datetime.utcnow() + REFRESH_MARGIN


def _scopes(token_payload: dict, provider: ChannelProvider) -> list[str]:
    scope = token_payload.get("scope")
    if isinstance(scope, str) and scope:
        return scope.replace(",", " ").split()
    if isinstance(scope, list):
        return scope
    return list(provider.scopes)
