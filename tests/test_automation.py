"""Tests for the queue-drainer: what may run itself, and what never may.

Most of this file is about the refusals rather than the work. The work is easy
to check and easy to see; the thing that has to hold under a hand-edited config,
an old stored shape and a caller that skipped the setter is that publishing
cannot be automated. So the gate is asserted at each of the three layers that
enforce it, including one that reaches past the API and writes the config
directly, the way a bad migration or a careless script would.
"""
import itertools
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.routers.studio as studio_router
import app.services.automation as automation
import app.services.channels.providers as providers_module
from app.services import crypto
from app.services.channels.base import ChannelAdapter
from app.services.channels.registry import register_adapter, unregister_adapter
from app.services.pipeline import READY
from app.db.session import Base, get_db
from app.deps import get_current_user
from app.main import app
from app.models.entities import (
    AutomationRun, ChannelConnection, ContentItem, Idea, Organization, Publication, User,
)

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(bind=engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
_counter = itertools.count()


class _SharedSession:
    """The drainer opens its own SessionLocal - it runs outside the request.
    Point that at the test's session and swallow its close()."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    s = TestingSessionLocal()
    # The drainer runs outside any request and opens its own session; so does
    # the render worker. Both must land on this one, whether or not the test
    # goes through the API.
    monkeypatch.setattr(automation, "SessionLocal", lambda: _SharedSession(s))
    monkeypatch.setattr(studio_router, "SessionLocal", lambda: _SharedSession(s))
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client(db, monkeypatch):
    def override_get_db():
        yield db

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


def _seed(db, client=None, **org_kwargs):
    user = User(email=f"auto-{next(_counter)}@example.com", hashed_password="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    org = Organization(owner_id=user.id, name="Vision Outreach Media", org_type="business", **org_kwargs)
    db.add(org)
    db.commit()
    db.refresh(org)
    if client is not None:
        client._holder["user"] = user
    return user, org


def _checkable(db, org, title="A piece"):
    """A draft in the studio's own shape, written but never measured - the
    NEEDS_CHECK queue."""
    item = ContentItem(
        organization_id=org.id, content_type="instagram", title=title,
        input_payload={"source": "studio"},
        output_payload={
            "body": "Most small business sites go quiet after six. Ours don't. Book a call.",
            "hashtags": [], "image_prompt": "", "image_alt": "", "fields": {}, "slides": [],
            "studio": {"version": 2, "goal": "leads", "idea": {"headline": title},
                       "surface": "instagram.feed_image", "surface_key": "feed_image",
                       "channel": "instagram", "step": "drafted"},
        },
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _steps(payload):
    return {s["key"]: s for s in payload["steps"]}


# ------------------------------------------------------------- publishing
#
# The conditions here replaced a blanket "publishing can never be automated".
# Each one can stop a post on its own, so each is tested on its own - and the
# most important assertion in this file is the negative one: when a channel is
# not genuinely ready, NOTHING is recorded. get_adapter() never refuses; it
# quietly hands back a simulated adapter, and distributing through that would
# write a Publication for a post that never happened, marking the piece
# published and dropping it out of the ready queue having never gone anywhere.


class _RealAdapter(ChannelAdapter):
    """Stands in for a live, API-backed adapter without touching a network.
    simulated = False is the whole point - it is what makes this a real send."""

    simulated = False

    def __init__(self, channel="instagram"):
        self.channel = channel
        self.sent = []

    def distribute(self, db, org, engagement):
        self.sent.append(engagement)
        return self._record_publication(
            db, org, url=f"https://{self.channel}.example/p/1", label="posted",
            content_item_id=engagement.get("content_item_id"),
        )


@contextmanager
def _live_channel(channel="instagram"):
    """Installs a real adapter for one channel, and takes it back out again.

    Unregisters rather than re-registering a simulated one on the way out -
    ARCHITECTURE 3.12 records a latent bug in this suite where "cleanup" pinned
    a channel to simulated for every test that ran afterwards.
    """
    adapter = _RealAdapter(channel)
    register_adapter(channel, adapter)
    try:
        yield adapter
    finally:
        unregister_adapter(channel)


def _ready(db, org, *, scheduled_on=None, passed=True, channel="instagram", title="A finished piece"):
    """A piece that has been written, checked and rendered - the ready queue."""
    item = ContentItem(
        organization_id=org.id, content_type=channel, title=title,
        input_payload={"source": "studio", "scheduled_on": scheduled_on.isoformat() if scheduled_on else None},
        output_payload={
            "channel": channel, "body": "Real copy that a person would actually read.",
            "hashtags": ["vom"], "media": "text",
            "studio": {"version": 2, "channel": channel, "surface": f"{channel}.feed_image",
                       "surface_key": "feed_image", "step": "checked",
                       "quality": {"score": 90, "passed": passed, "issues": [], "fixed": []},
                       "campaign": {"scheduled_on": scheduled_on.isoformat() if scheduled_on else None}},
        },
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_publishing_is_a_real_step_now_but_off_until_it_is_switched_on(db, client):
    _, org = _seed(db, client)

    steps = _steps(client.get(f"/organizations/{org.id}/automation").json())

    assert steps["channels.publish"]["automatable"] is True
    assert steps["channels.publish"]["gate"] is None
    # Off by default, like every step - the goal changed, the default did not.
    assert steps["channels.publish"]["enabled"] is False


def test_a_ready_piece_posts_on_the_day_it_was_planned_for(db, client):
    _, org = _seed(db, client)
    piece = _ready(db, org, scheduled_on=date.today())
    automation.set_settings(org, {"enabled": True, "steps": {"channels.publish": True}})
    db.commit()

    with _live_channel("instagram") as adapter:
        run = automation.run_now(db, org, trigger="manual")

    assert run.processed == 1 and run.failed == 0
    assert len(adapter.sent) == 1
    # The real copy went out, not a placeholder or a title.
    assert "Real copy" in adapter.sent[0]["content"]["body"]
    pub = db.query(Publication).filter(Publication.organization_id == org.id).one()
    assert pub.content_item_id == piece.id and pub.simulated is False


def test_nothing_is_recorded_when_the_channel_is_not_really_connected(db, client):
    """The load-bearing test. No override is installed, so the registry falls
    through to a simulated adapter - which must never be posted through."""
    _, org = _seed(db, client)
    _ready(db, org, scheduled_on=date.today())
    automation.set_settings(org, {"enabled": True, "steps": {"channels.publish": True}})
    db.commit()

    run = automation.run_now(db, org, trigger="manual")

    assert run.processed == 0 and run.failed == 0
    # No phantom publication - which is what would have marked the piece
    # published and dropped it out of the queue having never gone anywhere.
    assert db.query(Publication).filter(Publication.organization_id == org.id).count() == 0
    assert len(automation._content_by_position(db, org, READY)) == 1
    # And the reason is stated rather than the piece just being absent.
    reported = _steps(client.get(f"/organizations/{org.id}/automation").json())
    assert reported["channels.publish"]["holding"]["channel_not_ready"] == 1


def test_authorising_a_channel_is_not_consent_to_post_on_it(db, client, monkeypatch):
    """auto_post is the floor the whole design leans on: connecting a channel
    must not by itself start publishing."""
    _, org = _seed(db, client)
    monkeypatch.setattr(providers_module.settings, "facebook_client_id", "fb-id")
    monkeypatch.setattr(providers_module.settings, "facebook_client_secret", "fb-secret")
    connection = ChannelConnection(
        organization_id=org.id, channel="facebook", provider="facebook", status="connected",
        access_token_enc=crypto.encrypt("a-token"), auto_post=False,
        target={"page_id": "1", "page_token": "t"},
    )
    db.add(connection)
    db.commit()

    assert automation._live_adapter_or_none(db, org, "facebook") is None

    connection.auto_post = True
    db.commit()

    assert automation._live_adapter_or_none(db, org, "facebook") is not None


def test_a_piece_its_own_check_failed_is_not_posted(db, client):
    _, org = _seed(db, client)
    _ready(db, org, scheduled_on=date.today(), passed=False)
    automation.set_settings(org, {"enabled": True, "steps": {"channels.publish": True}})
    db.commit()

    with _live_channel("instagram") as adapter:
        run = automation.run_now(db, org, trigger="manual")

    # The studio already said this copy is wrong; publishing it anyway would be
    # the system contradicting itself.
    assert adapter.sent == []
    assert run.processed == 0


def test_a_piece_is_not_posted_before_its_day(db, client):
    _, org = _seed(db, client)
    _ready(db, org, scheduled_on=date.today() + timedelta(days=3))
    automation.set_settings(org, {"enabled": True, "steps": {"channels.publish": True}})
    db.commit()

    with _live_channel("instagram") as adapter:
        run = automation.run_now(db, org, trigger="manual")

    assert adapter.sent == []
    step = next(s for s in run.steps if s["key"] == "channels.publish")
    assert step["waiting"] == 0


def test_a_ready_piece_nobody_dated_is_counted_rather_than_stranded(db, client):
    """It will never post on a schedule it does not have. That has to be
    visible, or it is just content quietly going nowhere."""
    _, org = _seed(db, client)
    _ready(db, org, scheduled_on=None)

    steps = _steps(client.get(f"/organizations/{org.id}/automation").json())

    assert steps["channels.publish"]["waiting"] == 0
    assert steps["channels.publish"]["holding"]["no_date"] == 1


def test_a_publishing_mode_that_is_not_built_is_refused_not_ignored(db, client):
    """Storing "manual" and then posting anyway is the worst thing this could
    do, so an unbuilt mode is rejected at the door."""
    _, org = _seed(db, client)

    resp = client.patch(f"/organizations/{org.id}/automation", json={"publish_mode": "manual"})

    assert resp.status_code == 422
    assert "isn't built yet" in resp.json()["detail"]
    assert client.get(f"/organizations/{org.id}/automation").json()["publish_mode"] == "autonomous"


def test_an_unknown_publishing_mode_is_rejected(db, client):
    _, org = _seed(db, client)

    resp = client.patch(f"/organizations/{org.id}/automation", json={"publish_mode": "whenever"})

    assert resp.status_code == 422


# ------------------------------------------------------------------ defaults


def test_nothing_is_automated_until_someone_says_so(db, client):
    _, org = _seed(db, client)

    payload = client.get(f"/organizations/{org.id}/automation").json()

    assert payload["enabled"] is False
    assert all(step["enabled"] is False for step in payload["steps"])


def test_toggles_accept_a_bare_bool_or_a_cap_and_reject_nonsense(db, client):
    _, org = _seed(db, client)

    payload = client.patch(f"/organizations/{org.id}/automation", json={
        "enabled": True,
        "steps": {"studio.check": True, "studio.render": {"enabled": True, "max_per_run": 1}},
    }).json()

    steps = _steps(payload)
    assert payload["enabled"] is True
    assert steps["studio.check"]["enabled"] is True
    assert steps["studio.render"]["max_per_run"] == 1

    assert client.patch(f"/organizations/{org.id}/automation",
                        json={"steps": {"studio.invent": True}}).status_code == 422
    assert client.patch(f"/organizations/{org.id}/automation",
                        json={"steps": {"studio.check": {"max_per_run": 0}}}).status_code == 422
    assert client.patch(f"/organizations/{org.id}/automation",
                        json={"steps": {"studio.check": {"max_per_run": 10_000}}}).status_code == 422


def test_a_stored_cap_above_the_ceiling_is_clamped_not_obeyed(db):
    _, org = _seed(db)
    org.automation = {"enabled": True, "steps": {"studio.check": {"enabled": True, "max_per_run": 9999}}}
    db.commit()

    cap = automation.settings_for(org)["steps"]["studio.check"]["max_per_run"]

    assert cap == automation.MAX_PER_RUN_CEILING


# ------------------------------------------------------------- the two levels


def test_the_master_switch_governs_unattended_runs_not_the_run_now_button(db, client):
    _, org = _seed(db, client)
    _checkable(db, org)
    # The step is on; running while nobody watches is not.
    client.patch(f"/organizations/{org.id}/automation", json={"enabled": False, "steps": {"studio.check": True}})

    manual = _steps({"steps": client.get(f"/organizations/{org.id}/automation/preview?trigger=manual").json()["steps"]})
    scheduled = _steps({"steps": client.get(f"/organizations/{org.id}/automation/preview?trigger=scheduled").json()["steps"]})

    assert manual["studio.check"]["attempted"] == 1
    assert manual["studio.check"]["skipped_reason"] is None
    assert scheduled["studio.check"]["attempted"] == 0
    assert "unattended" in scheduled["studio.check"]["skipped_reason"].lower()


def test_a_scheduled_run_with_the_master_switch_off_does_nothing_and_says_so(db):
    _, org = _seed(db)
    _checkable(db, org)
    automation.set_settings(org, {"enabled": False, "steps": {"studio.check": True}})
    db.commit()

    run = automation.run_now(db, org, trigger="scheduled")

    assert run.status == "off"
    assert run.processed == 0


# ---------------------------------------------------------------- the queues


def test_waiting_counts_what_the_step_will_actually_act_on(db, client):
    _, org = _seed(db, client)
    _checkable(db, org, "one")
    _checkable(db, org, "two")
    # An older piece with no studio block sits in the same pipeline queue but
    # can never be measured - it must not be counted as work this step will do.
    db.add(ContentItem(organization_id=org.id, content_type="post", title="legacy",
                       input_payload={}, output_payload={"body": "from the old generator"}))
    db.commit()

    steps = _steps(client.get(f"/organizations/{org.id}/automation").json())

    assert steps["studio.check"]["waiting"] == 2


def test_a_run_drains_the_queue_and_records_what_it_touched(db, client):
    _, org = _seed(db, client)
    first, second = _checkable(db, org, "one"), _checkable(db, org, "two")
    automation.set_settings(org, {"enabled": True, "steps": {"studio.check": True}})
    db.commit()

    run = automation.run_now(db, org, trigger="manual")

    assert run.status == "done"
    assert run.processed == 2 and run.failed == 0
    check = next(s for s in run.steps if s["key"] == "studio.check")
    # Item-level, not just a count: the operator was not there, so "3 processed"
    # is not an answer to "what did it do".
    assert {i["ref"] for i in check["items"]} == {f"content:{first.id}", f"content:{second.id}"}
    assert all(i["ok"] is True for i in check["items"])

    db.refresh(first)
    assert (first.output_payload["studio"]).get("quality")


def test_a_run_never_takes_more_than_the_cap(db, client):
    _, org = _seed(db, client)
    for n in range(5):
        _checkable(db, org, f"piece {n}")
    automation.set_settings(org, {"enabled": True,
                                  "steps": {"studio.check": {"enabled": True, "max_per_run": 2}}})
    db.commit()

    run = automation.run_now(db, org, trigger="manual")

    check = next(s for s in run.steps if s["key"] == "studio.check")
    assert check["waiting"] == 5
    assert check["attempted"] == 2 and run.processed == 2


def test_one_items_failure_is_recorded_against_it_and_the_rest_still_run(db, client, monkeypatch):
    _, org = _seed(db, client)
    doomed = _checkable(db, org, "explodes")
    _checkable(db, org, "fine")
    automation.set_settings(org, {"enabled": True, "steps": {"studio.check": True}})
    db.commit()

    real = studio_router.recheck

    def flaky(session, organization, item, revise=False):
        if item.id == doomed.id:
            raise RuntimeError("the check blew up")
        return real(session, organization, item, revise)

    monkeypatch.setattr(studio_router, "recheck", flaky)

    run = automation.run_now(db, org, trigger="manual")

    assert run.processed == 1 and run.failed == 1
    check = next(s for s in run.steps if s["key"] == "studio.check")
    failed = next(i for i in check["items"] if i["ok"] is False)
    assert failed["ref"] == f"content:{doomed.id}"
    assert "blew up" in failed["detail"]


def test_something_that_can_never_advance_is_skipped_not_failed(db, client, monkeypatch):
    """A permanent "not possible" reported as a failure every sweep trains the
    operator to ignore the report."""
    _, org = _seed(db, client)
    _checkable(db, org, "no media to render")
    automation.set_settings(org, {"enabled": True, "steps": {"studio.check": True}})
    db.commit()

    monkeypatch.setattr(studio_router, "recheck",
                        lambda *a, **k: (_ for _ in ()).throw(automation.Skip("nothing to measure")))

    run = automation.run_now(db, org, trigger="manual")

    assert run.failed == 0 and run.processed == 0
    assert run.status == "nothing_to_do"
    check = next(s for s in run.steps if s["key"] == "studio.check")
    assert check["skipped"] == 1
    assert check["items"][0]["ok"] is None


def test_a_step_that_is_off_is_reported_rather_than_omitted(db, client):
    """"The checks didn't happen" and "the checks were never attempted" are
    different problems."""
    _, org = _seed(db, client)
    _checkable(db, org)
    automation.set_settings(org, {"enabled": True, "steps": {"studio.check": False}})
    db.commit()

    run = automation.run_now(db, org, trigger="manual")

    check = next(s for s in run.steps if s["key"] == "studio.check")
    assert check["waiting"] == 1 and check["attempted"] == 0
    assert check["skipped_reason"] == "This step is switched off."


# ------------------------------------------------------- the ideas queue


def test_writing_a_kept_idea_closes_the_loop_back_to_the_idea(db, client, monkeypatch):
    _, org = _seed(db, client)
    idea = Idea(organization_id=org.id, title="Why your site goes quiet after six",
                angle="the after-hours gap", channel="instagram", goal="leads", status="kept")
    db.add(idea)
    automation.set_settings(org, {"enabled": True, "steps": {"ideas.draft": True}})
    db.commit()
    db.refresh(idea)

    monkeypatch.setattr(automation.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(studio_router.studio, "draft_surface",
                        lambda context, seed, surface, goal, site_type, *a: {
                            "title": "Websites that work while you sleep",
                            "body": "Most small business sites go quiet after six. Ours don't. Book a free call.",
                            "hashtags": ["smallbusiness"], "image_prompt": "a shop counter at dusk",
                            "image_alt": "A shop counter at dusk",
                        })

    run = automation.run_now(db, org, trigger="manual")

    assert run.processed == 1
    db.refresh(idea)
    # The exact stall routers/ideas.py warns about: a written idea still marked
    # kept sits in the in-queue forever.
    assert idea.status == "drafted"
    assert idea.content_item_id is not None
    piece = db.query(ContentItem).filter(ContentItem.id == idea.content_item_id).first()
    assert piece.input_payload["source"] == "automation"  # who started it stays knowable
    assert piece.output_payload["studio"]["quality"]


def test_a_step_with_no_writing_key_is_blocked_visibly_not_silently(db, client, monkeypatch):
    _, org = _seed(db, client)
    db.add(Idea(organization_id=org.id, title="An idea", status="kept"))
    automation.set_settings(org, {"enabled": True, "steps": {"ideas.draft": True}})
    db.commit()
    monkeypatch.setattr(automation.settings, "anthropic_api_key", None)

    steps = _steps(client.get(f"/organizations/{org.id}/automation").json())

    assert steps["ideas.draft"]["enabled"] is True
    assert "key" in steps["ideas.draft"]["blocked_by"].lower()


def test_the_scan_queue_leaves_out_what_can_never_be_measured(db, client):
    _, org = _seed(db, client, enabled_modules=["analytics"])
    db.add(Publication(organization_id=org.id, channel="instagram", url="https://instagram.com/p/1"))
    db.add(Publication(organization_id=org.id, channel="email", url="newsletter-42"))
    db.commit()

    steps = _steps(client.get(f"/organizations/{org.id}/automation").json())

    # A private send is not publicly visible - there is nothing to search for,
    # now or ever, so it is not this step's backlog.
    assert steps["performance.scan"]["waiting"] == 1
    assert steps["performance.scan"]["blocked_by"] is None


# ------------------------------------------------------------------ the API


def test_run_now_drains_the_queue_end_to_end_through_the_api(db, client):
    _, org = _seed(db, client)
    piece = _checkable(db, org)
    client.patch(f"/organizations/{org.id}/automation", json={"steps": {"studio.check": True}})

    started = client.post(f"/organizations/{org.id}/automation/run").json()
    finished = client.get(f"/organizations/{org.id}/automation/runs/{started['id']}").json()

    assert finished["status"] == "done" and finished["processed"] == 1
    db.refresh(piece)
    assert piece.output_payload["studio"].get("quality")


def test_two_drainers_are_never_started_on_one_org(db, client):
    _, org = _seed(db, client)
    first = automation.start_run(db, org, trigger="manual")

    resp = client.post(f"/organizations/{org.id}/automation/run")

    assert resp.json()["id"] == first.id
    assert db.query(AutomationRun).filter(AutomationRun.organization_id == org.id).count() == 1


# ------------------------------------------- runs that died with their worker


def _abandoned(db, org, minutes_quiet: int) -> AutomationRun:
    """A run left "running" by a worker that went away - what a deploy landing
    mid-sweep leaves behind."""
    run = AutomationRun(organization_id=org.id, trigger="manual", status="running",
                        steps=[], processed=0, failed=0)
    db.add(run)
    db.commit()
    db.refresh(run)
    quiet_since = datetime.utcnow() - timedelta(minutes=minutes_quiet)
    run.started_at = quiet_since
    run.updated_at = quiet_since
    db.commit()
    return run


def test_a_run_abandoned_by_a_dead_worker_is_reaped_and_explained(db):
    _, org = _seed(db)
    run = _abandoned(db, org, minutes_quiet=45)

    reaped = automation.reap_stale_runs(db)

    assert reaped == 1
    db.refresh(run)
    assert run.status == "failed"
    assert run.finished_at is not None
    # The operator has to be able to tell "it broke" from "it is still going".
    assert "deploy" in (run.error or "").lower()


def test_an_abandoned_run_does_not_block_this_org_forever(db, client):
    """The bug this exists for: nothing else ever moved a "running" row out of
    that state, so one interrupted deploy locked the org out permanently."""
    _, org = _seed(db, client)
    _checkable(db, org)
    stuck = _abandoned(db, org, minutes_quiet=45)
    client.patch(f"/organizations/{org.id}/automation", json={"steps": {"studio.check": True}})

    started = client.post(f"/organizations/{org.id}/automation/run").json()

    # The POST answers the moment the row exists; the drain happens behind it.
    assert started["id"] != stuck.id
    finished = client.get(f"/organizations/{org.id}/automation/runs/{started['id']}").json()
    assert finished["status"] == "done" and finished["processed"] == 1


def test_a_run_still_making_progress_is_left_alone(db, client):
    """The reaper must not shoot a healthy long run - a sweep full of renders
    is legitimately slow, and only ever quiet between items."""
    _, org = _seed(db, client)
    live = _abandoned(db, org, minutes_quiet=2)

    resp = client.post(f"/organizations/{org.id}/automation/run").json()

    assert resp["id"] == live.id
    assert resp["status"] == "running"


def test_the_scheduled_sweep_will_not_race_a_run_already_in_flight(db):
    """run_now() walked straight past the one-drainer rule, so a sweep could
    start a second drainer on the same queues as a manual run."""
    _, org = _seed(db)
    _checkable(db, org)
    live = _abandoned(db, org, minutes_quiet=2)
    automation.set_settings(org, {"enabled": True, "steps": {"studio.check": True}})
    db.commit()

    returned = automation.run_now(db, org, trigger="scheduled")

    assert returned.id == live.id
    assert db.query(AutomationRun).filter(AutomationRun.organization_id == org.id).count() == 1


def test_the_scheduled_sweep_proceeds_once_the_stale_run_is_reaped(db):
    _, org = _seed(db)
    _checkable(db, org)
    stuck = _abandoned(db, org, minutes_quiet=45)
    automation.set_settings(org, {"enabled": True, "steps": {"studio.check": True}})
    db.commit()

    run = automation.run_now(db, org, trigger="scheduled")

    assert run.id != stuck.id
    assert run.status == "done" and run.processed == 1


def test_progress_is_recorded_as_the_run_goes_not_only_at_the_end(db):
    """updated_at is what tells a slow run from a dead one - if it were only
    written at the end, every long run would look abandoned."""
    _, org = _seed(db)
    _checkable(db, org)
    automation.set_settings(org, {"enabled": True, "steps": {"studio.check": True}})
    db.commit()

    run = automation.run_now(db, org, trigger="manual")

    assert run.updated_at is not None
    assert run.updated_at >= run.started_at


def test_a_run_is_readable_afterwards(db, client):
    _, org = _seed(db, client)
    _checkable(db, org)
    automation.set_settings(org, {"enabled": True, "steps": {"studio.check": True}})
    db.commit()
    run = automation.run_now(db, org, trigger="scheduled")

    listed = client.get(f"/organizations/{org.id}/automation/runs").json()
    one = client.get(f"/organizations/{org.id}/automation/runs/{run.id}").json()

    assert [r["id"] for r in listed] == [run.id]
    assert one["trigger"] == "scheduled" and one["status"] == "done"
    assert one["finished_at"]
    assert client.get(f"/organizations/{org.id}/automation/runs/9999").status_code == 404


def test_another_owner_cannot_read_or_change_this_orgs_automation(db, client):
    _, org = _seed(db, client)
    _seed(db, client)  # the second user becomes the caller

    assert client.get(f"/organizations/{org.id}/automation").status_code == 404
    assert client.patch(f"/organizations/{org.id}/automation",
                        json={"enabled": True}).status_code == 404
    assert client.post(f"/organizations/{org.id}/automation/run").status_code == 404
