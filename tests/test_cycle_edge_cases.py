"""Edge-case tests for the full engagement cycle orchestrator.

Hardened coverage for boundary conditions and defensive behavior:
- organizations with no improvement opportunities (already meeting targets)
- idempotency and monotonicity across multiple cycles
- graceful handling of missing API keys in live mode
- bounded improvements that respect enum ceilings
- reproducible, deterministic scoring
- dry-run followed by real-run behavior

All tests use in-memory SQLite with zero network/API calls."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.entities import AnalyticsSnapshot, EngagementCycleRun, Organization, Publication, User
from app.services.analytics_scoring import (
    FREQUENCY_LEVELS,
    FRESHNESS_LEVELS,
    QUALITATIVE_LEVELS,
    score_channel,
    score_org,
)
from app.services.cycle_measurement import (
    measure_and_rescore,
    project_kpi_improvement,
)
from app.services.engagement_cycle import run_full_cycle


@pytest.fixture
def db_session():
    """In-memory SQLite session for offline testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_channel_entry(channel: str, kpis: dict) -> dict:
    """Helper: create a channel snapshot entry with scoring."""
    score, breakdown = score_channel(channel, kpis)
    return {
        "channel": channel,
        "kpis": kpis,
        "notes": f"seed data for {channel}",
        "score": score,
        "score_breakdown": breakdown,
    }


def _seed_org_with_baseline(db_session, *, with_baseline: bool = True, channels: list[dict] | None = None) -> Organization:
    """Helper: create org and optionally seed a baseline snapshot.

    If channels is provided, use those entries instead of the default gapped ones."""
    user = User(email="test@example.com", hashed_password="hash")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    organization = Organization(
        owner_id=user.id,
        name="Test Organization",
        website_url="https://test.example",
        enabled_modules=["engagement_cycle"],
        target_org_score=80,
        target_channel_scores={"website": 80, "google_business": 90},
    )
    db_session.add(organization)
    db_session.commit()
    db_session.refresh(organization)

    if not with_baseline:
        return organization

    if channels is None:
        # Default: gapped baseline (same as test_engagement_cycle.py)
        channels = [
            _make_channel_entry("website", {
                "indexed": True,
                "pages_indexed_estimate": 3,
                "backlink_signal": "low",
                "freshness": "stale",
                "third_party_traffic_estimate": None,
            }),
            _make_channel_entry("google_business", {
                "found": True,
                "rating": 4.0,
                "review_count": 10,
            }),
            _make_channel_entry("youtube", {
                "found": False,
                "subscriber_count": None,
                "video_count": None,
                "posting_frequency": "none",
            }),
            _make_channel_entry("news_mentions", {
                "found": False,
                "mention_count_recent": None,
                "most_recent_mention_recency": "none",
            }),
            _make_channel_entry("facebook", {
                "found": False,
                "follower_count": None,
                "posting_frequency": "none",
                "engagement_level": "none",
            }),
            _make_channel_entry("instagram", {
                "found": False,
                "follower_count": None,
                "posting_frequency": "none",
                "engagement_level": "none",
            }),
            _make_channel_entry("linkedin", {
                "found": False,
                "follower_count": None,
                "posting_frequency": "none",
                "engagement_level": "none",
            }),
            _make_channel_entry("twitter_x", {
                "found": False,
                "follower_count": None,
                "posting_frequency": "none",
                "engagement_level": "none",
            }),
        ]

    org_score, org_score_breakdown = score_org({c["channel"]: c["score"] for c in channels})

    baseline = AnalyticsSnapshot(
        organization_id=organization.id,
        is_baseline=True,
        summary="Baseline scan.",
        channels=channels,
        org_score=org_score,
        org_score_breakdown=org_score_breakdown,
        sources=[],
        requested_channels=None,
        status="complete",
    )
    db_session.add(baseline)
    db_session.commit()
    db_session.refresh(baseline)

    return organization


# ============================================================================
# EDGE-CASE TESTS
# ============================================================================


def test_no_action_when_no_gaps(db_session):
    """No improvement opportunity: baseline already meets/exceeds all targets.

    Create an org where all distributable channels either:
    - have targets set and meet/exceed them, OR
    - are not white_space (have some presence)

    Assertion: run_full_cycle returns status "no_action", delta == 0, no Publications
    """
    # Create baseline where targets are already met for distributable channels
    channels = [
        _make_channel_entry("website", {
            "indexed": True,
            "pages_indexed_estimate": 50,  # high pages indexed
            "backlink_signal": "high",
            "freshness": "very_active",
            "third_party_traffic_estimate": None,
        }),
        _make_channel_entry("google_business", {
            "found": True,
            "rating": 5.0,  # perfect rating
            "review_count": 100,  # many reviews
        }),
        # Distributable channels with presence (not white_space) - no targets set
        _make_channel_entry("youtube", {
            "found": True,
            "subscriber_count": 500,  # has presence
            "video_count": 10,
            "posting_frequency": "weekly",
        }),
        _make_channel_entry("facebook", {
            "found": True,
            "follower_count": 200,  # has presence
            "posting_frequency": "weekly",
            "engagement_level": "medium",
        }),
        _make_channel_entry("instagram", {
            "found": True,
            "follower_count": 300,
            "posting_frequency": "weekly",
            "engagement_level": "medium",
        }),
        _make_channel_entry("linkedin", {
            "found": True,
            "follower_count": 150,
            "posting_frequency": "rare",
            "engagement_level": "low",
        }),
        _make_channel_entry("twitter_x", {
            "found": True,
            "follower_count": 250,
            "posting_frequency": "weekly",
            "engagement_level": "medium",
        }),
        # Non-distributable channels - not considered for planning
        _make_channel_entry("news_mentions", {
            "found": False,
            "mention_count_recent": None,
            "most_recent_mention_recency": "none",
        }),
    ]

    org = _seed_org_with_baseline(db_session, channels=channels)

    run = run_full_cycle(db_session, org, measure_mode="simulate")

    assert run.status == "no_action", f"Expected no_action but got {run.status}"
    assert run.delta == 0
    assert run.before_org_score == run.after_org_score
    assert run.engagement_count == 0

    # Verify no publications were created
    publications = db_session.query(Publication).filter_by(organization_id=org.id).all()
    assert len(publications) == 0

    # Verify no new snapshots beyond baseline
    new_snapshots = (
        db_session.query(AnalyticsSnapshot)
        .filter_by(organization_id=org.id, is_baseline=False)
        .all()
    )
    assert len(new_snapshots) == 0


def test_second_cycle_is_idempotent_or_monotonic(db_session):
    """Running the cycle twice on a gapped org maintains monotonicity.

    - run cycle once: score increases
    - run cycle again: score never decreases, second run's score >= first run's
    - each run persists as a separate EngagementCycleRun row
    """
    org = _seed_org_with_baseline(db_session)

    # First cycle
    run1 = run_full_cycle(db_session, org, measure_mode="simulate")
    assert run1.status == "completed"
    assert run1.before_org_score is not None
    assert run1.after_org_score is not None
    score_after_first_run = run1.after_org_score

    # Second cycle
    run2 = run_full_cycle(db_session, org, measure_mode="simulate")
    assert run2.status == "completed"
    assert run2.before_org_score is not None
    assert run2.after_org_score is not None

    # Monotonic: second run's after_org_score >= first run's after_org_score
    assert run2.after_org_score >= score_after_first_run

    # Each run is a separate persisted row
    runs = db_session.query(EngagementCycleRun).filter_by(organization_id=org.id).all()
    assert len(runs) == 2
    assert runs[0].id != runs[1].id
    assert runs[0].created_at < runs[1].created_at


def test_live_mode_guarded_returns_gracefully(db_session):
    """Live mode with no API key configured returns gracefully.

    - call measure_and_rescore(..., mode="live") with no API key
    - Assertion: returns a dict with after_org_score (int or None) and never raises
    """
    org = _seed_org_with_baseline(db_session)

    # Run a quick cycle to get some publications to measure
    run1 = run_full_cycle(db_session, org, measure_mode="simulate")
    publications = db_session.query(Publication).filter_by(organization_id=org.id).all()

    if not publications:
        # If no publications from first cycle, create a dummy one for testing
        from app.models.entities import Publication as PublicationModel
        pub = PublicationModel(
            organization_id=org.id,
            channel="website",
            title="Test Publication",
            url="https://test.example/test",
            published_at=None,
        )
        db_session.add(pub)
        db_session.commit()
        publications = [pub]

    # Call measure_and_rescore with mode="live" - should not raise
    engagements = [{"channel": "website", "type": "website_post"}]
    result = measure_and_rescore(db_session, org, publications, engagements, mode="live")

    assert isinstance(result, dict)
    assert "after_org_score" in result
    # after_org_score can be None (if measurement fails) or an int
    assert result["after_org_score"] is None or isinstance(result["after_org_score"], int)
    # Should not raise an exception


def test_project_kpi_improvement_is_bounded_and_pure(db_session):
    """project_kpi_improvement respects enum ceilings and never mutates input.

    - Call project_kpi_improvement with a KPI already at the top enum level
    - Assertion: output never exceeds the top level
    - Assertion: input dict is not mutated
    """
    # Test website at top freshness level
    kpis_website = {
        "indexed": True,
        "pages_indexed_estimate": 100,
        "backlink_signal": "high",
        "freshness": "very_active",  # Already at top
        "third_party_traffic_estimate": None,
    }
    original_website = dict(kpis_website)

    result = project_kpi_improvement("website", "website_post", kpis_website)

    # Input should not be mutated
    assert kpis_website == original_website

    # Output freshness should stay at "very_active" (top level)
    assert result["freshness"] == "very_active"

    # Test social channel at top posting_frequency
    kpis_social = {
        "found": True,
        "follower_count": 1000,
        "posting_frequency": "daily",  # Already at top
        "engagement_level": "high",
    }
    original_social = dict(kpis_social)

    result_social = project_kpi_improvement("facebook", "social_post", kpis_social)

    # Input should not be mutated
    assert kpis_social == original_social

    # Output posting_frequency should stay at "daily" (top level)
    assert result_social["posting_frequency"] == "daily"

    # Test channel_setup on white space (starts from bottom)
    kpis_ws = {"found": False, "follower_count": None, "posting_frequency": "none", "engagement_level": "none"}
    original_ws = dict(kpis_ws)

    result_ws = project_kpi_improvement("facebook", "channel_setup", kpis_ws)

    # Input should not be mutated
    assert kpis_ws == original_ws

    # Output should have found=True and conservative starter values
    assert result_ws["found"] is True
    assert result_ws["follower_count"] == 10
    assert result_ws["posting_frequency"] == "rare"
    assert result_ws["engagement_level"] == "low"


def test_score_is_reproducible(db_session):
    """Calling score_channel and score_org twice on same KPIs yields identical scores.

    - Assertion: determinism guard - no randomness, no caching issues
    """
    channels_data = [
        _make_channel_entry("website", {
            "indexed": True,
            "pages_indexed_estimate": 10,
            "backlink_signal": "low",
            "freshness": "active",
            "third_party_traffic_estimate": None,
        }),
        _make_channel_entry("google_business", {
            "found": True,
            "rating": 4.5,
            "review_count": 25,
        }),
        _make_channel_entry("facebook", {
            "found": True,
            "follower_count": 500,
            "posting_frequency": "weekly",
            "engagement_level": "medium",
        }),
    ]

    channel_scores_1 = {c["channel"]: c["score"] for c in channels_data}
    org_score_1, breakdown_1 = score_org(channel_scores_1)

    channel_scores_2 = {c["channel"]: c["score"] for c in channels_data}
    org_score_2, breakdown_2 = score_org(channel_scores_2)

    assert org_score_1 == org_score_2
    assert breakdown_1 == breakdown_2

    # Also test individual channel scores
    for channel_entry in channels_data:
        channel = channel_entry["channel"]
        kpis = channel_entry["kpis"]

        score_1, breakdown_s1 = score_channel(channel, kpis)
        score_2, breakdown_s2 = score_channel(channel, kpis)

        assert score_1 == score_2
        assert breakdown_s1 == breakdown_s2


def test_dry_run_then_real_run(db_session):
    """Dry run leaves score unchanged; subsequent real run still achieves delta >= 1.

    - run cycle with dry_run=True: after_org_score == before_org_score, no Publications
    - run cycle with dry_run=False on same org: achieves delta >= 1
    """
    org = _seed_org_with_baseline(db_session)

    baseline_snap = (
        db_session.query(AnalyticsSnapshot)
        .filter_by(organization_id=org.id, is_baseline=True)
        .first()
    )
    original_baseline_score = baseline_snap.org_score

    # Dry run
    dry_run_result = run_full_cycle(db_session, org, measure_mode="simulate", dry_run=True)

    assert dry_run_result.status == "dry_run"
    assert dry_run_result.before_org_score == original_baseline_score
    assert dry_run_result.after_org_score == original_baseline_score
    assert dry_run_result.delta == 0
    assert dry_run_result.engagement_count == 0

    # Verify no publications created by dry run
    publications_after_dry = db_session.query(Publication).filter_by(organization_id=org.id).all()
    assert len(publications_after_dry) == 0

    # Real run
    real_run_result = run_full_cycle(db_session, org, measure_mode="simulate", dry_run=False)

    assert real_run_result.status == "completed"
    assert real_run_result.delta >= 1  # Real run should improve score
    assert real_run_result.after_org_score > original_baseline_score

    # Verify publications were created by real run
    publications_after_real = db_session.query(Publication).filter_by(organization_id=org.id).all()
    assert len(publications_after_real) > 0
