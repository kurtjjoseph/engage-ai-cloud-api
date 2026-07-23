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
        "post_count": "int|null (total posts/pieces of content published on this channel)",
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
        pts = _tier(followers, [(0, 0), (100, 7), (1000, 13), (10000, 20), (100000, 25)])
        add("follower_count tier", pts, followers)

        # Number of pieces of content actually published on the channel - a
        # channel with an audience but nothing posted isn't really working.
        # The first tier starts at 1 so simply HAVING content counts, not just
        # having a lot of it.
        posts = kpis.get("post_count")
        pts = _tier(posts, [(0, 0), (1, 4), (5, 8), (25, 15), (100, 20)])
        add("post_count tier", pts, posts)

        freq = kpis.get("posting_frequency", "none")
        freq_points = {"none": 0, "rare": 4, "monthly": 8, "weekly": 15, "daily": 15}.get(freq, 0)
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
        pts = _tier(videos, [(0, 0), (1, 3), (5, 5), (20, 10), (50, 20)])  # 1+ videos counts, not just 5+
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
        pts = _tier(pages, [(0, 0), (1, 5), (5, 10), (20, 20), (50, 25)])  # 1+ pages counts, not just 5+
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


# A channel scores 0 if and only if it has no presence at all (every
# score_channel branch gates on found/indexed and returns 0 when absent, and
# awards >=20 the moment presence is confirmed). So "score > 0" is a reliable
# stand-in for "this channel is actually live" without threading presence
# flags separately - the invariant channel_availability() relies on.
def channel_availability(channel_scores: dict[str, int]) -> dict:
    """How many of the known channels the org is actually present on. Returns
    {"present": int, "total": int, "score": 0-100} where score is the plain
    percentage of channels that are live - 0 means no presence online at all."""
    all_channels = list(CHANNEL_KPI_SCHEMA.keys())
    total = len(all_channels)
    present = sum(1 for ch in all_channels if (channel_scores.get(ch) or 0) > 0)
    return {"present": present, "total": total, "score": round(present / total * 100) if total else 0}


# Field on each channel that counts self-published pieces of content (posts,
# pages, videos). Channels not listed here (google_business reviews,
# news_mentions) aren't content the org publishes, so they don't contribute.
_CONTENT_COUNT_FIELD = {
    "website": "pages_indexed_estimate",
    "youtube": "video_count",
    **{ch: "post_count" for ch in _FOLLOWER_CHANNELS},
}


def content_pieces(channel: str, kpis: dict) -> int | None:
    """Number of published pieces of content on one channel (pages online for
    the website, videos for YouTube, posts for social), or None if this channel
    doesn't track a content count or none was found."""
    field = _CONTENT_COUNT_FIELD.get(channel)
    if field is None:
        return None
    value = (kpis or {}).get(field)
    return value if isinstance(value, int) else None


# How much of the org score reflects breadth (how many channels are live at
# all) vs. depth (how developed each one is). Depth already rewards presence
# inside each channel; this makes "shows up on more channels" its own explicit
# lever on top of that, per the operator's scoring intent.
AVAILABILITY_WEIGHT = 0.3


def score_org(channel_scores: dict[str, int]) -> tuple[int, list[dict]]:
    """Blend of channel availability (breadth) and per-channel depth. Depth is
    the straight average across every known channel (see CHANNEL_KPI_SCHEMA),
    including 0 for channels with no presence - a missing channel is real white
    space and should pull the score down. Availability is the share of channels
    that are live at all. Both are 0 when there's no presence online anywhere,
    so a score of 0 still means exactly that."""
    all_channels = list(CHANNEL_KPI_SCHEMA.keys())
    breakdown = [
        {"channel": ch, "score": channel_scores.get(ch, 0)}
        for ch in all_channels
    ]
    if not all_channels:
        return 0, breakdown
    depth_avg = sum(b["score"] for b in breakdown) / len(all_channels)
    availability = channel_availability(channel_scores)["score"]
    org_score = round(AVAILABILITY_WEIGHT * availability + (1 - AVAILABILITY_WEIGHT) * depth_avg)
    return org_score, breakdown


SATURATED_THRESHOLD = 60  # a channel has to already be reasonably developed to call it "saturated" rather than just quiet
FLAT_DELTA = 5  # score movement within this range across recent scans counts as "flat"
GROWING_DELTA = 5  # a movement bigger than this counts as real growth


def classify_channel_trend(current_score: int, previous_scores: list[int]) -> str:
    """previous_scores: this channel's scores from prior FULL-SWEEP scans only
    (oldest first) - a channel-scoped scan not checking a channel isn't the
    same as that channel staying flat, so scoped-scan snapshots must be
    filtered out by the caller before this is called.

    Returns one of:
    - "white_space": no real presence - the cheapest reach opportunity
    - "new": presence exists but there's no history yet to judge a trend
    - "growing": score has moved up meaningfully since the last scans
    - "saturated": already well-developed (>=60) and score has plateaued -
      same channel, different approach needed, not just "do more"
    - "healthy": present, not flat, not saturated - steady state
    """
    if current_score == 0:
        return "white_space"
    if not previous_scores:
        return "new"

    delta = current_score - previous_scores[-1]
    if current_score >= SATURATED_THRESHOLD and abs(delta) <= FLAT_DELTA:
        return "saturated"
    if delta > GROWING_DELTA:
        return "growing"
    return "healthy"


# --- Publication-level (one specific published item, not a whole channel) ---
#
# Only channels with genuinely public content can be scanned this way. Email
# and WhatsApp sends are private by nature - there is no public page to
# search for, so no amount of prompt engineering can produce real numbers
# for them. They're still registerable (for record-keeping - what was sent,
# when) but the scan endpoint refuses to fabricate a score for them.
PUBLICATION_SCANNABLE_CHANNELS = ["website", "facebook", "instagram", "linkedin", "twitter_x", "youtube"]
PUBLICATION_UNSCANNABLE_CHANNELS = ["email", "whatsapp"]
PUBLICATION_CHANNELS = PUBLICATION_SCANNABLE_CHANNELS + PUBLICATION_UNSCANNABLE_CHANNELS

PUBLICATION_KPI_SCHEMA: dict[str, dict[str, str]] = {
    "website": {
        "indexed": "bool",
        "backlink_signal": f"enum{QUALITATIVE_LEVELS}",
        "freshness": f"enum{FRESHNESS_LEVELS}",
    },
    "facebook": {"found": "bool", "likes": "int|null", "comments": "int|null", "shares": "int|null"},
    "instagram": {"found": "bool", "likes": "int|null", "comments": "int|null"},
    "linkedin": {"found": "bool", "likes": "int|null", "comments": "int|null", "shares": "int|null"},
    "twitter_x": {"found": "bool", "likes": "int|null", "reposts": "int|null", "replies": "int|null"},
    "youtube": {"found": "bool", "views": "int|null", "likes": "int|null", "comments": "int|null"},
}


def score_publication(channel: str, kpis: dict) -> tuple[int, list[dict]]:
    """Same philosophy as score_channel: Claude only reports the fixed
    fields, this function is the only place a number gets turned into a
    score, so the same real state always scores the same way."""
    kpis = kpis or {}
    breakdown: list[dict] = []

    def add(rule: str, points: int, basis):
        breakdown.append({"rule": rule, "points": points, "basis": basis})

    if channel == "website":
        indexed = bool(kpis.get("indexed"))
        add("indexed", 30 if indexed else 0, indexed)
        if not indexed:
            return 0, breakdown
        backlink = kpis.get("backlink_signal", "none")
        add("backlink_signal", {"none": 0, "low": 12, "medium": 23, "high": 35}.get(backlink, 0), backlink)
        freshness = kpis.get("freshness", "stale")
        add("freshness", {"stale": 0, "occasional": 12, "active": 23, "very_active": 35}.get(freshness, 0), freshness)

    elif channel == "youtube":
        found = bool(kpis.get("found"))
        add("found", 20 if found else 0, found)
        if not found:
            return 0, breakdown
        add("views tier", _tier(kpis.get("views"), [(0, 0), (100, 10), (1000, 25), (10000, 40)]), kpis.get("views"))
        add("likes tier", _tier(kpis.get("likes"), [(0, 0), (10, 10), (100, 20)]), kpis.get("likes"))
        add("comments tier", _tier(kpis.get("comments"), [(0, 0), (3, 10), (20, 20)]), kpis.get("comments"))

    elif channel in ("facebook", "instagram", "linkedin", "twitter_x"):
        found = bool(kpis.get("found"))
        add("found", 20 if found else 0, found)
        if not found:
            return 0, breakdown
        engagement_fields = [k for k in kpis if k != "found"]
        per_field_cap = 80 // max(len(engagement_fields), 1)
        for field in engagement_fields:
            pts = _tier(kpis.get(field), [(0, 0), (5, per_field_cap // 2), (25, per_field_cap)])
            add(f"{field} tier", pts, kpis.get(field))

    else:
        return 0, breakdown

    score = min(100, sum(b["points"] for b in breakdown))
    return score, breakdown
