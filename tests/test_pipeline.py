"""The in/out queues, and the promise that nothing falls out of them.

The interesting tests here are not "does the ready pile contain the ready
piece" - they are the conservation ones. content_position() is meant to be
total, so the suite feeds it deliberately broken and unforeseen shapes and
asserts that every one still lands somewhere, and that the counts reconcile
against the raw row count rather than against each other.
"""
import itertools
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.entities import (
    Campaign, ContentItem, Idea, Organization, Publication, PublicationSnapshot, User,
)
from app.services.pipeline import (
    CONTENT_POSITIONS, NEEDS_CHECK, NEEDS_MEDIA, PUBLISHED, READY, UNROUTED,
    build_pipeline, content_position,
)

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(bind=engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
_counter = itertools.count()

TODAY = date(2026, 9, 15)


@pytest.fixture
def db():
    s = TestingSessionLocal()
    try:
        yield s
    finally:
        s.close()


def _org(db):
    user = User(email=f"pipeline-{next(_counter)}@example.com", hashed_password="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    org = Organization(owner_id=user.id, name="VOM")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _content(db, org, payload, title="A piece"):
    item = ContentItem(organization_id=org.id, content_type="post", title=title,
                       input_payload={}, output_payload=payload)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# --- content_position is total --------------------------------------------


@pytest.mark.parametrize("payload", [
    {},                                             # nothing at all
    {"studio": {}},                                 # a studio block with nothing in it
    {"studio": {"quality": {"score": 80}}},         # checked, text only
    {"body": "text", "media": "image", "studio": {"quality": {"score": 80}}},
    {"studio": {"quality": {"score": 80}, "render": {"status": "running"}}},
    {"studio": {"quality": {"score": 80}, "render": {"status": "failed"}}},
    {"studio": {"quality": {"score": 80}, "render": {"status": "done"}}},
    {"studio": None},                               # wrong type entirely
    {"unexpected": "shape"},                        # a payload nobody designed
    {"studio": {"step": "checked"}},                # step set but never checked
])
def test_every_shape_lands_in_exactly_one_position(db, payload):
    org = _org(db)
    item = _content(db, org, payload)

    position = content_position(item, published_ids=set())

    # The point of the exercise: no input returns None, raises, or invents a
    # position - including shapes that do not exist yet.
    assert position in CONTENT_POSITIONS


def test_a_published_piece_is_published_whatever_else_it_looks_like(db):
    org = _org(db)
    item = _content(db, org, {})  # would otherwise be unrouted

    assert content_position(item, published_ids={item.id}) == PUBLISHED


def test_an_unchecked_piece_waits_for_checking(db):
    org = _org(db)
    item = _content(db, org, {"body": "written but never checked"})

    assert content_position(item, set()) == NEEDS_CHECK


def test_a_piece_that_wants_media_is_not_ready_until_it_renders(db):
    org = _org(db)
    waiting = _content(db, org, {"media": "image", "studio": {"quality": {"score": 9}, "render": {"status": "running"}}})
    done = _content(db, org, {"media": "image", "studio": {"quality": {"score": 9}, "render": {"status": "done"}}})

    assert content_position(waiting, set()) == NEEDS_MEDIA
    assert content_position(done, set()) == READY


def test_a_failed_render_is_still_waiting_not_quietly_ready(db):
    org = _org(db)
    item = _content(db, org, {"media": "image", "studio": {"quality": {"score": 9}, "render": {"status": "failed"}}})

    # A failed render must not read as "ready to publish" - that is exactly the
    # kind of piece that would otherwise go out with no image.
    assert content_position(item, set()) == NEEDS_MEDIA


def test_a_text_piece_needs_no_media_to_be_ready(db):
    org = _org(db)
    item = _content(db, org, {"body": "words", "media": "text", "studio": {"quality": {"score": 9}}})

    assert content_position(item, set()) == READY


# --- the reconciliation ----------------------------------------------------


def test_the_counts_reconcile_against_the_raw_row_count(db):
    org = _org(db)
    for payload in ({}, {"body": "x"}, {"studio": {"quality": {"s": 1}}},
                    {"media": "image", "studio": {"quality": {"s": 1}}},
                    {"unexpected": "shape"}):
        _content(db, org, payload)

    result = build_pipeline(db, org.id, today=TODAY)
    rec = result["reconciliation"]

    # Not "the buckets agree with each other" - the buckets agree with the
    # number of rows in the table, which is the only figure that cannot be
    # wrong for the same reason the buckets are.
    assert rec["content_total"] == db.query(ContentItem).filter(ContentItem.organization_id == org.id).count()
    assert rec["accounted_for"] == rec["content_total"]
    assert sum(rec["by_position"].values()) == rec["content_total"]


def test_an_unrecognised_piece_is_shown_as_stuck_not_dropped(db):
    org = _org(db)
    _content(db, org, {})

    result = build_pipeline(db, org.id, today=TODAY)

    assert result["reconciliation"]["by_position"][UNROUTED] == 1
    assert result["stages"]["studio"]["stuck"]["count"] == 1
    assert result["stages"]["library"]["stuck"]["count"] == 1


# --- the stages ------------------------------------------------------------


def test_kept_ideas_are_the_ideas_in_queue_and_drafted_ones_the_out(db):
    org = _org(db)
    db.add_all([
        Idea(organization_id=org.id, title="Waiting", status="kept"),
        Idea(organization_id=org.id, title="Written", status="drafted"),
        Idea(organization_id=org.id, title="Turned down", status="dismissed"),
    ])
    db.commit()

    ideas = build_pipeline(db, org.id, today=TODAY)["stages"]["ideas"]

    assert ideas["in"]["count"] == 1
    assert ideas["out"]["count"] == 1


def test_a_piece_scheduled_in_the_past_and_never_written_is_overdue(db):
    org = _org(db)
    db.add(Campaign(organization_id=org.id, name="Autumn", plan=[
        {"index": 0, "channel": "instagram", "scheduled_on": (TODAY - timedelta(days=3)).isoformat(), "status": "planned"},
        {"index": 1, "channel": "facebook", "scheduled_on": (TODAY + timedelta(days=3)).isoformat(), "status": "planned"},
        {"index": 2, "channel": "website", "scheduled_on": (TODAY - timedelta(days=1)).isoformat(), "status": "drafted"},
    ]))
    db.commit()

    calendar = build_pipeline(db, org.id, today=TODAY)["stages"]["calendar"]

    # Only the first: the second has not come round yet, and the third was
    # actually written. Work that quietly did not happen is the whole point.
    assert calendar["stuck"]["count"] == 1
    assert calendar["in"]["count"] == 1


def test_an_undated_piece_is_stuck_rather_than_invisible(db):
    org = _org(db)
    db.add(Campaign(organization_id=org.id, name="No dates", plan=[
        {"index": 0, "channel": "instagram", "scheduled_on": None, "status": "planned"},
    ]))
    db.commit()

    calendar = build_pipeline(db, org.id, today=TODAY)["stages"]["calendar"]

    assert calendar["stuck"]["count"] == 1


def test_a_publication_nobody_measured_is_the_performance_in_queue(db):
    org = _org(db)
    measured = Publication(organization_id=org.id, channel="instagram", url="https://example.com/a")
    unmeasured = Publication(organization_id=org.id, channel="facebook", url="https://example.com/b")
    db.add_all([measured, unmeasured])
    db.commit()
    db.refresh(measured)
    db.add(PublicationSnapshot(publication_id=measured.id, score=70))
    db.commit()

    performance = build_pipeline(db, org.id, today=TODAY)["stages"]["performance"]

    assert performance["in"]["count"] == 1
    assert performance["out"]["count"] == 1


def test_another_orgs_work_never_appears(db):
    org = _org(db)
    other = _org(db)
    _content(db, other, {"body": "theirs"})

    result = build_pipeline(db, org.id, today=TODAY)

    assert result["reconciliation"]["content_total"] == 0


def test_an_archived_campaign_leaves_the_queues(db):
    org = _org(db)
    campaign = Campaign(organization_id=org.id, name="Done with", status="archived", plan=[
        {"index": 0, "channel": "instagram", "scheduled_on": (TODAY - timedelta(days=9)).isoformat(), "status": "planned"},
    ])
    db.add(campaign)
    db.commit()

    calendar = build_pipeline(db, org.id, today=TODAY)["stages"]["calendar"]

    # Archiving is how an operator says "stop showing me this"; an archived
    # campaign reporting overdue work forever would make the queue useless.
    assert calendar["stuck"]["count"] == 0
