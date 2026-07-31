"""Offline tests for per-channel posting authentication.

Nothing here touches a real provider: every provider call (authorize URL build,
code exchange, account resolution, the posts themselves) is monkeypatched, and
the app runs on an in-memory SQLite database with the auth dependency
overridden - the same pattern as tests/test_organizations_admin.py.

What these pin down is the part that has to be right regardless of which
platform is on the other end: credentials are encrypted and never returned,
authorization state is single-use, connecting alone doesn't grant autonomous
posting, and a channel with no connection still behaves exactly as it did
before this feature existed.
"""

import itertools
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.deps import get_current_user
from app.main import app
from app.models.entities import (
    ChannelAuthRequest,
    ChannelConnection,
    ContentItem,
    MediaAsset,
    Organization,
    User,
)
from app.services import crypto
from app.services.channels import connections as conn_service
from app.services.channels import providers as providers_module
from app.services.channels.live import FacebookPageAdapter, post_text
from app.services.channels.providers import ProviderError, get_provider
from app.services.channels.registry import get_adapter
from app.services.media_links import sign_media_url, verify_media_url

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

_email_counter = itertools.count()

ACCOUNT = {
    "account_id": "page-1",
    "account_name": "Grace Community Church",
    "account_url": "https://facebook.com/grace",
    "target": {"page_id": "page-1"},
    "access_token": "page-token-abc",
}


def _make_user(db) -> User:
    user = User(
        email=f"channels-owner-{next(_email_counter)}@example.com",
        hashed_password="not-a-real-hash",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_org(db, user: User, name: str = "Grace Community Church") -> Organization:
    org = Organization(owner_id=user.id, name=name, org_type="church")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


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

    holder: dict = {}

    def override_get_current_user():
        return holder["user"]

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    test_client = TestClient(app)
    test_client._holder = holder  # type: ignore[attr-defined]
    try:
        yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def _as(client: TestClient, user: User) -> None:
    client._holder["user"] = user  # type: ignore[attr-defined]


@pytest.fixture
def connected_facebook(monkeypatch):
    """A Facebook app configured, and every provider network call stubbed."""
    monkeypatch.setattr(providers_module.settings, "facebook_client_id", "fb-id")
    monkeypatch.setattr(providers_module.settings, "facebook_client_secret", "fb-secret")
    monkeypatch.setattr(
        providers_module, "exchange_code", lambda p, code, verifier: {
            "access_token": "user-token", "expires_in": 5184000
        }
    )
    monkeypatch.setattr(providers_module, "resolve_account", lambda p, token: dict(ACCOUNT))
    # The router imported these by name, so patch them there too.
    import app.routers.channel_connections as router_module

    monkeypatch.setattr(router_module, "exchange_code", lambda p, code, verifier: {
        "access_token": "user-token", "expires_in": 5184000
    })
    monkeypatch.setattr(router_module, "resolve_account", lambda p, token: dict(ACCOUNT))


# ------------------------------------------------------------------- status


def test_status_lists_every_connectable_channel_before_anything_is_connected(client, db_session):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    body = client.get(f"/organizations/{org.id}/channels").json()
    channels = {entry["channel"]: entry for entry in body["channels"]}

    assert set(channels) == {
        "facebook", "instagram", "linkedin", "youtube", "twitter_x", "google_business"
    }
    assert all(entry["status"] == "not_connected" for entry in channels.values())
    assert all(entry["connected"] is False for entry in channels.values())
    # The website is published to by the plugin itself - it must not appear as
    # something to authorize.
    assert "website" not in channels


def test_website_is_not_a_connectable_channel(client, db_session):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    response = client.get(f"/organizations/{org.id}/channels/website")
    assert response.status_code == 404
    assert "website" in response.json()["detail"]


def test_another_operators_org_is_not_visible(client, db_session):
    owner = _make_user(db_session)
    org = _make_org(db_session, owner)
    intruder = _make_user(db_session)
    _as(client, intruder)

    assert client.get(f"/organizations/{org.id}/channels").status_code == 404


# ------------------------------------------------------------- setup guide


def test_setup_guide_covers_every_channel_with_this_orgs_own_details(client, db_session):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    org.website_url = "https://grace.example"
    db_session.commit()
    _as(client, user)

    body = client.get(f"/organizations/{org.id}/channels/setup-guide").json()
    guide = {entry["channel"]: entry for entry in body["channels"]}

    assert set(guide) == {
        "facebook", "instagram", "linkedin", "youtube", "twitter_x", "google_business"
    }
    for entry in guide.values():
        assert entry["exists_question"]
        assert entry["create_steps"] and entry["prepare_steps"] and entry["connect_steps"]

    # The org's own name and website are filled into the copy-me chips, so
    # nobody has to translate a generic instruction into their own details.
    chips = [
        chip["value"]
        for entry in guide.values()
        for step in entry["create_steps"]
        for chip in step.get("chips", [])
    ]
    assert "Grace Community Church" in chips
    assert "https://grace.example" in chips


def test_every_token_step_carries_a_live_link_to_where_the_token_is_issued(
    client, db_session, monkeypatch
):
    # No OAuth app configured anywhere = every channel falls back to the
    # paste-a-token route, which is exactly the path that needs the links.
    for name in (
        "facebook_client_id", "facebook_client_secret", "google_client_id",
        "google_client_secret", "linkedin_client_id", "linkedin_client_secret",
        "twitter_client_id", "twitter_client_secret",
    ):
        monkeypatch.setattr(providers_module.settings, name, None)

    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    body = client.get(f"/organizations/{org.id}/channels/setup-guide").json()
    for entry in body["channels"]:
        assert entry["oauth_available"] is False
        # The tool that issues the token is named, and linked, on the first step.
        assert entry["token_tool"]["url"].startswith("https://")
        links = [s["link"]["url"] for s in entry["connect_steps"] if s.get("link")]
        assert links, f"{entry['channel']} has no link to where its token comes from"
        assert entry["token_tool"]["url"] in links


def test_setup_guide_reports_a_connected_channel_as_finished(
    client, db_session, connected_facebook
):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)
    client.post(f"/organizations/{org.id}/channels/facebook/token", json={"access_token": "t"})

    body = client.get(f"/organizations/{org.id}/channels/setup-guide").json()
    facebook = next(e for e in body["channels"] if e["channel"] == "facebook")
    assert facebook["connected"] is True
    assert facebook["account_name"] == "Grace Community Church"


def test_setup_guide_is_not_mistaken_for_a_channel_name(client, db_session):
    """The guide lives at .../channels/setup-guide, one path segment away from
    .../channels/{channel} - it must not resolve as a channel called
    "setup-guide"."""
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    response = client.get(f"/organizations/{org.id}/channels/setup-guide")
    assert response.status_code == 200
    assert "channels" in response.json()


# -------------------------------------------------------------- oauth flow


def test_authorize_returns_a_provider_url_and_records_single_use_state(
    client, db_session, connected_facebook
):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    body = client.post(
        f"/organizations/{org.id}/channels/facebook/authorize",
        json={"return_url": "https://church.example/wp-admin/admin.php?page=engageai-channels"},
    ).json()

    assert body["authorize_url"].startswith("https://www.facebook.com/")
    assert "client_id=fb-id" in body["authorize_url"]
    assert body["redirect_uri"].endswith("/channels/callback/facebook")

    row = db_session.query(ChannelAuthRequest).filter_by(organization_id=org.id).one()
    assert row.used is False
    assert row.channel == "facebook"
    assert row.expires_at > datetime.utcnow()


def test_authorize_without_a_configured_app_explains_the_token_alternative(client, db_session, monkeypatch):
    monkeypatch.setattr(providers_module.settings, "linkedin_client_id", None)
    monkeypatch.setattr(providers_module.settings, "linkedin_client_secret", None)
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    response = client.post(f"/organizations/{org.id}/channels/linkedin/authorize", json={})
    assert response.status_code == 400
    assert "access token" in response.json()["detail"]


def test_callback_stores_an_encrypted_connection_and_never_leaks_the_token(
    client, db_session, connected_facebook
):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    client.post(f"/organizations/{org.id}/channels/facebook/authorize", json={})
    state = db_session.query(ChannelAuthRequest).filter_by(organization_id=org.id).one().state

    page = client.get(f"/channels/callback/facebook?code=auth-code&state={state}")
    assert page.status_code == 200
    assert "Grace Community Church" in page.text
    assert "page-token-abc" not in page.text

    connection = db_session.query(ChannelConnection).filter_by(organization_id=org.id).one()
    assert connection.status == "connected"
    # Stored ciphertext, decryptable, and the PAGE token (not the user token).
    assert connection.access_token_enc and "page-token-abc" not in connection.access_token_enc
    assert crypto.decrypt(connection.access_token_enc) == "page-token-abc"

    status = client.get(f"/organizations/{org.id}/channels/facebook").json()
    assert status["connected"] is True
    assert status["account_name"] == "Grace Community Church"
    assert "page-token-abc" not in str(status)
    assert not any("token" in key for key in status if key.endswith("_enc"))


def test_a_callback_state_cannot_be_replayed(client, db_session, connected_facebook):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    client.post(f"/organizations/{org.id}/channels/facebook/authorize", json={})
    state = db_session.query(ChannelAuthRequest).filter_by(organization_id=org.id).one().state

    assert client.get(f"/channels/callback/facebook?code=c&state={state}").status_code == 200
    replay = client.get(f"/channels/callback/facebook?code=c&state={state}")
    assert replay.status_code == 400
    assert "expired" in replay.text.lower()


def test_an_unknown_state_connects_nothing(client, db_session, connected_facebook):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    response = client.get("/channels/callback/facebook?code=c&state=not-a-real-state")
    assert response.status_code == 400
    assert db_session.query(ChannelConnection).filter_by(organization_id=org.id).count() == 0


def test_a_callback_for_a_different_channel_than_the_request_is_rejected(
    client, db_session, connected_facebook
):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    client.post(f"/organizations/{org.id}/channels/facebook/authorize", json={})
    state = db_session.query(ChannelAuthRequest).filter_by(organization_id=org.id).one().state

    assert client.get(f"/channels/callback/instagram?code=c&state={state}").status_code == 400
    assert db_session.query(ChannelConnection).filter_by(organization_id=org.id).count() == 0


# ------------------------------------------------------------- manual token


def test_a_pasted_token_is_verified_before_it_is_stored(client, db_session, monkeypatch):
    import app.routers.channel_connections as router_module

    def refuse(provider, token):
        raise ProviderError("This token has expired.")

    monkeypatch.setattr(router_module, "resolve_account", refuse)
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    response = client.post(
        f"/organizations/{org.id}/channels/linkedin/token", json={"access_token": "bad"}
    )
    assert response.status_code == 400
    assert "expired" in response.json()["detail"]
    assert db_session.query(ChannelConnection).filter_by(organization_id=org.id).count() == 0


def test_a_working_pasted_token_connects_the_channel(client, db_session, connected_facebook):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    body = client.post(
        f"/organizations/{org.id}/channels/facebook/token", json={"access_token": "pasted"}
    ).json()

    assert body["connected"] is True
    assert body["auth_method"] == "manual_token"
    assert body["auto_post"] is False


# ------------------------------------------------ consent to autonomous posting


def test_connecting_does_not_enable_autonomous_posting(client, db_session, connected_facebook):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)
    client.post(f"/organizations/{org.id}/channels/facebook/token", json={"access_token": "t"})

    connection = db_session.query(ChannelConnection).filter_by(organization_id=org.id).one()
    assert connection.auto_post is False

    # The unattended path keeps using the simulated adapter until auto_post is on.
    assert get_adapter("facebook", db=db_session, org=org, require_auto_post=True).simulated is True
    # The human-initiated path gets the real one.
    assert get_adapter("facebook", db=db_session, org=org).simulated is False

    client.patch(f"/organizations/{org.id}/channels/facebook", json={"auto_post": True})
    db_session.refresh(connection)
    assert connection.auto_post is True
    assert get_adapter("facebook", db=db_session, org=org, require_auto_post=True).simulated is False


def test_auto_post_cannot_be_turned_on_for_a_broken_connection(client, db_session, connected_facebook):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)
    client.post(f"/organizations/{org.id}/channels/facebook/token", json={"access_token": "t"})
    client.delete(f"/organizations/{org.id}/channels/facebook")

    response = client.patch(f"/organizations/{org.id}/channels/facebook", json={"auto_post": True})
    assert response.status_code == 400


def test_an_org_with_no_connection_still_gets_the_simulated_adapter(db_session):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    for channel in ("facebook", "instagram", "linkedin", "youtube", "twitter_x", "website"):
        assert get_adapter(channel, db=db_session, org=org).simulated is True


# ------------------------------------------------------------- disconnecting


def test_disconnecting_drops_the_credentials_but_keeps_the_record(
    client, db_session, connected_facebook
):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)
    client.post(f"/organizations/{org.id}/channels/facebook/token", json={"access_token": "t"})

    body = client.delete(f"/organizations/{org.id}/channels/facebook").json()
    assert body["connected"] is False
    assert body["status"] == "revoked"

    connection = db_session.query(ChannelConnection).filter_by(organization_id=org.id).one()
    assert connection.access_token_enc is None
    assert connection.refresh_token_enc is None
    assert connection.auto_post is False
    assert connection.account_name == "Grace Community Church"  # history survives


def test_reconnecting_reuses_the_row_and_keeps_the_posting_setting(
    client, db_session, connected_facebook
):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)
    client.post(f"/organizations/{org.id}/channels/facebook/token", json={"access_token": "t"})
    client.patch(f"/organizations/{org.id}/channels/facebook", json={"auto_post": True})

    client.post(f"/organizations/{org.id}/channels/facebook/token", json={"access_token": "t2"})

    connections = db_session.query(ChannelConnection).filter_by(organization_id=org.id).all()
    assert len(connections) == 1
    assert connections[0].auto_post is True


# ----------------------------------------------------------------- publish


def test_publish_posts_the_piece_and_records_a_real_publication(
    client, db_session, connected_facebook, monkeypatch
):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)
    client.post(f"/organizations/{org.id}/channels/facebook/token", json={"access_token": "t"})

    item = ContentItem(
        organization_id=org.id,
        content_type="post_image",
        title="Sunday service",
        input_payload={},
        output_payload={"body": "Join us Sunday at 10.", "hashtags": ["grace"], "title": "Sunday service"},
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    posted: dict = {}

    def fake_publish(self, db, org_, engagement, token):
        posted.update({"text": post_text(engagement), "token": token})
        return "https://www.facebook.com/page-1_999", "Facebook post: Sunday service"

    monkeypatch.setattr(FacebookPageAdapter, "_publish", fake_publish)

    body = client.post(
        f"/organizations/{org.id}/channels/facebook/publish", json={"content_id": item.id}
    ).json()

    assert body["url"] == "https://www.facebook.com/page-1_999"
    assert body["simulated"] is False
    assert posted["token"] == "page-token-abc"
    assert posted["text"] == "Join us Sunday at 10.\n\n#grace"


def test_publish_to_an_unconnected_channel_is_refused(client, db_session):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)

    response = client.post(
        f"/organizations/{org.id}/channels/facebook/publish", json={"content_id": 1}
    )
    assert response.status_code == 404


def test_publish_surfaces_a_provider_failure_and_marks_the_connection(
    client, db_session, connected_facebook, monkeypatch
):
    from app.services.channels.live import PublishError

    user = _make_user(db_session)
    org = _make_org(db_session, user)
    _as(client, user)
    client.post(f"/organizations/{org.id}/channels/facebook/token", json={"access_token": "t"})

    item = ContentItem(
        organization_id=org.id, content_type="post", title="t",
        input_payload={}, output_payload={"body": "hello"},
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    def fail(self, db, org_, engagement, token):
        raise PublishError("Facebook post failed: the Page was unpublished.")

    monkeypatch.setattr(FacebookPageAdapter, "_publish", fail)

    response = client.post(
        f"/organizations/{org.id}/channels/facebook/publish", json={"content_id": item.id}
    )
    assert response.status_code == 502
    assert "unpublished" in response.json()["detail"]

    connection = db_session.query(ChannelConnection).filter_by(organization_id=org.id).one()
    assert connection.status == "error"
    assert "unpublished" in connection.last_error


# ------------------------------------------------------------- signed media


def test_signed_media_links_are_scoped_and_expire(client, db_session):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    asset = MediaAsset(organization_id=org.id, kind="image", mime="image/png", data=b"PNGBYTES")
    db_session.add(asset)
    db_session.commit()
    db_session.refresh(asset)
    _as(client, user)

    url = sign_media_url(asset.id)
    path = url.split("/channels/media/")[1]
    assert client.get(f"/channels/media/{path}").content == b"PNGBYTES"

    # A signature for one asset doesn't open another, and a stale link is dead.
    query = path.split("?", 1)[1]
    assert client.get(f"/channels/media/{asset.id + 1}?{query}").status_code == 403
    assert verify_media_url(asset.id, int(datetime.utcnow().timestamp()) - 10, "whatever") is False


# ----------------------------------------------------- token lifecycle units


def test_encrypted_tokens_round_trip_and_a_key_change_forces_a_reconnect(monkeypatch):
    monkeypatch.setattr(crypto.settings, "token_encryption_key", "first-key")
    ciphertext = crypto.encrypt("secret-token")
    assert ciphertext != "secret-token"
    assert crypto.decrypt(ciphertext) == "secret-token"

    monkeypatch.setattr(crypto.settings, "token_encryption_key", "rotated-key")
    assert crypto.decrypt(ciphertext) is None


def test_an_expired_token_with_no_refresh_token_asks_for_a_reconnect(db_session):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    connection = ChannelConnection(
        organization_id=org.id,
        channel="facebook",
        provider="facebook",
        status="connected",
        access_token_enc=crypto.encrypt("stale"),
        token_expires_at=datetime.utcnow() - timedelta(hours=1),
    )
    db_session.add(connection)
    db_session.commit()

    with pytest.raises(ProviderError, match="expired"):
        conn_service.access_token(db_session, connection)
    db_session.refresh(connection)
    assert connection.status == "expired"


def test_an_expiring_token_is_refreshed_before_it_is_used(db_session, monkeypatch):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    connection = ChannelConnection(
        organization_id=org.id,
        channel="youtube",
        provider="google",
        status="connected",
        access_token_enc=crypto.encrypt("old"),
        refresh_token_enc=crypto.encrypt("refresh-me"),
        token_expires_at=datetime.utcnow() + timedelta(seconds=30),
    )
    db_session.add(connection)
    db_session.commit()

    monkeypatch.setattr(
        conn_service, "refresh_access_token",
        lambda provider, refresh: {"access_token": "fresh", "expires_in": 3600},
    )

    assert conn_service.access_token(db_session, connection) == "fresh"
    db_session.refresh(connection)
    assert crypto.decrypt(connection.access_token_enc) == "fresh"
    assert connection.token_expires_at > datetime.utcnow() + timedelta(minutes=30)


def test_unreadable_credentials_are_reported_not_crashed(db_session):
    user = _make_user(db_session)
    org = _make_org(db_session, user)
    connection = ChannelConnection(
        organization_id=org.id,
        channel="facebook",
        provider="facebook",
        status="connected",
        access_token_enc="not-actually-ciphertext",
    )
    db_session.add(connection)
    db_session.commit()

    with pytest.raises(ProviderError, match="Reconnect"):
        conn_service.access_token(db_session, connection)
    db_session.refresh(connection)
    assert connection.status == "error"


def test_authorize_url_carries_pkce_only_where_the_provider_needs_it():
    from app.services.channels.providers import build_authorize_url

    google = get_provider("youtube")
    facebook = get_provider("facebook")
    assert google.use_pkce is True and facebook.use_pkce is False

    import app.services.channels.providers as p

    original = (p.settings.google_client_id, p.settings.google_client_secret)
    p.settings.google_client_id, p.settings.google_client_secret = "g-id", "g-secret"
    try:
        url = build_authorize_url(google, "state-1", "verifier-1")
    finally:
        p.settings.google_client_id, p.settings.google_client_secret = original

    assert "code_challenge_method=S256" in url
    assert "verifier-1" not in url  # the verifier never travels through the browser
    assert "access_type=offline" in url


def test_post_text_respects_a_channel_limit():
    engagement = {"content": {"body": "x" * 400, "hashtags": []}, "title": "t"}
    assert len(post_text(engagement, limit=280)) == 280
