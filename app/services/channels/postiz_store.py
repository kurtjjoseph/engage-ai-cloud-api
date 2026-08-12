"""Storage and lifecycle for an organization's Postiz workspace.

The counterpart to `connections.py`: everything that reads or writes a Postiz
API key goes through here, so there is one place that decrypts it, one place
that marks the workspace broken, and one place that decides which account a
channel posts to. Routers deal in status; adapters deal in a ready client;
neither touches the ciphertext.

Syncing is the load-bearing operation. Postiz knows what is connected; Engage
AI does not get to guess. `sync()` reconciles the workspace's integrations into
`PostizChannel` rows - adding what is new, updating names and disabled flags,
and marking rows whose integration disappeared - while preserving the two
things that are Engage AI's own and were chosen by a person: `auto_post` and
`settings`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.entities import Organization, PostizChannel, PostizWorkspace, Publication
from app.services.crypto import decrypt, encrypt

from .postiz import (
    POSTIZ_ROUTABLE_CHANNELS,
    PostizAdapter,
    PostizClient,
    PostizError,
    PostizIntegration,
    default_settings,
    missing_required_settings,
    normalize_base_url,
)

__all__ = [
    "connect",
    "describe",
    "disconnect",
    "get_channel",
    "get_workspace",
    "postiz_adapter_for",
    "reconcile_publications",
    "sync",
    "usable",
]


# ------------------------------------------------------------------- reading


def get_workspace(db: Session, org_id: int) -> PostizWorkspace | None:
    return (
        db.query(PostizWorkspace)
        .filter(PostizWorkspace.organization_id == org_id)
        .first()
    )


def usable(workspace: PostizWorkspace | None) -> bool:
    return bool(workspace and workspace.status == "connected" and workspace.api_key_enc)


def get_channel(
    db: Session, org_id: int, channel: str, integration_id: str | None = None
) -> PostizChannel | None:
    """The account this org posts to on `channel`.

    With no `integration_id`, the default account wins; an org with two
    Instagram accounts in one workspace posts to the one marked default unless
    a caller names another."""
    query = db.query(PostizChannel).filter(
        PostizChannel.organization_id == org_id,
        PostizChannel.channel == channel,
    )
    if integration_id:
        return query.filter(PostizChannel.integration_id == integration_id).first()
    rows = query.order_by(PostizChannel.is_default.desc(), PostizChannel.id.asc()).all()
    for row in rows:
        if not row.disabled:
            return row
    return rows[0] if rows else None


def list_channels(db: Session, org_id: int) -> list[PostizChannel]:
    return (
        db.query(PostizChannel)
        .filter(PostizChannel.organization_id == org_id)
        .order_by(PostizChannel.channel.asc(), PostizChannel.id.asc())
        .all()
    )


def client_for(workspace: PostizWorkspace) -> PostizClient:
    """A ready client, or a PostizError explaining why not. Raising here rather
    than returning None keeps every caller's failure path identical to a failed
    Postiz call."""
    key = decrypt(workspace.api_key_enc)
    if not key:
        raise PostizError(
            "The stored Postiz API key could not be read (the encryption key changed). "
            "Reconnect the Postiz workspace."
        )
    return PostizClient(workspace.base_url, key)


# ------------------------------------------------------------------- writing


def connect(
    db: Session,
    *,
    org_id: int,
    user_id: int | None,
    api_key: str,
    base_url: str | None = None,
    app_url: str | None = None,
) -> tuple[PostizWorkspace, list[PostizChannel]]:
    """Store the key and immediately sync what it can reach.

    The key is verified before it is stored - `GET /integrations` either answers
    or it doesn't - so a typo fails here, visibly, rather than at the moment
    someone tries to publish."""
    client = PostizClient(base_url, api_key)
    integrations = client.integrations()  # raises PostizError on a bad key/URL

    workspace = get_workspace(db, org_id)
    if workspace is None:
        workspace = PostizWorkspace(organization_id=org_id)
        db.add(workspace)

    workspace.base_url = normalize_base_url(base_url)
    workspace.app_url = (app_url or "").strip().rstrip("/") or None
    workspace.api_key_enc = encrypt(api_key.strip())
    workspace.status = "connected"
    workspace.last_error = None
    workspace.connected_by_user_id = user_id
    workspace.connected_at = datetime.utcnow()
    db.commit()
    db.refresh(workspace)

    channels = _apply_integrations(db, workspace, integrations)
    return workspace, channels


def sync(db: Session, workspace: PostizWorkspace) -> list[PostizChannel]:
    """Re-read the workspace's connected accounts and reconcile the rows."""
    try:
        integrations = client_for(workspace).integrations()
    except PostizError as exc:
        mark_error(db, workspace, str(exc))
        raise
    return _apply_integrations(db, workspace, integrations)


def _apply_integrations(
    db: Session, workspace: PostizWorkspace, integrations: list[PostizIntegration]
) -> list[PostizChannel]:
    existing = {row.integration_id: row for row in list_channels(db, workspace.organization_id)}
    seen: set[str] = set()

    for integration in integrations:
        if not integration.supported or not integration.id:
            # A Postiz platform Engage AI has no channel for. Skipped rather
            # than stored, so nothing can later route a post to it.
            continue
        seen.add(integration.id)
        row = existing.get(integration.id)
        if row is None:
            row = PostizChannel(
                workspace_id=workspace.id,
                organization_id=workspace.organization_id,
                integration_id=integration.id,
                channel=integration.channel,
                auto_post=False,
            )
            db.add(row)
        # Facts Postiz owns, refreshed every sync.
        row.channel = integration.channel
        row.identifier = integration.identifier
        row.account_name = integration.name
        row.account_picture = integration.picture
        row.disabled = integration.disabled
        # auto_post and settings are deliberately not touched here.

    for integration_id, row in existing.items():
        if integration_id not in seen:
            # Disconnected inside Postiz. Kept (past publications point at it)
            # but disabled and stripped of consent, so a re-appearing account
            # never silently resumes unattended posting.
            row.disabled = True
            row.auto_post = False
            row.last_error = "This account is no longer connected in Postiz."

    workspace.last_synced_at = datetime.utcnow()
    workspace.status = "connected"
    workspace.last_error = None
    db.commit()

    rows = list_channels(db, workspace.organization_id)
    _ensure_defaults(db, rows)
    return rows


def _ensure_defaults(db: Session, rows: list[PostizChannel]) -> None:
    """Exactly one default account per channel, and never a disabled one while
    a working account exists."""
    by_channel: dict[str, list[PostizChannel]] = {}
    for row in rows:
        by_channel.setdefault(row.channel, []).append(row)

    changed = False
    for channel_rows in by_channel.values():
        enabled = [row for row in channel_rows if not row.disabled]
        candidates = enabled or channel_rows
        current = [row for row in candidates if row.is_default]
        winner = current[0] if current else candidates[0]
        for row in channel_rows:
            wanted = row is winner
            if row.is_default != wanted:
                row.is_default = wanted
                changed = True
    if changed:
        db.commit()


def set_channel(
    db: Session,
    row: PostizChannel,
    *,
    auto_post: bool | None = None,
    settings: dict | None = None,
    is_default: bool | None = None,
) -> PostizChannel:
    if auto_post is not None:
        row.auto_post = auto_post
    if settings is not None:
        row.settings = settings or None
    if is_default:
        for sibling in (
            db.query(PostizChannel)
            .filter(
                PostizChannel.organization_id == row.organization_id,
                PostizChannel.channel == row.channel,
            )
            .all()
        ):
            sibling.is_default = sibling.id == row.id
    db.commit()
    db.refresh(row)
    return row


def disconnect(db: Session, workspace: PostizWorkspace) -> PostizWorkspace:
    """Forget the key. Channel rows are kept so past publications still name an
    account, but nothing can post through them again until a reconnect."""
    workspace.status = "revoked"
    workspace.api_key_enc = None
    for row in list_channels(db, workspace.organization_id):
        row.auto_post = False
        row.disabled = True
    db.commit()
    db.refresh(workspace)
    return workspace


def mark_error(db: Session, workspace: PostizWorkspace, message: str) -> None:
    workspace.status = "error"
    workspace.last_error = message[:1000]
    db.commit()


def mark_channel_error(db: Session, row: PostizChannel, message: str) -> None:
    row.last_error = message[:1000]
    db.commit()


def mark_channel_used(db: Session, row: PostizChannel) -> None:
    row.last_used_at = datetime.utcnow()
    row.last_error = None
    db.commit()


# ------------------------------------------------------------------ adapters


def postiz_adapter_for(
    db: Session, org_id: int, channel: str, integration_id: str | None = None
) -> PostizAdapter | None:
    """The adapter that posts `channel` through this org's Postiz workspace, or
    None if the org has no usable workspace or no account for that channel.

    Returning None rather than raising is what lets the registry treat Postiz as
    one option among several: no workspace simply means the next fallback
    applies."""
    if channel not in POSTIZ_ROUTABLE_CHANNELS:
        return None
    workspace = get_workspace(db, org_id)
    if not usable(workspace):
        return None
    row = get_channel(db, org_id, channel, integration_id)
    if row is None or row.disabled:
        return None
    try:
        client = client_for(workspace)
    except PostizError as exc:
        mark_error(db, workspace, str(exc))
        return None

    return PostizAdapter(
        channel=row.channel,
        client=client,
        integration_id=row.integration_id,
        identifier=row.identifier,
        account_name=row.account_name,
        settings_override=row.settings or {},
        on_error=lambda message, row=row: mark_channel_error(db, row, message),
        on_success=lambda row=row: mark_channel_used(db, row),
    )


# ---------------------------------------------------------------- reconciling


def reconcile_publications(db: Session, org: Organization, limit: int = 200) -> dict:
    """Fill in the permalinks of posts Postiz has since released.

    A post handed to Postiz comes back as an id, not a URL, so its Publication
    is recorded `status="queued"` pointing at the account rather than the post.
    Postiz reports a `releaseURL` once it has actually published; this walks the
    org's unresolved publications and promotes the ones that now have one.

    Nothing is invented: a post Postiz has not released yet is left exactly as
    it was."""
    workspace = get_workspace(db, org.id)
    if not usable(workspace):
        return {"checked": 0, "resolved": 0, "status": "not_connected"}

    pending = (
        db.query(Publication)
        .filter(
            Publication.organization_id == org.id,
            Publication.delivery == "postiz",
            Publication.status.in_(["queued", "scheduled"]),
        )
        .order_by(Publication.id.desc())
        .limit(limit)
        .all()
    )
    if not pending:
        return {"checked": 0, "resolved": 0, "status": "up_to_date"}

    try:
        posts = client_for(workspace).posts()
    except PostizError as exc:
        mark_error(db, workspace, str(exc))
        return {"checked": len(pending), "resolved": 0, "status": "error", "detail": str(exc)}

    released: dict[str, dict] = {}
    for post in posts:
        for key in ("id", "postId", "group"):
            value = post.get(key)
            if value:
                released[str(value)] = post

    resolved = 0
    for publication in pending:
        post = released.get(str(publication.external_id or ""))
        if not post:
            continue
        url = post.get("releaseURL") or post.get("releaseUrl")
        state = str(post.get("state") or post.get("status") or "").upper()
        if url:
            publication.url = url
            publication.status = "published"
            publication.published_at = publication.published_at or datetime.utcnow()
            resolved += 1
        elif state in {"ERROR", "FAILED"}:
            publication.status = "failed"
            resolved += 1

    if resolved:
        db.commit()
    return {"checked": len(pending), "resolved": resolved, "status": "ok"}


# ------------------------------------------------------------------ describe


def describe(db: Session, org_id: int) -> dict:
    """What the plugin and dashboard render. Contains no key material - only
    whether posting is possible, to which accounts, and what to fix if not."""
    workspace = get_workspace(db, org_id)
    if workspace is None:
        return {
            "connected": False,
            "status": "not_connected",
            "base_url": None,
            "app_url": None,
            "last_error": None,
            "last_synced_at": None,
            "channels": [],
        }

    rows = list_channels(db, org_id)
    return {
        "connected": usable(workspace),
        "status": workspace.status,
        "base_url": workspace.base_url,
        "app_url": workspace.app_url,
        "last_error": workspace.last_error,
        "connected_at": workspace.connected_at.isoformat() if workspace.connected_at else None,
        "last_synced_at": (
            workspace.last_synced_at.isoformat() if workspace.last_synced_at else None
        ),
        "channels": [describe_channel(row) for row in rows],
    }


def describe_channel(row: PostizChannel) -> dict:
    settings = row.settings or {}
    return {
        "channel": row.channel,
        "integration_id": row.integration_id,
        "identifier": row.identifier,
        "account_name": row.account_name,
        "account_picture": row.account_picture,
        "disabled": row.disabled,
        "is_default": row.is_default,
        "auto_post": row.auto_post,
        "settings": settings,
        # What still has to be chosen before this channel can post at all.
        "missing_settings": missing_required_settings(
            row.channel, {**default_settings(row.channel, row.identifier, ""), **settings}
        ),
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "last_error": row.last_error,
    }
