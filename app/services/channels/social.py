"""Simulated distribution for the social channels.

These channels are autonomous - no human approval gate lives in this layer,
that happens upstream (ticket approval) - but this adapter is SIMULATED: the
service has no real credentials for Facebook/Instagram/LinkedIn/YouTube/
Twitter-X, so instead of calling a real API it deterministically records
*where* a post would have gone, based on the org's known channel_details
handle/URL (falling back to a synthetic example.com-style URL when no handle
is on file).

A real, API-backed adapter for any one of these channels can be swapped in
at runtime without touching this module or the registry defaults, via:

    from app.services.channels import register_adapter
    register_adapter("facebook", RealFacebookAdapter())
"""

from sqlalchemy.orm import Session

from app.models.entities import Organization, Publication

from .base import ChannelAdapter, DISTRIBUTABLE_CHANNELS, slugify

# The social subset of DISTRIBUTABLE_CHANNELS (everything but "website").
SOCIAL_CHANNELS = [c for c in DISTRIBUTABLE_CHANNELS if c != "website"]


class SimulatedSocialAdapter(ChannelAdapter):
    """Records a simulated post for one social channel.

    Marked `simulated = True` so callers/tests can distinguish it from a
    real API-backed adapter registered in its place via register_adapter().
    """

    simulated = True

    def __init__(self, channel: str):
        self.channel = channel

    def distribute(self, db: Session, org: Organization, engagement: dict) -> Publication:
        title = engagement.get("title", "")
        slug = slugify(title)
        detail = (org.channel_details or {}).get(self.channel)

        if detail:
            base = detail if detail.startswith("http") else (
                f"https://{self.channel}.example/{detail.lstrip('@')}"
            )
            url = f"{base.rstrip('/')}/{slug}"
        else:
            org_slug = slugify(org.name)
            url = f"https://{self.channel}.example/{org_slug}/{slug}"

        label = f"Simulated {self.channel} post: {title}"
        content_item_id = engagement.get("content_item_id")
        return self._record_publication(
            db, org, url=url, label=label, content_item_id=content_item_id
        )
