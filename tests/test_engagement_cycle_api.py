"""Offline HTTP-layer tests for the engagement-cycle router
(app/routers/engagement_cycle.py). Uses fastapi.testclient.TestClient against
the real `app` from app.main, with get_db/get_current_user overridden onto an
in-memory SQLite session and a seeded user - same StaticPool pattern as
tests/test_engagement_cycle.py and tests/test_channels.py, but exercised
through the actual HTTP routes instead of calling run_full_cycle directly.

No ANTHROPIC_API_KEY / OPENAI_API_KEY is set anywhere in this file - measure
mode defaults to "simulate" (app.config.settings.cycle_measure_mode), which
is 100% offline/deterministic, so nothing here touches the network."""

import itertools

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.deps import get_current_user
from app.main import app
from app.models.entities import AnalyticsSnapshot, EngagementCycleRun, Organization, User
from app.services.analytics_scoring import score_channel, score_org

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# The in-memory SQLite DB (via StaticPool) is shared across every test
# function in this module, not reset between them - so each seeded user
# needs a genuinely unique email rather than one derived from a boolean flag.
_email_counter = itertools.count()


def _make_channel_entry(channel: str, kpis: dict) -> dict:
    score, breakdown = score_channel(channel, kpis)
    return {
        "channel": channel,
        "kpis": kpis,
        "notes": f"seed data for {channel}",
        "score": score,
        "score_breakdown": breakdown,
    }


def _seed_org_with_baseline(db, *, module_enabled: bool = True) -> Organization:
    """Same seeding shape as tests/test_engagement_cycle.py's
    _seed_org_with_baseline - a user, an org with real gaps to close, and a
    complete BASELINE full-sweep AnalyticsSnapshot so ANALYSE/PLAN have
    something to work with."""
    user = User(email=f"cycle-api-owner-{next(_email_counter)}@example.com", hashed_password="not-a-real-hash")
    db.add(user)
    db.commit()
    db.refresh(user)

    organization = Organization(
        owner_id=user.id,
        name="Grace Community Church",
        website_url="https://gracechurch.example",
        enabled_modules=["engagement_cycle"] if module_enabled else [],
        target_org_score=80,
        target_channel_scores={"website": 80, "google_business": 90},
    )
    db.add(organization)
    db.commit()
    db.refresh(organization)

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
    db.add(baseline)
    db.commit()
    db.refresh(baseline)

    return organization


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """Wires the real `app` up to the in-memory test DB and a fixed seeded
    user, so the actual HTTP routes (auth, gating, serialization) get
    exercised rather than the service functions directly."""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    seeded_user_holder: dict = {}

    def override_get_current_user():
        return seeded_user_holder["user"]

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    test_client = TestClient(app)
    test_client._seeded_user_holder = seeded_user_holder  # type: ignore[attr-defined]

    try:
        yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def _set_current_user(client: TestClient, user: User) -> None:
    client._seeded_user_holder["user"] = user  # type: ignore[attr-defined]


def test_run_cycle_returns_completed_with_positive_delta(client, db_session):
    org = _seed_org_with_baseline(db_session)
    _set_current_user(client, org.owner)

    response = client.post(f"/organizations/{org.id}/engagement-cycle/run", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["organization_id"] == org.id
    assert body["delta"] is not None
    assert body["delta"] >= 1
    assert body["after_org_score"] == body["before_org_score"] + body["delta"]
    assert isinstance(body["stages"], list) and len(body["stages"]) == 7
    assert isinstance(body["publication_ids"], list) and len(body["publication_ids"]) >= 1


def test_run_cycle_403s_when_module_not_enabled(client, db_session):
    org = _seed_org_with_baseline(db_session, module_enabled=False)
    _set_current_user(client, org.owner)

    response = client.post(f"/organizations/{org.id}/engagement-cycle/run", json={})
    assert response.status_code == 403
    assert "engagement_cycle" in response.json()["detail"]


def test_run_cycle_dry_run_returns_dry_run_status(client, db_session):
    org = _seed_org_with_baseline(db_session)
    _set_current_user(client, org.owner)

    response = client.post(f"/organizations/{org.id}/engagement-cycle/run", json={"dry_run": True})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "dry_run"
    assert body["delta"] == 0


def test_run_cycle_rejects_invalid_measure_mode(client, db_session):
    org = _seed_org_with_baseline(db_session)
    _set_current_user(client, org.owner)

    response = client.post(
        f"/organizations/{org.id}/engagement-cycle/run",
        json={"measure_mode": "not-a-real-mode"},
    )
    assert response.status_code == 422


def test_list_and_get_runs(client, db_session):
    org = _seed_org_with_baseline(db_session)
    _set_current_user(client, org.owner)

    run_response = client.post(f"/organizations/{org.id}/engagement-cycle/run", json={})
    assert run_response.status_code == 200
    run_id = run_response.json()["id"]

    list_response = client.get(f"/organizations/{org.id}/engagement-cycle/runs")
    assert list_response.status_code == 200
    runs = list_response.json()
    assert len(runs) == 1
    assert runs[0]["id"] == run_id

    get_response = client.get(f"/organizations/{org.id}/engagement-cycle/runs/{run_id}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == run_id

    bad_response = client.get(f"/organizations/{org.id}/engagement-cycle/runs/999999")
    assert bad_response.status_code == 404


def test_list_runs_newest_first(client, db_session):
    org = _seed_org_with_baseline(db_session)
    _set_current_user(client, org.owner)

    first = client.post(f"/organizations/{org.id}/engagement-cycle/run", json={"dry_run": True}).json()
    second = client.post(f"/organizations/{org.id}/engagement-cycle/run", json={"dry_run": True}).json()

    runs = client.get(f"/organizations/{org.id}/engagement-cycle/runs").json()
    assert [r["id"] for r in runs] == [second["id"], first["id"]]
