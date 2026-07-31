"""Channel -> adapter lookup, with per-organization live adapters.

Resolution order for a channel, most specific first:

1. an adapter explicitly installed at runtime via register_adapter()
2. the org's LIVE adapter, when it has authenticated that channel for posting
   (services/channels/live.py) - this is what makes a post real
3. the default simulated/draft adapter, which only records where a post would
   have gone

Step 2 is why get_adapter() takes a db/org: whether a post is real is a fact
about one organization's authorizations, not a global switch. An org that has
connected nothing behaves exactly as it did before this layer existed.
"""

from sqlalchemy.orm import Session

from app.models.entities import Organization, Publication

from .base import ChannelAdapter, DISTRIBUTABLE_CHANNELS
from .connections import can_post, get_connection
from .social import SimulatedSocialAdapter, SOCIAL_CHANNELS
from .website import WebsiteAdapter

_REGISTRY: dict[str, ChannelAdapter] = {
    "website": WebsiteAdapter(),
    **{channel: SimulatedSocialAdapter(channel=channel) for channel in SOCIAL_CHANNELS},
}

# Explicit runtime overrides, kept apart from the defaults above so an
# override always outranks the automatic live/simulated choice.
_OVERRIDES: dict[str, ChannelAdapter] = {}


def get_adapter(
    channel: str,
    db: Session | None = None,
    org: Organization | None = None,
    require_auto_post: bool = False,
) -> ChannelAdapter:
    """Return the adapter to use for `channel`.

    Pass db+org to get the organization's live, API-backed adapter when it has
    authenticated the channel. `require_auto_post` is for unattended paths (the
    engagement cycle): it holds the live adapter back unless the org explicitly
    turned on autonomous posting for that channel, so authorizing a channel
    never by itself starts publishing without a human.

    Raises ValueError if `channel` isn't one of DISTRIBUTABLE_CHANNELS (or has
    no adapter registered for it)."""
    if channel not in DISTRIBUTABLE_CHANNELS or channel not in _REGISTRY:
        raise ValueError(
            f"Channel {channel!r} is not a distributable channel. "
            f"Expected one of {DISTRIBUTABLE_CHANNELS}."
        )

    if channel in _OVERRIDES:
        return _OVERRIDES[channel]

    live = _live_adapter(channel, db, org, require_auto_post)
    if live is not None:
        return live

    return _REGISTRY[channel]


def _live_adapter(
    channel: str, db: Session | None, org: Organization | None, require_auto_post: bool
) -> ChannelAdapter | None:
    if db is None or org is None:
        return None
    # Imported here rather than at module scope: live.py pulls in the provider
    # layer, and none of that needs to load for a caller that only ever uses
    # the simulated adapters (tests, offline environments).
    from .live import LIVE_ADAPTERS, live_adapter_for
    from .providers import ProviderError

    if channel not in LIVE_ADAPTERS:
        return None
    connection = get_connection(db, org.id, channel)
    if not can_post(connection):
        return None
    if require_auto_post and not connection.auto_post:
        return None
    try:
        return live_adapter_for(connection)
    except ProviderError:
        return None


def register_adapter(channel: str, adapter: ChannelAdapter) -> None:
    """Override the adapter used for `channel` everywhere, for every
    organization (e.g. to pin a test double). Outranks live connections."""
    _OVERRIDES[channel] = adapter


def unregister_adapter(channel: str) -> None:
    """Drop an override installed by register_adapter()."""
    _OVERRIDES.pop(channel, None)


def distribute_engagement(
    db: Session, org: Organization, engagement: dict, require_auto_post: bool = False
) -> Publication:
    """Look up the adapter for engagement["channel"] and distribute it,
    returning the created Publication."""
    adapter = get_adapter(
        engagement["channel"], db=db, org=org, require_auto_post=require_auto_post
    )
    return adapter.distribute(db, org, engagement)
