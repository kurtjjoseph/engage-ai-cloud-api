"""Offline tests for the Postiz relay (app/services/channels/postiz*.py).

Nothing here reaches a real Postiz instance: every HTTP call is served by a
fake transport that also records what was sent, so the assertions are about the
request Engage AI actually makes - which is the part that has to be right.

What these pin down:

* the API key is encrypted at rest and never returned by any endpoint
* a direct ChannelConnection still wins over Postiz for the same channel
* connecting Postiz does not by itself grant unattended posting
* a queued post is recorded as queued, with no invented permalink, and
  reconcile() is what turns it into a published one
* one channel failing in a fan-out publish doesn't cancel the others
* a re-sync preserves the choices a person made (auto_post, settings)
"""

import itertools
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.deps import get_current_user
from app.main import app
from app.models.entities import (
    ChannelConnection,
    ContentItem,
    MediaAsset,
    Organization,
    PostizChannel,
    PostizWorkspace,
    Publication,
    User,
)
from app.services import crypto
from app.services.channels import postiz_store
from app.services.channels.postiz import (
    PostizClient,
    PostizError,
    normalize_base_url,
    post_text,
)
from app.services.channels.registry import get_adapter

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

_email_counter = itertools.count()

INTEGRATIONS = [
    {"id": "int-fb", "name": "Grace Church", "identifier": "facebook", "picture": None},
    {"id": "int-li", "name": "Grace Church", "identifier": "linkedin-page"},
    {"id": "int-tt", "name": "@gracechurch", "identifier": "tiktok"},
    {"id": "int-lemmy", "name": "Lemmy", "identifier": "lemmy"},
    {"id": "int-off", "name": "Old X", "identifier": "x", "disabled": True},
]


# ------------------------------------------------------------------ fake API


class FakePostiz:
    """A stand-in Postiz instance. Records every request and answers with the
    shapes the real public API returns."""

    def __init__(self, integrations=None):
        self.integrations = integrations if integrations is not None else INTEGRATIONS
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []
        self.fail_for_integration: set[str] = set()
        self.released: dict[str, str] = {}
        self._ids = itertools.count(1)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        if path.endswith("/integrations"):
            if request.headers.get("Authorization") != "key-abc":
                return httpx.Response(401, json={"message": "Unauthorized"})
            return httpx.Response(200, json=self.integrations)

        if path.endswith("/upload"):
            return httpx.Response(200, json={"id": "media-1", "path": "https://cdn.example/m.png"})

        if path.endswith("/posts") and request.method == "POST":
            body = json.loads(request.content)
            self.bodies.append(body)
            integration_id = body["posts"][0]["integration"]["id"]
            if integration_id in self.fail_for_integration:
                return httpx.Response(400, json={"message": f"{integration_id} refused the post"})
            return httpx.Response(200, json=[{"id": f"post-{next(self._ids)}"}])

        if path.endswith("/posts") and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"id": post_id, "releaseURL": url, "state": "PUBLISHED"}
                    for post_id, url in self.released.items()
                ],
            )

        return httpx.Response(404, json={"message": f"no route for {path}"})

    def install(self, monkeypatch):
        transport = httpx.MockTransport(self.handler)

        def fake_request(method, url, **kwargs):
            kwargs.pop("timeout", None)
            with httpx.Client(transport=transport) as client:
                return client.request(method, url, **kwargs)

        monkeypatch.setattr(httpx, "request", fake_request)
        return self


# ------------------------------------------------------------------ fixtures


def _make_user(db) -> User:
    user = User(
        email=f"postiz-owner-{next(_email_counter)}@example.com",
        hashed_password="not-a-real-hash",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def org(db_session):
    user = _make_user(db_session)
    organization = Organization(
        owner_id=user.id,
        name="Grace Community Church",
        channel_details={"facebook": "https://facebook.com/gracechurch"},
    )
    db_session.add(organization)
    db_session.commit()
    db_session.refresh(organization)
    return organization


@pytest.fixture
def client(db_session, org):
    """A TestClient authenticated as the org's owner, on the same session the
    test inspects - so a row written by an endpoint is visible to assertions."""

    def override_get_db():
        yield db_session

    def override_user():
        return db_session.query(User).filter(User.id == org.owner_id).first()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_user
    # Not a context manager on purpose: entering one runs the app's startup
    # hooks, which reach for the real configured database.
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def postiz(monkeypatch):
    return FakePostiz().install(monkeypatch)


def _content(db_session, org, title="Sunday Service") -> ContentItem:
    item = ContentItem(
        organization_id=org.id,
        content_type="announcement",
        title=title,
        input_payload={"topic": title},
        output_payload={"title": title, "body": "Join us this Sunday!", "hashtags": ["grace"]},
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def _connect(client, org) -> dict:
    response = client.post(
        f"/organizations/{org.id}/postiz/connect",
        json={"api_key": "key-abc", "base_url": "https://postiz.example/api"},
    )
    assert response.status_code == 200, response.text
    return response.json()


# -------------------------------------------------------------------- units


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "https://api.postiz.com/public/v1"),
        ("https://api.postiz.com", "https://api.postiz.com/public/v1"),
        ("https://api.postiz.com/", "https://api.postiz.com/public/v1"),
        ("https://api.postiz.com/public/v1", "https://api.postiz.com/public/v1"),
        ("http://postiz.internal:5000/api", "http://postiz.internal:5000/api/public/v1"),
    ],
)
def test_normalize_base_url_accepts_what_operators_paste(raw, expected):
    assert normalize_base_url(raw) == expected


def test_post_text_trims_to_the_channel_limit():
    engagement = {"content": {"body": "x" * 400, "hashtags": ["a"]}, "title": "T"}
    text = post_text(engagement, limit=280)
    assert len(text) == 280
    assert text.endswith("…")


def test_client_strips_a_pasted_bearer_prefix(postiz):
    PostizClient("https://postiz.example/api", "Bearer key-abc").integrations()
    assert postiz.requests[-1].headers["Authorization"] == "key-abc"


def test_client_reports_a_bad_key_readably(postiz):
    with pytest.raises(PostizError) as excinfo:
        PostizClient("https://postiz.example/api", "wrong-key").integrations()
    assert "401" in str(excinfo.value)


# ------------------------------------------------------------------- connect


def test_connect_stores_the_key_encrypted_and_never_returns_it(client, db_session, org, postiz):
    body = _connect(client, org)

    assert body["connected"] is True
    assert "key-abc" not in json.dumps(body)

    workspace = db_session.query(PostizWorkspace).filter_by(organization_id=org.id).one()
    assert workspace.api_key_enc and workspace.api_key_enc != "key-abc"
    assert crypto.decrypt(workspace.api_key_enc) == "key-abc"
    assert workspace.base_url == "https://postiz.example/api/public/v1"


def test_connect_maps_known_platforms_and_skips_unknown_ones(client, db_session, org, postiz):
    body = _connect(client, org)

    channels = {row["integration_id"]: row for row in body["channels"]}
    assert set(channels) == {"int-fb", "int-li", "int-tt", "int-off"}
    assert channels["int-li"]["channel"] == "linkedin"  # linkedin-page -> linkedin
    assert channels["int-off"]["disabled"] is True
    # lemmy has no Engage AI channel, so it is never stored and can't be posted to
    assert "int-lemmy" not in channels


def test_connect_rejects_a_bad_key_before_storing_anything(client, db_session, org, postiz):
    response = client.post(f"/organizations/{org.id}/postiz/connect", json={"api_key": "nope"})
    assert response.status_code == 400
    assert "401" in response.json()["detail"]
    assert db_session.query(PostizWorkspace).filter_by(organization_id=org.id).count() == 0


def test_connecting_does_not_turn_on_unattended_posting(client, org, postiz):
    body = _connect(client, org)
    assert all(row["auto_post"] is False for row in body["channels"])


def test_sync_preserves_the_choices_a_person_made(client, db_session, org, postiz):
    _connect(client, org)
    client.patch(
        f"/organizations/{org.id}/postiz/channels/int-tt",
        json={"auto_post": True, "settings": {"privacy_level": "SELF_ONLY"}},
    )

    postiz.integrations = [
        {"id": "int-fb", "name": "Grace Church (renamed)", "identifier": "facebook"},
        {"id": "int-tt", "name": "@gracechurch", "identifier": "tiktok"},
    ]
    body = client.post(f"/organizations/{org.id}/postiz/sync").json()
    channels = {row["integration_id"]: row for row in body["channels"]}

    assert channels["int-fb"]["account_name"] == "Grace Church (renamed)"  # Postiz owns this
    assert channels["int-tt"]["auto_post"] is True  # we own this
    assert channels["int-tt"]["settings"] == {"privacy_level": "SELF_ONLY"}
    # Removed in Postiz -> disabled here, and consent revoked with it.
    assert channels["int-li"]["disabled"] is True
    assert channels["int-li"]["auto_post"] is False


def test_disconnect_forgets_the_key_and_stops_posting(client, db_session, org, postiz):
    _connect(client, org)
    body = client.delete(f"/organizations/{org.id}/postiz").json()

    assert body["connected"] is False
    workspace = db_session.query(PostizWorkspace).filter_by(organization_id=org.id).one()
    assert workspace.api_key_enc is None
    assert postiz_store.postiz_adapter_for(db_session, org.id, "facebook") is None


# ------------------------------------------------------------------ routing


def test_registry_prefers_a_direct_connection_over_postiz(client, db_session, org, postiz):
    _connect(client, org)

    direct = ChannelConnection(
        organization_id=org.id,
        channel="facebook",
        provider="facebook",
        status="connected",
        access_token_enc=crypto.encrypt("page-token"),
        target={"page_id": "page-1"},
    )
    db_session.add(direct)
    db_session.commit()

    adapter = get_adapter("facebook", db=db_session, org=org)
    assert type(adapter).__name__ == "FacebookPageAdapter"

    # LinkedIn has no direct connection, so it relays.
    assert type(get_adapter("linkedin", db=db_session, org=org)).__name__ == "PostizAdapter"


def test_postiz_only_channel_falls_back_to_simulated_without_a_workspace(db_session, org):
    adapter = get_adapter("tiktok", db=db_session, org=org)
    assert adapter.simulated is True


def test_unknown_channel_still_raises(db_session, org):
    with pytest.raises(ValueError):
        get_adapter("email", db=db_session, org=org)


def test_auto_post_gates_the_unattended_path(client, db_session, org, postiz):
    _connect(client, org)

    assert get_adapter("facebook", db=db_session, org=org, require_auto_post=True).simulated is True

    client.patch(f"/organizations/{org.id}/postiz/channels/int-fb", json={"auto_post": True})
    adapter = get_adapter("facebook", db=db_session, org=org, require_auto_post=True)
    assert adapter.simulated is False


# ------------------------------------------------------------------ publish


def test_publish_needs_an_explicit_confirmation(client, db_session, org, postiz):
    _connect(client, org)
    item = _content(db_session, org)

    response = client.post(
        f"/organizations/{org.id}/postiz/publish",
        json={"content_id": item.id, "channels": ["facebook"]},
    )
    assert response.status_code == 400
    assert "confirm=true" in response.json()["detail"]["message"]
    assert db_session.query(Publication).filter_by(organization_id=org.id).count() == 0


def test_publish_sends_the_right_body_and_records_a_queued_publication(
    client, db_session, org, postiz
):
    _connect(client, org)
    item = _content(db_session, org)

    body = client.post(
        f"/organizations/{org.id}/postiz/publish",
        json={"content_id": item.id, "channels": ["facebook"], "confirm": True},
    ).json()

    assert body["published"] == 1 and body["failed"] == 0
    result = body["results"][0]
    assert result["status"] == "queued"
    assert result["postiz_post_id"] == "post-1"
    # No invented permalink: it points at the account, not at a post that
    # doesn't exist yet.
    assert result["url"] == "https://facebook.com/gracechurch"

    sent = postiz.bodies[-1]
    assert sent["type"] == "now"
    assert sent["posts"][0]["integration"]["id"] == "int-fb"
    assert sent["posts"][0]["settings"]["__type"] == "facebook"
    assert "Join us this Sunday!" in sent["posts"][0]["value"][0]["content"]
    assert "#grace" in sent["posts"][0]["value"][0]["content"]

    publication = db_session.query(Publication).filter_by(organization_id=org.id).one()
    assert publication.simulated is False
    assert publication.delivery == "postiz"
    assert publication.status == "queued"
    assert publication.external_id == "post-1"


def test_publish_to_every_channel_reports_each_outcome_separately(
    client, db_session, org, postiz
):
    _connect(client, org)
    item = _content(db_session, org)
    postiz.fail_for_integration.add("int-li")

    body = client.post(
        f"/organizations/{org.id}/postiz/publish", json={"content_id": item.id, "confirm": True}
    ).json()

    outcomes = {result["channel"]: result for result in body["results"]}
    # int-off is disabled in Postiz, so twitter_x is never attempted.
    assert set(outcomes) == {"facebook", "linkedin", "tiktok"}
    assert outcomes["facebook"]["ok"] is True
    assert outcomes["linkedin"]["ok"] is False
    assert "refused the post" in outcomes["linkedin"]["error"]
    # TikTok needs media and this piece has none - refused before the API call.
    assert outcomes["tiktok"]["ok"] is False
    assert "image or video" in outcomes["tiktok"]["error"]

    assert body["published"] == 1 and body["failed"] == 2
    # The one that worked is still a real, recorded publication.
    assert db_session.query(Publication).filter_by(organization_id=org.id).count() == 1


def test_scheduled_publish_sends_a_schedule_with_the_date(client, db_session, org, postiz):
    _connect(client, org)
    item = _content(db_session, org)
    when = datetime.now(timezone.utc) + timedelta(days=3)

    body = client.post(
        f"/organizations/{org.id}/postiz/publish",
        json={
            "content_id": item.id,
            "channels": ["facebook"],
            "confirm": True,
            "scheduled_for": when.isoformat(),
        },
    ).json()

    assert body["results"][0]["status"] == "scheduled"
    sent = postiz.bodies[-1]
    assert sent["type"] == "schedule"
    assert sent["date"].startswith(when.strftime("%Y-%m-%dT%H:%M"))


def test_media_is_uploaded_first_and_referenced_by_id(client, db_session, org, postiz):
    _connect(client, org)
    item = _content(db_session, org)
    asset = MediaAsset(
        organization_id=org.id,
        content_item_id=item.id,
        kind="image",
        mime="image/png",
        data=b"not-really-a-png",
    )
    db_session.add(asset)
    db_session.commit()

    client.post(
        f"/organizations/{org.id}/postiz/publish",
        json={"content_id": item.id, "channels": ["tiktok"], "confirm": True},
    )

    assert any(request.url.path.endswith("/upload") for request in postiz.requests)
    sent = postiz.bodies[-1]
    assert sent["posts"][0]["value"][0]["image"] == [
        {"id": "media-1", "path": "https://cdn.example/m.png"}
    ]
    # TikTok's defaults are the conservative ones, not whatever Postiz assumes.
    assert sent["posts"][0]["settings"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert sent["posts"][0]["settings"]["duet"] is False


def test_reddit_refuses_until_a_subreddit_is_set(client, db_session, org, postiz):
    postiz.integrations = [{"id": "int-rd", "name": "u/grace", "identifier": "reddit"}]
    _connect(client, org)
    item = _content(db_session, org)

    body = client.post(
        f"/organizations/{org.id}/postiz/publish",
        json={"content_id": item.id, "channels": ["reddit"], "confirm": True},
    ).json()
    assert body["failed"] == 1
    assert "subreddit" in body["results"][0]["error"]

    client.patch(
        f"/organizations/{org.id}/postiz/channels/int-rd",
        json={"settings": {"subreddit": "r/churches"}},
    )
    body = client.post(
        f"/organizations/{org.id}/postiz/publish",
        json={"content_id": item.id, "channels": ["reddit"], "confirm": True},
    ).json()
    assert body["published"] == 1


def test_publishing_to_a_channel_with_no_account_is_a_clear_refusal(
    client, db_session, org, postiz
):
    _connect(client, org)
    item = _content(db_session, org)

    response = client.post(
        f"/organizations/{org.id}/postiz/publish",
        json={"content_id": item.id, "channels": ["bluesky"], "confirm": True},
    )
    assert response.status_code == 400
    assert "Sync the workspace" in response.json()["detail"]


# ---------------------------------------------------------------- reconcile


def test_reconcile_promotes_a_released_post_to_its_real_permalink(
    client, db_session, org, postiz
):
    _connect(client, org)
    item = _content(db_session, org)
    client.post(
        f"/organizations/{org.id}/postiz/publish",
        json={"content_id": item.id, "channels": ["facebook"], "confirm": True},
    )

    # Nothing released yet - the queued post is left exactly as it was.
    first = client.post(f"/organizations/{org.id}/postiz/reconcile").json()
    assert first["resolved"] == 0
    publication = db_session.query(Publication).filter_by(organization_id=org.id).one()
    assert publication.status == "queued"

    postiz.released["post-1"] = "https://facebook.com/gracechurch/posts/999"
    second = client.post(f"/organizations/{org.id}/postiz/reconcile").json()
    assert second["resolved"] == 1

    db_session.refresh(publication)
    assert publication.status == "published"
    assert publication.url == "https://facebook.com/gracechurch/posts/999"


def test_reconcile_without_a_workspace_is_a_404(client, org):
    assert client.post(f"/organizations/{org.id}/postiz/reconcile").status_code == 404
