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


def test_content_types_catalog_has_five_per_channel(client, db_session):
    user, _org = _seed(db_session)
    client._holder["user"] = user
    resp = client.get("/content/types")
    assert resp.status_code == 200
    cat = resp.json()
    assert set(cat) == {"website", "google_business", "youtube", "facebook",
                        "instagram", "linkedin", "twitter_x", "news_mentions"}
    for channel, types in cat.items():
        assert len(types) == 5, channel
        assert all({"key", "label", "raises"} <= set(t) for t in types)


def test_suggest_for_channel_saves_channel_content(client, db_session, monkeypatch):
    user, org = _seed(db_session, site_type="church")
    client._holder["user"] = user
    seen = {}

    def fake(org_context, channel, content_type, site_type, count):
        seen.update(channel=channel, content_type=content_type, site_type=site_type)
        return [{"title": "Post", "body": "caption line", "hashtags": ["faith", "hope"],
                 "label": "Educational carousel", "angle": "Raises engagement + saves"}]

    monkeypatch.setattr(content_router.content_ideas, "suggest_for_channel", fake)
    resp = client.post(f"/content/suggest?organization_id={org.id}&channel=instagram&content_type=ig_carousel")
    assert resp.status_code == 200
    body = resp.json()
    assert seen == {"channel": "instagram", "content_type": "ig_carousel", "site_type": "church"}
    item = body[0]
    assert item["content_type"] == "instagram"
    assert item["output_payload"]["channel"] == "instagram"
    assert item["output_payload"]["body"] == "caption line"
    assert item["output_payload"]["hashtags"] == ["faith", "hope"]
    assert "website_post" not in item["output_payload"]  # not a website channel


def test_suggest_for_website_channel_sets_website_post(client, db_session, monkeypatch):
    user, org = _seed(db_session, site_type="business")
    client._holder["user"] = user
    monkeypatch.setattr(content_router.content_ideas, "suggest_for_channel",
                        lambda *a, **k: [{"title": "T", "body": "<p>hi</p>", "hashtags": [], "label": "Blog article", "angle": "Raises freshness"}])
    resp = client.post(f"/content/suggest?organization_id={org.id}&channel=website&content_type=blog_post")
    assert resp.status_code == 200
    op = resp.json()[0]["output_payload"]
    assert op["website_post"]["body_html"] == "<p>hi</p>"  # website drafts stay WP-publishable


def test_suggest_invalid_content_type_returns_503(client, db_session):
    # No monkeypatch: the real service returns [] for an unknown type without any
    # network call (entry lookup short-circuits), so the endpoint reports 503.
    user, org = _seed(db_session, site_type="business")
    client._holder["user"] = user
    resp = client.post(f"/content/suggest?organization_id={org.id}&channel=instagram&content_type=not_a_real_type")
    assert resp.status_code == 503


class _FakeImageGen:
    def __init__(self, enabled, result=None):
        self._enabled = enabled
        self._result = result

    @property
    def enabled(self):
        return self._enabled

    def generate_image(self, prompt, size="1024x1024"):
        return self._result


def test_media_for_maps_types_to_media():
    from app.services.content_ideas import media_for
    assert media_for("ig_reel") == "video"
    assert media_for("short_script") == "video"
    assert media_for("blog_post") == "image"
    assert media_for("ig_carousel") == "image"
    assert media_for("x_thread") == "text"
    assert media_for("review_request") == "text"


def test_pack_saves_a_piece_per_channel(client, db_session, monkeypatch):
    user, org = _seed(db_session, site_type="church")
    client._holder["user"] = user

    def fake_pack(org_context, site_type, topic, selections):
        return {"topic": "Our new service", "pieces": [
            {"channel": "website", "content_type": "blog_post", "content_type_label": "Blog article",
             "media": "image", "title": "Web Post", "body": "<p>hi</p>", "hashtags": [],
             "image_prompt": "a church laptop", "image_alt": "laptop", "video_plan": None, "angle": "Raises freshness"},
            {"channel": "instagram", "content_type": "ig_carousel", "content_type_label": "Educational carousel",
             "media": "image", "title": "IG", "body": "caption", "hashtags": ["faith"],
             "image_prompt": "a bright carousel", "image_alt": "carousel", "video_plan": None, "angle": "Raises engagement"},
        ]}

    monkeypatch.setattr(content_router.content_ideas, "generate_pack", fake_pack)
    resp = client.post(f"/content/pack?organization_id={org.id}", json={"topic": "Our new service", "channels": ["website", "instagram"]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    web = next(p for p in body if p["content_type"] == "website")
    assert web["output_payload"]["website_post"]["body_html"] == "<p>hi</p>"  # website stays publishable
    ig = next(p for p in body if p["content_type"] == "instagram")
    assert ig["output_payload"]["media"] == "image"
    assert ig["output_payload"]["image_prompt"] == "a bright carousel"


def test_generate_image_503_without_provider(client, db_session, monkeypatch):
    user, org = _seed(db_session, site_type="business")
    client._holder["user"] = user
    item = ContentItem(organization_id=org.id, content_type="instagram", title="X",
                       input_payload={}, output_payload={"image_prompt": "a photo", "media": "image"})
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    monkeypatch.setattr(content_router, "image_gen", _FakeImageGen(enabled=False))
    resp = client.post(f"/content/{item.id}/image?organization_id={org.id}")
    assert resp.status_code == 503


def test_generate_image_stores_and_serves_asset(client, db_session, monkeypatch):
    user, org = _seed(db_session, site_type="business")
    client._holder["user"] = user
    item = ContentItem(organization_id=org.id, content_type="instagram", title="X",
                       input_payload={}, output_payload={"image_prompt": "a photo", "media": "image"})
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    png = b"\x89PNG\r\n\x1a\nFAKE"
    monkeypatch.setattr(content_router, "image_gen", _FakeImageGen(enabled=True, result=(png, "image/png")))

    resp = client.post(f"/content/{item.id}/image?organization_id={org.id}")
    assert resp.status_code == 200
    asset_id = resp.json()["asset_id"]

    # linked back on the item, and the bytes serve
    db_session.refresh(item)
    assert item.output_payload["image_asset_id"] == asset_id
    served = client.get(f"/content/asset/{asset_id}")
    assert served.status_code == 200
    assert served.content == png
    assert served.headers["content-type"].startswith("image/png")


class _FakeVideoGen:
    def __init__(self, result):
        self._result = result

    def assemble(self, video_plan, **kwargs):
        return self._result


def test_generate_video_stores_and_serves_mp4(client, db_session, monkeypatch):
    user, org = _seed(db_session, site_type="church")
    client._holder["user"] = user
    plan = {"scenes": [{"caption": "hi", "image_prompt": "a church"}], "voiceover": "vo"}
    item = ContentItem(organization_id=org.id, content_type="youtube", title="Short",
                       input_payload={}, output_payload={"media": "video", "video_plan": plan})
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    mp4 = b"\x00\x00\x00\x18ftypmp42FAKE"
    monkeypatch.setattr(content_router, "video_gen", _FakeVideoGen((mp4, "video/mp4")))

    resp = client.post(f"/content/{item.id}/video?organization_id={org.id}")
    assert resp.status_code == 200
    asset_id = resp.json()["asset_id"]
    db_session.refresh(item)
    assert item.output_payload["video_asset_id"] == asset_id
    served = client.get(f"/content/asset/{asset_id}")
    assert served.status_code == 200
    assert served.content == mp4
    assert served.headers["content-type"].startswith("video/mp4")


def test_generate_video_400_without_plan(client, db_session):
    user, org = _seed(db_session, site_type="church")
    client._holder["user"] = user
    item = ContentItem(organization_id=org.id, content_type="youtube", title="Short",
                       input_payload={}, output_payload={"media": "video"})  # no video_plan
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    resp = client.post(f"/content/{item.id}/video?organization_id={org.id}")
    assert resp.status_code == 400


def test_image_gen_service_enabled_without_key():
    # The service is always "enabled" now: a keyless fallback is available even
    # with no OPENAI_API_KEY, so image generation never hard-fails on config.
    from app.services.media_gen import ImageGenService
    assert ImageGenService().enabled is True


def test_asset_404_for_unowned(client, db_session):
    from app.models.entities import MediaAsset
    owner, org = _seed(db_session, site_type="business")
    asset = MediaAsset(organization_id=org.id, kind="image", mime="image/png", data=b"x")
    db_session.add(asset)
    db_session.commit()
    db_session.refresh(asset)
    intruder = User(email=f"intruder2-{next(_email_counter)}@example.com", hashed_password="x")
    db_session.add(intruder)
    db_session.commit()
    db_session.refresh(intruder)
    client._holder["user"] = intruder
    assert client.get(f"/content/asset/{asset.id}").status_code == 404


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
