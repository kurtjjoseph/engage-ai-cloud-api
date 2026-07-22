"""Measurement/re-score engine for the full engagement cycle
(services/engagement_cycle.py).

Two measurement modes:
- "simulate": 100% offline and deterministic. Starts from the latest
  full-sweep AnalyticsSnapshot, applies a bounded, honest projection of what
  each distributed engagement would do to its channel's KPIs
  (project_kpi_improvement), recomputes scores in code (never invented), and
  writes a new snapshot clearly labeled as a projection, not a live
  measurement. This is what lets "the cycle demonstrably raises org_score"
  be checked in a fast offline test.
- "live": best-effort real re-measurement via publication_search.py's
  web-search scan of each distributed publication. Guarded end to end so a
  missing API key or a network hiccup never breaks the cycle - it just
  returns after_org_score=None and callers treat that as "couldn't measure
  yet", not as a crash. Not exercised by the offline test suite.
"""

import copy

from sqlalchemy.orm import Session

from app.config import settings
from app.models.entities import AnalyticsSnapshot, Organization, Publication, PublicationSnapshot
from app.services.analytics_scoring import (
    CHANNEL_KPI_SCHEMA,
    FREQUENCY_LEVELS,
    FRESHNESS_LEVELS,
    QUALITATIVE_LEVELS,
    score_channel,
    score_org,
)

SIMULATED_PROJECTION_SUMMARY = (
    "[SIMULATED PROJECTION] Post-cycle projected footprint — not a live web-search measurement."
)


def is_cycle_enabled(org: Organization) -> bool:
    return settings.cycle_module_key in (org.enabled_modules or [])


def latest_full_sweep(db: Session, org_id: int) -> AnalyticsSnapshot | None:
    """Most recent snapshot that covered every channel (requested_channels is
    None) and finished writing (status not pending/failed) - same rule
    compute_insights uses for its "latest_full_sweep", kept as its own
    lookup here since the measurement engine needs the actual ORM row (to
    read/copy its .channels), not just the derived insights dict.

    Filters in Python, not SQL: SQLAlchemy's JSON column type encodes a
    Python None as the JSON literal "null" (text), not SQL NULL - see
    analytics_insights.compute_insights, which filters the same way for the
    same reason. A `.filter(...requested_channels.is_(None))` query would
    silently match nothing."""
    recent = (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.organization_id == org_id)
        .order_by(AnalyticsSnapshot.created_at.desc())
        .limit(20)
        .all()
    )
    for snapshot in recent:
        if not snapshot.requested_channels and snapshot.status not in ("pending", "failed"):
            return snapshot
    return None


def _bump(levels: list[str], current: str | None) -> str:
    """One step up an enum ladder, capped at the top level. Unknown/None
    current values are treated as the bottom rung."""
    idx = levels.index(current) if current in levels else 0
    idx = min(idx + 1, len(levels) - 1)
    return levels[idx]


def project_kpi_improvement(channel: str, engagement_type: str, kpis: dict) -> dict:
    """Return a NEW kpis dict (never mutates the input) reflecting the
    honest, bounded effect of one distributed engagement on this channel's
    measured state. Deterministic - same channel/type/kpis always produces
    the same projection, so this is a real, checkable claim, not a vibe:

    - website + website_post: gets indexed, freshness moves up one rung,
      one more page indexed.
    - social channel + social_post (already present): posting_frequency and
      engagement_level each move up one rung, +10 followers.
    - social channel + channel_setup (was white space): the channel is
      established from scratch at the bottom rung of activity, not
      instantly "good" - found=True, 10 followers, "rare"/"low".

    Never exceeds the top enum level (via _bump). Never touches a channel
    the engagement didn't target - callers only pass the one channel's kpis
    that a given engagement improved.
    """
    kpis = dict(kpis or {})

    if channel == "website" and engagement_type == "website_post":
        kpis["indexed"] = True
        kpis["freshness"] = _bump(FRESHNESS_LEVELS, kpis.get("freshness"))
        kpis["pages_indexed_estimate"] = (kpis.get("pages_indexed_estimate") or 0) + 1
        return kpis

    schema = CHANNEL_KPI_SCHEMA.get(channel, {})
    if "found" not in schema:
        # Not a channel this projection knows how to improve (e.g. website
        # with a non-website_post engagement type, or an unknown channel) -
        # return unchanged rather than fabricate something.
        return kpis

    if engagement_type == "channel_setup":
        kpis["found"] = True
        if "follower_count" in schema:
            kpis["follower_count"] = 10
        if "posting_frequency" in schema:
            kpis["posting_frequency"] = "rare"
        if "engagement_level" in schema:
            kpis["engagement_level"] = "low"
        return kpis

    if engagement_type == "social_post" and kpis.get("found"):
        if "posting_frequency" in schema:
            kpis["posting_frequency"] = _bump(FREQUENCY_LEVELS, kpis.get("posting_frequency"))
        if "engagement_level" in schema:
            kpis["engagement_level"] = _bump(QUALITATIVE_LEVELS, kpis.get("engagement_level"))
        if "follower_count" in schema:
            kpis["follower_count"] = (kpis.get("follower_count") or 0) + 10
        return kpis

    return kpis


def _simulate(db: Session, org: Organization, publications: list[Publication], engagements: list[dict]) -> dict:
    sweep = latest_full_sweep(db, org.id)
    if sweep is None:
        return {
            "after_org_score": None,
            "new_snapshot_id": None,
            "publication_snapshot_ids": [],
            "detail": "No baseline full-sweep snapshot to project improvements from.",
        }

    channels = copy.deepcopy(sweep.channels or [])
    by_channel = {entry.get("channel"): entry for entry in channels}

    # Each distributed engagement maps 1:1, in order, to the publication it
    # produced (see engagement_cycle.run_full_cycle's DISTRIBUTE stage).
    matched_pairs = list(zip(publications, engagements))

    for _publication, engagement in matched_pairs:
        channel = engagement.get("channel")
        entry = by_channel.get(channel)
        if entry is None:
            continue  # engagement targeted a channel with no snapshot entry - nothing to improve
        new_kpis = project_kpi_improvement(channel, engagement.get("type"), entry.get("kpis") or {})
        score, breakdown = score_channel(channel, new_kpis)
        entry["kpis"] = new_kpis
        entry["score"] = score
        entry["score_breakdown"] = breakdown

    channel_scores = {entry.get("channel"): entry.get("score", 0) for entry in channels}
    new_org_score, org_breakdown = score_org(channel_scores)

    snapshot = AnalyticsSnapshot(
        organization_id=org.id,
        is_baseline=False,
        summary=SIMULATED_PROJECTION_SUMMARY,
        channels=channels,
        org_score=new_org_score,
        org_score_breakdown=org_breakdown,
        sources=[],
        requested_channels=None,
        status="complete",
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    publication_snapshot_ids: list[int] = []
    for publication, engagement in matched_pairs:
        channel = engagement.get("channel")
        entry = by_channel.get(channel)
        if entry is None:
            continue
        pub_snapshot = PublicationSnapshot(
            publication_id=publication.id,
            kpis=entry.get("kpis"),
            notes="Simulated projection tied to this engagement cycle - not a live web-search measurement.",
            score=entry.get("score"),
            score_breakdown=entry.get("score_breakdown"),
            sources=[],
        )
        db.add(pub_snapshot)
        db.flush()
        publication_snapshot_ids.append(pub_snapshot.id)
    db.commit()

    return {
        "after_org_score": new_org_score,
        "new_snapshot_id": snapshot.id,
        "publication_snapshot_ids": publication_snapshot_ids,
        "detail": "Simulated projection written as a new non-baseline AnalyticsSnapshot.",
    }


def _live(db: Session, org: Organization, publications: list[Publication], engagements: list[dict]) -> dict:
    # Imported lazily and guarded end to end so a missing dependency/API key
    # or a network failure can never break the cycle - "best effort" means
    # after_org_score can legitimately come back None.
    try:
        from app.services.analytics_insights import compute_insights
        from app.services.analytics_scoring import PUBLICATION_SCANNABLE_CHANNELS, score_publication
        from app.services.publication_search import PublicationSearchService

        service = PublicationSearchService()
    except Exception as exc:  # pragma: no cover - defensive, no offline test path
        return {
            "after_org_score": None,
            "new_snapshot_id": None,
            "publication_snapshot_ids": [],
            "detail": f"Live measurement unavailable: {exc}",
        }

    publication_snapshot_ids: list[int] = []
    for publication in publications:
        if publication.channel not in PUBLICATION_SCANNABLE_CHANNELS:
            continue
        try:
            result = service.scan(publication.channel, publication.url)
            kpis = result.get("kpis") or {}
            score, breakdown = score_publication(publication.channel, kpis)
            pub_snapshot = PublicationSnapshot(
                publication_id=publication.id,
                kpis=kpis,
                notes=result.get("notes"),
                score=score,
                score_breakdown=breakdown,
                sources=result.get("sources"),
            )
            db.add(pub_snapshot)
            db.commit()
            db.refresh(pub_snapshot)
            publication_snapshot_ids.append(pub_snapshot.id)
        except Exception:  # pragma: no cover - one bad scan shouldn't sink the whole cycle
            db.rollback()
            continue

    try:
        insights = compute_insights(db, org.id)
    except Exception:  # pragma: no cover
        insights = None

    after_org_score = insights["org_score"] if insights else None
    return {
        "after_org_score": after_org_score,
        "new_snapshot_id": insights["latest_snapshot_id"] if insights else None,
        "publication_snapshot_ids": publication_snapshot_ids,
        "detail": "Live best-effort re-measurement via publication_search.py.",
    }


def measure_and_rescore(
    db: Session,
    org: Organization,
    publications: list[Publication],
    engagements: list[dict],
    mode: str,
) -> dict:
    """MEASURE stage of the full engagement cycle. Returns
    {"after_org_score": int|None, "new_snapshot_id": int|None,
    "publication_snapshot_ids": [...], "detail": str}.

    mode == "simulate" is fully offline/deterministic (no Anthropic/OpenAI/
    httpx calls at all) - see _simulate. mode == "live" is best-effort real
    measurement - see _live. Any other mode value is treated as "simulate"
    (the safe, offline default)."""
    if mode == "live":
        return _live(db, org, publications, engagements)
    return _simulate(db, org, publications, engagements)
