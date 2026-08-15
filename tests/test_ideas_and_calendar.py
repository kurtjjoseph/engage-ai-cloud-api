"""The idea cache and the calendar aggregate.

Network-free - neither endpoint calls a model. What is worth pinning down is the
behaviour that is easy to get quietly wrong: that keeping a batch twice does not
duplicate it, that dismissing keeps the row (so the same idea is not proposed
back), that a piece nobody dated is surfaced rather than dropped, and that "no
target set" never reads as "missed a target of zero".
"""
import itertools
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.deps import get_current_user
from app.main import app
from app.models.entities import Campaign, Idea, Organization, User

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(bind=engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
_email_counter = itertools.count()

TODAY = date(2026, 9, 1)


@pytest.fixture
def db_session():
    s = TestingSessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    holder: dict = {}
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: holder["user"]
    tc = TestClient(app)
    tc._holder = holder  # type: ignore[attr-defined]
    try:
        yield tc
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def _seed(db, client):
    user = User(email=f"ideas-{next(_email_counter)}@example.com", hashed_password="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    org = Organization(owner_id=user.id, name="Vision Outreach Media", org_type="business")
    db.add(org)
    db.commit()
    db.refresh(org)
    client._holder["user"] = user
    return user, org


# --- the idea cache -------------------------------------------------------


def test_a_batch_of_ideas_is_kept_in_one_call(client, db_session):
    _, org = _seed(db_session, client)

    res = client.post(f"/organizations/{org.id}/ideas", json=[
        {"title": "Behind the scenes of a Sunday", "angle": "show the setup", "goal": "attract"},
        {"title": "Three questions we get asked", "channel": "instagram"},
    ])

    assert res.status_code == 201
    kept = res.json()
    assert [i["title"] for i in kept] == ["Behind the scenes of a Sunday", "Three questions we get asked"]
    assert {i["status"] for i in kept} == {"kept"}


def test_keeping_the_same_idea_twice_does_not_duplicate_it(client, db_session):
    _, org = _seed(db_session, client)
    idea = {"title": "Behind the scenes of a Sunday"}

    client.post(f"/organizations/{org.id}/ideas", json=[idea])
    second = client.post(f"/organizations/{org.id}/ideas", json=[idea, {"title": "  behind THE scenes of a sunday  "}])

    # Re-running the generator on the same goal returns overlapping ideas, and
    # pressing Keep twice is an ordinary slip - neither should leave two rows.
    assert second.json() == []
    listed = client.get(f"/organizations/{org.id}/ideas").json()
    assert len(listed) == 1


def test_dismissing_keeps_the_row_so_it_is_not_proposed_again(client, db_session):
    _, org = _seed(db_session, client)
    created = client.post(f"/organizations/{org.id}/ideas", json=[{"title": "A tour of the building"}]).json()

    res = client.patch(f"/organizations/{org.id}/ideas/{created[0]['id']}", json={"status": "dismissed"})

    assert res.status_code == 200
    assert res.json()["status"] == "dismissed"
    # Scoped to this org: the in-memory database is shared across the module,
    # so a bare count() would measure every other test's rows too.
    assert db_session.query(Idea).filter(Idea.organization_id == org.id).count() == 1
    assert client.get(f"/organizations/{org.id}/ideas", params={"status": "kept"}).json() == []


def test_an_unknown_status_is_refused_rather_than_stored(client, db_session):
    _, org = _seed(db_session, client)
    created = client.post(f"/organizations/{org.id}/ideas", json=[{"title": "Something"}]).json()

    res = client.patch(f"/organizations/{org.id}/ideas/{created[0]['id']}", json={"status": "maybe-later"})

    assert res.status_code == 400


def test_ideas_from_another_org_are_not_reachable(client, db_session):
    _, org = _seed(db_session, client)
    created = client.post(f"/organizations/{org.id}/ideas", json=[{"title": "Ours"}]).json()

    other_user = User(email=f"other-{next(_email_counter)}@example.com", hashed_password="x")
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)
    other_org = Organization(owner_id=other_user.id, name="Someone Else")
    db_session.add(other_org)
    db_session.commit()
    db_session.refresh(other_org)

    res = client.patch(f"/organizations/{other_org.id}/ideas/{created[0]['id']}", json={"title": "Theirs"})

    assert res.status_code in (403, 404)


# --- the calendar ---------------------------------------------------------


def _campaign(db, org, entries, name="Autumn open evening"):
    campaign = Campaign(organization_id=org.id, name=name, goal="awareness", plan=entries)
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


def test_the_calendar_gathers_pieces_from_every_campaign(client, db_session):
    _, org = _seed(db_session, client)
    _campaign(db_session, org, [
        {"index": 0, "channel": "instagram", "scheduled_on": TODAY.isoformat(), "status": "planned"},
        {"index": 1, "channel": "facebook", "scheduled_on": (TODAY + timedelta(days=2)).isoformat(), "status": "drafted"},
    ])
    _campaign(db_session, org, [
        {"index": 0, "channel": "instagram", "scheduled_on": (TODAY + timedelta(days=1)).isoformat()},
    ], name="Second run")

    res = client.get(f"/organizations/{org.id}/calendar", params={"start": TODAY.isoformat()})

    body = res.json()
    assert [i["scheduled_on"] for i in body["items"]] == [
        TODAY.isoformat(),
        (TODAY + timedelta(days=1)).isoformat(),
        (TODAY + timedelta(days=2)).isoformat(),
    ]
    assert [i["written"] for i in body["items"]] == [False, False, True]


def test_a_piece_nobody_dated_is_surfaced_not_dropped(client, db_session):
    _, org = _seed(db_session, client)
    _campaign(db_session, org, [
        {"index": 0, "channel": "instagram", "scheduled_on": None},
        {"index": 1, "channel": "facebook", "scheduled_on": "not a date"},
    ])

    body = client.get(f"/organizations/{org.id}/calendar", params={"start": TODAY.isoformat()}).json()

    # Hiding these would make the calendar look complete while two pieces have
    # nowhere to go - the opposite of what it is for.
    assert body["items"] == []
    assert len(body["undated"]) == 2


def test_pieces_outside_the_window_are_not_returned(client, db_session):
    _, org = _seed(db_session, client)
    _campaign(db_session, org, [
        {"index": 0, "channel": "instagram", "scheduled_on": (TODAY - timedelta(days=5)).isoformat()},
        {"index": 1, "channel": "instagram", "scheduled_on": (TODAY + timedelta(days=90)).isoformat()},
    ])

    body = client.get(f"/organizations/{org.id}/calendar", params={
        "start": TODAY.isoformat(), "end": (TODAY + timedelta(days=6)).isoformat()}).json()

    assert body["items"] == []


def test_a_channel_with_no_target_reports_null_not_a_shortfall(client, db_session):
    _, org = _seed(db_session, client)
    _campaign(db_session, org, [
        {"index": 0, "channel": "instagram", "scheduled_on": TODAY.isoformat()},
    ])

    body = client.get(f"/organizations/{org.id}/calendar", params={
        "start": TODAY.isoformat(), "end": (TODAY + timedelta(days=6)).isoformat()}).json()

    row = next(r for r in body["by_channel"] if r["channel"] == "instagram")
    assert row["planned"] == 1
    assert row["target_per_week"] is None
    assert row["shortfall"] is None


def test_a_shortfall_is_reported_against_a_real_target(client, db_session):
    _, org = _seed(db_session, client)
    client.put(f"/organizations/{org.id}/posting-targets", json={"targets": {"instagram": 3}})
    _campaign(db_session, org, [
        {"index": 0, "channel": "instagram", "scheduled_on": TODAY.isoformat()},
    ])

    body = client.get(f"/organizations/{org.id}/calendar", params={
        "start": TODAY.isoformat(), "end": (TODAY + timedelta(days=6)).isoformat()}).json()

    row = next(r for r in body["by_channel"] if r["channel"] == "instagram")
    assert row["expected_in_window"] == 3
    assert row["shortfall"] == 2


def test_a_target_of_zero_is_removed_rather_than_stored(client, db_session):
    _, org = _seed(db_session, client)

    res = client.put(f"/organizations/{org.id}/posting-targets",
                     json={"targets": {"instagram": 3, "facebook": 0, "linkedin": -1}})

    # One representation of "no target", so the calendar never has to guess
    # whether a stored zero meant "none set" or "deliberately never".
    assert res.json()["targets"] == {"instagram": 3}


def test_an_archived_campaign_is_not_on_the_calendar(client, db_session):
    _, org = _seed(db_session, client)
    campaign = _campaign(db_session, org, [
        {"index": 0, "channel": "instagram", "scheduled_on": TODAY.isoformat()},
    ])
    campaign.status = "archived"
    db_session.commit()

    body = client.get(f"/organizations/{org.id}/calendar", params={"start": TODAY.isoformat()}).json()

    assert body["items"] == []
