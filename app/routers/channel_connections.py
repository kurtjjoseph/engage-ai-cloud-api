"""Per-channel posting authentication.

Lets an organization's admin authorize Engage AI to post on each channel it
runs - Facebook Page, Instagram, LinkedIn, YouTube, X and Google Business
Profile - and then publish a specific piece of content to a connected channel.

The shape of the flow:

    GET    /organizations/{id}/channels                  what's connected
    POST   /organizations/{id}/channels/{ch}/authorize   -> authorize_url
    GET    /channels/callback/{ch}                       provider comes back here
    POST   /organizations/{id}/channels/{ch}/token       paste a long-lived token
    POST   /organizations/{id}/channels/{ch}/verify      re-check it still works
    PATCH  /organizations/{id}/channels/{ch}             turn auto-posting on/off
    DELETE /organizations/{id}/channels/{ch}             disconnect
    POST   /organizations/{id}/channels/{ch}/publish     publish one piece now

Two rules hold everywhere in here:

* No endpoint ever returns token material. The API reports that a channel is
  connected, as which account, and until when - never the credential.
* Connecting is not consent to publish. Every real post is either an explicit
  publish call for one named piece of content, or a channel the admin
  separately switched auto_post on for.
"""

from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import (
    ChannelAuthRequest,
    ChannelConnection,
    ContentItem,
    MediaAsset,
    User,
)
from app.routers.organizations import get_owned_org
from app.services.channels import connections as conn_service
from app.services.channels.live import PublishError, live_adapter_for
from app.services.channels.providers import (
    AUTHENTICATABLE_CHANNELS,
    ProviderError,
    build_authorize_url,
    exchange_code,
    get_provider,
    new_code_verifier,
    new_state,
    resolve_account,
)
from app.services.channels.setup_guide import build_guide
from app.services.media_links import verify_media_url

router = APIRouter(tags=["channel-connections"])

# How long an admin has to complete the provider's consent screen.
AUTH_REQUEST_TTL = timedelta(minutes=15)


# ------------------------------------------------------------------ schemas


class AuthorizeRequest(BaseModel):
    # Where to send the admin back to afterwards (the plugin's Channels page).
    # Only http(s) is accepted; anything else is dropped rather than rejected,
    # so a stray value can't turn the callback into an open redirect.
    return_url: str | None = None


class ManualTokenRequest(BaseModel):
    """The escape hatch for a provider whose OAuth app isn't registered on this
    deployment yet: the admin generates a long-lived token themselves and
    pastes it in. It is verified against the provider before being stored, so
    a bad token fails here rather than silently at publish time."""

    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None


class ConnectionUpdate(BaseModel):
    # Explicit opt-in to unattended posting on this channel.
    auto_post: bool


class PublishRequest(BaseModel):
    content_id: int
    # Overrides the media the piece already has (defaults to whatever the
    # Content Studio rendered for it).
    media_asset_id: int | None = None


# ------------------------------------------------------------------ helpers


def _valid_channel(channel: str) -> str:
    if channel not in AUTHENTICATABLE_CHANNELS:
        raise HTTPException(
            status_code=404,
            detail=(
                f"'{channel}' isn't a channel Engage AI can be authenticated for. "
                f"Connectable channels: {', '.join(AUTHENTICATABLE_CHANNELS)}. "
                "(The website is posted to by the WordPress plugin itself, so it "
                "needs no separate authorization.)"
            ),
        )
    return channel


def _get_connection_or_404(db: Session, org_id: int, channel: str) -> ChannelConnection:
    connection = conn_service.get_connection(db, org_id, channel)
    if connection is None:
        raise HTTPException(
            status_code=404, detail=f"{channel} isn't connected for this organization."
        )
    return connection


def _safe_return_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") and parsed.netloc else None


def _callback_page(title: str, message: str, return_url: str | None, ok: bool) -> HTMLResponse:
    """The page the provider redirects the admin's browser to. Deliberately a
    dead end with a link back rather than an automatic redirect - the admin
    reads what happened and clicks."""
    colour = "#0f7b47" if ok else "#b3261e"
    link = f'<p><a class="back" href="{return_url}">Back to Engage AI</a></p>' if return_url else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #f6f7f9; margin: 0; padding: 3rem 1.25rem; color: #1d2129; }}
  .card {{ max-width: 32rem; margin: 0 auto; background: #fff; border-radius: 12px;
           padding: 2rem; box-shadow: 0 1px 3px rgba(0,0,0,.12); }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .75rem; color: {colour}; }}
  p {{ line-height: 1.55; margin: 0 0 1rem; }}
  .back {{ display: inline-block; background: #1d4ed8; color: #fff; text-decoration: none;
           padding: .6rem 1rem; border-radius: 8px; font-weight: 600; }}
</style></head>
<body><div class="card"><h1>{title}</h1><p>{message}</p>{link}</div></body></html>""",
        status_code=200 if ok else 400,
    )


# ------------------------------------------------------------------- status


@router.get("/organizations/{org_id}/channels")
def list_channel_connections(
    org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Every connectable channel and where it stands for this organization -
    connected or not, as which account, expiring when, and if something broke,
    why. Never includes credentials."""
    org = get_owned_org(org_id, db, user)
    return {"organization_id": org.id, "channels": conn_service.describe_all(db, org.id)}


@router.get("/organizations/{org_id}/channels/setup-guide")
def channel_setup_guide(
    org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """The step-by-step wizard behind the plugin's "Set up a channel" page:
    for each channel, how to create it, what has to be true before it can be
    authorized, and how to authorize it - including the live link to the page
    that issues an access token where one is needed.

    Declared before /{channel} so "setup-guide" is matched as this route rather
    than as a channel name.

    Served from the API rather than baked into the plugin because providers move
    these pages, and one deploy is better than every install carrying a stale
    link."""
    org = get_owned_org(org_id, db, user)
    statuses = conn_service.describe_all(db, org.id)
    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "channels": build_guide(org, statuses),
    }


@router.get("/organizations/{org_id}/channels/{channel}")
def get_channel_connection(
    org_id: int,
    channel: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org = get_owned_org(org_id, db, user)
    _valid_channel(channel)
    return conn_service.describe(channel, conn_service.get_connection(db, org.id, channel))


# ------------------------------------------------------------- oauth connect


@router.post("/organizations/{org_id}/channels/{channel}/authorize")
def start_channel_authorization(
    org_id: int,
    channel: str,
    payload: AuthorizeRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Start the OAuth flow: returns the URL to send the admin's browser to.

    The caller doesn't get to choose the state or the redirect - both are
    minted here and remembered server-side, so the callback can only complete
    an authorization this API actually started."""
    org = get_owned_org(org_id, db, user)
    _valid_channel(channel)
    provider = get_provider(channel)

    if not provider.configured:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This Engage AI deployment has no {provider.label} app configured "
                f"({provider.client_id_setting} / {provider.client_secret_setting}), so there "
                "is no consent screen to send you to. Paste a long-lived access token instead: "
                f"{provider.manual_token_hint}"
            ),
        )

    state = new_state()
    verifier = new_code_verifier() if provider.use_pkce else None
    request_row = ChannelAuthRequest(
        organization_id=org.id,
        user_id=user.id,
        channel=channel,
        state=state,
        code_verifier=verifier,
        return_url=_safe_return_url(payload.return_url if payload else None),
        expires_at=datetime.utcnow() + AUTH_REQUEST_TTL,
    )
    db.add(request_row)
    db.commit()

    try:
        url = build_authorize_url(provider, state, verifier)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "channel": channel,
        "authorize_url": url,
        "redirect_uri": provider.redirect_uri,
        "scopes": provider.scopes,
        "expires_at": request_row.expires_at.isoformat(),
    }


@router.get("/channels/callback/{channel}", response_class=HTMLResponse)
def channel_authorization_callback(
    channel: str,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    """Where the provider sends the admin back to.

    Public by necessity - the provider redirects a browser here with no bearer
    token - which is exactly why the `state` is single-use and was issued to a
    specific authenticated user for a specific organization."""
    if channel not in AUTHENTICATABLE_CHANNELS:
        return _callback_page(
            "Unknown channel", f"'{channel}' isn't a connectable channel.", None, False
        )

    request_row = (
        db.query(ChannelAuthRequest).filter(ChannelAuthRequest.state == state).first()
        if state
        else None
    )
    if request_row is None or request_row.channel != channel:
        return _callback_page(
            "That link didn't check out",
            "This authorization couldn't be matched to a request Engage AI started. "
            "Start the connection again from the Channels page.",
            None,
            False,
        )

    return_url = _safe_return_url(request_row.return_url)

    if request_row.used or request_row.expires_at < datetime.utcnow():
        return _callback_page(
            "That link has expired",
            "Authorization links are single-use and last 15 minutes. Start the connection again.",
            return_url,
            False,
        )

    # Burn the state before doing anything with the code, so a replay of the
    # same callback URL can't produce a second connection.
    request_row.used = True
    db.commit()

    provider = get_provider(channel)

    if error:
        return _callback_page(
            "Connection cancelled",
            f"{provider.label} reported: {error_description or error}",
            return_url,
            False,
        )
    if not code:
        return _callback_page(
            "Connection incomplete",
            "The provider came back without an authorization code. Please try again.",
            return_url,
            False,
        )

    try:
        token_payload = exchange_code(provider, code, request_row.code_verifier)
        account = resolve_account(provider, token_payload["access_token"])
    except ProviderError as exc:
        return _callback_page("Couldn't connect", str(exc), return_url, False)

    conn_service.save_connection(
        db,
        org_id=request_row.organization_id,
        channel=channel,
        user_id=request_row.user_id,
        auth_method="oauth",
        token_payload=token_payload,
        account=account,
    )

    return _callback_page(
        f"{provider.label} connected",
        f"Engage AI can now post to <strong>{account.get('account_name') or 'this account'}</strong>. "
        "Nothing goes out automatically - you still publish each piece yourself, unless you "
        "switch automatic posting on for this channel.",
        return_url,
        True,
    )


# ------------------------------------------------------------ manual connect


@router.post("/organizations/{org_id}/channels/{channel}/token")
def connect_with_token(
    org_id: int,
    channel: str,
    payload: ManualTokenRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Connect a channel with an access token the admin generated themselves.

    Verified against the provider first: if the token can't name an account to
    post as, it isn't stored."""
    org = get_owned_org(org_id, db, user)
    _valid_channel(channel)
    provider = get_provider(channel)

    token = payload.access_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="No access token was provided.")

    try:
        account = resolve_account(provider, token)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=f"That token didn't work: {exc}") from exc

    connection = conn_service.save_connection(
        db,
        org_id=org.id,
        channel=channel,
        user_id=user.id,
        auth_method="manual_token",
        token_payload={
            "access_token": token,
            "refresh_token": payload.refresh_token,
            "expires_in": payload.expires_in,
        },
        account=account,
    )
    return conn_service.describe(channel, connection)


# --------------------------------------------------------------- management


@router.post("/organizations/{org_id}/channels/{channel}/verify")
def verify_channel_connection(
    org_id: int,
    channel: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Re-check the connection against the provider and refresh what it knows
    about the account. Answers "will this actually post?" without posting."""
    org = get_owned_org(org_id, db, user)
    _valid_channel(channel)
    connection = _get_connection_or_404(db, org.id, channel)
    return conn_service.describe(channel, conn_service.verify(db, connection))


@router.patch("/organizations/{org_id}/channels/{channel}")
def update_channel_connection(
    org_id: int,
    channel: str,
    payload: ConnectionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Turn autonomous posting on or off for this channel.

    Off (the default) means Engage AI only posts here when someone publishes a
    specific piece; on lets the engagement cycle distribute to it unattended."""
    org = get_owned_org(org_id, db, user)
    _valid_channel(channel)
    connection = _get_connection_or_404(db, org.id, channel)
    if payload.auto_post and not conn_service.can_post(connection):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{channel} isn't in a state where it can post, so automatic posting "
                "can't be turned on."
            ),
        )
    connection.auto_post = payload.auto_post
    db.commit()
    db.refresh(connection)
    return conn_service.describe(channel, connection)


@router.delete("/organizations/{org_id}/channels/{channel}")
def disconnect_channel(
    org_id: int,
    channel: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Forget this channel's credentials. Past publications keep their record;
    Engage AI just can't post here any more until it's reconnected."""
    org = get_owned_org(org_id, db, user)
    _valid_channel(channel)
    connection = _get_connection_or_404(db, org.id, channel)
    return conn_service.describe(channel, conn_service.disconnect(db, connection))


# ------------------------------------------------------------------ publish


@router.post("/organizations/{org_id}/channels/{channel}/publish")
def publish_to_channel(
    org_id: int,
    channel: str,
    payload: PublishRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Publish one piece of content to a connected channel, now.

    This is the human-in-the-loop path: it posts exactly the piece named in the
    request, once, because someone asked for it. It doesn't consult auto_post
    and it never picks its own content."""
    org = get_owned_org(org_id, db, user)
    _valid_channel(channel)
    connection = _get_connection_or_404(db, org.id, channel)
    if not conn_service.can_post(connection):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{channel} isn't connected right now"
                + (f": {connection.last_error}" if connection.last_error else ".")
            ),
        )

    item = (
        db.query(ContentItem)
        .filter(ContentItem.id == payload.content_id, ContentItem.organization_id == org.id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Content not found.")

    engagement = _engagement_from_content(db, item, channel, payload.media_asset_id)
    adapter = live_adapter_for(connection)
    try:
        publication = adapter.distribute(db, org, engagement)
    except (PublishError, ProviderError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "publication_id": publication.id,
        "channel": channel,
        "url": publication.url,
        "label": publication.label,
        "simulated": False,
        "published_at": publication.published_at.isoformat() if publication.published_at else None,
        "account_name": connection.account_name,
    }


def _engagement_from_content(
    db: Session, item: ContentItem, channel: str, media_asset_id: int | None
) -> dict:
    """Turn a stored ContentItem into the engagement dict the adapters take.

    Reads the flattened fields the Content Studio writes (body/hashtags) and
    finds the media it rendered for this piece, so publishing needs no argument
    beyond "this one"."""
    output = item.output_payload or {}

    if media_asset_id is not None:
        owns_asset = (
            db.query(MediaAsset)
            .filter(
                MediaAsset.id == media_asset_id,
                MediaAsset.organization_id == item.organization_id,
            )
            .first()
        )
        if not owns_asset:
            raise HTTPException(
                status_code=404, detail="That media asset doesn't belong to this organization."
            )
        asset_id = media_asset_id
    else:
        asset_id = ((output.get("studio") or {}).get("render") or {}).get("asset_id")
        if asset_id is None:
            latest = (
                db.query(MediaAsset)
                .filter(MediaAsset.content_item_id == item.id)
                .order_by(MediaAsset.id.desc())
                .first()
            )
            asset_id = latest.id if latest else None

    return {
        "channel": channel,
        "type": output.get("content_type_key") or item.content_type,
        "title": output.get("title") or item.title,
        "content": {"body": output.get("body", ""), "hashtags": output.get("hashtags") or []},
        "risk": "high",
        "content_item_id": item.id,
        "media_asset_id": asset_id,
    }


# ------------------------------------------------------------- signed media


@router.get("/channels/media/{asset_id}")
def public_signed_media(
    asset_id: int,
    expires: int = Query(...),
    signature: str = Query(...),
    db: Session = Depends(get_db),
):
    """Serve one generated asset to a provider that fetches media by URL
    (Instagram, Google Business Profile). Unauthenticated by necessity, but
    scoped to a single asset for a few minutes by the signature in the link -
    see services/media_links.py."""
    if not verify_media_url(asset_id, expires, signature):
        raise HTTPException(status_code=403, detail="This media link is invalid or has expired.")
    asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    return Response(
        content=asset.data,
        media_type=asset.mime or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=300"},
    )
