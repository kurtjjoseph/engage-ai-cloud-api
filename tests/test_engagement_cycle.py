"""Offline/deterministic tests for the full engagement cycle orchestrator
(app/services/engagement_cycle.py) and its measurement/re-score engine
(app/services/cycle_measurement.py). Uses an in-memory SQLite database via
StaticPool, same pattern as tests/test_channels.py.

No ANTHROPIC_API_KEY / OPENAI_API_KEY is set anywhere in this file - the
"simulate" measure mode never touches Anthropic/OpenAI/httpx, which is what
makes the whole cycle runnable here with zero network access."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.entities import AnalyticsSnapshot, EngagementCycleRun, Organization, Publication, User
from app.services.analytics_scoring import score_channel, score_org
from app.services.engagement_cycle import run_full_cycle


@pytest.fixture
def db_session():
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
    score, breakdown = score_channel(channel, kpis)
    return {
        "channel": channel,
        "kpis": kpis,
        "notes": f"seed data for {channel}",
        "score": score,
        "score_breakdown": breakdown,
    }


def _seed_org_with_baseline(db_session, *, with_baseline: bool = True) -> Organization:
    user = User(email="cycle-owner@example.com", hashed_password="not-a-real-hash")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    organization = Organization(
        owner_id=user.id,
        name="Grace Community Church",
        website_url="https://gracechurch.example",
        enabled_modules=["engagement_cycle"],
        target_org_score=80,
        target_channel_scores={"website": 80, "google_business": 90},
    )
    db_session.add(organization)
    db_session.commit()
    db_session.refresh(organization)

    if not with_baseline:
        return organization

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


def test_full_cycle_raises_score_by_at_least_one(db_session):
    org = _seed_org_with_baseline(db_session)

    run = run_full_cycle(db_session, org, measure_mode="simulate")

    assert run.status == "completed"
    assert run.before_org_score is not None
    assert run.after_org_score is not None
    assert run.after_org_score >= run.before_org_score + 1
    assert run.delta == run.after_org_score - run.before_org_score
    assert run.delta >= 1


def test_distribution_creates_publications(db_session):
    org = _seed_org_with_baseline(db_session)

    run = run_full_cycle(db_session, org, measure_mode="simulate")

    publications = db_session.query(Publication).filter_by(organization_id=org.id).all()
    assert len(publications) == run.engagement_count
    assert len(publications) >= 1

    new_snapshots = (
        db_session.query(AnalyticsSnapshot)
        .filter_by(organization_id=org.id, is_baseline=False)
        .all()
    )
    assert len(new_snapshots) == 1
    assert new_snapshots[0].org_score == run.after_org_score


def test_seven_stages_recorded(db_session):
    org = _seed_org_with_baseline(db_session)

    run = run_full_cycle(db_session, org, measure_mode="simulate")

    assert len(run.stages) == 7
    expected_names = ["ANALYSE", "PLAN", "COPY", "GENERATE", "APPROVE", "DISTRIBUTE", "MEASURE"]
    assert [s["name"] for s in run.stages] == expected_names
    assert [s["stage"] for s in run.stages] == [1, 2, 3, 4, 5, 6, 7]


def test_dry_run_distributes_nothing(db_session):
    org = _seed_org_with_baseline(db_session)

    run = run_full_cycle(db_session, org, measure_mode="simulate", dry_run=True)

    assert run.status == "dry_run"
    assert run.after_org_score == run.before_org_score
    assert run.delta == 0

    publications = db_session.query(Publication).filter_by(organization_id=org.id).all()
    assert len(publications) == 0

    new_snapshots = (
        db_session.query(AnalyticsSnapshot)
        .filter_by(organization_id=org.id, is_baseline=False)
        .all()
    )
    assert len(new_snapshots) == 0


def test_blocked_without_baseline(db_session):
    org = _seed_org_with_baseline(db_session, with_baseline=False)

    run = run_full_cycle(db_session, org, measure_mode="simulate")

    assert run.status == "blocked_no_baseline"
    assert run.after_org_score is None
    assert run.before_org_score is None
    assert isinstance(run, EngagementCycleRun)


def test_offline_no_network_dependency(db_session, monkeypatch):
    """Belt-and-suspenders: with no Anthropic/OpenAI key available at all, the
    simulate-mode cycle still completes and raises the score - proving this path
    never depends on network access.

    The keys are taken away here rather than asserted absent. Asserting on the
    ambient settings tested whoever's .env happened to be on the machine: it
    failed for any developer with a real key configured, and passed vacuously
    where there was none, without ever proving the cycle avoids the network."""
    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    assert not settings.anthropic_api_key
    assert not settings.openai_api_key

    org = _seed_org_with_baseline(db_session)
    run = run_full_cycle(db_session, org, measure_mode="simulate")

    assert run.status == "completed"
    assert run.delta >= 1


def test_placeholder_copy_is_never_put_on_a_real_account(db_session):
    """The cycle and the automation drainer read the SAME per-channel auto_post
    flag. So an operator who switches Instagram on so their finished, quality-
    checked Studio pieces can go out must not thereby also start posting this
    module's "[DRAFT - ...] placeholder content pending review" filler to that
    same account. Nobody would choose that, and they would hear about it from
    their audience rather than from us.

    Asserted through the registry rather than by inspecting the copy: a real
    adapter is installed for the channel, and the test is that the cycle
    declines to use it while its own copy is still templated.
    """
    from app.services.channels.base import ChannelAdapter
    from app.services.channels.registry import register_adapter, unregister_adapter

    class _RealAdapter(ChannelAdapter):
        simulated = False

        def __init__(self, channel):
            self.channel = channel
            self.sent = []

        def distribute(self, db, org, engagement):
            self.sent.append(engagement)
            return self._record_publication(db, org, url="https://real.example/p/1", label="live")

    organization = _seed_org_with_baseline(db_session)
    adapters = {channel: _RealAdapter(channel) for channel in
                ("website", "facebook", "instagram", "linkedin", "youtube", "twitter_x")}
    for channel, adapter in adapters.items():
        register_adapter(channel, adapter)
    try:
        run = run_full_cycle(db_session, organization)
    finally:
        # Unregister rather than re-registering a simulated adapter - see
        # ARCHITECTURE 3.12 for the latent bug that pattern caused here before.
        for channel in adapters:
            unregister_adapter(channel)

    assert run.engagement_count > 0, "the cycle needs to have planned something for this to mean anything"
    assert all(adapter.sent == [] for adapter in adapters.values()), \
        "placeholder copy reached a real adapter"
    # It still records where the work would have gone, so the cycle's own
    # reporting is unchanged - it is only the sending that is withheld.
    publications = db_session.query(Publication).filter(
        Publication.organization_id == organization.id
    ).all()
    assert publications and all(p.simulated for p in publications)
    assert run.simulation["distribution"] == "simulated"
