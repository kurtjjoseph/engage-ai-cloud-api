"""Per-channel authentication providers.

One entry per channel Engage AI can be authorized to post on. Each entry knows
four things: where to send the admin to approve (authorize_url), how to turn
the returned code into tokens (exchange_code), how to keep those tokens alive
(refresh_access_token), and how to work out *which account* the authorization
actually grants - the Page, Instagram business account, LinkedIn member,
YouTube channel or Business Profile location Engage AI will post as
(resolve_account).

Two things are deliberate here:

* A provider with no client id/secret configured is not an error. It reports
  `configured = False` and the channel falls back to the manual long-lived
  token path (see routers/channel_connections.py), so an org can connect a
  channel today without waiting on a platform app review.
* Nothing in this module writes to the database or decides whether a post may
  go out. It only handles identity. Authorization is a separate question from
  consent to publish (ChannelConnection.auto_post).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx

from app.config import settings

# Facebook Graph API version used for every Meta call (auth + posting), pinned
# so a Graph deprecation is a deliberate bump rather than a surprise.
GRAPH_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

HTTP_TIMEOUT = 30.0


class ProviderError(RuntimeError):
    """A provider call failed - message is safe to show an admin."""


@dataclass(frozen=True)
class ChannelProvider:
    """Everything needed to authenticate one channel."""

    channel: str
    label: str
    # Identity provider behind the channel: "facebook" backs facebook +
    # instagram, "google" backs youtube + google_business.
    provider: str
    authorize_endpoint: str
    token_endpoint: str
    scopes: list[str]
    # Which settings.* pair holds this provider's app credentials.
    client_id_setting: str
    client_secret_setting: str
    # PKCE is required by X and recommended by Google; Meta/LinkedIn are left
    # on plain authorization-code + client secret, which is what their docs
    # describe for confidential server-side clients.
    use_pkce: bool = False
    # Extra static params some providers need on the authorize call, e.g.
    # Google's offline access (without it there is no refresh token at all).
    extra_authorize_params: dict = field(default_factory=dict)
    # Some token endpoints want the client credentials as HTTP Basic (X)
    # rather than form fields.
    token_auth_basic: bool = False
    # What the admin has to paste when this provider has no app configured.
    manual_token_hint: str = "A long-lived access token for this account."
    # Whether the channel can carry an image/video with the post at all.
    supports_media: bool = True

    @property
    def client_id(self) -> str | None:
        return getattr(settings, self.client_id_setting, None)

    @property
    def client_secret(self) -> str | None:
        return getattr(settings, self.client_secret_setting, None)

    @property
    def configured(self) -> bool:
        """True when this deployment has an OAuth app registered for the
        provider. False means: manual token only."""
        return bool(self.client_id and self.client_secret)

    @property
    def redirect_uri(self) -> str:
        return f"{settings.api_base_url.rstrip('/')}/channels/callback/{self.channel}"


PROVIDERS: dict[str, ChannelProvider] = {
    "facebook": ChannelProvider(
        channel="facebook",
        label="Facebook Page",
        provider="facebook",
        authorize_endpoint=f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth",
        token_endpoint=f"{GRAPH_BASE}/oauth/access_token",
        scopes=[
            "pages_show_list",
            "pages_manage_posts",
            "pages_read_engagement",
            "business_management",
        ],
        client_id_setting="facebook_client_id",
        client_secret_setting="facebook_client_secret",
        manual_token_hint=(
            "A Page access token with pages_manage_posts (Graph API Explorer > "
            "your Page > Generate token, then extend it to a long-lived token)."
        ),
    ),
    "instagram": ChannelProvider(
        channel="instagram",
        label="Instagram",
        provider="facebook",
        authorize_endpoint=f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth",
        token_endpoint=f"{GRAPH_BASE}/oauth/access_token",
        scopes=[
            "instagram_basic",
            "instagram_content_publish",
            "pages_show_list",
            "business_management",
        ],
        client_id_setting="facebook_client_id",
        client_secret_setting="facebook_client_secret",
        manual_token_hint=(
            "A Page access token for the Page your Instagram business account "
            "is linked to, with instagram_content_publish."
        ),
    ),
    "linkedin": ChannelProvider(
        channel="linkedin",
        label="LinkedIn",
        provider="linkedin",
        authorize_endpoint="https://www.linkedin.com/oauth/v2/authorization",
        token_endpoint="https://www.linkedin.com/oauth/v2/accessToken",
        scopes=["openid", "profile", "w_member_social"],
        client_id_setting="linkedin_client_id",
        client_secret_setting="linkedin_client_secret",
        manual_token_hint="A LinkedIn access token with the w_member_social scope.",
    ),
    "youtube": ChannelProvider(
        channel="youtube",
        label="YouTube",
        provider="google",
        authorize_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
        ],
        client_id_setting="google_client_id",
        client_secret_setting="google_client_secret",
        use_pkce=True,
        extra_authorize_params={"access_type": "offline", "prompt": "consent"},
        manual_token_hint="A Google OAuth access token with the youtube.upload scope.",
    ),
    "google_business": ChannelProvider(
        channel="google_business",
        label="Google Business Profile",
        provider="google",
        authorize_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/business.manage"],
        client_id_setting="google_client_id",
        client_secret_setting="google_client_secret",
        use_pkce=True,
        extra_authorize_params={"access_type": "offline", "prompt": "consent"},
        manual_token_hint="A Google OAuth access token with the business.manage scope.",
    ),
    "twitter_x": ChannelProvider(
        channel="twitter_x",
        label="X (Twitter)",
        provider="twitter_x",
        authorize_endpoint="https://x.com/i/oauth2/authorize",
        token_endpoint="https://api.x.com/2/oauth2/token",
        scopes=["tweet.read", "tweet.write", "users.read", "offline.access"],
        client_id_setting="twitter_client_id",
        client_secret_setting="twitter_client_secret",
        use_pkce=True,
        token_auth_basic=True,
        manual_token_hint="An X OAuth 2.0 user access token with tweet.write.",
        # X media upload needs the v1.1 endpoint and its own OAuth 1.0a
        # signing, which this integration doesn't carry - posts go out as text.
        supports_media=False,
    ),
}

# Channels that can be authenticated for posting. "website" is intentionally
# absent: the WordPress plugin already posts to the site with its own
# credentials, so there is nothing to authorize here.
AUTHENTICATABLE_CHANNELS = list(PROVIDERS)


def get_provider(channel: str) -> ChannelProvider:
    provider = PROVIDERS.get(channel)
    if provider is None:
        raise ProviderError(
            f"'{channel}' can't be authenticated for posting. "
            f"Connectable channels: {', '.join(AUTHENTICATABLE_CHANNELS)}."
        )
    return provider


# --------------------------------------------------------------- PKCE + state


def new_state() -> str:
    return secrets.token_urlsafe(32)


def new_code_verifier() -> str:
    return secrets.token_urlsafe(64)[:128]


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def build_authorize_url(provider: ChannelProvider, state: str, code_verifier: str | None) -> str:
    """The URL to send the admin's browser to, to approve Engage AI."""
    if not provider.configured:
        raise ProviderError(
            f"No {provider.label} app is configured on this Engage AI deployment "
            f"({provider.client_id_setting}/{provider.client_secret_setting} are unset), "
            "so there is nothing to redirect to. Paste a long-lived token instead."
        )
    params = {
        "client_id": provider.client_id,
        "redirect_uri": provider.redirect_uri,
        "response_type": "code",
        "scope": " ".join(provider.scopes),
        "state": state,
        **provider.extra_authorize_params,
    }
    if provider.use_pkce and code_verifier:
        params["code_challenge"] = code_challenge(code_verifier)
        params["code_challenge_method"] = "S256"
    return f"{provider.authorize_endpoint}?{urlencode(params)}"


# ------------------------------------------------------------------- tokens


def _token_request(provider: ChannelProvider, data: dict) -> dict:
    headers = {"Accept": "application/json"}
    if provider.token_auth_basic:
        basic = base64.b64encode(
            f"{provider.client_id}:{provider.client_secret}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {basic}"
    else:
        data = {**data, "client_id": provider.client_id, "client_secret": provider.client_secret}

    try:
        response = httpx.post(
            provider.token_endpoint, data=data, headers=headers, timeout=HTTP_TIMEOUT
        )
    except httpx.HTTPError as exc:
        raise ProviderError(f"Could not reach {provider.label}: {exc}") from exc

    payload = _json_or_empty(response)
    if response.status_code >= 400:
        raise ProviderError(f"{provider.label} rejected the token request: {_error_text(payload, response)}")
    token = payload.get("access_token")
    if not token:
        raise ProviderError(f"{provider.label} returned no access token.")
    return payload


def exchange_code(provider: ChannelProvider, code: str, code_verifier: str | None) -> dict:
    """Authorization code -> token payload
    ({access_token, refresh_token?, expires_in?, scope?})."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": provider.redirect_uri,
    }
    if provider.use_pkce and code_verifier:
        data["code_verifier"] = code_verifier
    payload = _token_request(provider, data)

    # Meta hands back a short-lived user token; exchanging it here means a
    # connection survives more than an hour without the admin noticing.
    if provider.provider == "facebook":
        payload = {**payload, **_extend_facebook_token(provider, payload["access_token"])}
    return payload


def refresh_access_token(provider: ChannelProvider, refresh_token: str) -> dict:
    """Refresh-token grant. Providers without refresh tokens never get here -
    the caller only calls this when one was stored."""
    return _token_request(
        provider, {"grant_type": "refresh_token", "refresh_token": refresh_token}
    )


def _extend_facebook_token(provider: ChannelProvider, short_lived: str) -> dict:
    """Swap a short-lived Meta user token for the ~60-day long-lived one.
    Best-effort: if the exchange fails, the short-lived token still works for
    now, and the connection will simply report an earlier expiry."""
    try:
        response = httpx.get(
            provider.token_endpoint,
            params={
                "grant_type": "fb_exchange_token",
                "client_id": provider.client_id,
                "client_secret": provider.client_secret,
                "fb_exchange_token": short_lived,
            },
            timeout=HTTP_TIMEOUT,
        )
    except httpx.HTTPError:
        return {}
    payload = _json_or_empty(response)
    if response.status_code >= 400 or not payload.get("access_token"):
        return {}
    return payload


# ---------------------------------------------------------------- identity


def resolve_account(provider: ChannelProvider, access_token: str) -> dict:
    """Work out which account this token posts as.

    Returns {account_id, account_name, account_url, target, access_token} -
    `target` is the provider-specific posting destination stored on the
    connection, and `access_token` may be REPLACED (Meta: the user token is
    swapped for the Page token, which is what actually posts).

    Doubles as the health check behind POST .../verify: it's a real call to
    the provider with the stored credentials, so a revoked or expired token
    fails here rather than at publish time.
    """
    if provider.provider == "facebook":
        return _resolve_meta(provider, access_token)
    if provider.channel == "linkedin":
        return _resolve_linkedin(access_token)
    if provider.channel == "youtube":
        return _resolve_youtube(access_token)
    if provider.channel == "google_business":
        return _resolve_google_business(access_token)
    if provider.channel == "twitter_x":
        return _resolve_x(access_token)
    raise ProviderError(f"No identity check implemented for {provider.channel}.")


def _get_json(url: str, token: str, params: dict | None = None, headers: dict | None = None) -> dict:
    try:
        response = httpx.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}", **(headers or {})},
            timeout=HTTP_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ProviderError(f"Could not reach the provider: {exc}") from exc
    payload = _json_or_empty(response)
    if response.status_code >= 400:
        raise ProviderError(_error_text(payload, response))
    return payload


def _resolve_meta(provider: ChannelProvider, access_token: str) -> dict:
    """Meta grants a *user* token; posting happens as a Page. Pick the first
    Page the token administers (and, for Instagram, the business account
    linked to it), and keep the PAGE token as the credential."""
    pages = _get_json(
        f"{GRAPH_BASE}/me/accounts",
        access_token,
        params={"fields": "id,name,access_token,link,instagram_business_account{id,username}"},
    ).get("data") or []

    if provider.channel == "instagram":
        for page in pages:
            ig = page.get("instagram_business_account") or {}
            if ig.get("id"):
                username = ig.get("username")
                return {
                    "account_id": ig["id"],
                    "account_name": f"@{username}" if username else page.get("name"),
                    "account_url": f"https://www.instagram.com/{username}" if username else None,
                    "target": {"ig_user_id": ig["id"], "page_id": page.get("id")},
                    "access_token": page.get("access_token") or access_token,
                }
        raise ProviderError(
            "None of the Pages this account administers has an Instagram business "
            "account linked. Link the Instagram account to the Page in Meta Business "
            "Suite, then connect again."
        )

    if not pages:
        raise ProviderError(
            "This account doesn't administer any Facebook Page, so there is nowhere "
            "to post. Grant access to the Page and connect again."
        )
    page = pages[0]
    return {
        "account_id": page.get("id"),
        "account_name": page.get("name"),
        "account_url": page.get("link") or f"https://www.facebook.com/{page.get('id')}",
        "target": {"page_id": page.get("id")},
        "access_token": page.get("access_token") or access_token,
    }


def _resolve_linkedin(access_token: str) -> dict:
    me = _get_json("https://api.linkedin.com/v2/userinfo", access_token)
    subject = me.get("sub")
    if not subject:
        raise ProviderError("LinkedIn didn't identify the authorizing member.")
    return {
        "account_id": subject,
        "account_name": me.get("name"),
        "account_url": None,
        "target": {"author_urn": f"urn:li:person:{subject}"},
        "access_token": access_token,
    }


def _resolve_youtube(access_token: str) -> dict:
    items = _get_json(
        "https://www.googleapis.com/youtube/v3/channels",
        access_token,
        params={"part": "snippet", "mine": "true"},
    ).get("items") or []
    if not items:
        raise ProviderError("This Google account has no YouTube channel.")
    channel = items[0]
    title = (channel.get("snippet") or {}).get("title")
    return {
        "account_id": channel.get("id"),
        "account_name": title,
        "account_url": f"https://www.youtube.com/channel/{channel.get('id')}",
        "target": {"channel_id": channel.get("id")},
        "access_token": access_token,
    }


def _resolve_google_business(access_token: str) -> dict:
    accounts = _get_json(
        "https://mybusinessaccountmanagement.googleapis.com/v1/accounts", access_token
    ).get("accounts") or []
    if not accounts:
        raise ProviderError("This Google account manages no Business Profile.")
    account = accounts[0]
    locations = _get_json(
        f"https://mybusinessbusinessinformation.googleapis.com/v1/{account['name']}/locations",
        access_token,
        params={"readMask": "name,title"},
    ).get("locations") or []
    if not locations:
        raise ProviderError(
            "This Business Profile has no locations, so there is nowhere to post."
        )
    location = locations[0]
    return {
        "account_id": location.get("name"),
        "account_name": location.get("title") or account.get("accountName"),
        "account_url": None,
        # v4 localPosts wants the account-qualified location resource name.
        "target": {"location": f"{account['name']}/{location['name']}"},
        "access_token": access_token,
    }


def _resolve_x(access_token: str) -> dict:
    data = _get_json("https://api.x.com/2/users/me", access_token).get("data") or {}
    if not data.get("id"):
        raise ProviderError("X didn't identify the authorizing account.")
    username = data.get("username")
    return {
        "account_id": data["id"],
        "account_name": f"@{username}" if username else data.get("name"),
        "account_url": f"https://x.com/{username}" if username else None,
        "target": {"user_id": data["id"]},
        "access_token": access_token,
    }


# ------------------------------------------------------------------ helpers


def _json_or_empty(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {"data": payload}


def _error_text(payload: dict, response: httpx.Response) -> str:
    """Providers disagree on where the message lives; try each shape, then
    fall back to the raw body, truncated so a stray HTML error page doesn't
    end up in an admin notice."""
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("error_description")
        if message:
            return str(message)
    for key in ("error_description", "message", "detail", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return f"HTTP {response.status_code}: {response.text[:200]}"
