from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import AnalyticsSnapshot, ContentItem, Organization, Publication, PublicationSnapshot, User
from app.routers.organizations import get_owned_org
from app.schemas import AnalyticsInsightsOut, AnalyticsSnapshotOut, ChannelRankingEntry, EngagementTypeRankingEntry
from app.services.analytics_insights import compute_insights
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


@router.get("/insights", response_model=AnalyticsInsightsOut)
def get_insights(org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The org score, a channel ranking (best to worst), and a white_space /
    saturated / growing / healthy / new classification per channel - the
    single endpoint the WordPress dashboard reads for the "how is each
    channel doing" view. Classification needs trend, so it's only computed
    from FULL-SWEEP snapshots (a channel-scoped scan not checking a channel
    isn't the same as that channel going flat)."""
    get_analytics_enabled_org(org_id, db, user)

    insights = compute_insights(db, org_id)
    if insights is None:
        raise HTTPException(status_code=404, detail="No full-sweep scans yet - run one via POST .../analytics/scan (no channels param) first.")

    return AnalyticsInsightsOut(
        **{**insights, "ranking": [ChannelRankingEntry(**r) for r in insights["ranking"]]},
    )


@router.get("/engagement-type-ranking", response_model=list[EngagementTypeRankingEntry])
def get_engagement_type_ranking(org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Which KIND of content performs best - a sermon clip vs. an event
    announcement vs. a devotional post - averaged across every scanned
    Publication of that content_type, regardless of which channel it went
    out on. Answers "what should we make more of," a different question
    than the channel ranking above ("where should we post"). Only
    Publications linked back to a ContentItem carry a content_type, so
    standalone Publications (not generated by Engage AI) aren't counted."""
    get_analytics_enabled_org(org_id, db, user)

    rows = (
        db.query(ContentItem.content_type, Publication.id)
        .join(Publication, Publication.content_item_id == ContentItem.id)
        .filter(Publication.organization_id == org_id)
        .all()
    )
    if not rows:
        return []

    pub_ids = [pub_id for _, pub_id in rows]
    latest_scores: dict[int, int | None] = {}
    snapshots = (
        db.query(PublicationSnapshot)
        .filter(PublicationSnapshot.publication_id.in_(pub_ids))
        .order_by(PublicationSnapshot.scanned_at.desc())
        .all()
    )
    for snap in snapshots:
        latest_scores.setdefault(snap.publication_id, snap.score)

    by_type: dict[str, dict] = {}
    for content_type, pub_id in rows:
        bucket = by_type.setdefault(content_type, {"scores": [], "publication_count": 0})
        bucket["publication_count"] += 1
        score = latest_scores.get(pub_id)
        if score is not None:
            bucket["scores"].append(score)

    ranking = [
        EngagementTypeRankingEntry(
            content_type=content_type,
            avg_score=round(sum(b["scores"]) / len(b["scores"]), 1) if b["scores"] else 0.0,
            publication_count=b["publication_count"],
            scanned_publication_count=len(b["scores"]),
        )
        for content_type, b in by_type.items()
    ]
    ranking.sort(key=lambda r: r.avg_score, reverse=True)
    return ranking
