"""Website distribution: publishes as a WordPress DRAFT, never live.

This is the one reversible channel in DISTRIBUTABLE_CHANNELS - the draft URL
carries an `engage_ai_draft` query marker so it's unambiguous that nothing
went live. There is no real WordPress API call here (no network per the
constraints of this layer); the URL is deterministically constructed so
downstream code/tests can rely on it.
"""

from sqlalchemy.orm import Session

from app.models.entities import Organization, Publication

from .base import ChannelAdapter, slugify

DEFAULT_WEBSITE_BASE = "https://example.org"


class WebsiteAdapter(ChannelAdapter):
    """Publishes an engagement to the org's website as a WordPress draft."""

    channel = "website"
    # No real WordPress API call happens here (see module docstring) - the draft
    # URL is constructed, nothing is actually posted - so this is reported as
    # simulated too, until a real WP-API-backed adapter is registered.
    simulated = True

    def distribute(self, db: Session, org: Organization, engagement: dict) -> Publication:
        base = (org.website_url or DEFAULT_WEBSITE_BASE).rstrip("/")
        slug = slugify(engagement.get("title", ""))
        url = f"{base}/?engage_ai_draft={slug}"
        label = f"WP draft: {engagement.get('title', '')}"
        content_item_id = engagement.get("content_item_id")
        return self._record_publication(
            db, org, url=url, label=label, content_item_id=content_item_id
        )
