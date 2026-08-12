"""The surface catalog and the surface-aware studio passes.

Network-free: the drafting pass is monkeypatched, and the deterministic check
never needed a key in the first place - which is the point of it.
"""
import pytest

from app.services import surfaces as S
from app.services.studio import StudioService


# --------------------------------------------------------------- the catalog


def test_every_channel_has_at_least_one_surface_and_a_valid_default():
    for channel in S.CHANNELS:
        found = S.surfaces_for(channel)
        assert found, f"{channel} has no surfaces"
        default = S.surface_for(channel)
        assert default.channel == channel
        assert default in found


def test_surface_ids_are_unique():
    ids = [s.id for s in S.SURFACES]
    assert len(ids) == len(set(ids))


def test_every_surface_declares_a_coherent_media_and_render_pair():
    allowed = {
        "none": {"none"},
        "image": {"post_image", "text_image"},
        "images": {"carousel"},
        "video": {"slideshow"},
        "document": {"document"},
    }
    for surface in S.SURFACES:
        assert surface.render in allowed[surface.media], surface.id
        assert surface.publish in {"live", "draft", "manual"}, surface.id
        # Anything that paints pixels needs a canvas to paint on.
        if surface.render != "none":
            assert surface.width and surface.height, surface.id
        if surface.render == "slideshow":
            assert surface.seconds > 0, surface.id


def test_text_image_surfaces_name_a_field_that_actually_exists():
    for surface in S.SURFACES:
        for key in (surface.headline_field, surface.subhead_field):
            if key:
                assert surface.field(key) is not None, f"{surface.id} points at missing field {key}"


def test_slides_fields_declare_their_required_key_first():
    for surface in S.SURFACES:
        spec = surface.slides_field
        if spec is not None:
            assert spec.item_keys, surface.id
            assert spec.min_items >= 1, surface.id
            assert spec.max_items >= spec.min_items, surface.id


def test_unknown_channel_or_surface_degrades_instead_of_failing():
    assert S.surface_for("not_a_channel").channel == S.DEFAULT_CHANNEL
    assert S.surface_for("instagram", "not_a_surface").key == "feed_image"
    assert S.resolve("instagram.nope") is None
    assert S.resolve("instagram.carousel").media == "images"


def test_prompt_and_json_shape_are_built_from_the_fields():
    surface = S.resolve("google_business.offer")
    instructions = S.draft_instructions(surface)
    shape = S.json_shape(surface)
    for spec in surface.fields:
        assert f'"{spec.key}"' in instructions, spec.key
        assert f'"{spec.key}"' in shape, spec.key
    # The declared caps have to reach the model as numbers, not just prose.
    assert "58 characters" in instructions
    # And a choice field has to arrive as its actual options.
    assert "BOOK" in S.draft_instructions(S.resolve("google_business.whats_new"))


def test_catalog_is_serialisable_and_complete():
    catalog = S.catalog()
    assert {c["key"] for c in catalog["channels"]} == set(S.CHANNELS)
    total = sum(len(c["surfaces"]) for c in catalog["channels"])
    assert total == len(S.SURFACES)
    sample = catalog["channels"][0]["surfaces"][0]
    assert {"id", "key", "label", "media", "render", "publish", "fields"} <= set(sample)


# ------------------------------------------------------------- the check pass


@pytest.fixture()
def studio():
    return StudioService()


def test_poll_options_are_capped_counted_and_trimmed(studio):
    surface = S.resolve("linkedin.poll")
    draft = {
        "title": "Poll", "body": "Which matters most to you? Curious what people think.",
        "hashtags": [], "question": "Q" * 200,
        "options": ["A" * 60, "Bee", "See", "Dee", "Eee"],
        "duration": "forever",
    }
    checked, report = studio.check_surface(draft, surface, "community")

    assert len(checked["options"]) == 4                     # 5 -> max_items
    assert all(len(o) <= 30 for o in checked["options"])    # each trimmed
    assert len(checked["question"]) <= 140                  # trimmed to cap
    assert checked["duration"] == "1 day"                   # snapped to an option
    assert report["passed"] is True
    assert any("isn't an option" in f for f in report["fixed"])


def test_too_few_poll_options_is_an_error_not_a_silent_fix(studio):
    surface = S.resolve("twitter_x.poll")
    draft = {"title": "P", "body": "Pick one", "hashtags": [], "options": ["only one"],
             "duration": "1 day"}
    _, report = studio.check_surface(draft, surface, "community")
    assert report["passed"] is False
    assert any(i["field"] == "options" and i["severity"] == "error" for i in report["issues"])


def test_dates_are_validated_and_a_backwards_range_is_repaired(studio):
    surface = S.resolve("google_business.event")
    draft = {"title": "E", "body": "Come along, everyone is welcome. Book your seat.",
             "hashtags": [], "event_title": "Spring open day",
             "starts_on": "2026-09-10", "ends_on": "2026-09-01",
             "cta_label": "SIGN_UP", "cta_url": "https://example.org/rsvp"}
    checked, report = studio.check_surface(draft, surface, "attendance")
    assert checked["ends_on"] == "2026-09-10"
    assert any("before the start date" in f for f in report["fixed"])

    draft["starts_on"] = "next Tuesday"
    _, report = studio.check_surface(draft, surface, "attendance")
    assert any(i["field"] == "starts_on" and i["severity"] == "error" for i in report["issues"])


def test_an_unusable_url_becomes_a_placeholder_rather_than_shipping(studio):
    surface = S.resolve("facebook.link_post")
    draft = {"title": "L", "body": "Worth a read - here's why it matters.", "hashtags": [],
             "link_url": "just some words"}
    checked, report = studio.check_surface(draft, surface, "awareness")
    assert checked["link_url"] == "[link]"
    assert report["passed"] is True
    # A bracketed placeholder is a legitimate answer and must survive untouched.
    checked, _ = studio.check_surface({**draft, "link_url": "[link]"}, surface, "awareness")
    assert checked["link_url"] == "[link]"


def test_thread_parts_are_held_to_the_post_limit(studio):
    surface = S.resolve("twitter_x.thread")
    draft = {"title": "T", "body": "A short opener.", "hashtags": [],
             "parts": ["x" * 400, "second part", "third part"]}
    checked, report = studio.check_surface(draft, surface, "awareness")
    assert all(len(p) <= 270 for p in checked["parts"])
    assert report["passed"] is True


def test_carousel_pages_are_counted_and_their_text_kept_legible(studio):
    surface = S.resolve("instagram.carousel")
    draft = {
        "title": "C", "body": "Swipe through - save this one.", "hashtags": ["tips"],
        "image_prompt": "a bright studio", "image_alt": "pages",
        "slides": [{"headline": "H" * 120, "body": "B" * 300, "image_prompt": ""}] * 5,
    }
    checked, report = studio.check_surface(draft, surface, "awareness")
    assert len(checked["slides"]) == 5
    for page in checked["slides"]:
        assert len(page["headline"]) <= 60
        assert len(page["body"]) <= 140
        assert page["image_prompt"]          # derived from the headline
    assert report["passed"] is True


def test_a_slide_with_no_text_is_not_a_slide(studio):
    surface = S.resolve("instagram.reel")
    draft = {"title": "R", "body": "Watch this.", "hashtags": [],
             "image_prompt": "a street", "image_alt": "reel",
             "slides": [{"narration": "hook", "image_prompt": "a"},
                        {"narration": "   ", "image_prompt": "b"}]}
    _, report = studio.check_surface(draft, surface, "awareness")
    assert any(i["field"] == "slides" and i["severity"] == "error" for i in report["issues"])


def test_hashtags_are_enforced_per_surface_not_per_channel(studio):
    # Same channel, two surfaces, two different ceilings.
    tags = [f"tag{i}" for i in range(20)]
    feed, _ = studio.check_surface(
        {"title": "x", "body": "hi", "hashtags": tags, "image_prompt": "p", "image_alt": "a"},
        S.resolve("instagram.feed_image"), "awareness")
    story, _ = studio.check_surface(
        {"title": "x", "body": "hi", "hashtags": tags, "image_prompt": "p", "image_alt": "a",
         "overlay_headline": "Hello"},
        S.resolve("instagram.story"), "awareness")
    assert len(feed["hashtags"]) == 12
    assert len(story["hashtags"]) == 2


def test_check_runs_with_no_api_key_configured(studio, monkeypatch):
    monkeypatch.setattr(studio, "client", None)
    surface = S.resolve("facebook.text_post")
    checked, report = studio.check_surface(
        {"title": "t", "body": "Hello there, what do you think?", "hashtags": []},
        surface, "community")
    assert report["passed"] is True
    assert checked["body"]


# ------------------------------------------------------------- the draft pass


def test_draft_keeps_only_the_fields_the_surface_declares(studio, monkeypatch):
    surface = S.resolve("google_business.offer")
    monkeypatch.setattr(studio, "client", object())
    monkeypatch.setattr(studio, "_json_call", lambda *a, **k: {
        "title": "Spring offer", "body": "Book before April and save.", "hashtags": ["spring"],
        "image_prompt": "a sunlit shopfront", "image_alt": "shopfront",
        "offer_title": "20% off first visit", "starts_on": "2026-03-01", "ends_on": "2026-04-30",
        "coupon_code": "SPRING20", "terms": "New customers only.", "cta_url": "https://example.org",
        # Fields belonging to other surfaces must not survive.
        "options": ["a", "b"], "slides": [{"narration": "nope"}], "parts": ["nope"],
    })
    draft = studio.draft_surface({}, {"headline": "Spring"}, surface, "sales", "business")

    assert draft["offer_title"] == "20% off first visit"
    assert draft["coupon_code"] == "SPRING20"
    assert "options" not in draft
    assert "parts" not in draft
    assert "slides" not in draft
    # Hashtags are stripped by the check, not the shaper - the surface says 0.
    checked, _ = studio.check_surface(draft, surface, "sales")
    assert checked["hashtags"] == []


def test_draft_for_a_text_only_surface_carries_no_image_fields(studio, monkeypatch):
    surface = S.resolve("twitter_x.tweet")
    monkeypatch.setattr(studio, "client", object())
    monkeypatch.setattr(studio, "_json_call", lambda *a, **k: {
        "title": "Take", "body": "A sharp, defensible opinion.", "hashtags": ["x"],
        "image_prompt": "should be dropped", "image_alt": "dropped",
    })
    draft = studio.draft_surface({}, {"headline": "Take"}, surface, "awareness", "business")
    assert "image_prompt" not in draft
    assert "image_alt" not in draft


def test_surface_ideas_reject_a_surface_outside_the_allowed_channels(studio, monkeypatch):
    monkeypatch.setattr(studio, "client", object())
    monkeypatch.setattr(studio, "_json_call", lambda *a, **k: {
        "ideas": [{"headline": "One", "angle": "a", "why": "w", "surface": "youtube.short"}]
    })
    ideas = studio.surface_ideas({}, "awareness", "business", channels=["linkedin"])
    assert ideas[0]["channel"] == "linkedin"
    assert ideas[0]["surface"].startswith("linkedin.")


def test_draft_returns_empty_without_a_model_rather_than_raising(studio, monkeypatch):
    monkeypatch.setattr(studio, "client", None)
    assert studio.draft_surface({}, {"headline": "x"}, S.resolve("linkedin.poll"), "leads", "business") == {}
    assert studio.surface_ideas({}, "awareness", "business") == []


# ---------------------------------------------------------------- the API
#
# Reuses test_studio's fixtures: the surface path shares check / edit / render
# with the format path, so what has to be proven here is that the dispatch
# picks the right one and the format path still works untouched.

from tests.test_studio import client, db_session, _seed  # noqa: E402,F401


def _api(client, db, surface_id, payload):
    """Drafts one surface piece through the real endpoint."""
    user, org = _seed(db)
    client._holder["user"] = user
    import app.routers.studio as studio_router
    studio_router.studio.client = object()
    studio_router.studio._json_call = lambda *a, **k: payload
    response = client.post(
        f"/studio/surfaces/draft?organization_id={org.id}",
        json={"idea": {"headline": "An idea"}, "surface": surface_id, "goal": "leads"},
    )
    return response, org


def test_surfaces_endpoint_lists_every_surface(client, db_session):
    user, _ = _seed(db_session)
    client._holder["user"] = user
    body = client.get("/studio/surfaces").json()
    assert {c["key"] for c in body["channels"]} == set(S.CHANNELS)
    assert body["goals"]
    assert set(body["publish_states"]) == {"live", "draft", "manual"}


def test_drafting_a_poll_stores_its_declared_fields(client, db_session):
    response, org = _api(client, db_session, "linkedin.poll", {
        "title": "What slows you down?", "body": "Genuinely curious - which of these costs you most?",
        "hashtags": ["ops"], "question": "What slows your week down most?",
        "options": ["Admin", "Follow-up", "Content", "Hiring"], "duration": "1 week",
    })
    assert response.status_code == 200, response.text
    output = response.json()["output_payload"]
    assert output["surface"] == "linkedin.poll"
    assert output["media"] == "none"
    assert output["publish"] == "manual"
    assert output["fields"]["options"] == ["Admin", "Follow-up", "Content", "Hiring"]
    assert output["fields"]["duration"] == "1 week"
    assert output["studio"]["quality"]["passed"] is True


def test_a_text_only_surface_refuses_to_render(client, db_session):
    response, org = _api(client, db_session, "twitter_x.tweet", {
        "title": "Take", "body": "Book the call before you build the site.", "hashtags": [],
    })
    content_id = response.json()["id"]
    render = client.post(f"/studio/{content_id}/render?organization_id={org.id}")
    assert render.status_code == 400
    assert "no file to render" in render.json()["detail"]


def test_a_piece_says_up_front_that_it_has_no_media_pass(client, db_session):
    """The refusal above is correct but late: a caller that only learns from a
    400 has already offered the operator a button that cannot work. The piece
    carries the surface's render mode, so the media pass can be skipped."""
    text_only, _ = _api(client, db_session, "twitter_x.tweet", {
        "title": "Take", "body": "Book the call before you build the site.", "hashtags": [],
    })
    assert text_only.json()["output_payload"]["render"] == "none"

    with_media, _ = _api(client, db_session, "instagram.feed_image", {
        "title": "Look", "body": "One frame, one idea.", "hashtags": [],
        "image_prompt": "a clean workshop bench", "image_alt": "a workbench",
    })
    assert with_media.json()["output_payload"]["render"] != "none"


def test_editing_a_surface_piece_rechecks_its_declared_fields(client, db_session):
    response, org = _api(client, db_session, "twitter_x.poll", {
        "title": "Poll", "body": "Pick one.", "hashtags": [],
        "options": ["Yes", "No"], "duration": "1 day",
    })
    content_id = response.json()["id"]

    edited = client.post(
        f"/studio/{content_id}/edit?organization_id={org.id}",
        json={"fields": {"options": ["A" * 90, "B"], "duration": "a fortnight"},
              "not_a_field": "ignored"},
    )
    assert edited.status_code == 200, edited.text
    fields = edited.json()["output_payload"]["fields"]
    assert len(fields["options"][0]) <= 25          # re-trimmed on save
    assert fields["duration"] == "1 day"            # snapped back to a real option


def test_a_carousel_render_produces_one_asset_per_page(client, db_session, monkeypatch):
    import app.routers.studio as studio_router
    monkeypatch.setattr(studio_router.renderer, "render_carousel",
                        lambda slides, w, h: [(b"page-%d" % i, "image/jpeg") for i in range(len(slides))])

    response, org = _api(client, db_session, "instagram.carousel", {
        "title": "Deck", "body": "Swipe through - save this one.", "hashtags": ["tips"],
        "image_prompt": "a bright studio", "image_alt": "pages",
        "slides": [{"headline": f"Page {i}", "body": "A line.", "image_prompt": "a scene"}
                   for i in range(4)],
    })
    content_id = response.json()["id"]

    started = client.post(f"/studio/{content_id}/render?organization_id={org.id}")
    assert started.status_code == 200, started.text
    assert started.json()["kind"] == "image"

    status = client.get(f"/studio/{content_id}/render?organization_id={org.id}").json()
    assert status["status"] == "done"
    assert len(status["asset_ids"]) == 4
    assert len(status["urls"]) == 4
    assert status["url"] == status["urls"][0]      # older callers still get one


def test_a_reel_render_uses_the_surfaces_own_duration_not_the_old_eight_seconds(client, db_session, monkeypatch):
    import app.routers.studio as studio_router
    seen: dict = {}

    def fake_slideshow(slides, width, height, seconds, max_slides=8):
        seen.update(slides=len(slides), width=width, height=height, seconds=seconds)
        return b"mp4", "video/mp4"

    monkeypatch.setattr(studio_router.renderer, "render_slideshow", fake_slideshow)
    response, org = _api(client, db_session, "instagram.reel", {
        "title": "Reel", "body": "Watch this one.", "hashtags": ["reel"],
        "image_prompt": "a street", "image_alt": "reel",
        "slides": [{"narration": f"Line {i}", "image_prompt": "a scene"} for i in range(6)],
    })
    content_id = response.json()["id"]
    client.post(f"/studio/{content_id}/render?organization_id={org.id}")

    assert seen == {"slides": 6, "width": 720, "height": 1280, "seconds": 30.0}
