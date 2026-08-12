"""Posting through a Postiz workspace.

The second way to make an Engage AI draft into a real post. `channel_connections`
authorizes each platform directly and is preferred where it works; this relays
through Postiz, which already holds the org's authorizations - and reaches
TikTok, Threads, Bluesky, Mastodon, Pinterest, Telegram, Discord, Reddit and
Slack, which this codebase has no direct provider for.

    GET    /organizations/{id}/postiz                        what's connected
    POST   /organizations/{id}/postiz/connect                store + verify a key
    POST   /organizations/{id}/postiz/sync                   re-read the accounts
    PATCH  /organizations/{id}/postiz/channels/{integration} auto-post, settings
    DELETE /organizations/{id}/postiz                        forget the key
    POST   /organizations/{id}/postiz/publish                one piece -> N channels
    POST   /organizations/{id}/postiz/reconcile              fill in permalinks

The same two rules as channel_connections hold here:

* No endpoint returns key material. Status, accounts, and what to fix - never
  the credential.
* Connecting is not consent to publish. `auto_post` is off per account until an
  admin turns it on, and every other post is an explicit call naming one piece.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import ContentItem, PostizChannel, PostizWorkspace, User
from app.routers.channel_connections import _engagement_from_content
from app.routers.organizations import get_owned_org
from app.services.channels import postiz_store
from app.services.channels.postiz import POSTIZ_ROUTABLE_CHANNELS, PostizError

router = APIRouter(tags=["postiz"])


# ------------------------------------------------------------------ schemas


class ConnectRequest(BaseModel):
    """Postiz > Settings > Public API generates the key. `base_url` is only
    needed for a self-hosted instance - it defaults to the hosted API."""

    api_key: str
    base_url: str | None = None
    # Where the workspace's UI lives, e.g. https://postiz.mychurch.org. Used to
    # link an admin to a queued post; never required.
    app_url: str | None = None


class ChannelUpdate(BaseModel):
    auto_post: bool | None = None
    # Provider settings the platform demands a choice on (a subreddit, a
    # non-default YouTube visibility). Merged over the built-in defaults.
    settings: dict | None = None
    is_default: bool | None = None


class PublishRequest(BaseModel):
    content_id: int
    # Which channels to send this piece to. Omitted means every connected,
    # non-disabled account in the workspace - the "post it everywhere" case.
    channels: list[str] | None = None
    media_asset_id: int | None = None
    # UTC. Omitted posts now (into Postiz's queue); set, Postiz holds it.
    scheduled_for: datetime | None = None
    # Refuses to send unless True. Publishing to every channel at once is the
    # one action here that is both wide and irreversible, so it asks out loud.
    confirm: bool = Field(default=False)


# ------------------------------------------------------------------ helpers


def _workspace_or_404(db: Session, org_id: int) -> PostizWorkspace:
    workspace = postiz_store.get_workspace(db, org_id)
    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail="No Postiz workspace is connected for this organization.",
        )
    return workspace


def _channel_row_or_404(db: Session, org_id: int, integration_id: str) -> PostizChannel:
    row = (
        db.query(PostizChannel)
        .filter(
            PostizChannel.organization_id == org_id,
            PostizChannel.integration_id == integration_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"No Postiz account '{integration_id}' for this organization."
        )
    return row


# ------------------------------------------------------------------- status


@router.get("/organizations/{org_id}/postiz")
def get_postiz(
    org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """The workspace and every account in it, with what each still needs before
    it can post. Never includes the API key."""
    org = get_owned_org(org_id, db, user)
    state = postiz_store.describe(db, org.id)
    state["organization_id"] = org.id
    state["routable_channels"] = sorted(POSTIZ_ROUTABLE_CHANNELS)
    return state


# ------------------------------------------------------------------ connect


@router.post("/organizations/{org_id}/postiz/connect")
def connect_postiz(
    org_id: int,
    payload: ConnectRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Store a Postiz API key and read the accounts it can reach.

    The key is used before it is stored: a bad key, a wrong self-hosted URL, or
    an unreachable instance fails here with the provider's own reason, rather
    than at the moment someone tries to publish."""
    org = get_owned_org(org_id, db, user)
    if not payload.api_key.strip():
        raise HTTPException(status_code=400, detail="No Postiz API key was provided.")

    try:
        postiz_store.connect(
            db,
            org_id=org.id,
            user_id=user.id,
            api_key=payload.api_key,
            base_url=payload.base_url,
            app_url=payload.app_url,
        )
    except PostizError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state = postiz_store.describe(db, org.id)
    state["organization_id"] = org.id
    return state


@router.post("/organizations/{org_id}/postiz/sync")
def sync_postiz(
    org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Re-read which accounts the workspace has connected.

    Postiz is the source of truth for that, so this is how an account added or
    removed there becomes visible here. `auto_post` and per-channel settings
    survive a sync - they are choices a person made, not facts Postiz reports."""
    org = get_owned_org(org_id, db, user)
    workspace = _workspace_or_404(db, org.id)
    try:
        postiz_store.sync(db, workspace)
    except PostizError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    state = postiz_store.describe(db, org.id)
    state["organization_id"] = org.id
    return state


@router.patch("/organizations/{org_id}/postiz/channels/{integration_id}")
def update_postiz_channel(
    org_id: int,
    integration_id: str,
    payload: ChannelUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Turn unattended posting on or off for one account, set the provider
    settings it needs, or make it the default for its channel."""
    org = get_owned_org(org_id, db, user)
    row = _channel_row_or_404(db, org.id, integration_id)

    if payload.auto_post and row.disabled:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{row.account_name or row.channel} is disabled in Postiz, so automatic "
                "posting can't be turned on for it."
            ),
        )

    postiz_store.set_channel(
        db,
        row,
        auto_post=payload.auto_post,
        settings=payload.settings,
        is_default=payload.is_default,
    )
    return postiz_store.describe_channel(row)


@router.delete("/organizations/{org_id}/postiz")
def disconnect_postiz(
    org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Forget the API key. Past publications keep their record; nothing can be
    posted through Postiz again until it is reconnected."""
    org = get_owned_org(org_id, db, user)
    workspace = _workspace_or_404(db, org.id)
    postiz_store.disconnect(db, workspace)
    state = postiz_store.describe(db, org.id)
    state["organization_id"] = org.id
    return state


# ------------------------------------------------------------------ publish


@router.post("/organizations/{org_id}/postiz/publish")
def publish_via_postiz(
    org_id: int,
    payload: PublishRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send one piece of content to one, several, or every connected channel.

    This is the human-in-the-loop path: it posts exactly the piece named in the
    request, to exactly the channels named in the request, because someone asked
    for it. It does not consult `auto_post` and it never picks its own content.

    One channel failing does not cancel the others - each result carries its own
    outcome, because a piece that reached five of six accounts is a real,
    partial success and reporting it as a failure would be wrong.
    """
    org = get_owned_org(org_id, db, user)
    workspace = _workspace_or_404(db, org.id)
    if not postiz_store.usable(workspace):
        raise HTTPException(
            status_code=400,
            detail=(
                "The Postiz workspace isn't usable right now"
                + (f": {workspace.last_error}" if workspace.last_error else ".")
            ),
        )

    item = (
        db.query(ContentItem)
        .filter(ContentItem.id == payload.content_id, ContentItem.organization_id == org.id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Content not found.")

    targets = _resolve_targets(db, org.id, payload.channels)
    if not targets:
        raise HTTPException(
            status_code=400,
            detail=(
                "None of those channels have a usable account in this Postiz workspace. "
                "Sync the workspace, or connect the account in Postiz first."
            ),
        )

    if not payload.confirm:
        # Deliberately a refusal, not a warning: this endpoint can put the same
        # piece on every channel an organization owns in one call.
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    f"This will post '{item.title}' to {len(targets)} channel(s). "
                    "Re-send with confirm=true to publish."
                ),
                "channels": [
                    {
                        "channel": row.channel,
                        "account_name": row.account_name,
                        "integration_id": row.integration_id,
                    }
                    for row in targets
                ],
            },
        )

    when = _as_utc(payload.scheduled_for)
    results = []
    for row in targets:
        engagement = _engagement_from_content(db, item, row.channel, payload.media_asset_id)
        if when is not None:
            engagement["scheduled_for"] = when

        adapter = postiz_store.postiz_adapter_for(db, org.id, row.channel, row.integration_id)
        if adapter is None:
            results.append(_failure(row, "This account is no longer usable. Sync the workspace."))
            continue
        try:
            publication = adapter.distribute(db, org, engagement)
        except PostizError as exc:
            results.append(_failure(row, str(exc)))
            continue

        results.append(
            {
                "channel": row.channel,
                "account_name": row.account_name,
                "integration_id": row.integration_id,
                "ok": True,
                "publication_id": publication.id,
                "postiz_post_id": publication.external_id,
                "status": publication.status,
                "url": publication.url,
                "label": publication.label,
                "simulated": False,
            }
        )

    succeeded = [result for result in results if result["ok"]]
    return {
        "organization_id": org.id,
        "content_id": item.id,
        "requested": len(results),
        "published": len(succeeded),
        "failed": len(results) - len(succeeded),
        "scheduled_for": when.isoformat() if when else None,
        # Postiz accepts a post into its own queue and releases it afterwards,
        # so nothing here claims the post is live yet - reconcile does that.
        "note": (
            "Accepted by Postiz. Queued posts get their real permalink from "
            "POST /organizations/{id}/postiz/reconcile once Postiz releases them."
        ),
        "results": results,
    }


@router.post("/organizations/{org_id}/postiz/reconcile")
def reconcile_postiz(
    org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Replace the placeholder URL on queued publications with the real
    permalink, for every post Postiz has since released."""
    org = get_owned_org(org_id, db, user)
    _workspace_or_404(db, org.id)
    return {"organization_id": org.id, **postiz_store.reconcile_publications(db, org)}


# ------------------------------------------------------------------ internals


def _resolve_targets(
    db: Session, org_id: int, channels: list[str] | None
) -> list[PostizChannel]:
    """Which accounts this publish call actually addresses.

    No channels named means every usable account in the workspace. Named
    channels resolve to that channel's default account, so "post to linkedin"
    is unambiguous for an org with two LinkedIn accounts."""
    rows = [row for row in postiz_store.list_channels(db, org_id) if not row.disabled]
    if channels is None:
        return rows

    wanted = [channel.strip() for channel in channels if channel.strip()]
    unknown = [channel for channel in wanted if channel not in POSTIZ_ROUTABLE_CHANNELS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Postiz can't post to {', '.join(unknown)}. "
                f"Reachable channels: {', '.join(sorted(POSTIZ_ROUTABLE_CHANNELS))}."
            ),
        )

    selected: list[PostizChannel] = []
    for channel in dict.fromkeys(wanted):
        row = postiz_store.get_channel(db, org_id, channel)
        if row is not None and not row.disabled:
            selected.append(row)
    return selected


def _failure(row: PostizChannel, message: str) -> dict:
    return {
        "channel": row.channel,
        "account_name": row.account_name,
        "integration_id": row.integration_id,
        "ok": False,
        "error": message,
    }


def _as_utc(value: datetime | None) -> datetime | None:
    """A naive datetime from a client is read as UTC, matching every other
    timestamp this API stores."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
