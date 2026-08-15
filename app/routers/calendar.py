"""One view of what goes out when, across every campaign and channel.

The dates already existed - a campaign plan spreads its pieces over a window and
stamps each entry with `scheduled_on` (services/campaign.py). What did not exist
was any way to see all of them at once: a campaign could only be read one
campaign at a time, so "what is going out next week, and on which channels?" had
no answer, and neither did "we said three Instagram posts a week and there are
none planned."

This aggregates rather than schedules. Nothing here publishes anything, and no
date written here causes a post to go out - delivery stays with the operator's
explicit publish, or with Postiz where that is connected. Moving a date is
editing a campaign plan, which is `PATCH /campaigns/{id}/items/{index}`, not a
calendar write.
"""
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import Campaign, User
from app.routers.organizations import get_owned_org
from app.schemas import PostingTargets

router = APIRouter(prefix="/organizations/{org_id}", tags=["calendar"])

# A campaign entry's status, mapped to whether the piece actually exists yet.
# "drafted" means there is a ContentItem; everything else is still just a plan.
WRITTEN_STATUSES = ("drafted",)


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (ValueError, TypeError, AttributeError):
        return None


@router.get("/calendar")
def get_calendar(
    org_id: int,
    start: str | None = Query(None, description="ISO date, defaults to today"),
    end: str | None = Query(None, description="ISO date, defaults to start + 27 days"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every planned piece in the window, plus how the week's volume compares
    to the org's posting targets.

    An entry with no readable `scheduled_on` is not silently dropped - it comes
    back under `undated`, because a piece nobody scheduled is exactly the thing
    an operator needs to see, and hiding it would make the calendar look
    complete when it is not.
    """
    org = get_owned_org(org_id, db, user)

    window_start = _as_date(start) or date.today()
    window_end = _as_date(end) or (window_start + timedelta(days=27))
    if window_end < window_start:
        raise HTTPException(status_code=400, detail="end must not be before start")

    campaigns = (
        db.query(Campaign)
        .filter(Campaign.organization_id == org_id, Campaign.status != "archived")
        .all()
    )

    items: list[dict] = []
    undated: list[dict] = []

    for campaign in campaigns:
        for entry in (campaign.plan or []):
            if not isinstance(entry, dict):
                continue
            record = {
                "campaign_id": campaign.id,
                "campaign_name": campaign.name,
                "index": entry.get("index"),
                "channel": entry.get("channel"),
                "surface": entry.get("surface"),
                "role": entry.get("role"),
                "headline": entry.get("headline"),
                "status": entry.get("status", "planned"),
                "content_id": entry.get("content_id"),
                "written": entry.get("status") in WRITTEN_STATUSES,
                "scheduled_on": None,
            }
            scheduled = _as_date(entry.get("scheduled_on"))
            if scheduled is None:
                undated.append(record)
                continue
            record["scheduled_on"] = scheduled.isoformat()
            if window_start <= scheduled <= window_end:
                items.append(record)

    items.sort(key=lambda r: (r["scheduled_on"], r["campaign_id"], r["index"] or 0))

    return {
        "start": window_start.isoformat(),
        "end": window_end.isoformat(),
        "items": items,
        "undated": undated,
        "by_channel": _per_channel_volume(items, window_start, window_end, org.posting_targets or {}),
    }


def _per_channel_volume(items: list[dict], start: date, end: date, targets: dict) -> list[dict]:
    """Planned volume against intent, per channel, normalised to a week.

    A channel with no target reports `target: null` and `shortfall: null` rather
    than zero - "you set no target" and "you set a target and missed it by
    everything" are different facts, and collapsing them would invent a
    shortfall the operator never asked for.
    """
    days = max((end - start).days + 1, 1)
    weeks = days / 7

    planned: dict[str, int] = defaultdict(int)
    for item in items:
        planned[item.get("channel") or "unassigned"] += 1

    channels = sorted(set(planned) | {c for c in targets if targets.get(c)})
    out = []
    for channel in channels:
        target_per_week = targets.get(channel)
        expected = round(target_per_week * weeks) if target_per_week else None
        count = planned.get(channel, 0)
        out.append({
            "channel": channel,
            "planned": count,
            "target_per_week": target_per_week,
            "expected_in_window": expected,
            "shortfall": max(expected - count, 0) if expected is not None else None,
        })
    return out


@router.get("/posting-targets")
def get_posting_targets(
    org_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org = get_owned_org(org_id, db, user)
    return {"targets": org.posting_targets or {}}


@router.put("/posting-targets")
def set_posting_targets(
    org_id: int,
    payload: PostingTargets,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Replaces the whole map. A channel set to 0 or below is removed rather
    than stored, so "no target" has exactly one representation and the calendar
    never has to decide whether a stored zero meant "none" or "never post"."""
    org = get_owned_org(org_id, db, user)

    cleaned = {
        str(channel): int(per_week)
        for channel, per_week in (payload.targets or {}).items()
        if isinstance(per_week, (int, float)) and int(per_week) > 0
    }
    org.posting_targets = cleaned
    db.commit()
    return {"targets": cleaned}
