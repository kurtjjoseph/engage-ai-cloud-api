"""Offline HTTP-layer tests for the organization management endpoints added
for the console: DELETE /organizations/{id} (cascade delete), GET
/organizations/{id}/plugin.zip (per-site personalized download), and POST
/organizations/{id}/site-hello (first-run URL report + duplicate merge).

Same in-memory StaticPool + dependency-override pattern as
tests/test_engagement_cycle_api.py - no network, no API keys touched."""

import io
import itertools
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.deps import get_current_user
from app.main import app
from app.models.entities import (
    AgentRun,
    AnalyticsSnapshot,
    ContentItem,
    Organization,
    Publication,
    PublicationSnapshot,
    User,
)

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

_email_counter = itertools.count()


def _make_user(db) -> User:
    user = User(email=f"admin-owner-{next(_email_counter)}@example.com", hashed_password="not-a-real-hash")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_org(db, user: User, *, name: str, website_url: str | None = None) -> Organization:
    org = Organization(owner_id=user.id, name=name, org_type="church", website_url=website_url)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _seed_children(db, org: Organization) -> Publication:
    """A snapshot, an agent run, a content item, and a publication with its own
    snapshot - one row in every table that hangs off an org."""
    db.add(AnalyticsSnapshot(organization_id=org.id, status="complete", org_score=50, requested_channels=None))
    db.add(AgentRun(organization_id=org.id, niche="youtube_channel", summary="ran"))
    db.add(ContentItem(organization_id=org.id, content_type="event", title="t", input_payload={}, output_payload={}))
    pub = Publication(organization_id=org.id, channel="website", url="https://x.example/post")
    db.add(pub)
    db.commit()
    db.refresh(pub)
    db.add(PublicationSnapshot(publication_id=pub.id, score=10))
    db.commit()
    return pub


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

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


def test_delete_org_removes_org_and_all_children(client, db_session):
    user = _make_user(db_session)
    org = _make_org(db_session, user, name="To Delete")
    pub_id = _seed_children(db_session, org).id
    org_id = org.id
    _set_current_user(client, user)

    resp = client.request("DELETE", f"/organizations/{org_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    assert db_session.get(Organization, org_id) is None
    assert db_session.query(AnalyticsSnapshot).filter_by(organization_id=org_id).count() == 0
    assert db_session.query(AgentRun).filter_by(organization_id=org_id).count() == 0
    assert db_session.query(ContentItem).filter_by(organization_id=org_id).count() == 0
    assert db_session.query(Publication).filter_by(organization_id=org_id).count() == 0
    assert db_session.query(PublicationSnapshot).filter_by(publication_id=pub_id).count() == 0


def test_delete_other_users_org_404s(client, db_session):
    owner = _make_user(db_session)
    org = _make_org(db_session, owner, name="Not Yours")
    intruder = _make_user(db_session)
    _set_current_user(client, intruder)

    resp = client.request("DELETE", f"/organizations/{org.id}")
    assert resp.status_code == 404
    assert db_session.get(Organization, org.id) is not None


def test_plugin_zip_is_personalized_download(client, db_session):
    user = _make_user(db_session)
    org = _make_org(db_session, user, name="Fresh Site")
    _set_current_user(client, user)

    resp = client.get(f"/organizations/{org.id}/plugin.zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        assert "engage-ai/includes/preconfigured.php" in names
        baked = zf.read("engage-ai/includes/preconfigured.php").decode()
    assert f"'organization_id' => {org.id}" in baked


def test_site_hello_sets_url_when_no_duplicate(client, db_session):
    user = _make_user(db_session)
    org = _make_org(db_session, user, name="Solo")
    _set_current_user(client, user)

    resp = client.post(f"/organizations/{org.id}/site-hello", json={"home_url": "https://www.solo.example/"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"organization_id": org.id, "merged": False, "merged_from": None}
    db_session.refresh(org)
    assert org.website_url == "https://www.solo.example/"


def test_site_hello_merges_into_established_org(client, db_session):
    user = _make_user(db_session)
    # Established record the operator created earlier, same domain, real history.
    known = _make_org(db_session, user, name="Known", website_url="https://grace.example")
    _seed_children(db_session, known)
    # Fresh org the plugin download was tied to; the plugin now checks in with a
    # home_url on the same domain (different scheme / www to prove normalization).
    fresh = _make_org(db_session, user, name="Fresh")
    fresh_pub = _seed_children(db_session, fresh)
    _set_current_user(client, user)

    resp = client.post(f"/organizations/{fresh.id}/site-hello", json={"home_url": "http://www.grace.example/"})
    assert resp.status_code == 200
    body = resp.json()
    # The richer record survives; the fresh one is folded in and deleted.
    assert body["merged"] is True
    assert body["organization_id"] == known.id
    assert body["merged_from"] == fresh.id
    assert db_session.get(Organization, fresh.id) is None
    # Fresh org's children were reassigned to the surviving org, not dropped.
    assert db_session.get(Publication, fresh_pub.id).organization_id == known.id
    assert db_session.query(AnalyticsSnapshot).filter_by(organization_id=known.id).count() == 2
