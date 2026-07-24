"""The Content Studio's format + channel-layout catalog.

Three content formats, deliberately only three, because each one is rendered by
a deterministic renderer that works with no API key and therefore produces a
usable asset every single time:

    post_image      - post copy + a generated illustrative image
    image_text      - a graphic with the headline set ON the image (quote /
                      announcement card), plus the caption to post with it
    video_slideshow - an 8-second vertical video: slides with the narration
                      line centred on screen, held and cross-faded

A "layout" is the intersection of a format and a channel: the pixel dimensions
the channel wants, how long the copy may be, and how many hashtags belong on
it. The same layout object drives three different passes - it shapes the
drafting prompt (pass 2), it is what the quality check measures against (pass
3), and it sets the canvas the renderer paints on (pass 4) - so the copy, the
check and the image can never disagree about the target.
"""
from dataclasses import dataclass, asdict

# One place both the drafting prompt and the quality check read from.
FORMATS: dict[str, dict] = {
    "post_image": {
        "key": "post_image",
        "label": "Post with image",
        "summary": "Written post plus a generated image to go with it.",
        "media": "image",
        "best_for": "Announcements, tips, stories - anywhere the words carry the message.",
    },
    "image_text": {
        "key": "image_text",
        "label": "Image with text on it",
        "summary": "A graphic with your headline set on the image, plus a caption.",
        "media": "image",
        "best_for": "Quotes, offers, one-line announcements - stops the scroll on its own.",
    },
    "video_slideshow": {
        "key": "video_slideshow",
        "label": "8-second video",
        "summary": "Vertical slideshow video with the narration centred on screen.",
        "media": "video",
        "best_for": "Reels, Shorts, Stories - the formats the algorithms push hardest.",
    },
}

DEFAULT_FORMAT = "post_image"

# Total runtime of a video_slideshow, and how it is divided. Fixed at 8s: long
# enough to land a message, short enough to hold attention and to render fast.
VIDEO_SECONDS = 8.0
VIDEO_SLIDES = 4
VIDEO_SECONDS_PER_SLIDE = VIDEO_SECONDS / VIDEO_SLIDES


@dataclass(frozen=True)
class Layout:
    """The rendering + copy contract for one (channel, format) pair."""

    channel: str
    format: str
    width: int
    height: int
    aspect: str
    body_max: int          # hard character ceiling for the post copy
    body_target: str       # human-readable length target, used in the prompt
    hashtags_max: int      # 0 = this channel doesn't take hashtags
    headline_max: int      # chars of on-image headline (image_text)
    subhead_max: int
    notes: str

    def as_dict(self) -> dict:
        return asdict(self)


# Per-channel copy rules. Dimensions come from the format (below), because the
# canvas is a property of the format far more than of the channel.
_CHANNEL_COPY: dict[str, dict] = {
    "website": {
        "label": "Website",
        "body_max": 4000,
        "body_target": "180-350 words of safe HTML (<p>, <h2>, <h3>, <ul>, <li>, <strong>, <em>, <a> only)",
        "hashtags_max": 0,
        "notes": "Becomes a WordPress post; the image becomes its featured image.",
    },
    "instagram": {
        "label": "Instagram",
        "body_max": 2200,
        "body_target": "a strong first line, then 3-6 short lines",
        "hashtags_max": 12,
        "notes": "First line is the hook - it's all that shows before 'more'.",
    },
    "facebook": {
        "label": "Facebook",
        "body_max": 1200,
        "body_target": "2-5 short conversational sentences ending in a question or call to action",
        "hashtags_max": 3,
        "notes": "Conversational; questions earn comments.",
    },
    "linkedin": {
        "label": "LinkedIn",
        "body_max": 1800,
        "body_target": "a hook line, then 100-200 words, ending with a discussion prompt",
        "hashtags_max": 3,
        "notes": "Professional tone; no hype.",
    },
    "twitter_x": {
        "label": "X / Twitter",
        "body_max": 270,
        "body_target": "one post of at most 270 characters",
        "hashtags_max": 2,
        "notes": "Hard character limit - the check enforces it.",
    },
    "google_business": {
        "label": "Google Business",
        "body_max": 1500,
        "body_target": "100-250 words of plain text with one clear call to action",
        "hashtags_max": 0,
        "notes": "No hashtags; Google posts want a plain call to action.",
    },
    "youtube": {
        "label": "YouTube",
        "body_max": 900,
        "body_target": "a Short script: HOOK (first 3 seconds), BODY, CTA",
        "hashtags_max": 3,
        "notes": "Vertical Short; the video is the post.",
    },
}

# Canvas per format. Video is always vertical (Reels/Shorts/Stories all take
# 9:16, and a vertical video still embeds fine on a website).
_FORMAT_CANVAS: dict[str, dict] = {
    "post_image": {"width": 1080, "height": 1080, "aspect": "1:1"},
    "image_text": {"width": 1080, "height": 1350, "aspect": "4:5"},
    "video_slideshow": {"width": 720, "height": 1280, "aspect": "9:16"},
}

# A few channels want a different still-image shape than the square default.
_CANVAS_OVERRIDES: dict[tuple[str, str], dict] = {
    ("website", "post_image"): {"width": 1200, "height": 630, "aspect": "1.91:1"},
    ("website", "image_text"): {"width": 1200, "height": 630, "aspect": "1.91:1"},
    ("linkedin", "post_image"): {"width": 1200, "height": 627, "aspect": "1.91:1"},
    ("facebook", "post_image"): {"width": 1200, "height": 630, "aspect": "1.91:1"},
    ("twitter_x", "post_image"): {"width": 1200, "height": 675, "aspect": "16:9"},
    ("twitter_x", "image_text"): {"width": 1200, "height": 675, "aspect": "16:9"},
    ("google_business", "post_image"): {"width": 1200, "height": 900, "aspect": "4:3"},
    ("google_business", "image_text"): {"width": 1200, "height": 900, "aspect": "4:3"},
    ("youtube", "post_image"): {"width": 1280, "height": 720, "aspect": "16:9"},
    ("youtube", "image_text"): {"width": 1280, "height": 720, "aspect": "16:9"},
}

CHANNELS = list(_CHANNEL_COPY.keys())
DEFAULT_CHANNEL = "instagram"

# On-image text has to stay legible at thumbnail size, so it is capped much
# harder than the caption is.
_HEADLINE_MAX = 70
_SUBHEAD_MAX = 90


def channel_label(channel: str) -> str:
    entry = _CHANNEL_COPY.get(channel)
    return entry["label"] if entry else channel.replace("_", " ").title()


def layout_for(channel: str, fmt: str) -> Layout:
    """The layout for a (channel, format) pair, falling back to sane defaults so
    an unknown channel can never break a render."""
    fmt = fmt if fmt in FORMATS else DEFAULT_FORMAT
    copy = _CHANNEL_COPY.get(channel) or _CHANNEL_COPY[DEFAULT_CHANNEL]
    channel = channel if channel in _CHANNEL_COPY else DEFAULT_CHANNEL
    canvas = _CANVAS_OVERRIDES.get((channel, fmt)) or _FORMAT_CANVAS[fmt]
    return Layout(
        channel=channel,
        format=fmt,
        width=canvas["width"],
        height=canvas["height"],
        aspect=canvas["aspect"],
        body_max=copy["body_max"],
        body_target=copy["body_target"],
        hashtags_max=copy["hashtags_max"],
        headline_max=_HEADLINE_MAX,
        subhead_max=_SUBHEAD_MAX,
        notes=copy["notes"],
    )


def catalog() -> dict:
    """Everything the plugin's studio UI needs to render its pickers: the three
    formats, the channels each supports, and the layout for every pair."""
    return {
        "formats": [
            {
                **FORMATS[key],
                "channels": [
                    {"key": ch, "label": channel_label(ch), "layout": layout_for(ch, key).as_dict()}
                    for ch in CHANNELS
                ],
            }
            for key in FORMATS
        ],
        "video": {"seconds": VIDEO_SECONDS, "slides": VIDEO_SLIDES},
        "default_format": DEFAULT_FORMAT,
        "default_channel": DEFAULT_CHANNEL,
    }


# The business goals the studio starts from (pass 1). Each carries the angle
# that actually serves it, so the idea pass isn't guessing what "more sales"
# means in terms of content.
GOALS: dict[str, dict] = {
    "awareness": {
        "label": "Get discovered by new people",
        "guidance": "Prioritise reach: a strong hook, a broadly relatable idea, and something worth sharing. Avoid inside references.",
    },
    "trust": {
        "label": "Build trust and credibility",
        "guidance": "Prioritise proof: real results, customer stories, transparency about how the work is done.",
    },
    "leads": {
        "label": "Get enquiries and bookings",
        "guidance": "Prioritise one specific offer and one clear next step. Name the problem it solves before the offer.",
    },
    "sales": {
        "label": "Sell a product or service",
        "guidance": "Prioritise the product's concrete benefit and a direct call to action. Be helpful first, commercial second.",
    },
    "community": {
        "label": "Grow engagement with our community",
        "guidance": "Prioritise conversation: ask something real, invite replies, celebrate people by name where possible.",
    },
    "attendance": {
        "label": "Fill an event or service",
        "guidance": "Prioritise the practical details (what, when, where) plus one emotional reason to come.",
    },
}

DEFAULT_GOAL = "awareness"


def goal_guidance(goal: str | None) -> str:
    entry = GOALS.get((goal or "").lower())
    return entry["guidance"] if entry else GOALS[DEFAULT_GOAL]["guidance"]


def goal_label(goal: str | None) -> str:
    entry = GOALS.get((goal or "").lower())
    return entry["label"] if entry else GOALS[DEFAULT_GOAL]["label"]


def goals_catalog() -> list[dict]:
    return [{"key": key, **value} for key, value in GOALS.items()]
