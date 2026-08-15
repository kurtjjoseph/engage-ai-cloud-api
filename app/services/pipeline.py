"""Where every piece of work currently is, and what each stage is waiting on.

Each page in the plugin has an in-queue (work that has arrived and needs this
stage to act) and an out-queue (work this stage finished and handed on). This
computes both.

Deliberately DERIVED, not stored. The obvious implementation is a queue table
with a row per handoff, and it is the wrong one here: it would be a second
record of where something is, able to disagree with the thing itself. A piece
whose queue row says "ready to publish" while its payload has no body is worse
than no queue at all, and this codebase has already been bitten once by two
copies of the same truth drifting apart. Everything below is read from the item
itself, so it cannot go stale and needs no backfill for the work that already
exists.

"Nothing is lost" is enforced by construction rather than by hope:
`content_position()` is total - every ContentItem maps to exactly one position,
and anything matching no known state lands in `unrouted` instead of falling
through. `unrouted` is surfaced in the UI rather than swallowed, so a piece in a
state nobody anticipated shows up as a visible anomaly instead of silently
ceasing to exist. The counts are asserted to reconcile in tests.
"""
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models.entities import Campaign, ContentItem, Idea, Publication, PublicationSnapshot

# Positions a piece of content can occupy. Order matters: it is the order the
# work actually flows in, and the UI reads them in this sequence.
NEEDS_CHECK = "needs_check"
NEEDS_MEDIA = "needs_media"
READY = "ready"
PUBLISHED = "published"
UNROUTED = "unrouted"

CONTENT_POSITIONS = (NEEDS_CHECK, NEEDS_MEDIA, READY, PUBLISHED, UNROUTED)

# A surface that renders an image or video is not finished until the render is
# done; a text-only piece has no media step to wait on.
_MEDIA_DONE = ("done",)


def _studio_state(item: ContentItem) -> dict:
    state = (item.output_payload or {}).get("studio")
    return state if isinstance(state, dict) else {}


def _wants_media(item: ContentItem, state: dict) -> bool:
    """True when this piece is supposed to have a rendered image or video.

    Read from the piece itself rather than from a format lookup: a piece that
    already carries a render block wants media by definition, and one whose
    payload asks for an image prompt or a video plan wants it too. A format
    table would be a second opinion about the same piece.
    """
    if state.get("render"):
        return True
    output = item.output_payload or {}
    if output.get("image_prompt") or output.get("video_plan"):
        return True
    return (output.get("media") or "text") not in ("text", "")


def content_position(item: ContentItem, published_ids: set[int]) -> str:
    """The single position this piece occupies. Total by construction - every
    input returns exactly one of CONTENT_POSITIONS, and UNROUTED is the honest
    answer for a shape this function does not recognise."""
    if item.id in published_ids:
        return PUBLISHED

    state = _studio_state(item)
    output = item.output_payload or {}

    # Nothing written at all - not a draft yet, whatever else is true of it.
    if not state and not output:
        return UNROUTED

    if not state.get("quality"):
        return NEEDS_CHECK

    if _wants_media(item, state):
        render = state.get("render") or {}
        if render.get("status") not in _MEDIA_DONE:
            return NEEDS_MEDIA

    return READY


def _campaign_entries(campaigns: list[Campaign]) -> list[dict]:
    out = []
    for campaign in campaigns:
        for entry in (campaign.plan or []):
            if isinstance(entry, dict):
                out.append({**entry, "campaign_id": campaign.id, "campaign_name": campaign.name})
    return out


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (ValueError, TypeError, AttributeError):
        return None


def build_pipeline(db: Session, org_id: int, today: date | None = None) -> dict:
    """Every stage's in-queue, out-queue and anything stuck, for one org."""
    today = today or date.today()

    items: list[ContentItem] = (
        db.query(ContentItem).filter(ContentItem.organization_id == org_id).all()
    )
    publications: list[Publication] = (
        db.query(Publication).filter(Publication.organization_id == org_id).all()
    )
    campaigns: list[Campaign] = (
        db.query(Campaign)
        .filter(Campaign.organization_id == org_id, Campaign.status != "archived")
        .all()
    )
    ideas: list[Idea] = db.query(Idea).filter(Idea.organization_id == org_id).all()

    published_ids = {p.content_item_id for p in publications if p.content_item_id}
    scanned_pub_ids = {
        pid for (pid,) in db.query(PublicationSnapshot.publication_id)
        .filter(PublicationSnapshot.publication_id.in_([p.id for p in publications] or [0]))
        .distinct()
        .all()
    }

    by_position: dict[str, list[ContentItem]] = {p: [] for p in CONTENT_POSITIONS}
    for item in items:
        by_position[content_position(item, published_ids)].append(item)

    entries = _campaign_entries(campaigns)
    planned = [e for e in entries if e.get("status") in (None, "", "planned")]
    drafted = [e for e in entries if e.get("status") == "drafted"]
    failed = [e for e in entries if e.get("status") == "failed"]
    undated = [e for e in entries if _as_date(e.get("scheduled_on")) is None]
    # Scheduled for a day that has passed, with nothing written. The single most
    # useful thing this whole endpoint reports: work that quietly did not happen.
    overdue = [
        e for e in entries
        if (d := _as_date(e.get("scheduled_on"))) is not None
        and d < today
        and e.get("status") != "drafted"
    ]
    upcoming = [
        e for e in entries
        if (d := _as_date(e.get("scheduled_on"))) is not None and d >= today
    ]

    unscanned = [p for p in publications if p.id not in scanned_pub_ids]
    scanned = [p for p in publications if p.id in scanned_pub_ids]

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "stages": {
            "ideas": _stage(
                inbox=[_idea(i) for i in ideas if i.status == "kept"],
                outbox=[_idea(i) for i in ideas if i.status == "drafted"],
                stuck=[],
                inbox_label="waiting to be written",
                outbox_label="written",
            ),
            "campaigns": _stage(
                inbox=[_entry(e) for e in planned],
                outbox=[_entry(e) for e in drafted],
                stuck=[_entry(e) for e in failed],
                inbox_label="planned, not written",
                outbox_label="written",
                stuck_label="failed to build",
            ),
            "studio": _stage(
                inbox=[_item(i) for i in by_position[NEEDS_CHECK] + by_position[NEEDS_MEDIA]],
                outbox=[_item(i) for i in by_position[READY]],
                stuck=[_item(i) for i in by_position[UNROUTED]],
                inbox_label="needs checking or media",
                outbox_label="ready to publish",
                stuck_label="in an unrecognised state",
            ),
            "library": _stage(
                inbox=[_item(i) for i in by_position[READY]],
                outbox=[_item(i) for i in by_position[PUBLISHED]],
                stuck=[_item(i) for i in by_position[UNROUTED]],
                inbox_label="made, not published",
                outbox_label="published",
                stuck_label="in an unrecognised state",
            ),
            "calendar": _stage(
                inbox=[_entry(e) for e in upcoming],
                outbox=[_entry(e) for e in drafted],
                stuck=[_entry(e) for e in overdue] + [_entry(e) for e in undated],
                inbox_label="scheduled ahead",
                outbox_label="written",
                stuck_label="overdue or undated",
            ),
            "channels": _stage(
                inbox=[_item(i) for i in by_position[READY]],
                outbox=[_publication(p) for p in publications],
                stuck=[],
                inbox_label="ready, not sent",
                outbox_label="sent",
            ),
            "performance": _stage(
                inbox=[_publication(p) for p in unscanned],
                outbox=[_publication(p) for p in scanned],
                stuck=[],
                inbox_label="published, never measured",
                outbox_label="measured",
            ),
        },
        # The reconciliation: every content item sits in exactly one position.
        # If `total` ever stops equalling the sum, something has been lost and
        # the UI says so rather than showing a plausible-looking subset.
        "reconciliation": {
            "content_total": len(items),
            "by_position": {p: len(by_position[p]) for p in CONTENT_POSITIONS},
            "accounted_for": sum(len(v) for v in by_position.values()),
        },
    }


def _stage(inbox, outbox, stuck, inbox_label, outbox_label, stuck_label="") -> dict:
    # Lists are capped in the payload but the counts are not, so a page can say
    # "47 waiting" while only rendering the first handful.
    return {
        "in": {"count": len(inbox), "label": inbox_label, "items": inbox[:20]},
        "out": {"count": len(outbox), "label": outbox_label, "items": outbox[:20]},
        "stuck": {"count": len(stuck), "label": stuck_label, "items": stuck[:20]},
    }


def _item(item: ContentItem) -> dict:
    state = _studio_state(item)
    return {
        "kind": "content",
        "id": item.id,
        "title": item.title,
        "channel": state.get("channel") or (item.output_payload or {}).get("channel"),
        "campaign_id": item.campaign_id,
    }


def _idea(idea: Idea) -> dict:
    return {"kind": "idea", "id": idea.id, "title": idea.title, "channel": idea.channel}


def _entry(entry: dict) -> dict:
    return {
        "kind": "campaign_entry",
        "campaign_id": entry.get("campaign_id"),
        "campaign_name": entry.get("campaign_name"),
        "index": entry.get("index"),
        "title": entry.get("headline") or entry.get("role"),
        "channel": entry.get("channel"),
        "scheduled_on": entry.get("scheduled_on"),
        "content_id": entry.get("content_id"),
    }


def _publication(pub: Publication) -> dict:
    return {
        "kind": "publication",
        "id": pub.id,
        "title": pub.label or pub.url,
        "channel": pub.channel,
        "content_id": pub.content_item_id,
    }
