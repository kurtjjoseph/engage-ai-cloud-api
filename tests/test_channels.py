"""Offline/deterministic tests for the channel-distribution adapter layer
(app/services/channels/). Uses an in-memory SQLite database via StaticPool
so the single connection is shared across the whole test, independent of
whatever DATABASE_URL the app is configured with elsewhere."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.entities import Organization, Publication, User
from app.services.channels import (
    ChannelAdapter,
    DISTRIBUTABLE_CHANNELS,
    distribute_engagement,
    get_adapter,
    register_adapter,
)
from app.services.channels.base import slugify


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


@pytest.fixture
def org(db_session):
    user = User(email="owner@example.com", hashed_password="not-a-real-hash")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    organization = Organization(
        owner_id=user.id,
        name="Grace Community Church",
        website_url="https://gracechurch.example",
        channel_details={"facebook": "https://facebook.com/gracechurch"},
    )
    db_session.add(organization)
    db_session.commit()
    db_session.refresh(organization)
    return organization


def make_engagement(channel: str, title: str = "Sunday Service Highlights") -> dict:
    return {
        "channel": channel,
        "type": "post",
        "title": title,
        "content": {"body": "Join us this Sunday!"},
        "risk": "low",
        "source_ticket_id": None,
    }


def test_distribute_engagement_creates_publication_for_each_distributable_channel(db_session, org):
    assert len(DISTRIBUTABLE_CHANNELS) == 6

    for channel in DISTRIBUTABLE_CHANNELS:
        engagement = make_engagement(channel)
        publication = distribute_engagement(db_session, org, engagement)

        assert isinstance(publication, Publication)
        assert publication.id is not None
        assert publication.organization_id == org.id
        assert publication.channel == channel
        assert publication.url
        assert publication.published_at is not None

    stored = db_session.query(Publication).filter_by(organization_id=org.id).all()
    assert len(stored) == 6
    assert {p.channel for p in stored} == set(DISTRIBUTABLE_CHANNELS)


def test_get_adapter_raises_for_unknown_channel():
    with pytest.raises(ValueError):
        get_adapter("email")


def test_register_adapter_overrides_channel(db_session, org):
    sentinel_url = "https://real-api.example/sentinel-post"

    class DummyAdapter(ChannelAdapter):
        channel = "facebook"

        def distribute(self, db, org, engagement):
            return self._record_publication(
                db, org, url=sentinel_url, label="dummy override"
            )

    register_adapter("facebook", DummyAdapter())
    try:
        publication = distribute_engagement(db_session, org, make_engagement("facebook"))
        assert publication.url == sentinel_url
        assert publication.label == "dummy override"
    finally:
        # Restore the default so this test doesn't leak state into others -
        # the registry is a module-level singleton shared across the process.
        from app.services.channels.social import SimulatedSocialAdapter

        register_adapter("facebook", SimulatedSocialAdapter(channel="facebook"))


def test_website_url_reflects_draft_marker(db_session, org):
    engagement = make_engagement("website", title="Easter Sunday Announcement!")
    publication = distribute_engagement(db_session, org, engagement)

    expected_slug = slugify("Easter Sunday Announcement!")
    assert expected_slug == "easter-sunday-announcement"
    assert publication.url == f"https://gracechurch.example/?engage_ai_draft={expected_slug}"
    assert "engage_ai_draft" in publication.url
    assert publication.label.startswith("WP draft:")


def test_website_url_falls_back_when_no_website_url_set(db_session):
    user = User(email="nowebsite@example.com", hashed_password="hash")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    organization = Organization(owner_id=user.id, name="No Website Org")
    db_session.add(organization)
    db_session.commit()
    db_session.refresh(organization)

    publication = distribute_engagement(db_session, organization, make_engagement("website"))
    assert publication.url.startswith("https://example.org/?engage_ai_draft=")


def test_social_url_is_deterministic(db_session, org):
    engagement = make_engagement("twitter_x", title="New Sermon Series")

    publication_1 = distribute_engagement(db_session, org, engagement)
    publication_2 = distribute_engagement(db_session, org, dict(engagement))

    assert publication_1.url == publication_2.url
    slug = slugify("New Sermon Series")
    assert publication_1.url == f"https://twitter_x.example/{slugify(org.name)}/{slug}"


def test_social_url_uses_channel_details_when_present(db_session, org):
    # org fixture has channel_details={"facebook": "https://facebook.com/gracechurch"}
    engagement = make_engagement("facebook", title="Weekly Update")
    publication = distribute_engagement(db_session, org, engagement)

    assert publication.url.startswith("https://facebook.com/gracechurch/")
    assert publication.label.startswith("Simulated facebook post:")
