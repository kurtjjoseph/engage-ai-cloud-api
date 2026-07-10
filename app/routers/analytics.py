from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.session import SessionLocal, get_db
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
        "channel_details": org.channel_details,
        "mission": org.mission,
        "audience": org.audience,
        "locations": org.locations,
    }


def _execute_scan(snapshot_id: int, org_context: dict, channels: list[str] | None, include_pages: bool) -> None:
    """Runs the actual Claude call, scores the result, and writes it onto the
    already-created pending snapshot. Called from a FastAPI background task,
    so it opens its own DB session (the request-scoped one is long gone by
    the time this runs) - same pattern as services/scheduler.py's background
    agent-cycle job. Any exception here is caught and recorded as a "failed"
    snapshot rather than left "pending" forever."""
    db = SessionLocal()
    try:
        snapshot = db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.id == snapshot_id).first()
        if snapshot is None:
            return

        try:
            result = search_service.scan(org_context, channels=channels, include_pages=include_pages)

            scored_channels = []
            channel_scores: dict[str, int] = {}
            for entry in result.get("channels", []):
                score, breakdown = score_channel(entry.get("channel"), entry.get("kpis"))
                channel_scores[entry.get("channel")] = score
                scored_channels.append({**entry, "score": score, "score_breakdown": breakdown})

            # An org score built from a channel-scoped scan would silently treat
            # every unchecked channel as 0 (score_org's "missing = 0" rule,
            # correct for a full sweep, misleading here) - only a full sweep has
            # enough data to represent the whole org, so a scoped scan just
            # doesn't get an org_score at all.
            if channels is None:
                org_score, org_breakdown = score_org(channel_scores)
            else:
                org_score, org_breakdown = None, None

            snapshot.summary = result.get("summary")
            snapshot.channels = scored_channels
            snapshot.org_score = org_score
            snapshot.org_score_breakdown = org_breakdown
            snapshot.sources = result.get("sources", [])
            snapshot.status = "complete"
            # Same print-to-Render-logs convention as analytics_search.py -
            # without this, everything after "Claude call finished" is
            # silent, so "did the snapshot actually get written?" can't be
            # answered from logs when someone reports stale analytics.
            print(
                f"[analytics] snapshot {snapshot.id} complete: org_score={org_score}, "
                f"{len(scored_channels)} channels, summary={str(snapshot.summary)[:120]!r}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - deliberately broad: this must never leave the snapshot stuck "pending"
            snapshot.status = "failed"
            snapshot.summary = f"Scan failed: {exc}"
            print(f"[analytics] snapshot {snapshot.id} FAILED: {exc}", flush=True)

        db.commit()
    finally:
        db.close()


def reap_stale_pending_snapshots() -> None:
    """BackgroundTasks don't survive a process restart - a scan in flight
    when a deploy lands dies silently, leaving its snapshot "pending"
    forever, which the plugin renders as a permanent "Scan in progress".
    Deploys here happen many times a day, so this isn't hypothetical. Called
    on startup (main.py) - by definition every pending snapshot older than a
    scan could plausibly still be running is orphaned. The 10-minute grace
    covers the brief deploy overlap where the outgoing instance may still
    finish a young scan (its later "complete" write simply overrides this)."""
    from datetime import datetime, timedelta

    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=10)
        stale = (
            db.query(AnalyticsSnapshot)
            .filter(AnalyticsSnapshot.status == "pending", AnalyticsSnapshot.created_at < cutoff)
            .all()
        )
        for snapshot in stale:
            snapshot.status = "failed"
            snapshot.summary = "Scan was interrupted (most likely a deploy restarted the API mid-scan) - run a new scan."
            print(f"[analytics] reaped stale pending snapshot {snapshot.id} (created {snapshot.created_at})", flush=True)
        if stale:
            db.commit()
    finally:
        db.close()


@router.post("/scan", response_model=AnalyticsSnapshotOut, status_code=202)
def run_scan(
    org_id: int,
    background_tasks: BackgroundTasks,
    channels: list[str] | None = Query(None, description=f"Scope the scan to specific channels: {', '.join(KNOWN_CHANNELS)}. Omit for the full sweep."),
    include_pages: bool = Query(False, description="Adds a per-page visibility ranking to the website channel. Only applies when 'website' is in scope. Costs more (more searches, bigger response)."),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Starts one web-search-based scan and returns immediately with a
    "pending" snapshot - the scan itself (a Claude call with web_search/
    web_fetch tool use) routinely takes 30-90s+, too long to hold an HTTP
    request open for. Poll GET .../analytics (or /insights, once a full
    sweep completes) and watch for this snapshot's status to leave
    "pending". The first scan for an org is flagged as its baseline; every
    later scan is meant to be compared against that fixed reference point."""
    org = get_analytics_enabled_org(org_id, db, user)

    valid_channels = [c for c in channels if c in KNOWN_CHANNELS] if channels else None
    if channels and not valid_channels:
        raise HTTPException(status_code=400, detail=f"None of the requested channels are recognized. Valid channels: {', '.join(KNOWN_CHANNELS)}")

    is_first = (
        db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.organization_id == org.id).first() is None
    )

    snapshot = AnalyticsSnapshot(
        organization_id=org.id,
        is_baseline=is_first,
        requested_channels=valid_channels,
        status="pending",
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    background_tasks.add_task(_execute_scan, snapshot.id, _org_context(org), valid_channels, include_pages)

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
