import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.session import SessionLocal, get_db
from app.deps import get_current_user
from app.models.entities import AnalyticsSnapshot, ContentItem, Organization, Publication, PublicationSnapshot, User
from app.routers.organizations import get_owned_org
from app.schemas import AnalyticsInsightsOut, AnalyticsSnapshotOut, ChannelRankingEntry, EngagementTypeRankingEntry
from app.config import settings
from app.services.analytics_insights import compute_insights
from app.services.analytics_reconcile import reconcile_channels
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


def build_request_context(org: Organization, requested_channels: list[str] | None, include_pages: bool) -> dict:
    """Everything that goes into the scan request, captured at attempt time for
    the per-scan details page - so an operator can see exactly what data the
    model was given (org context + pinned channel handles), which channels were
    asked for, and the model/tool used."""
    # If no Anthropic key is configured, the scan can't actually research
    # anything - it returns empty/stub data. Declare that here so no consumer
    # mistakes a keyless stub run for a real measurement.
    stubbed = search_service.client is None
    ctx = {
        "org_context": _org_context(org),
        "model": settings.anthropic_model,
        "requested_channels": requested_channels,  # None = full 8-channel sweep
        "resolved_channels": list(requested_channels) if requested_channels else list(KNOWN_CHANNELS),
        "include_pages": include_pages,
        "tool": "web_search_20250305",
        "mode": "per-channel parallel web search",
        "stubbed": stubbed,
    }
    if stubbed:
        ctx["stub_reason"] = "ANTHROPIC_API_KEY not configured - no real web research was performed"
    return ctx


def _scored_not_found(channel: str) -> dict:
    """A genuine not-found channel entry (score 0), used when a per-channel
    scan returns nothing or fails - reconciliation may still hold last-known
    over it if the channel had a real prior value."""
    score, breakdown = score_channel(channel, {})
    return {"channel": channel, "kpis": {}, "notes": "", "score": score, "score_breakdown": breakdown}


def _deterministic_summary(final_channels: list[dict], org_score: int | None) -> str:
    """Build the snapshot summary in code from the scored channels, instead of
    an extra LLM call. Free, instant, and always consistent with the numbers."""
    ranked = sorted(final_channels, key=lambda e: e.get("score", 0), reverse=True)
    parts = [f"Org score {org_score}." if org_score is not None else "Channel scan complete."]
    if ranked:
        parts.append(f"Strongest: {ranked[0]['channel']} ({ranked[0].get('score', 0)}).")
    gaps = [e["channel"] for e in final_channels if e.get("score", 0) == 0]
    if gaps:
        parts.append(f"White space (no presence found): {', '.join(gaps)}.")
    held = [e["channel"] for e in final_channels if e.get("stale")]
    if held:
        parts.append(f"Carried forward (not re-found this scan): {', '.join(held)}.")
    return " ".join(parts)


def _execute_scan(snapshot_id: int, org_context: dict, channels: list[str] | None, include_pages: bool) -> None:
    """Scans each channel with its own parallel web-search call and streams the
    scored result onto the pending snapshot as it lands, so the dashboard fills
    in channel-by-channel over a few seconds instead of waiting 30-90s for one
    sequential mega-call to finish. When every channel is in, a final pass
    reconciles against the prior sweep (hold-last-known, anomaly flags) and marks
    the snapshot complete.

    Called from a FastAPI background task (or the scheduler), so it opens its own
    DB session - the per-channel Anthropic calls are DB-free and run in a thread
    pool, while all DB writes happen here on the main thread. Any unexpected
    error records the snapshot "failed" rather than leaving it "pending"."""
    db = SessionLocal()
    try:
        snapshot = db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.id == snapshot_id).first()
        if snapshot is None:
            return

        started_at = time.monotonic()
        try:
            is_full_sweep = channels is None
            target_channels = list(channels) if channels else list(KNOWN_CHANNELS)

            def scan_one(channel: str):
                # Reuses the scoped-scan primitive: scan() with a single channel
                # uses the small per-channel budget (services/analytics_search.py).
                res = search_service.scan(
                    org_context, channels=[channel], include_pages=(include_pages and channel == "website")
                )
                entries = res.get("channels", [])
                return entries[0] if entries else None, res.get("sources", []) or []

            scored_by_channel: dict[str, dict] = {}
            all_sources: set[str] = set()
            errors = 0

            with ThreadPoolExecutor(max_workers=min(8, len(target_channels))) as pool:
                futures = {pool.submit(scan_one, ch): ch for ch in target_channels}
                for future in as_completed(futures):
                    channel = futures[future]
                    try:
                        entry, sources = future.result()
                    except Exception as exc:  # noqa: BLE001 - one channel failing must not sink the scan
                        print(f"[analytics] snapshot {snapshot.id} channel {channel} failed: {exc}", flush=True)
                        entry, sources, errors = None, [], errors + 1
                    if entry is None:
                        scored = _scored_not_found(channel)
                    else:
                        score, breakdown = score_channel(entry.get("channel"), entry.get("kpis"))
                        scored = {**entry, "score": score, "score_breakdown": breakdown}
                    # Per-channel sources (the URLs the model drew on for THIS
                    # channel) - part of "all data used", shown on the details page.
                    if sources:
                        scored["sources"] = sorted(set(sources))
                    scored_by_channel[channel] = scored
                    all_sources |= set(sources)
                    # Stream progress: reassigning the JSON list marks it dirty so
                    # this partial state is committed and any reader polling the
                    # in-flight snapshot sees the radar fill in.
                    snapshot.channels = list(scored_by_channel.values())
                    snapshot.sources = sorted(all_sources)
                    db.commit()

            if errors == len(target_channels):
                # Every channel's call raised (not merely "found nothing") - the
                # research layer is broken this run; fail loudly instead of
                # writing an all-zero snapshot that looks like total white space.
                raise RuntimeError(f"All {errors} per-channel scans failed - not recording as complete.")

            scored_channels = list(scored_by_channel.values())

            # Reconcile against the org's most recent prior full sweep: a channel
            # the web search failed to *find* this run (score 0) that had a real
            # value last time is held forward (marked stale) instead of recorded
            # as a fabricated 0 that cliffs the trend - and big swings get flagged
            # for review. See services/analytics_reconcile.py.
            # Most recent prior COMPLETE full sweep. Filter requested_channels in
            # Python, not SQL: the JSON column stores None as JSON 'null', not SQL
            # NULL, so `.is_(None)` never matches - same reason analytics_insights.py
            # filters full sweeps with `not s.requested_channels`.
            recent_prior = (
                db.query(AnalyticsSnapshot)
                .filter(
                    AnalyticsSnapshot.organization_id == snapshot.organization_id,
                    AnalyticsSnapshot.id != snapshot.id,
                )
                .order_by(AnalyticsSnapshot.created_at.desc())
                .limit(20)
                .all()
            )
            prior = next(
                (s for s in recent_prior if not s.requested_channels and s.status not in ("pending", "failed")),
                None,
            )
            prior_by_channel = {e.get("channel"): e for e in (prior.channels or [])} if prior else {}
            final_channels, needs_review = reconcile_channels(
                scored_channels,
                prior_by_channel,
                prior.created_at if prior else None,
                is_full_sweep,
                settings.analytics_anomaly_delta,
            )

            # An org score built from a channel-scoped scan would silently treat
            # every unchecked channel as 0 (score_org's "missing = 0" rule,
            # correct for a full sweep, misleading here) - only a full sweep has
            # enough data to represent the whole org, so a scoped scan just
            # doesn't get an org_score at all. The org score is computed from the
            # RECONCILED channels, so a held value keeps the average from cliffing.
            if is_full_sweep:
                org_score, org_breakdown = score_org({e["channel"]: e.get("score", 0) for e in final_channels})
            else:
                org_score, org_breakdown = None, None

            snapshot.summary = _deterministic_summary(final_channels, org_score)
            snapshot.channels = final_channels
            snapshot.org_score = org_score
            snapshot.org_score_breakdown = org_breakdown
            snapshot.sources = sorted(all_sources)
            snapshot.needs_review = needs_review
            snapshot.duration_seconds = round(time.monotonic() - started_at, 1)
            snapshot.status = "complete"
            # Same print-to-Render-logs convention as analytics_search.py -
            # without this, everything after "Claude call finished" is
            # silent, so "did the snapshot actually get written?" can't be
            # answered from logs when someone reports stale analytics.
            held = [e["channel"] for e in final_channels if e.get("stale")]
            print(
                f"[analytics] snapshot {snapshot.id} complete: org_score={org_score}, "
                f"{len(final_channels)} channels, needs_review={needs_review}, "
                f"held_last_known={held or '-'}, summary={str(snapshot.summary)[:500]!r}",
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
    """Starts a scan and returns immediately with a "pending" snapshot. The
    scan runs each channel as its own parallel web-search call and streams the
    scored result onto the snapshot as it lands, so a client polling
    GET .../analytics/snapshot/{id} sees the radar fill in channel-by-channel
    over a few seconds (rather than waiting on one 30-90s all-or-nothing call).
    Meanwhile the dashboard should keep showing the last COMPLETE snapshot from
    /insights, so the view is never blank. Status leaves "pending" once every
    channel is in and the final reconcile has run. The first scan for an org is
    its baseline; later scans are compared against it."""
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
        request_context=build_request_context(org, valid_channels, include_pages),
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


@router.get("/snapshot/{snapshot_id}", response_model=AnalyticsSnapshotOut)
def get_snapshot(org_id: int, snapshot_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """One snapshot by id - for polling a scan in progress. While a scan runs,
    its `channels` list grows as each channel's parallel sub-scan lands (status
    stays "pending" until the final reconcile), so a client can render the radar
    filling in live instead of waiting on a single all-or-nothing result."""
    get_analytics_enabled_org(org_id, db, user)
    snapshot = (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.id == snapshot_id, AnalyticsSnapshot.organization_id == org_id)
        .first()
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found for this organization.")
    return snapshot


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
