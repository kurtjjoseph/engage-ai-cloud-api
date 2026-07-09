from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import AnalyticsSnapshot, Organization, User
from app.routers.organizations import get_owned_org
from app.schemas import AnalyticsInsightsOut, AnalyticsSnapshotOut, ChannelRankingEntry
from app.services.analytics_scoring import classify_channel_trend, score_channel, score_org
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

    # SQLAlchemy's JSON columns store Python None as the JSON literal `null`
    # (text), not SQL NULL, so a DB-level `.is_(None)`/`== None` filter never
    # matches - filtering in Python after a bounded fetch sidesteps that
    # JSON-NULL-vs-SQL-NULL dialect quirk entirely instead of fighting it.
    recent = (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.organization_id == org_id)
        .order_by(AnalyticsSnapshot.created_at.desc())
        .limit(20)
        .all()
    )
    full_sweeps = [s for s in recent if not s.requested_channels][:6]
    if not full_sweeps:
        raise HTTPException(status_code=404, detail="No full-sweep scans yet - run one via POST .../analytics/scan (no channels param) first.")

    latest = full_sweeps[0]
    older = list(reversed(full_sweeps[1:]))  # oldest-first, for classify_channel_trend's "previous_scores"

    def prior_scores(channel: str) -> list[int]:
        scores = []
        for snap in older:
            for entry in snap.channels or []:
                if entry.get("channel") == channel and entry.get("score") is not None:
                    scores.append(entry["score"])
        return scores

    ranking = []
    for entry in latest.channels or []:
        channel = entry.get("channel")
        score = entry.get("score", 0)
        ranking.append({
            "channel": channel,
            "score": score,
            "classification": classify_channel_trend(score, prior_scores(channel)),
            "score_breakdown": entry.get("score_breakdown", []),
            "notes": entry.get("notes"),
        })
    ranking.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(ranking):
        r["rank"] = i + 1

    baseline = db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.organization_id == org_id, AnalyticsSnapshot.is_baseline.is_(True)).first()

    return AnalyticsInsightsOut(
        latest_snapshot_id=latest.id,
        latest_created_at=latest.created_at,
        org_score=latest.org_score,
        org_score_breakdown=latest.org_score_breakdown,
        baseline_org_score=baseline.org_score if baseline else None,
        ranking=[ChannelRankingEntry(**r) for r in ranking],
        summary=latest.summary,
    )
