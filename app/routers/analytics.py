from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import AnalyticsSnapshot, Organization, User
from app.routers.organizations import get_owned_org
from app.schemas import AnalyticsSnapshotOut
from app.services.analytics_scoring import score_channel, score_org
from app.services.analytics_search import KNOWN_CHANNELS, AnalyticsSearchService

router = APIRouter(prefix="/organizations/{org_id}/analytics", tags=["analytics"])

search_service = AnalyticsSearchService()


def get_analytics_enabled_org(org_id: int, db: Session, user: User) -> Organization:
    org = get_owned_org(org_id, db, user)
    if "analytics" not in (org.enabled_modules or []):
        raise HTTPException(
            status_code=403,
            detail=f"The 'analytics' module is not enabled for this organization. Enable it via PATCH /organizations/{org_id}/modules first.",
        )
    return org


def _org_context(org: Organization) -> dict:
    return {
        "name": org.name,
        "org_type": org.org_type,
        "website_url": org.website_url,
        "mission": org.mission,
        "audience": org.audience,
        "locations": org.locations,
    }


@router.post("/scan", response_model=AnalyticsSnapshotOut)
def run_scan(
    org_id: int,
    channels: list[str] | None = Query(None, description=f"Scope the scan to specific channels: {', '.join(KNOWN_CHANNELS)}. Omit for the full sweep."),
    include_pages: bool = Query(False, description="Adds a per-page visibility ranking to the website channel. Only applies when 'website' is in scope. Costs more (more searches, bigger response)."),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Runs one web-search-based scan now. The first scan for an org is
    flagged as its baseline; every later scan is meant to be compared
    against that fixed reference point."""
    org = get_analytics_enabled_org(org_id, db, user)

    valid_channels = [c for c in channels if c in KNOWN_CHANNELS] if channels else None
    if channels and not valid_channels:
        raise HTTPException(status_code=400, detail=f"None of the requested channels are recognized. Valid channels: {', '.join(KNOWN_CHANNELS)}")

    result = search_service.scan(_org_context(org), channels=valid_channels, include_pages=include_pages)

    scored_channels = []
    channel_scores: dict[str, int] = {}
    for entry in result.get("channels", []):
        score, breakdown = score_channel(entry.get("channel"), entry.get("kpis"))
        channel_scores[entry.get("channel")] = score
        scored_channels.append({**entry, "score": score, "score_breakdown": breakdown})

    # An org score built from a channel-scoped scan would silently treat every
    # unchecked channel as 0 (score_org's "missing = 0" rule, correct for a full
    # sweep, misleading here) - only a full sweep has enough data to represent
    # the whole org, so a scoped scan just doesn't get an org_score at all.
    if valid_channels is None:
        org_score, org_breakdown = score_org(channel_scores)
    else:
        org_score, org_breakdown = None, None

    is_first = (
        db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.organization_id == org.id).first() is None
    )

    snapshot = AnalyticsSnapshot(
        organization_id=org.id,
        is_baseline=is_first,
        summary=result.get("summary"),
        channels=scored_channels,
        org_score=org_score,
        org_score_breakdown=org_breakdown,
        sources=result.get("sources", []),
        requested_channels=valid_channels,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


@router.get("", response_model=list[AnalyticsSnapshotOut])
def list_snapshots(org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_analytics_enabled_org(org_id, db, user)
    return (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.organization_id == org_id)
        .order_by(AnalyticsSnapshot.created_at.desc())
        .all()
    )
