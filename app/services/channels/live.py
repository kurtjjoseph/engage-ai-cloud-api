"""Real, API-backed distribution for the authenticated channels.

Each adapter here posts through the provider's own API using the credentials
on the org's ChannelConnection, and records the resulting live URL as a
Publication with `simulated = False` - the counterpart to the placeholder
adapters in social.py, which only record where a post *would* have gone.

Which one runs is decided per organization, not globally: registry.get_adapter()
returns a live adapter when the org has a healthy connection for the channel,
and the simulated one otherwise, so an org that has authorized nothing keeps
behaving exactly as it did before.

These adapters do not decide *whether* something may be published. They are
reached either because a human pressed publish for one specific piece, or
because the org turned on auto_post for the channel.
"""

from __future__ import annotations

import json
import secrets
import time

import httpx
from sqlalchemy.orm import Session

from app.models.entities import ChannelConnection, MediaAsset, Organization, Publication
from app.services.media_links import sign_media_url

from .base import ChannelAdapter
from .connections import access_token, mark_error, mark_used
from .providers import GRAPH_BASE, ProviderError, get_provider

POST_TIMEOUT = 60.0
# Instagram and YouTube process an uploaded video before it exists as a post;
# both are polled rather than assumed-good, so a failed encode surfaces as an
# error instead of a Publication pointing at nothing.
MEDIA_POLL_ATTEMPTS = 20
MEDIA_POLL_INTERVAL = 3.0


class PublishError(RuntimeError):
    """Posting failed - message is safe to show an admin."""


class LiveChannelAdapter(ChannelAdapter):
    """Base for the API-backed adapters.

    Bound to one ChannelConnection, so the token/target it posts with is
    unambiguous and one org's adapter can never be reused for another's.
    """

    simulated = False

    def __init__(self, connection: ChannelConnection):
        self.connection = connection
        self.channel = connection.channel

    # --- subclass contract ---
    def _publish(self, db: Session, org: Organization, engagement: dict, token: str) -> tuple[str, str]:
        """Post it. Returns (url, label)."""
        raise NotImplementedError

    def distribute(self, db: Session, org: Organization, engagement: dict) -> Publication:
        token = access_token(db, self.connection)  # raises ProviderError if unusable
        try:
            url, label = self._publish(db, org, engagement, token)
        except (PublishError, ProviderError) as exc:
            mark_error(db, self.connection, str(exc))
            raise
        mark_used(db, self.connection)
        return self._record_publication(
            db, org, url=url, label=label, content_item_id=engagement.get("content_item_id")
        )

    # --- shared helpers ---
    def _target(self, key: str) -> str:
        value = (self.connection.target or {}).get(key)
        if not value:
            raise PublishError(
                f"This {self.channel} connection doesn't know where to post "
                f"(missing '{key}'). Reconnect the channel."
            )
        return value

    def _media(self, db: Session, engagement: dict) -> MediaAsset | None:
        asset_id = engagement.get("media_asset_id")
        if not asset_id:
            return None
        return db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()


def post_text(engagement: dict, limit: int | None = None) -> str:
    """The text that actually goes out: the generated body plus its hashtags.

    Falls back through the shapes the generators produce (studio drafts store
    a dict, older content items sometimes a plain string) so no adapter has to
    care which pass created the piece."""
    content = engagement.get("content")
    if isinstance(content, dict):
        body = content.get("body") or content.get("text") or ""
        hashtags = content.get("hashtags") or []
    else:
        body = str(content or "")
        hashtags = []

    body = body.strip() or (engagement.get("title") or "").strip()
    if hashtags:
        tags = " ".join(f"#{tag.lstrip('#')}" for tag in hashtags)
        body = f"{body}\n\n{tags}".strip()
    if limit and len(body) > limit:
        body = body[: limit - 1].rstrip() + "…"
    return body


def _json_or_empty(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {"data": payload}


def _check(response: httpx.Response, what: str) -> dict:
    payload = _json_or_empty(response)
    if response.status_code >= 400:
        error = payload.get("error")
        message = None
        if isinstance(error, dict):
            message = error.get("message") or error.get("error_user_msg")
        message = message or payload.get("message") or payload.get("detail")
        raise PublishError(
            f"{what} failed: {message or f'HTTP {response.status_code} {response.text[:200]}'}"
        )
    return payload


def _request(method: str, url: str, what: str, **kwargs) -> httpx.Response:
    try:
        response = httpx.request(method, url, timeout=POST_TIMEOUT, **kwargs)
    except httpx.HTTPError as exc:
        raise PublishError(f"{what} failed: could not reach the provider ({exc}).") from exc
    _check(response, what)
    return response


# ---------------------------------------------------------------- Facebook


class FacebookPageAdapter(LiveChannelAdapter):
    """Publishes to the connected Facebook Page. An image goes out as a photo
    post (caption + picture in one item) rather than a link-less text post."""

    def _publish(self, db, org, engagement, token):
        page_id = self._target("page_id")
        message = post_text(engagement)
        asset = self._media(db, engagement)

        if asset and asset.kind == "image":
            payload = _json_or_empty(
                _request(
                    "POST",
                    f"{GRAPH_BASE}/{page_id}/photos",
                    "Facebook photo post",
                    data={"caption": message, "access_token": token},
                    files={"source": ("image", asset.data, asset.mime)},
                ),
            )
            post_id = payload.get("post_id") or payload.get("id")
        else:
            payload = _json_or_empty(
                _request(
                    "POST",
                    f"{GRAPH_BASE}/{page_id}/feed",
                    "Facebook post",
                    data={"message": message, "access_token": token},
                ),
            )
            post_id = payload.get("id")

        if not post_id:
            raise PublishError("Facebook accepted the post but returned no post id.")
        return f"https://www.facebook.com/{post_id}", f"Facebook post: {engagement.get('title', '')}"


# --------------------------------------------------------------- Instagram


class InstagramAdapter(LiveChannelAdapter):
    """Instagram's publishing API is two-step: create a media container from a
    publicly fetchable URL, then publish it. Media is mandatory - a caption
    with no image or video is not a thing Instagram can post."""

    def _publish(self, db, org, engagement, token):
        ig_user_id = self._target("ig_user_id")
        caption = post_text(engagement, limit=2200)
        asset = self._media(db, engagement)
        if asset is None:
            raise PublishError(
                "Instagram posts need an image or video. Generate the media for this "
                "piece first, then publish."
            )

        media_url = sign_media_url(asset.id)
        params = {"caption": caption, "access_token": token}
        if asset.kind == "video":
            params.update({"media_type": "REELS", "video_url": media_url})
        else:
            params["image_url"] = media_url

        container = _json_or_empty(
            _request("POST", f"{GRAPH_BASE}/{ig_user_id}/media", "Instagram upload", data=params),
        )
        creation_id = container.get("id")
        if not creation_id:
            raise PublishError("Instagram didn't return a media container id.")

        if asset.kind == "video":
            self._await_container(creation_id, token)

        published = _json_or_empty(
            _request(
                "POST",
                f"{GRAPH_BASE}/{ig_user_id}/media_publish",
                "Instagram publish",
                data={"creation_id": creation_id, "access_token": token},
            ),
        )
        media_id = published.get("id")
        if not media_id:
            raise PublishError("Instagram accepted the publish but returned no media id.")

        permalink = _json_or_empty(
            httpx.get(
                f"{GRAPH_BASE}/{media_id}",
                params={"fields": "permalink", "access_token": token},
                timeout=POST_TIMEOUT,
            )
        ).get("permalink")
        url = permalink or f"https://www.instagram.com/p/{media_id}"
        return url, f"Instagram post: {engagement.get('title', '')}"

    def _await_container(self, creation_id: str, token: str) -> None:
        """Video containers are transcoded asynchronously; publishing one that
        isn't FINISHED just fails, so wait for it."""
        for _ in range(MEDIA_POLL_ATTEMPTS):
            status = _json_or_empty(
                httpx.get(
                    f"{GRAPH_BASE}/{creation_id}",
                    params={"fields": "status_code,status", "access_token": token},
                    timeout=POST_TIMEOUT,
                )
            )
            code = status.get("status_code")
            if code == "FINISHED":
                return
            if code == "ERROR":
                raise PublishError(
                    f"Instagram couldn't process the video: {status.get('status') or 'unknown error'}."
                )
            time.sleep(MEDIA_POLL_INTERVAL)
        raise PublishError(
            "Instagram is still processing the video. It may still publish - check the "
            "account before trying again."
        )


# ---------------------------------------------------------------- LinkedIn


class LinkedInAdapter(LiveChannelAdapter):
    """Posts to the authorizing member's feed via the versioned Posts API."""

    API_VERSION = "202409"

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": self.API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }

    def _publish(self, db, org, engagement, token):
        author = self._target("author_urn")
        commentary = post_text(engagement, limit=3000)
        asset = self._media(db, engagement)

        body = {
            "author": author,
            "commentary": commentary,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

        if asset is not None and asset.kind == "image":
            image_urn = self._upload_image(author, asset, token)
            body["content"] = {"media": {"id": image_urn, "title": engagement.get("title", "")[:200]}}

        response = _request(
            "POST",
            "https://api.linkedin.com/rest/posts",
            "LinkedIn post",
            headers=self._headers(token),
            content=json.dumps(body),
        )
        # The created post's URN comes back in a header, not the (empty) body.
        post_urn = response.headers.get("x-restli-id") or response.headers.get("X-RestLi-Id")
        if not post_urn:
            raise PublishError("LinkedIn accepted the post but didn't return its id.")
        url = f"https://www.linkedin.com/feed/update/{post_urn}"
        return url, f"LinkedIn post: {engagement.get('title', '')}"

    def _upload_image(self, author: str, asset: MediaAsset, token: str) -> str:
        initialized = _json_or_empty(
            _request(
                "POST",
                "https://api.linkedin.com/rest/images?action=initializeUpload",
                "LinkedIn image upload",
                headers=self._headers(token),
                content=json.dumps({"initializeUploadRequest": {"owner": author}}),
            ),
        )
        value = initialized.get("value") or {}
        upload_url, image_urn = value.get("uploadUrl"), value.get("image")
        if not upload_url or not image_urn:
            raise PublishError("LinkedIn didn't return an upload target for the image.")

        _request(
            "PUT",
            upload_url,
            "LinkedIn image upload",
            headers={"Authorization": f"Bearer {token}"},
            content=asset.data,
        )
        return image_urn


# ----------------------------------------------------------------- YouTube


class YouTubeAdapter(LiveChannelAdapter):
    """Uploads the rendered video to the connected channel.

    Uploaded UNLISTED on purpose: an autonomous system putting a video in
    front of a congregation's subscribers unannounced is not a reversible
    action, and unlisted keeps the human able to flip it public."""

    PRIVACY_STATUS = "unlisted"

    def _publish(self, db, org, engagement, token):
        asset = self._media(db, engagement)
        if asset is None or asset.kind != "video":
            raise PublishError(
                "YouTube needs a video. Render the video for this piece first, then publish."
            )

        title = (engagement.get("title") or "Untitled").strip()[:100]
        description = post_text(engagement, limit=5000)
        metadata = {
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": self.PRIVACY_STATUS, "selfDeclaredMadeForKids": False},
        }

        boundary = f"engageai{secrets.token_hex(16)}"
        body = b"".join([
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
            json.dumps(metadata).encode(),
            f"\r\n--{boundary}\r\nContent-Type: {asset.mime or 'video/mp4'}\r\n\r\n".encode(),
            asset.data,
            f"\r\n--{boundary}--\r\n".encode(),
        ])

        payload = _json_or_empty(
            _request(
                "POST",
                "https://www.googleapis.com/upload/youtube/v3/videos"
                "?uploadType=multipart&part=snippet,status",
                "YouTube upload",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
                content=body,
            ),
        )
        video_id = payload.get("id")
        if not video_id:
            raise PublishError("YouTube accepted the upload but returned no video id.")
        return (
            f"https://www.youtube.com/watch?v={video_id}",
            f"YouTube upload ({self.PRIVACY_STATUS}): {title}",
        )


# ---------------------------------------------------------------- X/Twitter


class XAdapter(LiveChannelAdapter):
    """Posts text to X. Media isn't attached - X's upload endpoint needs OAuth
    1.0a signing this integration doesn't carry (providers.supports_media is
    False for the channel, and the UI says so before anyone renders an image)."""

    def _publish(self, db, org, engagement, token):
        text = post_text(engagement, limit=280)
        payload = _json_or_empty(
            _request(
                "POST",
                "https://api.x.com/2/tweets",
                "X post",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps({"text": text}),
            ),
        )
        post_id = (payload.get("data") or {}).get("id")
        if not post_id:
            raise PublishError("X accepted the post but returned no id.")
        handle = (self.connection.account_name or "i").lstrip("@")
        return f"https://x.com/{handle}/status/{post_id}", f"X post: {engagement.get('title', '')}"


# --------------------------------------------------- Google Business Profile


class GoogleBusinessAdapter(LiveChannelAdapter):
    """Creates a local post ("What's new") on the connected location."""

    def _publish(self, db, org, engagement, token):
        location = self._target("location")
        summary = post_text(engagement, limit=1500)
        asset = self._media(db, engagement)

        body: dict = {"languageCode": "en", "summary": summary, "topicType": "STANDARD"}
        if asset is not None and asset.kind == "image":
            body["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": sign_media_url(asset.id)}]

        payload = _json_or_empty(
            _request(
                "POST",
                f"https://mybusiness.googleapis.com/v4/{location}/localPosts",
                "Google Business post",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=json.dumps(body),
            ),
        )
        url = payload.get("searchUrl") or self.connection.account_url or "https://business.google.com/posts"
        return url, f"Google Business post: {engagement.get('title', '')}"


LIVE_ADAPTERS: dict[str, type[LiveChannelAdapter]] = {
    "facebook": FacebookPageAdapter,
    "instagram": InstagramAdapter,
    "linkedin": LinkedInAdapter,
    "youtube": YouTubeAdapter,
    "twitter_x": XAdapter,
    "google_business": GoogleBusinessAdapter,
}


def live_adapter_for(connection: ChannelConnection) -> LiveChannelAdapter:
    adapter_class = LIVE_ADAPTERS.get(connection.channel)
    if adapter_class is None:
        raise ProviderError(f"No live adapter for channel '{connection.channel}'.")
    # Validates the channel is one providers.py knows, so a hand-edited row
    # can't route a post somewhere unconfigured.
    get_provider(connection.channel)
    return adapter_class(connection)
