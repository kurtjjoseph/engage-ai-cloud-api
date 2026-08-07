"""The Campaign Creator: plan -> save -> build.

Network-free. The one model call the planner makes is monkeypatched, and so is
the studio's drafting pass - what is tested for real is everything that must
hold whatever the model returns: surfaces resolve to real surfaces, dates stay
inside the operator's window, the arc keeps its order, a piece that fails
doesn't take the run down with it, and deleting a plan never deletes the drafts
it already produced.
"""
import itertools
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.routers.campaigns as campaigns_router
from app.db.session import Base, get_db
from app.deps import get_current_user
from app.main import app
from app.models.entities import Campaign, ContentItem, Organization, User
from app.services.campaign import CampaignPlanner, MAX_PIECES, MIN_PIECES, window

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(bind=engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
_email_counter = itertools.count()

START = date(2026, 9, 1)
END = date(2026, 9, 15)


@pytest.fixture
def db_session():
    s = TestingSessionLocal()
    try:
        yield s
    finally:
        s.close()


class _SharedSession:
    """The build worker opens its own SessionLocal (it runs outside the
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

    monkeypatch.setattr(campaigns_router, "SessionLocal", lambda: _SharedSession(db_session))
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
    user = User(email=f"campaign-{next(_email_counter)}@example.com", hashed_password="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    org = Organization(owner_id=user.id, name="Vision Outreach Media", org_type="business",
                       site_facts={"site_type": "business"})
    db.add(org)
    db.commit()
    db.refresh(org)
    return user, org


def _pieces(count=4):
    surfaces = ["instagram.feed_image", "linkedin.text_post", "facebook.photo_post", "website.post",
                "twitter_x.tweet", "instagram.carousel"]
    roles = ["hook", "teach", "proof", "offer", "recap", "answer"]
    return [
        {"headline": f"Piece {i}", "angle": f"Angle {i}", "why": f"Why {i}",
         "role": roles[i % len(roles)], "surface": surfaces[i % len(surfaces)], "day": i * 3}
        for i in range(count)
    ]


def _plan_response(count=4):
    return {"name": "Autumn open evening", "big_idea": "Come and see the work before you commit.",
            "audience": "Local owners who've been putting off a new site.", "pieces": _pieces(count)}


def _draft_payload():
    return {
        "title": "Websites that work while you sleep",
        "body": "Most small business sites go quiet after six. Ours don't. Book a free call to see how.",
        "hashtags": ["smallbusiness"],
        "image_prompt": "an empty shop counter at dusk, warm light",
        "image_alt": "An empty shop counter at dusk",
    }


@pytest.fixture
def planned(client, db_session, monkeypatch):
    """A saved campaign of four pieces, ready to build."""
    user, org = _seed(db_session)
    client._holder["user"] = user  # type: ignore[attr-defined]
    monkeypatch.setattr(campaigns_router.planner, "_json_call",
                        lambda system, user_payload, max_tokens: _plan_response())
    campaigns_router.planner.client = object()  # "a model is configured"

    plan = client.post(
        f"/campaigns/plan?organization_id={org.id}",
        json={"goal": "leads", "theme": "the autumn open evening", "pieces": 4,
              "starts_on": START.isoformat(), "ends_on": END.isoformat()},
    )
    assert plan.status_code == 200, plan.text
    body = plan.json()
    saved = client.post(f"/campaigns?organization_id={org.id}", json={
        "name": body["name"], "goal": "leads", "theme": body["theme"], "big_idea": body["big_idea"],
        "audience": body["audience"], "starts_on": body["starts_on"], "ends_on": body["ends_on"],
        "items": body["items"],
    })
    assert saved.status_code == 200, saved.text
    return org, saved.json()


# ------------------------------------------------------------------ the plan


def test_plan_returns_an_arc_scheduled_inside_the_window(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user  # type: ignore[attr-defined]
    monkeypatch.setattr(campaigns_router.planner, "_json_call",
                        lambda system, user_payload, max_tokens: _plan_response(5))
    campaigns_router.planner.client = object()

    resp = client.post(
        f"/campaigns/plan?organization_id={org.id}",
        json={"goal": "leads", "theme": "open evening", "pieces": 5,
              "starts_on": START.isoformat(), "ends_on": END.isoformat()},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["big_idea"]
    items = data["items"]
    assert len(items) == 5
    # Ordered, dated inside the window, and every piece knows its surface, its
    # channel and its job in the run.
    dates = [date.fromisoformat(i["scheduled_on"]) for i in items]
    assert dates == sorted(dates)
    assert all(START <= d <= END for d in dates)
    assert [i["index"] for i in items] == [0, 1, 2, 3, 4]
    assert all(i["status"] == "planned" and i["content_id"] is None for i in items)
    assert all(i["channel"] == i["surface"].split(".")[0] for i in items)


def test_plan_without_a_model_is_a_service_error_not_an_empty_campaign(client, db_session, monkeypatch):
    user, org = _seed(db_session)
    client._holder["user"] = user  # type: ignore[attr-defined]
    monkeypatch.setattr(campaigns_router.planner, "client", None)
    resp = client.post(f"/campaigns/plan?organization_id={org.id}", json={"goal": "leads"})
    assert resp.status_code == 503


def test_an_invented_surface_degrades_instead_of_failing_the_plan():
    planner = CampaignPlanner()
    from app.services.surfaces import SURFACES

    allowed = [s for s in SURFACES if s.channel == "instagram"]
    items = planner.shape_items(
        [{"headline": "H", "surface": "myspace.glitter", "role": "vibes", "day": 0}],
        allowed, 5, START, END,
    )
    assert len(items) == 1
    assert items[0]["surface"] == allowed[0].id
    assert items[0]["role"] == "hook"  # an unknown role degrades too


def test_pieces_out_of_the_window_are_pulled_back_into_it():
    planner = CampaignPlanner()
    from app.services.surfaces import SURFACES

    raw = [{"headline": "A", "surface": "instagram.feed_image", "day": -40},
           {"headline": "B", "surface": "linkedin.text_post", "day": 900}]
    items = planner.shape_items(raw, list(SURFACES), 5, START, END)
    assert [i["scheduled_on"] for i in items] == [START.isoformat(), END.isoformat()]


def test_a_plan_with_no_pacing_at_all_gets_spread_across_the_window():
    planner = CampaignPlanner()
    from app.services.surfaces import SURFACES

    raw = [{"headline": f"P{i}", "surface": "instagram.feed_image", "day": 0} for i in range(4)]
    items = planner.shape_items(raw, list(SURFACES), 4, START, END)
    dates = [date.fromisoformat(i["scheduled_on"]) for i in items]
    assert dates[0] == START and dates[-1] == END
    assert len(set(dates)) == 4


def test_reshaping_a_saved_plan_keeps_the_dates_it_was_given():
    """A plan goes out with "day" and comes back in with "scheduled_on" - the
    round trip must not silently reschedule a run the operator arranged."""
    planner = CampaignPlanner()
    from app.services.surfaces import SURFACES

    first = planner.shape_items(_pieces(4), list(SURFACES), 4, START, END)
    again = planner.shape_items(first, list(SURFACES), 4, START, END)
    assert [i["scheduled_on"] for i in again] == [i["scheduled_on"] for i in first]


def test_duplicate_pieces_are_dropped():
    planner = CampaignPlanner()
    from app.services.surfaces import SURFACES

    raw = [{"headline": "Same idea", "surface": "instagram.feed_image", "day": 0},
           {"headline": "same IDEA", "surface": "instagram.feed_image", "day": 4}]
    assert len(planner.shape_items(raw, list(SURFACES), 5, START, END)) == 1


def test_the_window_defaults_forwards_and_never_runs_backwards():
    today = date(2026, 9, 1)
    start, end = window(None, None, today)
    assert start == today and end > start
    start, end = window("2026-09-20", "2026-09-02", today)
    assert start == date(2026, 9, 20) and end == start


# ----------------------------------------------------------------- saving it


def test_saving_a_plan_stores_it_scoped_to_the_owner(client, planned, db_session):
    org, campaign = planned
    assert campaign["status"] == "planned"
    assert campaign["counts"] == {"total": 4, "drafted": 0, "failed": 0}
    stored = db_session.query(Campaign).filter(Campaign.id == campaign["id"]).first()
    assert stored.organization_id == org.id

    listed = client.get(f"/campaigns?organization_id={org.id}")
    assert [c["id"] for c in listed.json()] == [campaign["id"]]


def test_another_owners_campaign_is_not_reachable(client, planned, db_session):
    _, campaign = planned
    other_user, other_org = _seed(db_session)
    client._holder["user"] = other_user  # type: ignore[attr-defined]
    resp = client.get(f"/campaigns/{campaign['id']}?organization_id={other_org.id}")
    assert resp.status_code == 404


def test_pieces_can_be_moved_and_dropped_before_they_are_written(client, planned):
    org, campaign = planned
    moved = client.patch(
        f"/campaigns/{campaign['id']}/items/0?organization_id={org.id}",
        json={"scheduled_on": END.isoformat(), "surface": "linkedin.poll", "role": "answer"},
    )
    assert moved.status_code == 200, moved.text
    items = moved.json()["items"]
    # Moving a piece to the end of the run re-orders the plan and re-indexes it.
    assert [i["index"] for i in items] == [0, 1, 2, 3]
    assert items[-1]["surface"] == "linkedin.poll"
    assert items[-1]["channel"] == "linkedin"
    assert items[-1]["scheduled_on"] == END.isoformat()

    dropped = client.delete(f"/campaigns/{campaign['id']}/items/0?organization_id={org.id}")
    assert dropped.status_code == 200
    assert dropped.json()["counts"]["total"] == 3


def test_an_unknown_surface_or_date_is_rejected_on_edit(client, planned):
    org, campaign = planned
    bad_surface = client.patch(f"/campaigns/{campaign['id']}/items/0?organization_id={org.id}",
                               json={"surface": "myspace.glitter"})
    assert bad_surface.status_code == 400
    bad_date = client.patch(f"/campaigns/{campaign['id']}/items/0?organization_id={org.id}",
                            json={"scheduled_on": "next tuesday"})
    assert bad_date.status_code == 400


# ---------------------------------------------------------------- the build


def test_building_writes_every_piece_as_a_checked_content_item(client, planned, db_session, monkeypatch):
    org, campaign = planned
    seen: list[dict] = []

    def fake_draft(org_context, idea, surface, goal, site_type, brief=""):
        seen.append({"surface": surface.id, "brief": brief})
        return _draft_payload()

    monkeypatch.setattr(campaigns_router.studio, "draft_surface", fake_draft)

    started = client.post(f"/campaigns/{campaign['id']}/build?organization_id={org.id}")
    assert started.status_code == 200, started.text

    done = client.get(f"/campaigns/{campaign['id']}?organization_id={org.id}").json()
    assert done["status"] == "ready"
    assert done["counts"] == {"total": 4, "drafted": 4, "failed": 0}
    assert done["build"]["status"] == "done"

    items = done["items"]
    assert all(i["status"] == "drafted" and i["content_id"] for i in items)
    # Each piece is a real, quality-checked ContentItem carrying its campaign.
    for item in items:
        content = db_session.query(ContentItem).filter(ContentItem.id == item["content_id"]).first()
        assert content.campaign_id == campaign["id"]
        assert content.output_payload["body"]
        assert content.output_payload["studio"]["campaign"]["id"] == campaign["id"]
        assert item["quality"]["passed"] is True

    # The brief is the only thing a campaign piece knows that a one-off doesn't:
    # what the run argues, and where this piece sits in it.
    assert len(seen) == 4
    assert all("Come and see the work" in s["brief"] for s in seen)
    assert any("hook" in s["brief"] for s in seen)


def test_a_piece_is_held_to_its_role_not_the_campaigns_goal(client, planned, monkeypatch):
    """The goal here is "leads", which normally demands a call to action. A
    hook is briefed NOT to ask - so flagging it for obeying its brief would put
    a warning on almost every campaign, which is how warnings get ignored."""
    org, campaign = planned
    no_ask = {**_draft_payload(), "body": "Most of Amersfoort is asleep. This is what real sourdough takes."}
    monkeypatch.setattr(campaigns_router.studio, "draft_surface", lambda *a, **kw: no_ask)
    client.post(f"/campaigns/{campaign['id']}/build?organization_id={org.id}")

    items = client.get(f"/campaigns/{campaign['id']}?organization_id={org.id}").json()["items"]
    by_role = {i["role"]: i for i in items}
    def cta_issues(item):
        return [x for x in item["quality"]["issues"] if "call to action" in x["message"]]

    assert not cta_issues(by_role["hook"]), "a hook was flagged for not asking"
    assert not cta_issues(by_role["teach"])
    assert not cta_issues(by_role["proof"])
    # The piece whose whole job IS the ask still gets held to it.
    assert cta_issues(by_role["offer"])

    # And re-checking it in the studio keeps the same standard, rather than
    # re-flagging what the campaign deliberately allowed.
    hook_id = by_role["hook"]["content_id"]
    recheck = client.post(f"/studio/{hook_id}/check?organization_id={org.id}")
    assert recheck.status_code == 200, recheck.text
    assert not [x for x in recheck.json()["quality"]["issues"] if "call to action" in x["message"]]


def test_the_internal_role_never_leaks_into_a_pieces_title():
    strip = campaigns_router._strip_role_tag
    assert strip("Why your sourdough falls flat (Hook)", "hook") == "Why your sourdough falls flat"
    assert strip("While the city sleeps [Campaign Hook]", "hook") == "While the city sleeps"
    # Only this piece's own role, only bracketed, only at the end.
    assert strip("Why your sourdough falls flat (Hook)", "offer") == "Why your sourdough falls flat (Hook)"
    assert strip("Our autumn offer", "offer") == "Our autumn offer"
    assert strip("Hooked on real bread", "hook") == "Hooked on real bread"
    assert strip("(Hook)", "hook") == "(Hook)"  # never leaves a piece untitled


def test_one_failing_piece_does_not_abandon_the_run(client, planned, db_session, monkeypatch):
    org, campaign = planned
    calls = itertools.count()

    def flaky_draft(org_context, idea, surface, goal, site_type, brief=""):
        if next(calls) == 1:
            raise RuntimeError("the model hung up")
        return _draft_payload()

    monkeypatch.setattr(campaigns_router.studio, "draft_surface", flaky_draft)
    client.post(f"/campaigns/{campaign['id']}/build?organization_id={org.id}")

    done = client.get(f"/campaigns/{campaign['id']}?organization_id={org.id}").json()
    assert done["counts"] == {"total": 4, "drafted": 3, "failed": 1}
    assert done["status"] == "partial"
    assert done["build"]["status"] == "failed"
    failed = [i for i in done["items"] if i["status"] == "failed"]
    assert "the model hung up" in failed[0]["error"]


def test_a_failed_piece_can_be_retried_on_its_own(client, planned, monkeypatch):
    org, campaign = planned
    calls = itertools.count()

    def flaky_draft(org_context, idea, surface, goal, site_type, brief=""):
        if next(calls) == 0:
            return {}
        return _draft_payload()

    monkeypatch.setattr(campaigns_router.studio, "draft_surface", flaky_draft)
    client.post(f"/campaigns/{campaign['id']}/build?organization_id={org.id}")
    after_run = client.get(f"/campaigns/{campaign['id']}?organization_id={org.id}").json()
    assert after_run["counts"]["failed"] == 1

    retry = client.post(f"/campaigns/{campaign['id']}/items/0/build?organization_id={org.id}")
    assert retry.status_code == 200, retry.text
    assert retry.json()["item"]["status"] == "drafted"
    assert client.get(f"/campaigns/{campaign['id']}?organization_id={org.id}").json()["status"] == "ready"


def test_a_written_piece_is_not_silently_rewritten(client, planned, monkeypatch):
    org, campaign = planned
    monkeypatch.setattr(campaigns_router.studio, "draft_surface",
                        lambda *a, **kw: _draft_payload())
    client.post(f"/campaigns/{campaign['id']}/build?organization_id={org.id}")

    assert client.post(f"/campaigns/{campaign['id']}/items/0/build?organization_id={org.id}").status_code == 400
    assert client.patch(f"/campaigns/{campaign['id']}/items/0?organization_id={org.id}",
                        json={"headline": "changed"}).status_code == 400
    # Building again is a no-op rather than a second set of drafts.
    again = client.post(f"/campaigns/{campaign['id']}/build?organization_id={org.id}")
    assert again.json()["status"] == "done"
    assert client.get(f"/campaigns/{campaign['id']}?organization_id={org.id}").json()["counts"]["drafted"] == 4


def test_deleting_a_campaign_keeps_the_content_it_produced(client, planned, db_session, monkeypatch):
    org, campaign = planned
    monkeypatch.setattr(campaigns_router.studio, "draft_surface",
                        lambda *a, **kw: _draft_payload())
    client.post(f"/campaigns/{campaign['id']}/build?organization_id={org.id}")
    built = client.get(f"/campaigns/{campaign['id']}?organization_id={org.id}").json()
    content_ids = [i["content_id"] for i in built["items"]]

    assert client.delete(f"/campaigns/{campaign['id']}?organization_id={org.id}").status_code == 200
    assert client.get(f"/campaigns/{campaign['id']}?organization_id={org.id}").status_code == 404
    # The drafts are real work - some possibly already published. They stay,
    # unlinked from the campaign that is gone.
    remaining = db_session.query(ContentItem).filter(ContentItem.id.in_(content_ids)).all()
    assert len(remaining) == len(content_ids)
    assert all(c.campaign_id is None for c in remaining)


# ---------------------------------------------------------------- the pickers


def test_options_expose_the_goals_roles_and_surfaces_the_ui_picks_from(client, db_session):
    user, _ = _seed(db_session)
    client._holder["user"] = user  # type: ignore[attr-defined]
    data = client.get("/campaigns/options").json()
    assert {g["key"] for g in data["goals"]}
    assert {r["key"] for r in data["roles"]} >= {"hook", "proof", "offer"}
    assert data["pieces"] == {"min": MIN_PIECES, "max": MAX_PIECES, "default": 5}
    assert all(c["surfaces"] for c in data["channels"])
