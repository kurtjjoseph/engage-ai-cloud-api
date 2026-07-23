"""Tests for site-type-tailored content suggestion + tracking.

POST /content/suggest drafts a few website posts (via Claude, monkeypatched
here so it's network-free) tailored to the org's site_type, saves each as a
tracked ContentItem, and returns them; GET /content lists them."""
import itertools

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.routers.content as content_router
from app.db.session import Base, get_db
from app.deps import get_current_user
from app.main import app
from app.models.entities import ContentItem, Organization, User
from app.services.content_ideas import guidance_for

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(bind=engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
_email_counter = itertools.count()


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


def _seed(db, *, site_type=None):
    user = User(email=f"content-{next(_email_counter)}@example.com", hashed_password="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    org = Organization(owner_id=user.id, name="Grace Church", org_type="church",
                       site_facts={"site_type": site_type} if site_type else None)
    db.add(org)
    db.commit()
    db.refresh(org)
    return user, org


def test_guidance_falls_back_to_business_for_unknown_type():
    assert "church" in guidance_for("church").lower()
    assert "shop" in guidance_for("ecommerce").lower()
    assert guidance_for(None) == guidance_for("business")
    assert guidance_for("nonsense") == guidance_for("business")


def test_suggest_saves_and_returns_tracked_content(client, db_session, monkeypatch):
    user, org = _seed(db_session, site_type="church")
    client._holder["user"] = user

    seen = {}

    def fake_suggest(org_context, site_type, count):
        seen["site_type"] = site_type
        seen["count"] = count
        return [
            {"title": f"Idea {i}", "angle": f"why {i}", "body_html": f"<p>Body {i}</p>"}
            for i in range(count)
        ]

    monkeypatch.setattr(content_router.content_ideas, "suggest", fake_suggest)

    resp = client.post(f"/content/suggest?organization_id={org.id}&count=2")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert seen["site_type"] == "church"  # pulled from org.site_facts
    assert seen["count"] == 2
    first = body[0]
    assert first["content_type"] == "website_post"
    assert first["output_payload"]["website_post"]["body_html"] == "<p>Body 0</p>"
    assert first["output_payload"]["angle"] == "why 0"

    # persisted + listable
    assert db_session.query(ContentItem).filter_by(organization_id=org.id).count() == 2
    listed = client.get(f"/content?organization_id={org.id}")
    assert listed.status_code == 200
    assert len(listed.json()) == 2


def test_suggest_defaults_site_type_to_business_when_unset(client, db_session, monkeypatch):
    user, org = _seed(db_session, site_type=None)
    client._holder["user"] = user
    captured = {}

    def fake(ctx, st, n):
        captured["st"] = st
        return [{"title": "T", "angle": "a", "body_html": "<p>b</p>"}]

    monkeypatch.setattr(content_router.content_ideas, "suggest", fake)
    resp = client.post(f"/content/suggest?organization_id={org.id}")
    assert resp.status_code == 200
    assert captured["st"] == "business"


def test_suggest_503_when_generation_empty(client, db_session, monkeypatch):
    user, org = _seed(db_session, site_type="business")
    client._holder["user"] = user
    monkeypatch.setattr(content_router.content_ideas, "suggest", lambda ctx, st, n: [])
    resp = client.post(f"/content/suggest?organization_id={org.id}")
    assert resp.status_code == 503


def test_suggest_404_for_unowned_org(client, db_session, monkeypatch):
    _owner, org = _seed(db_session, site_type="business")
    intruder = User(email=f"intruder-{next(_email_counter)}@example.com", hashed_password="x")
    db_session.add(intruder)
    db_session.commit()
    db_session.refresh(intruder)
    client._holder["user"] = intruder
    monkeypatch.setattr(content_router.content_ideas, "suggest", lambda ctx, st, n: [{"title": "T", "angle": "a", "body_html": "<p>b</p>"}])
    resp = client.post(f"/content/suggest?organization_id={org.id}")
    assert resp.status_code == 404
