"""Tests for the Content Studio's multi-pass workflow.

The two model-backed passes (ideas, draft) are monkeypatched so the suite stays
network-free; the quality check and the layout catalog are pure logic and are
tested for real. The renderers are stubbed at the boundary - what matters here
is that a render is started in the background, its status is reported honestly,
and the bytes come back owner-scoped."""
import itertools
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.routers.studio as studio_router
from app.db.session import Base, get_db
from app.deps import get_current_user
from app.main import app
from app.models.entities import ContentItem, Organization, User
from app.services.studio import StudioService
from app.services.studio_formats import (
    FORMATS,
    VIDEO_SECONDS,
    VIDEO_SLIDES,
    catalog,
    layout_for,
)

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


class _SharedSession:
    """The render worker opens its own SessionLocal (it runs outside the
    request). Point that at the test's in-memory session, and swallow the
    worker's close() so the fixture's session survives it."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


@pytest.fixture
def client(db_session, monkeypatch):
    def override_get_db():
        yield db_session

    monkeypatch.setattr(studio_router, "SessionLocal", lambda: _SharedSession(db_session))
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


def _seed(db):
    user = User(email=f"studio-{next(_email_counter)}@example.com", hashed_password="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    org = Organization(owner_id=user.id, name="Vision Outreach Media", org_type="business",
                       site_facts={"site_type": "business"})
    db.add(org)
    db.commit()
    db.refresh(org)
    return user, org


def _draft_payload(fmt="post_image"):
    base = {
        "title": "Websites that work while you sleep",
        "body": "Most small business sites go quiet after six. Ours don't. Book a free call to see how.",
        "hashtags": ["smallbusiness", "webdesign"],
        "image_prompt": "an empty shop counter at dusk, warm light",
        "image_alt": "An empty shop counter at dusk",
        "overlay": {},
        "slides": [],
    }
    if fmt == "image_text":
        base["overlay"] = {"headline": "Work while you sleep", "subhead": "Done-for-you sites", "cta": "Book a call"}
    if fmt == "video_slideshow":
        base["slides"] = [
            {"narration": f"Line {i}", "image_prompt": f"scene {i}"} for i in range(VIDEO_SLIDES)
        ]
    return base


def _make_draft(client, org, fmt="post_image", channel="instagram", goal="leads", monkeypatch=None):
    monkeypatch.setattr(studio_router.studio, "draft",
                        lambda org_context, idea, layout, g, site_type: _draft_payload(layout.format))
    resp = client.post(
        f"/studio/draft?organization_id={org.id}",
        json={"idea": {"headline": "H", "angle": "A", "why": "W"}, "format": fmt, "channel": channel, "goal": goal},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------------------------------------------ catalog
def test_catalog_exposes_exactly_the_three_supported_formats():
    data = catalog()
    assert [f["key"] for f in data["formats"]] == ["post_image", "image_text", "video_slideshow"]
    assert data["video"] == {"seconds": VIDEO_SECONDS, "slides": VIDEO_SLIDES}
    # Every format carries a usable layout for every channel it offers.
    for fmt in data["formats"]:
        for channel in fmt["channels"]:
            layout = channel["layout"]
            assert layout["width"] > 0 and layout["height"] > 0
            assert layout["body_max"] > 0


def test_video_is_eight_seconds_of_four_slides():
    assert VIDEO_SECONDS == 8.0
    assert VIDEO_SLIDES == 4
    assert VIDEO_SECONDS / VIDEO_SLIDES == 2.0


def test_layout_uses_channel_overrides_and_falls_back_safely():
    assert layout_for("instagram", "image_text").aspect == "4:5"
    assert layout_for("website", "post_image").width == 1200          # landscape override
    assert layout_for("instagram", "video_slideshow").aspect == "9:16"  # video is always vertical
    assert layout_for("twitter_x", "post_image").body_max == 270       # X's hard limit
    assert layout_for("website", "post_image").hashtags_max == 0
    unknown = layout_for("myspace", "telepathy")                       # never raises
    assert unknown.format in FORMATS and unknown.width > 0


# ------------------------------------------------------------ quality check
def test_check_trims_copy_past_the_channel_limit():
    service = StudioService.__new__(StudioService)  # no API key needed for the check
    service.client = None
    layout = layout_for("twitter_x", "post_image")
    draft = {**_draft_payload(), "body": "word " * 200}
    fixed, report = service.check(draft, layout, "awareness")
    assert len(fixed["body"]) <= layout.body_max
    assert any("Trimmed" in note for note in report["fixed"])
    assert report["passed"] is True  # trimming is a repair, not a failure


def test_check_strips_hashtags_where_the_channel_has_none():
    service = StudioService.__new__(StudioService)
    service.client = None
    fixed, report = service.check(_draft_payload(), layout_for("website", "post_image"), "awareness")
    assert fixed["hashtags"] == []
    assert any("hashtag" in note.lower() for note in report["fixed"])


def test_check_flags_placeholder_text_as_an_error():
    service = StudioService.__new__(StudioService)
    service.client = None
    draft = {**_draft_payload(), "body": "Come to [insert event name] on Sunday."}
    _, report = service.check(draft, layout_for("facebook", "post_image"), "awareness")
    assert report["passed"] is False
    assert any(i["severity"] == "error" for i in report["issues"])


def test_check_wants_a_call_to_action_only_when_the_goal_needs_one():
    service = StudioService.__new__(StudioService)
    service.client = None
    layout = layout_for("facebook", "post_image")
    draft = {**_draft_payload(), "body": "Here is a thought about small business websites today."}
    _, leads = service.check(draft, layout, "leads")
    _, awareness = service.check(draft, layout, "awareness")
    assert any("call to action" in i["message"] for i in leads["issues"])
    assert not any("call to action" in i["message"] for i in awareness["issues"])
    assert leads["score"] < awareness["score"]


def test_check_shortens_on_image_headline_to_stay_legible():
    service = StudioService.__new__(StudioService)
    service.client = None
    layout = layout_for("instagram", "image_text")
    draft = _draft_payload("image_text")
    draft["overlay"]["headline"] = "A headline " * 20
    fixed, report = service.check(draft, layout, "awareness")
    assert len(fixed["overlay"]["headline"]) <= layout.headline_max
    assert any("legible" in note for note in report["fixed"])


def test_check_requires_all_four_slides_and_trims_long_narration():
    service = StudioService.__new__(StudioService)
    service.client = None
    layout = layout_for("instagram", "video_slideshow")

    short = {**_draft_payload("video_slideshow"), "slides": [{"narration": "only one", "image_prompt": "x"}]}
    _, report = service.check(short, layout, "awareness")
    assert report["passed"] is False
    assert any("slides" in i["field"] for i in report["issues"])

    wordy = _draft_payload("video_slideshow")
    wordy["slides"][0]["narration"] = "a very long narration line " * 8
    fixed, report = service.check(wordy, layout, "awareness")
    assert len(fixed["slides"][0]["narration"]) <= 90
    assert report["passed"] is True


def test_check_fills_missing_alt_text_rather_than_failing():
    service = StudioService.__new__(StudioService)
    service.client = None
    draft = {**_draft_payload(), "image_alt": ""}
    fixed, report = service.check(draft, layout_for("instagram", "post_image"), "awareness")
    assert fixed["image_alt"]
    assert any("alt text" in note for note in report["fixed"])


# -------------------------------------------------------------- pass 1 + 2
def test_ideas_returns_503_when_no_ideas_come_back(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    monkeypatch.setattr(studio_router.studio, "ideas", lambda *a, **k: [])
    assert client.post(f"/studio/ideas?organization_id={org.id}", json={"goal": "leads"}).status_code == 503


def test_ideas_passes_the_goal_through(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    seen = {}

    def fake(org_context, goal, site_type, notes, count):
        seen.update(goal=goal, site_type=site_type, notes=notes)
        return [{"headline": "H", "angle": "", "why": "", "format": "post_image", "channel": "instagram"}]

    monkeypatch.setattr(studio_router.studio, "ideas", fake)
    resp = client.post(f"/studio/ideas?organization_id={org.id}", json={"goal": "sales", "notes": "3 slots left"})
    assert resp.status_code == 200
    assert seen == {"goal": "sales", "site_type": "business", "notes": "3 slots left"}
    assert resp.json()["ideas"][0]["headline"] == "H"


def test_draft_saves_a_checked_content_item(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    item = _make_draft(client, org, monkeypatch=monkeypatch)

    out = item["output_payload"]
    assert out["channel"] == "instagram"
    assert out["media"] == "image"
    assert out["studio"]["step"] == "checked"
    assert out["studio"]["quality"]["score"] == 100
    assert out["studio"]["layout"]["width"] == 1080
    # The older Content library reads these same fields.
    assert out["body"] and out["content_type_label"] == FORMATS["post_image"]["label"]


def test_website_drafts_carry_a_publishable_website_post(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    item = _make_draft(client, org, channel="website", monkeypatch=monkeypatch)
    assert item["output_payload"]["website_post"]["body_html"] == item["output_payload"]["body"]


def test_draft_returns_503_when_the_copy_pass_produces_nothing(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    monkeypatch.setattr(studio_router.studio, "draft", lambda *a, **k: {})
    resp = client.post(f"/studio/draft?organization_id={org.id}",
                       json={"idea": {"headline": "H"}, "format": "post_image", "channel": "instagram"})
    assert resp.status_code == 503


# ------------------------------------------------------------------ pass 3
def test_edit_saves_and_rechecks_in_one_step(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    item = _make_draft(client, org, monkeypatch=monkeypatch)

    resp = client.post(f"/studio/{item['id']}/edit?organization_id={org.id}",
                       json={"body": "x" * 5000, "hashtags": ["a"] * 30})
    assert resp.status_code == 200
    out = resp.json()["output_payload"]
    layout = layout_for("instagram", "post_image")
    assert len(out["body"]) <= layout.body_max
    assert len(out["hashtags"]) <= layout.hashtags_max
    assert out["studio"]["quality"]["fixed"]


def test_check_can_send_a_failing_draft_back_for_a_rewrite(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    item = _make_draft(client, org, goal="leads", monkeypatch=monkeypatch)
    # Break it: no call to action, and the goal demands one.
    client.post(f"/studio/{item['id']}/edit?organization_id={org.id}",
                json={"body": "A thought about websites in general."})

    def fake_revise(draft, layout, report, org_context):
        return {**draft, "body": "A thought about websites. Book a free call today."}

    monkeypatch.setattr(studio_router.studio, "revise", fake_revise)
    resp = client.post(f"/studio/{item['id']}/check?organization_id={org.id}&revise=true")
    assert resp.status_code == 200
    quality = resp.json()["quality"]
    assert quality["revised"] is True
    assert quality["passed"] is True
    assert not quality["issues"]


def test_check_rejects_content_that_did_not_come_from_the_studio(client, db_session):
    user, org = _seed(db_session)
    client._holder["user"] = user
    item = ContentItem(organization_id=org.id, content_type="website_post", title="Old",
                       input_payload={}, output_payload={"body": "x"})
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    assert client.post(f"/studio/{item.id}/check?organization_id={org.id}").status_code == 400


# ------------------------------------------------------------------ pass 4
def test_render_runs_in_the_background_and_serves_the_asset(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    item = _make_draft(client, org, fmt="image_text", monkeypatch=monkeypatch)

    seen = {}

    def fake_render_text_image(prompt, headline, subhead, cta, width, height):
        seen.update(headline=headline, cta=cta, width=width, height=height)
        return b"\xff\xd8fake-jpeg", "image/jpeg"

    monkeypatch.setattr(studio_router.renderer, "render_text_image", fake_render_text_image)

    started = client.post(f"/studio/{item['id']}/render?organization_id={org.id}")
    assert started.status_code == 200
    # TestClient runs background tasks before returning, so it's already done.
    status = client.get(f"/studio/{item['id']}/render?organization_id={org.id}").json()
    assert status["status"] == "done"
    assert status["kind"] == "image"
    assert seen["headline"] == "Work while you sleep"
    assert (seen["width"], seen["height"]) == (1080, 1350)

    asset = client.get(f"/content/asset/{status['asset_id']}")
    assert asset.status_code == 200
    assert asset.content == b"\xff\xd8fake-jpeg"


def test_video_render_uses_the_eight_second_slideshow(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    item = _make_draft(client, org, fmt="video_slideshow", monkeypatch=monkeypatch)

    seen = {}

    def fake_slideshow(slides, width, height, seconds):
        seen.update(slides=len(slides), width=width, height=height, seconds=seconds)
        return b"fake-mp4", "video/mp4"

    monkeypatch.setattr(studio_router.renderer, "render_slideshow", fake_slideshow)
    client.post(f"/studio/{item['id']}/render?organization_id={org.id}")

    status = client.get(f"/studio/{item['id']}/render?organization_id={org.id}").json()
    assert status["status"] == "done" and status["kind"] == "video"
    assert seen == {"slides": VIDEO_SLIDES, "width": 720, "height": 1280, "seconds": VIDEO_SECONDS}


def test_a_failed_render_is_reported_not_swallowed(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    item = _make_draft(client, org, monkeypatch=monkeypatch)
    monkeypatch.setattr(studio_router.renderer, "render_post_image", lambda *a, **k: None)

    client.post(f"/studio/{item['id']}/render?organization_id={org.id}")
    status = client.get(f"/studio/{item['id']}/render?organization_id={org.id}").json()
    assert status["status"] == "failed"
    assert status["error"]


def test_a_render_stuck_running_is_reported_failed_so_it_can_be_retried(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    item = _make_draft(client, org, monkeypatch=monkeypatch)

    stored = db_session.query(ContentItem).filter(ContentItem.id == item["id"]).first()
    output = dict(stored.output_payload)
    state = dict(output["studio"])
    state["render"] = {"status": "running",
                       "started_at": (datetime.utcnow() - timedelta(hours=2)).isoformat()}
    output["studio"] = state
    stored.output_payload = output
    db_session.commit()

    status = client.get(f"/studio/{item['id']}/render?organization_id={org.id}").json()
    assert status["status"] == "failed"


def test_render_needs_something_to_render(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    item = _make_draft(client, org, monkeypatch=monkeypatch)

    stored = db_session.query(ContentItem).filter(ContentItem.id == item["id"]).first()
    output = dict(stored.output_payload)
    output["image_prompt"] = ""
    stored.output_payload = output
    db_session.commit()

    assert client.post(f"/studio/{item['id']}/render?organization_id={org.id}").status_code == 400


def test_assets_are_scoped_to_their_owner(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user
    item = _make_draft(client, org, monkeypatch=monkeypatch)
    monkeypatch.setattr(studio_router.renderer, "render_post_image", lambda *a, **k: (b"bytes", "image/jpeg"))
    client.post(f"/studio/{item['id']}/render?organization_id={org.id}")
    asset_id = client.get(f"/studio/{item['id']}/render?organization_id={org.id}").json()["asset_id"]

    other, _ = _seed(db_session)
    client._holder["user"] = other
    assert client.get(f"/content/asset/{asset_id}").status_code == 404
