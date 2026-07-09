"""Deterministic scoring for analytics channel KPIs.

Claude's only job (see analytics_search.py) is to research and report the
fixed fields below, honestly, using the fixed enum values where specified -
it never computes a score itself. Scores are computed here, in code, from
those fields, so the same real-world state always produces the same score
and a score is always reproducible from its stored breakdown. This is what
makes "is this actually improving" a checkable claim instead of a vibe.
"""

# Allowed enum values Claude must choose from for qualitative signals -
# fixed vocabulary, not free text, so scoring can be a plain dict lookup
# with no NLP parsing/ambiguity.
FREQUENCY_LEVELS = ["none", "rare", "monthly", "weekly", "daily"]
QUALITATIVE_LEVELS = ["none", "low", "medium", "high"]
FRESHNESS_LEVELS = ["stale", "occasional", "active", "very_active"]
RECENCY_LEVELS = ["none", "over_a_year", "within_year", "within_quarter", "within_month"]

_FOLLOWER_CHANNELS = ["facebook", "instagram", "linkedin", "twitter_x"]

# Documents the exact fields Claude must populate per channel (null/enum
# default when not found) - both the prompt builder and the scorer read
# this, so they can't drift apart.
CHANNEL_KPI_SCHEMA: dict[str, dict[str, str]] = {
    "website": {
        "indexed": "bool",
        "pages_indexed_estimate": "int|null",
        "backlink_signal": f"enum{QUALITATIVE_LEVELS}",
        "freshness": f"enum{FRESHNESS_LEVELS}",
        "third_party_traffic_estimate": "int|null (attribute the source in notes)",
    },
    "google_business": {
        "found": "bool",
        "rating": "float 1.0-5.0|null",
        "review_count": "int|null",
    },
    "youtube": {
        "found": "bool",
        "subscriber_count": "int|null",
        "video_count": "int|null",
        "posting_frequency": f"enum{FREQUENCY_LEVELS}",
    },
    "news_mentions": {
        "found": "bool",
        "mention_count_recent": "int|null",
        "most_recent_mention_recency": f"enum{RECENCY_LEVELS}",
    },
}
for _channel in _FOLLOWER_CHANNELS:
    CHANNEL_KPI_SCHEMA[_channel] = {
        "found": "bool",
        "follower_count": "int|null",
        "posting_frequency": f"enum{FREQUENCY_LEVELS}",
        "engagement_level": f"enum{QUALITATIVE_LEVELS}",
    }


def _tier(value: int | None, thresholds: list[tuple[int, int]]) -> int:
    """thresholds: list of (min_value, points), highest match wins. 0 if value is None or below all thresholds."""
    if value is None:
        return 0
    points = 0
    for min_value, pts in thresholds:
        if value >= min_value:
            points = pts
    return points


def score_channel(channel: str, kpis: dict) -> tuple[int, list[dict]]:
    """Returns (score 0-100, breakdown) for one channel's KPI fields.
    breakdown is a list of {"rule": str, "points": int, "basis": value} -
    stored alongside the score so it can be displayed/audited later, not
    just recomputed silently."""
    kpis = kpis or {}
    breakdown: list[dict] = []

    def add(rule: str, points: int, basis):
        breakdown.append({"rule": rule, "points": points, "basis": basis})

    if channel in _FOLLOWER_CHANNELS:
        found = bool(kpis.get("found"))
        add("channel presence found", 20 if found else 0, found)
        if not found:
            return 0, breakdown

        followers = kpis.get("follower_count")
        pts = _tier(followers, [(0, 0), (100, 10), (1000, 20), (10000, 30), (100000, 40)])
        add("follower_count tier", pts, followers)

        freq = kpis.get("posting_frequency", "none")
        freq_points = {"none": 0, "rare": 5, "monthly": 10, "weekly": 20, "daily": 20}.get(freq, 0)
        add("posting_frequency", freq_points, freq)

        engagement = kpis.get("engagement_level", "none")
        engagement_points = {"none": 0, "low": 5, "medium": 12, "high": 20}.get(engagement, 0)
        add("engagement_level", engagement_points, engagement)

    elif channel == "youtube":
        found = bool(kpis.get("found"))
        add("channel presence found", 20 if found else 0, found)
        if not found:
            return 0, breakdown

        subs = kpis.get("subscriber_count")
        pts = _tier(subs, [(0, 0), (100, 10), (1000, 20), (10000, 30), (100000, 40)])
        add("subscriber_count tier", pts, subs)

        videos = kpis.get("video_count")
        pts = _tier(videos, [(0, 0), (5, 5), (20, 10), (50, 20)])
        add("video_count tier", pts, videos)

        freq = kpis.get("posting_frequency", "none")
        freq_points = {"none": 0, "rare": 5, "monthly": 10, "weekly": 20, "daily": 20}.get(freq, 0)
        add("posting_frequency", freq_points, freq)

    elif channel == "google_business":
        found = bool(kpis.get("found"))
        add("channel presence found", 30 if found else 0, found)
        if not found:
            return 0, breakdown

        rating = kpis.get("rating")
        rating_points = 0
        if rating is not None:
            rating_points = max(0, round((rating - 1) / 4 * 40))  # 1.0->0, 5.0->40
        add("rating", rating_points, rating)

        reviews = kpis.get("review_count")
        pts = _tier(reviews, [(0, 0), (5, 10), (25, 20), (100, 30)])
        add("review_count tier", pts, reviews)

    elif channel == "website":
        indexed = bool(kpis.get("indexed"))
        add("indexed", 25 if indexed else 0, indexed)
        if not indexed:
            return 0, breakdown

        pages = kpis.get("pages_indexed_estimate")
        pts = _tier(pages, [(0, 0), (5, 10), (20, 20), (50, 25)])
        add("pages_indexed_estimate tier", pts, pages)

        backlink = kpis.get("backlink_signal", "none")
        backlink_points = {"none": 0, "low": 8, "medium": 17, "high": 25}.get(backlink, 0)
        add("backlink_signal", backlink_points, backlink)

        freshness = kpis.get("freshness", "stale")
        freshness_points = {"stale": 0, "occasional": 8, "active": 17, "very_active": 25}.get(freshness, 0)
        add("freshness", freshness_points, freshness)

    elif channel == "news_mentions":
        found = bool(kpis.get("found"))
        add("channel presence found", 20 if found else 0, found)
        if not found:
            return 0, breakdown

        mentions = kpis.get("mention_count_recent")
        pts = _tier(mentions, [(0, 0), (1, 20), (5, 40), (15, 40)])
        add("mention_count_recent tier", pts, mentions)

        recency = kpis.get("most_recent_mention_recency", "none")
        recency_points = {"none": 0, "over_a_year": 5, "within_year": 15, "within_quarter": 30, "within_month": 40}.get(recency, 0)
        add("most_recent_mention_recency", recency_points, recency)

    else:
        return 0, breakdown

    score = min(100, sum(b["points"] for b in breakdown))
    return score, breakdown


def score_org(channel_scores: dict[str, int]) -> tuple[int, list[dict]]:
    """Straight average across every known channel (see CHANNEL_KPI_SCHEMA),
    including 0 for channels with no presence at all - a missing channel is
    real white space and should genuinely pull the org score down, not be
    excluded from the average."""
    all_channels = list(CHANNEL_KPI_SCHEMA.keys())
    breakdown = [
        {"channel": ch, "score": channel_scores.get(ch, 0)}
        for ch in all_channels
    ]
    total = sum(b["score"] for b in breakdown)
    org_score = round(total / len(all_channels)) if all_channels else 0
    return org_score, breakdown
