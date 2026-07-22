"""Channel -> adapter lookup, with runtime override support.

The default registry wires each DISTRIBUTABLE_CHANNELS entry to its
simulated/draft adapter. A real, API-backed adapter can replace any of these
at runtime via register_adapter() without restarting the process or editing
this module.
"""

from sqlalchemy.orm import Session

from app.models.entities import Organization, Publication

from .base import ChannelAdapter, DISTRIBUTABLE_CHANNELS
from .social import SimulatedSocialAdapter, SOCIAL_CHANNELS
from .website import WebsiteAdapter

_REGISTRY: dict[str, ChannelAdapter] = {
    "website": WebsiteAdapter(),
    **{channel: SimulatedSocialAdapter(channel=channel) for channel in SOCIAL_CHANNELS},
}


def get_adapter(channel: str) -> ChannelAdapter:
    """Return the adapter currently registered for `channel`.

    Raises ValueError if `channel` isn't one of DISTRIBUTABLE_CHANNELS (or
    has no adapter registered for it)."""
    if channel not in DISTRIBUTABLE_CHANNELS or channel not in _REGISTRY:
        raise ValueError(
            f"Channel {channel!r} is not a distributable channel. "
            f"Expected one of {DISTRIBUTABLE_CHANNELS}."
        )
    return _REGISTRY[channel]


def register_adapter(channel: str, adapter: ChannelAdapter) -> None:
    """Override the adapter used for `channel` (e.g. to swap in a real,
    API-backed adapter in place of the default simulated one)."""
    _REGISTRY[channel] = adapter


def distribute_engagement(db: Session, org: Organization, engagement: dict) -> Publication:
    """Look up the adapter for engagement["channel"] and distribute it,
    returning the created Publication."""
    adapter = get_adapter(engagement["channel"])
    return adapter.distribute(db, org, engagement)
