"""Postiz as a delivery backend - one API instead of six platform reviews.

The per-channel providers in `providers.py` authenticate Engage AI *directly*
with Meta, Google, LinkedIn and X. That path gives the most control and the
least dependency, and it stays the preferred one - but every provider on it
gates real posting behind an app review (Meta App Review, LinkedIn Community
Management, TikTok's audit), which an organization cannot make go faster.

Postiz (https://postiz.com, AGPL, self-hostable) already holds those
authorizations. An organization connects its accounts *inside Postiz*, and
Engage AI posts through Postiz's public API with a single API key. That trades
a direct dependency for a shorter route to a real post, and it reaches channels
this codebase has no provider for at all - TikTok, Threads, Bluesky, Mastodon,
Pinterest, Telegram, Discord, Reddit, Slack.

Three things are deliberate here:

* **This is a transport, not a channel.** A post sent through Postiz to a
  Facebook Page is still a `facebook` publication. Nothing downstream -
  analytics, publications, the engagement cycle - learns a new channel name.
* **Direct connections still win.** `registry.get_adapter()` prefers an org's
  own `ChannelConnection` and only falls back to Postiz, so adding Postiz never
  silently re-routes a channel that was already authorized directly.
* **A queued post is not a published post.** Postiz accepts a post into its own
  queue and releases it a moment (or a week) later, so what comes back is a
  post id, not a permalink. That is recorded honestly as `status="queued"` with
  no invented URL, and `reconcile()` fills in the real permalink once Postiz
  reports one. `simulated` stays False throughout - the post is real.

This module talks to Postiz and nothing else: no database, no models. Storage
and the org-facing lifecycle live in `postiz_store.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import httpx

from sqlalchemy.orm import Session

from app.config import settings
from app.models.entities import MediaAsset, Organization, Publication

from .base import ChannelAdapter

# Where a connect request goes when it names no instance. Hosted Postiz by
# default; a deployment serving self-hosted instances sets POSTIZ_BASE_URL to
# its own backend (commonly whatever NEXT_PUBLIC_BACKEND_URL is there).
PUBLIC_API_PATH = "/public/v1"

TIMEOUT = 60.0
# Uploads carry the rendered image or an 8s MP4; the read timeout has to
# outlast a slow instance writing the file, not just the request.
UPLOAD_TIMEOUT = 180.0


class PostizError(RuntimeError):
    """A Postiz call failed - the message is safe to show an admin."""


# ------------------------------------------------------------------ channels

# Postiz names a connected account's platform in `identifier`. Several map onto
# one Engage AI channel (a LinkedIn company page and a member profile are both
# `linkedin` here), because what differs is *which account*, not which channel.
POSTIZ_IDENTIFIER_TO_CHANNEL: dict[str, str] = {
    "facebook": "facebook",
    "instagram": "instagram",
    "instagram-standalone": "instagram",
    "linkedin": "linkedin",
    "linkedin-page": "linkedin",
    "youtube": "youtube",
    "x": "twitter_x",
    "twitter": "twitter_x",
    "tiktok": "tiktok",
    "threads": "threads",
    "bluesky": "bluesky",
    "mastodon": "mastodon",
    "pinterest": "pinterest",
    "telegram": "telegram",
    "discord": "discord",
    "reddit": "reddit",
    "slack": "slack",
    "warpcast": "farcaster",
    "farcaster": "farcaster",
}

# Every Engage AI channel a Postiz workspace can carry. Used by the registry to
# accept a channel that has no direct provider (tiktok, bluesky, ...) as long as
# the org has mapped it in Postiz.
POSTIZ_ROUTABLE_CHANNELS: set[str] = set(POSTIZ_IDENTIFIER_TO_CHANNEL.values())

# Text ceilings, applied before the post leaves here so an over-long body is
# trimmed by us (visibly, with an ellipsis) rather than rejected by Postiz or
# silently cut by the platform.
CHANNEL_TEXT_LIMITS: dict[str, int] = {
    "twitter_x": 280,
    "bluesky": 300,
    "mastodon": 500,
    "threads": 500,
    "pinterest": 500,
    "instagram": 2200,
    "facebook": 63206,
    "linkedin": 3000,
    "tiktok": 2200,
    "youtube": 5000,
    "reddit": 40000,
    "telegram": 4096,
    "discord": 2000,
    "slack": 4000,
    "farcaster": 320,
}

# Channels Postiz will not accept a text-only post for: the platform itself
# requires a media item.
MEDIA_REQUIRED_CHANNELS: set[str] = {"instagram", "youtube", "tiktok", "pinterest"}


def default_settings(channel: str, identifier: str, title: str) -> dict:
    """The provider-specific `settings` block Postiz requires for a channel.

    Most channels need nothing beyond `__type`. The ones here refuse a post
    without a choice being made, so a defensible default is made *once*, here,
    rather than left to fail at publish time:

    * YouTube goes out **unlisted**, matching the direct YouTubeAdapter - an
      automated system putting a video in front of a congregation's subscribers
      is not a reversible action, and unlisted keeps a human able to flip it.
    * TikTok goes out with interaction settings off and self-declared
      non-commercial content, the most conservative combination it accepts.

    Reddit is deliberately absent: it needs a subreddit nobody can guess, so the
    operator supplies it per channel (`PostizChannel.settings`) or the post is
    refused with a message saying so.
    """
    base = {"__type": identifier}
    if channel == "youtube":
        return {**base, "title": title[:100] or "Untitled", "type": "unlisted"}
    if channel == "tiktok":
        return {
            **base,
            "title": title[:150],
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "duet": False,
            "stitch": False,
            "comment": False,
            "brand_content_toggle": False,
            "brand_organic_toggle": False,
            "content_posting_method": "DIRECT_POST",
        }
    if channel == "pinterest":
        return {**base, "title": title[:100]}
    return base


def missing_required_settings(channel: str, provided: dict | None) -> list[str]:
    """Settings a channel cannot be posted to without, and that nothing can
    default sensibly. Checked before the API call so the admin gets a clear
    "set this" instead of a Postiz 400."""
    provided = provided or {}
    if channel == "reddit" and not provided.get("subreddit"):
        return ["subreddit"]
    return []


# -------------------------------------------------------------------- client


def normalize_base_url(raw: str | None) -> str:
    """Accept any of the forms an operator actually pastes and return the
    public-API root: `https://api.postiz.com`, `.../public/v1`, or a
    self-hosted `http://postiz.internal:5000/api` all resolve correctly."""
    base = (raw or settings.postiz_base_url).strip().rstrip("/")
    if not base:
        base = settings.postiz_base_url.rstrip("/")
    if base.endswith(PUBLIC_API_PATH):
        return base
    return f"{base}{PUBLIC_API_PATH}"


@dataclass(frozen=True)
class PostizIntegration:
    """One account connected inside Postiz."""

    id: str
    name: str
    identifier: str
    channel: str | None
    picture: str | None = None
    disabled: bool = False
    profile: str | None = None

    @property
    def supported(self) -> bool:
        """False for a Postiz platform Engage AI has no channel for (lemmy,
        nostr, ...). Listed, never posted to."""
        return self.channel is not None


class PostizClient:
    """Thin, synchronous client for the Postiz public API.

    Every response shape is read defensively: Postiz is a fast-moving
    self-hosted project, and a list that arrives wrapped in `{"integrations":
    [...]}` on one version and bare on another must not break posting.
    """

    def __init__(self, base_url: str | None, api_key: str):
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key

    # --- plumbing ---
    def _headers(self) -> dict:
        # Postiz takes the key raw in Authorization - no "Bearer" prefix. A key
        # pasted with one is tolerated rather than sent through as garbage.
        key = self.api_key.strip()
        if key.lower().startswith("bearer "):
            key = key[7:].strip()
        return {"Authorization": key, "Accept": "application/json"}

    def _request(self, method: str, path: str, what: str, timeout: float = TIMEOUT, **kwargs) -> dict | list:
        url = f"{self.base_url}{path}"
        try:
            response = httpx.request(
                method, url, headers={**self._headers(), **kwargs.pop("headers", {})},
                timeout=timeout, **kwargs
            )
        except httpx.HTTPError as exc:
            raise PostizError(f"{what} failed: could not reach Postiz at {url} ({exc}).") from exc
        return _check(response, what)

    # --- endpoints ---
    def integrations(self) -> list[PostizIntegration]:
        """Every account connected in the workspace this key belongs to."""
        payload = self._request("GET", "/integrations", "Listing Postiz channels")
        return [_integration(row) for row in _as_list(payload, "integrations")]

    def upload(self, filename: str, data: bytes, mime: str) -> dict:
        """Upload one media file and return `{"id": ..., "path": ...}`.

        Postiz posts reference media by id; sending bytes inline hits its 50 MB
        request ceiling, so an image or video is always uploaded first."""
        payload = self._request(
            "POST",
            "/upload",
            "Uploading media to Postiz",
            timeout=UPLOAD_TIMEOUT,
            files={"file": (filename, data, mime or "application/octet-stream")},
        )
        media = payload if isinstance(payload, dict) else (payload[0] if payload else {})
        if not media.get("id") and not media.get("path"):
            raise PostizError("Postiz accepted the upload but returned no media reference.")
        return {"id": media.get("id"), "path": media.get("path")}

    def create_post(
        self,
        *,
        integration_id: str,
        content: str,
        settings: dict,
        images: list[dict] | None = None,
        when: datetime | None = None,
        tags: list[str] | None = None,
        short_link: bool = False,
    ) -> dict:
        """Queue one post for one integration.

        `when=None` means Postiz's `now` - which is "into the queue now", not
        "on the platform now". The caller records the difference."""
        post: dict = {
            "integration": {"id": integration_id},
            "value": [{"content": content, **({"image": images} if images else {})}],
            "settings": settings,
        }
        body = {
            "type": "now" if when is None else "schedule",
            # Postiz validates `date` on both types, so one is always sent.
            "date": _iso(when or datetime.now(timezone.utc)),
            "shortLink": short_link,
            "tags": tags or [],
            "posts": [post],
        }
        payload = self._request(
            "POST", "/posts", "Creating the Postiz post", json=body,
            headers={"Content-Type": "application/json"},
        )
        created = _as_list(payload, "posts")
        first = created[0] if created else (payload if isinstance(payload, dict) else {})
        post_id = first.get("id") or first.get("postId") or first.get("group")
        if not post_id:
            raise PostizError("Postiz accepted the post but returned no post id.")
        return {
            "id": str(post_id),
            "release_url": first.get("releaseURL") or first.get("releaseUrl"),
            "raw": first,
        }

    def posts(self, **params) -> list[dict]:
        """Recent posts. Postiz has changed this endpoint's required query
        params across versions (week/year, then a date range), so the shapes are
        tried in turn and the first that answers wins."""
        attempts: list[dict] = [params] if params else []
        now = datetime.now(timezone.utc)
        iso_year, iso_week, _ = now.isocalendar()
        attempts += [
            {"display": "week", "week": iso_week, "year": iso_year, "month": now.month, "day": now.day},
            {"week": iso_week, "year": iso_year},
            {},
        ]
        last: PostizError | None = None
        for attempt in attempts:
            try:
                payload = self._request("GET", "/posts", "Listing Postiz posts", params=attempt)
            except PostizError as exc:
                last = exc
                continue
            return [row for row in _as_list(payload, "posts") if isinstance(row, dict)]
        raise last or PostizError("Postiz returned no posts and no error.")

    def delete_post(self, post_id: str) -> None:
        self._request("DELETE", f"/posts/{post_id}", "Deleting the Postiz post")


# -------------------------------------------------------------------- adapter


class PostizAdapter(ChannelAdapter):
    """Delivers one channel's post through a Postiz workspace.

    Real, not simulated: a Publication recorded here corresponds to a post
    Postiz has accepted for a real account. What it is *not* is confirmed
    live - see `status`.
    """

    simulated = False

    def __init__(
        self,
        *,
        channel: str,
        client: PostizClient,
        integration_id: str,
        identifier: str,
        account_name: str | None = None,
        settings_override: dict | None = None,
        on_error: Callable[[str], None] | None = None,
        on_success: Callable[[], None] | None = None,
    ):
        self.channel = channel
        self.client = client
        self.integration_id = integration_id
        self.identifier = identifier
        self.account_name = account_name
        self.settings_override = settings_override or {}
        self.on_error = on_error
        self.on_success = on_success

    def distribute(self, db: Session, org: Organization, engagement: dict) -> Publication:
        try:
            return self._distribute(db, org, engagement)
        except PostizError as exc:
            if self.on_error:
                self.on_error(str(exc))
            raise

    def _distribute(self, db: Session, org: Organization, engagement: dict) -> Publication:
        title = (engagement.get("title") or "").strip()
        text = post_text(engagement, limit=CHANNEL_TEXT_LIMITS.get(self.channel))

        settings = {
            **default_settings(self.channel, self.identifier, title),
            **self.settings_override,
        }
        missing = missing_required_settings(self.channel, settings)
        if missing:
            raise PostizError(
                f"{self.channel} needs {', '.join(missing)} set on the Postiz channel "
                "before Engage AI can post to it."
            )

        images = self._upload_media(db, engagement)
        if not images and self.channel in MEDIA_REQUIRED_CHANNELS:
            raise PostizError(
                f"{self.channel} will not accept a post without an image or video. "
                "Render the media for this piece first, then publish."
            )

        when = engagement.get("scheduled_for")
        created = self.client.create_post(
            integration_id=self.integration_id,
            content=text,
            settings=settings,
            images=images or None,
            when=when,
        )

        if self.on_success:
            self.on_success()

        status = "scheduled" if when else "queued"
        url = created["release_url"] or _fallback_url(org, self.channel)
        if created["release_url"]:
            status = "published"

        where = f" to {self.account_name}" if self.account_name else ""
        label = (
            f"{self.channel} post via Postiz{where}"
            + (f", scheduled {when:%Y-%m-%d %H:%M} UTC" if when else " (queued)")
            + (f": {title}" if title else "")
        )

        return self._record_publication(
            db,
            org,
            url=url,
            label=label,
            content_item_id=engagement.get("content_item_id"),
            delivery="postiz",
            external_id=created["id"],
            status=status,
        )

    def _upload_media(self, db: Session, engagement: dict) -> list[dict]:
        asset_id = engagement.get("media_asset_id")
        if not asset_id:
            return []
        asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
        if asset is None:
            return []
        extension = "mp4" if asset.kind == "video" else (asset.mime or "image/png").split("/")[-1]
        uploaded = self.client.upload(
            f"engage-ai-{asset.id}.{extension}", asset.data, asset.mime
        )
        return [{k: v for k, v in uploaded.items() if v}]


# -------------------------------------------------------------------- helpers


def post_text(engagement: dict, limit: int | None = None) -> str:
    """The text that goes out: body plus hashtags, trimmed to the channel's
    ceiling. Mirrors `live.post_text` so a piece reads identically whether it
    goes direct or through Postiz."""
    content = engagement.get("content")
    if isinstance(content, dict):
        body = content.get("body") or content.get("text") or ""
        hashtags = content.get("hashtags") or []
    else:
        body = str(content or "")
        hashtags = []

    body = (body or "").strip() or (engagement.get("title") or "").strip()
    if hashtags:
        tags = " ".join(f"#{str(tag).lstrip('#')}" for tag in hashtags)
        body = f"{body}\n\n{tags}".strip()
    if limit and len(body) > limit:
        body = body[: limit - 1].rstrip() + "…"
    return body


def _fallback_url(org: Organization, channel: str) -> str:
    """A queued post has no permalink yet. Point at the account it will appear
    on when one is on file, rather than inventing a post URL that 404s."""
    detail = (org.channel_details or {}).get(channel)
    if detail and str(detail).startswith("http"):
        return str(detail)
    return f"https://{channel.replace('_', '')}.example/pending"


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _integration(row: dict) -> PostizIntegration:
    identifier = (row.get("identifier") or row.get("providerIdentifier") or "").lower()
    return PostizIntegration(
        id=str(row.get("id") or ""),
        name=row.get("name") or identifier or "Unnamed",
        identifier=identifier,
        channel=POSTIZ_IDENTIFIER_TO_CHANNEL.get(identifier),
        picture=row.get("picture"),
        disabled=bool(row.get("disabled")),
        profile=row.get("profile"),
    )


def _as_list(payload, key: str) -> list:
    """Postiz returns a bare list on some versions and `{"<key>": [...]}` on
    others. Both are read; anything else yields an empty list rather than a
    TypeError deep in a loop."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for candidate in (key, "data", "results"):
            value = payload.get(candidate)
            if isinstance(value, list):
                return value
        if payload:
            return [payload]
    return []


def _check(response: httpx.Response, what: str) -> dict | list:
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code >= 400:
        raise PostizError(f"{what} failed: {_error_text(response, payload)}")
    return payload


def _error_text(response: httpx.Response, payload) -> str:
    if response.status_code == 401:
        return "Postiz rejected the API key (401). Check the key in Postiz > Settings."
    if response.status_code == 403:
        return "That Postiz API key does not own this resource (403)."
    if response.status_code == 429:
        return (
            "Postiz rate limit reached (90 posts/hour per instance). "
            "Schedule the post instead of sending it now, or raise API_LIMIT on the instance."
        )
    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, list) and value:
                return "; ".join(str(item) for item in value)
    return f"HTTP {response.status_code}: {response.text[:200]}"
