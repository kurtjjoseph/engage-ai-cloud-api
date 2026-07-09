from sqlalchemy.orm import Session
from app.models.entities import AnalyticsSnapshot
from app.services.analytics_scoring import classify_channel_trend


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

    baseline = (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.organization_id == organization_id, AnalyticsSnapshot.is_baseline.is_(True))
        .first()
    )

    return {
        "latest_snapshot_id": latest.id,
        "latest_created_at": latest.created_at,
        "org_score": latest.org_score,
        "org_score_breakdown": latest.org_score_breakdown,
        "baseline_org_score": baseline.org_score if baseline else None,
        "ranking": ranking,
        "summary": latest.summary,
    }
