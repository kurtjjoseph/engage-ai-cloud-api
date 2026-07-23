from sqlalchemy.orm import Session
from app.models.entities import AnalyticsSnapshot
from app.services.analytics_scoring import (
    channel_availability,
    classify_channel_trend,
    content_pieces,
    score_org,
)


def compute_insights(db: Session, organization_id: int) -> dict | None:
    """Pure, framework-free version of the ranking/classification logic
    behind GET /analytics/insights - shared by that endpoint and the
    engagement_growth agent niche (services/cycle_engine.py), so both read
    the exact same numbers instead of two implementations drifting apart.
    Returns None if there's no full-sweep scan yet - callers decide how to
    handle that (404 for the API, a "run a baseline scan" ticket for the
    agent)."""
    recent = (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.organization_id == organization_id)
        .order_by(AnalyticsSnapshot.created_at.desc())
        .limit(20)
        .all()
    )
    # Skip snapshots the background scan hasn't finished (or failed) writing -
    # see AnalyticsSnapshot.status. A pending/failed snapshot has no real
    # channel data, so treating it as "the latest full sweep" would blank out
    # everything until the old rule (any full sweep is fair game) let it in.
    full_sweeps = [
        s for s in recent
        if not s.requested_channels and s.status not in ("pending", "failed")
    ][:6]
    if not full_sweeps:
        return None

    latest_full_sweep = full_sweeps[0]
    older_full_sweeps = list(reversed(full_sweeps[1:]))  # oldest-first, for classify_channel_trend's "previous_scores"

    def prior_scores(channel: str) -> list[int]:
        # Full-sweep history only, per classify_channel_trend's contract - a
        # channel-scoped scan not checking a channel isn't the same as that
        # channel staying flat, so it can't be used as trend history.
        scores = []
        for snap in older_full_sweeps:
            for entry in snap.channels or []:
                if entry.get("channel") == channel and entry.get("score") is not None:
                    scores.append(entry["score"])
        return scores

    # Per-channel data starts from the latest full sweep (the only kind of
    # scan that covers every channel), then any channel-scoped scan newer
    # than it overrides just its own channel. That channel really was
    # re-checked since, so its fresher result should count immediately
    # instead of waiting for the next full sweep to roll around - this is
    # what makes a quick single-channel rescan actually show up here.
    effective_entries: dict[str, dict] = {
        entry.get("channel"): entry for entry in (latest_full_sweep.channels or [])
    }
    newer_partial_scans = [
        s for s in recent
        if s.requested_channels and s.created_at > latest_full_sweep.created_at
        and s.status not in ("pending", "failed")
    ]
    for snap in reversed(newer_partial_scans):  # oldest-first, so the newest override wins last
        for entry in snap.channels or []:
            channel = entry.get("channel")
            if channel in effective_entries:
                effective_entries[channel] = entry

    ranking = []
    for channel, entry in effective_entries.items():
        score = entry.get("score", 0)
        ranking.append({
            "channel": channel,
            "score": score,
            "classification": classify_channel_trend(score, prior_scores(channel)),
            "score_breakdown": entry.get("score_breakdown", []),
            # Pieces of content published on this channel (pages/videos/posts),
            # or None for channels that don't track a content count.
            "content_count": content_pieces(channel, entry.get("kpis") or {}),
            "notes": entry.get("notes"),
            # Reliability flags from the scan reconciliation (services/analytics_reconcile.py):
            # a held-forward value the web search failed to re-find this run (stale),
            # when it was last really measured, and whether this channel warrants a
            # human sanity-check (held value or a suspicious swing). Lets any client
            # (plugin, CLI) show an honest "not refreshed" / "verify" badge.
            "stale": bool(entry.get("stale")),
            "last_measured_at": entry.get("last_measured_at"),
            "needs_review": bool(entry.get("needs_review")),
            "review_reason": entry.get("review_reason"),
        })
    ranking.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(ranking):
        r["rank"] = i + 1

    org_score, org_score_breakdown = score_org({r["channel"]: r["score"] for r in ranking})

    # Breadth: how many channels are actually live (folded into org_score, and
    # surfaced on its own so "5/8 channels online" is a first-class number).
    availability = channel_availability({r["channel"]: r["score"] for r in ranking})

    # Volume: total pieces of content published across content-bearing channels,
    # plus the per-channel counts that make up the total.
    content_by_channel = {
        r["channel"]: r["content_count"]
        for r in ranking
        if r["content_count"] is not None
    }
    content_volume = {
        "total": sum(content_by_channel.values()),
        "by_channel": content_by_channel,
    }

    baseline = (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.organization_id == organization_id, AnalyticsSnapshot.is_baseline.is_(True))
        .first()
    )

    return {
        "latest_snapshot_id": latest_full_sweep.id,
        "latest_created_at": latest_full_sweep.created_at,
        "org_score": org_score,
        "org_score_breakdown": org_score_breakdown,
        "baseline_org_score": baseline.org_score if baseline else None,
        "availability": availability,
        "content_volume": content_volume,
        "ranking": ranking,
        "summary": latest_full_sweep.summary,
        # Snapshot-level roll-up: true if the latest sweep was flagged, or any
        # effective channel is held/anomalous - the one field a report-sender
        # checks before shipping the monthly report unattended.
        "needs_review": bool(latest_full_sweep.needs_review) or any(r["needs_review"] for r in ranking),
    }
