"""Tests for the availability + content-volume scoring additions:
- social channels now count/score post_count (pieces of content published),
- score_org folds channel availability (breadth) in alongside depth,
- a score of 0 still means no presence online at all,
- compute_insights surfaces availability + content_volume + per-channel counts.
"""

import itertools

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.entities import AnalyticsSnapshot, Organization, User
from app.services.analytics_insights import compute_insights
from app.services.analytics_scoring import (
    CHANNEL_KPI_SCHEMA,
    AVAILABILITY_WEIGHT,
    channel_availability,
    content_pieces,
    score_channel,
    score_org,
)


# --- social post_count scoring ---

def test_post_count_raises_social_score_and_stays_bounded():
    base = {"found": True, "follower_count": 500, "posting_frequency": "weekly", "engagement_level": "medium"}
    no_posts, _ = score_channel("instagram", {**base, "post_count": 0})
    some_posts, _ = score_channel("instagram", {**base, "post_count": 30})
    many_posts, breakdown = score_channel("instagram", {**base, "post_count": 5000})
    assert some_posts > no_posts
    assert many_posts >= some_posts
    assert many_posts <= 100
    assert any(b["rule"] == "post_count tier" for b in breakdown)


def test_absent_social_channel_scores_zero_regardless_of_posts():
    score, _ = score_channel("facebook", {"found": False, "post_count": 999, "follower_count": 999})
    assert score == 0


def test_merely_having_content_counts_below_the_volume_threshold():
    """A channel with just a few pieces of content should score more than the
    same channel with none - existence of content counts, not only volume."""
    base = {"found": True, "follower_count": 0, "posting_frequency": "none", "engagement_level": "none"}
    none_posts, _ = score_channel("instagram", {**base, "post_count": 0})
    few_posts, _ = score_channel("instagram", {**base, "post_count": 2})
    assert few_posts > none_posts

    # Same for website pages and YouTube videos (content that exists but is thin).
    web_none, _ = score_channel("website", {"indexed": True, "pages_indexed_estimate": 0, "backlink_signal": "none", "freshness": "stale"})
    web_few, _ = score_channel("website", {"indexed": True, "pages_indexed_estimate": 2, "backlink_signal": "none", "freshness": "stale"})
    assert web_few > web_none

    yt_none, _ = score_channel("youtube", {"found": True, "subscriber_count": 0, "video_count": 0, "posting_frequency": "none"})
    yt_few, _ = score_channel("youtube", {"found": True, "subscriber_count": 0, "video_count": 2, "posting_frequency": "none"})
    assert yt_few > yt_none


# --- availability ---

def test_channel_availability_counts_present_channels():
    total = len(CHANNEL_KPI_SCHEMA)
    a = channel_availability({"website": 40, "youtube": 20})  # 2 present
    assert a == {"present": 2, "total": total, "score": round(2 / total * 100)}


def test_availability_zero_when_no_presence():
    assert channel_availability({})["present"] == 0
    assert channel_availability({ch: 0 for ch in CHANNEL_KPI_SCHEMA})["score"] == 0


# --- score_org fold-in ---

def test_org_score_zero_when_nothing_online():
    org_score, _ = score_org({ch: 0 for ch in CHANNEL_KPI_SCHEMA})
    assert org_score == 0


def test_org_score_rewards_breadth_over_pure_depth_average():
    # Same total depth spread thin across many channels vs. concentrated in one.
    broad = {"website": 20, "youtube": 20, "facebook": 20, "instagram": 20}
    narrow = {"website": 80}
    depth_broad = sum(broad.values()) / len(CHANNEL_KPI_SCHEMA)
    broad_score, _ = score_org(broad)
    narrow_score, _ = score_org(narrow)
    # Broad presence beats a single strong channel of equal depth-sum, because
    # availability is now part of the score.
    assert broad_score > narrow_score
    # And the blended score sits above the pure depth average for a broad org.
    assert broad_score > round(depth_broad)


def test_org_score_is_deterministic():
    scores = {"website": 55, "facebook": 30, "youtube": 40}
    assert score_org(scores)[0] == score_org(dict(scores))[0]


def test_availability_weight_is_a_sane_fraction():
    assert 0 < AVAILABILITY_WEIGHT < 1


# --- content_pieces ---

def test_website_ground_truth_scores_a_site_search_cannot_find():
    """A live site the web search can't find (indexed=false, no pages) still
    scores its real presence + content once the plugin reports ground truth."""
    from app.routers.analytics import _apply_website_ground_truth

    # What a scan of a small/unindexed site comes back with: not found.
    not_found = {"channel": "website", "kpis": {"indexed": False, "pages_indexed_estimate": None,
                                                "backlink_signal": "none", "freshness": "stale"}}
    before, _ = score_channel("website", not_found["kpis"])
    assert before == 0

    facts = {"website_present": True, "published_posts": 40, "published_pages": 6}
    merged = _apply_website_ground_truth(not_found, facts)
    after, _ = score_channel("website", merged["kpis"])
    assert merged["kpis"]["indexed"] is True
    assert merged["kpis"]["pages_indexed_estimate"] == 46  # 40 posts + 6 pages
    assert after > 0
    assert content_pieces("website", merged["kpis"]) == 46


def test_website_ground_truth_builds_entry_when_model_found_nothing():
    """Even when the scan returned no website entry at all (None), ground truth
    produces a scored website entry."""
    from app.routers.analytics import _apply_website_ground_truth

    entry = _apply_website_ground_truth(None, {"website_present": True, "published_posts": 3, "published_pages": 1})
    assert entry["channel"] == "website"
    assert entry["kpis"]["indexed"] is True
    assert entry["kpis"]["pages_indexed_estimate"] == 4


def test_website_ground_truth_noop_without_facts():
    from app.routers.analytics import _apply_website_ground_truth

    # No plugin facts and no website_url -> no probe, nothing to confirm.
    entry = {"channel": "website", "kpis": {"indexed": False}}
    assert _apply_website_ground_truth(entry, None) is entry
    assert _apply_website_ground_truth(entry, {"website_present": False}) is entry


def test_website_ground_truth_uses_server_probe_when_no_plugin(monkeypatch):
    """A live site with no plugin still scores via the direct server-side check."""
    import app.routers.analytics as an
    monkeypatch.setattr(an, "_probe_website", lambda url: {"live": True, "page_count": 34})
    merged = an._apply_website_ground_truth(None, None, "https://example.org/")
    assert merged["kpis"]["indexed"] is True
    assert merged["kpis"]["pages_indexed_estimate"] == 34
    assert "server-side check" in merged["notes"]
    score, _ = score_channel("website", merged["kpis"])
    assert score > 0


def test_website_ground_truth_noop_when_site_unreachable(monkeypatch):
    import app.routers.analytics as an
    monkeypatch.setattr(an, "_probe_website", lambda url: None)  # dead site
    entry = {"channel": "website", "kpis": {"indexed": False}}
    assert an._apply_website_ground_truth(entry, None, "https://dead.example/") is entry


def test_ssrf_guard_blocks_private_hosts():
    from app.routers.analytics import _is_public_host
    assert _is_public_host("127.0.0.1") is False
    assert _is_public_host("169.254.169.254") is False  # cloud metadata
    assert _is_public_host("10.0.0.5") is False


def test_content_pieces_per_channel_source_field():
    assert content_pieces("website", {"pages_indexed_estimate": 42}) == 42
    assert content_pieces("youtube", {"video_count": 12}) == 12
    assert content_pieces("linkedin", {"post_count": 7}) == 7
    # channels that aren't self-published content contribute nothing
    assert content_pieces("google_business", {"review_count": 99}) is None
    assert content_pieces("news_mentions", {"mention_count_recent": 5}) is None
    # missing / non-int counts -> None
    assert content_pieces("website", {}) is None


# --- insights integration ---

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(bind=engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
_email_counter = itertools.count()


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _entry(channel: str, kpis: dict) -> dict:
    score, breakdown = score_channel(channel, kpis)
    return {"channel": channel, "kpis": kpis, "score": score, "score_breakdown": breakdown, "notes": ""}


def test_compute_insights_surfaces_availability_and_content_volume(db_session):
    user = User(email=f"counts-{next(_email_counter)}@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    org = Organization(owner_id=user.id, name="Counts Co", enabled_modules=["analytics"])
    db_session.add(org)
    db_session.commit()

    channels = [
        _entry("website", {"indexed": True, "pages_indexed_estimate": 30, "backlink_signal": "low", "freshness": "active"}),
        _entry("youtube", {"found": True, "subscriber_count": 500, "video_count": 12, "posting_frequency": "weekly"}),
        _entry("instagram", {"found": True, "follower_count": 800, "post_count": 40, "posting_frequency": "weekly", "engagement_level": "medium"}),
    ]
    db_session.add(AnalyticsSnapshot(
        organization_id=org.id, is_baseline=True, channels=channels,
        requested_channels=None, status="complete", summary="s",
    ))
    db_session.commit()

    insights = compute_insights(db_session, org.id)
    assert insights is not None
    assert insights["availability"]["present"] == 3
    assert insights["availability"]["total"] == len(CHANNEL_KPI_SCHEMA)
    # 30 pages + 12 videos + 40 posts
    assert insights["content_volume"]["total"] == 82
    assert insights["content_volume"]["by_channel"] == {"website": 30, "youtube": 12, "instagram": 40}
    by_channel = {r["channel"]: r["content_count"] for r in insights["ranking"]}
    assert by_channel["instagram"] == 40
