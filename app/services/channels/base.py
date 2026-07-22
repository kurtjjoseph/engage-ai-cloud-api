"""Shared contract for channel-distribution adapters.

An "Engagement" is the unit of work an adapter distributes. It is a plain
dict (not an ORM model) with the following shape:

    {
        "channel": str,            # one of DISTRIBUTABLE_CHANNELS
        "type": str,                # e.g. "post", "update", "announcement"
        "title": str,                # human-readable title, used to derive slugs/labels
        "content": dict | str,      # the generated content payload/body
        "risk": "low" | "high",     # informational - gating happens upstream of this layer
        "source_ticket_id": int | None,  # Ticket.id this engagement came from, if any
    }

Adapters in this package don't enforce the human-approval gate themselves -
by the time distribute() is called, the engagement is assumed to already be
approved. Adapters are only responsible for getting the (possibly simulated)
content to the channel and recording the result as a Publication.
"""

import re
from abc import ABC, abstractmethod
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.entities import Organization, Publication

# Channels an engagement can be autonomously distributed to. "website" is the
# only reversible one - it is published as a WordPress DRAFT, never live.
DISTRIBUTABLE_CHANNELS = [
    "website",
    "facebook",
    "instagram",
    "linkedin",
    "youtube",
    "twitter_x",
]


def slugify(title: str) -> str:
    """Deterministic slug: lowercase, run(s) of non-alphanumeric -> single '-',
    trimmed of leading/trailing '-'. Empty/whitespace-only titles slugify to
    "untitled" so callers always get a non-empty slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return slug or "untitled"


class ChannelAdapter(ABC):
    """Base class for a single channel's distribution adapter.

    Subclasses set a class attribute `channel` (one of DISTRIBUTABLE_CHANNELS,
    or the specific channel a given instance handles) and implement
    distribute() to actually push the engagement out and return the
    Publication record created for it.
    """

    channel: str
    # Whether this adapter actually delivers to the real channel, or only
    # records where it *would* have gone (no real API call). The API reports
    # this on every Publication (Publication.simulated) so nothing simulated is
    # ever mistaken for a real, live post. Real API-backed adapters set False.
    simulated: bool = False

    @abstractmethod
    def distribute(self, db: Session, org: Organization, engagement: dict) -> Publication:
        """Distribute `engagement` for `org` and return the created Publication."""
        raise NotImplementedError

    def _record_publication(
        self,
        db: Session,
        org: Organization,
        url: str,
        label: str | None,
        content_item_id: int | None = None,
    ) -> Publication:
        """Create, commit, and refresh a Publication row for this distribution."""
        publication = Publication(
            organization_id=org.id,
            content_item_id=content_item_id,
            channel=self.channel,
            url=url,
            label=label,
            simulated=self.simulated,
            published_at=datetime.utcnow(),
        )
        db.add(publication)
        db.commit()
        db.refresh(publication)
        return publication
