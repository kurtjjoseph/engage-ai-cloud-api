from sqlalchemy.orm import Session
from app.models.entities import AnalyticsSnapshot
from app.services.analytics_scoring import classify_channel_trend, score_org


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
    full_sweeps = [s for s in recent if not s.requested_channels][:6]
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
            "notes": entry.get("notes"),
        })
    ranking.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(ranking):
        r["rank"] = i + 1

    org_score, org_score_breakdown = score_org({r["channel"]: r["score"] for r in ranking})

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
        "ranking": ranking,
        "summary": latest_full_sweep.summary,
    }
