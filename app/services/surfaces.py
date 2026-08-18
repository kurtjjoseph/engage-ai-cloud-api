"""The channel surface catalog: every post object each channel actually accepts.

There were already two taxonomies in here and neither answered the question
"what can I post to Instagram?":

    content_ideas.CHANNEL_CONTENT_TYPES   the editorial angle  (what to say)
    studio_formats.FORMATS                the media shape      (how it renders)

A *surface* is the third thing: the object the platform's API takes. Instagram
accepts a feed image, a carousel, a Reel and a Story - four different request
shapes, four different specs, four different sets of fields to fill in. A
Google Business "offer" needs a coupon code and an expiry that a "what's new"
post has no place for. A poll is not copy at all, it is a question plus
options plus a duration.

So a surface carries three things, and one definition drives all three:

    fields   -> the JSON the copywriter is asked to produce (pass 2)
    limits   -> what the deterministic check measures (pass 3)
    render   -> which renderer produces the file, at which canvas (pass 4)

Adding a channel surface therefore means adding one entry here, not editing a
prompt, a validator and a renderer separately and hoping they agree.

`publish` records the honest truth about distribution, checked against
services/channels/live.py:

    live    an adapter exists and will post this today
    draft   it lands as a draft the owner approves (the WordPress path)
    manual  we generate it, the owner posts it by hand - no adapter yet

`publish` is documentation, not enforcement. Generation never depends on it.
"""
from dataclasses import dataclass, asdict

# ---------------------------------------------------------------- field types
#
# The copywriter is asked for these by name, the check measures them by name,
# and the renderer reads them by name. Kinds:
#
#   text      one short string, capped at max_chars
#   richtext  a long string (safe HTML for the website, plain text elsewhere)
#   lines     a list of short strings (thread parts, poll options)
#   slides    a list of objects, each with item_keys (carousel pages, video slides)
#   date      an ISO-8601 date, YYYY-MM-DD
#   url       an absolute URL, or a bracketed placeholder the owner fills in
#   choice    one of options


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str
    describe: str
    max_chars: int = 0
    min_items: int = 0
    max_items: int = 0
    required: bool = True
    options: tuple[str, ...] = ()
    item_keys: tuple[str, ...] = ()
    # Per-key character caps inside a "slides" item. The first item_key is
    # always the required one; anything without a cap is uncapped.
    item_caps: tuple[tuple[str, int], ...] = ()

    def cap_for(self, item_key: str) -> int:
        return dict(self.item_caps).get(item_key, 0)

    def as_dict(self) -> dict:
        return asdict(self)


def _f(key: str, label: str, kind: str, describe: str, **kw) -> Field:
    return Field(key=key, label=label, kind=kind, describe=describe, **kw)


# Reusable field groups, so the same idea is spelled the same way everywhere.
_CTA_URL = _f("cta_url", "Button link", "url",
              "The URL the button opens. Use [link] if the organization hasn't given one.",
              required=False)
_CTA_LABEL = _f("cta_label", "Button", "choice",
                "Which action button fits this post.", required=False,
                options=("BOOK", "ORDER", "SHOP", "LEARN_MORE", "SIGN_UP", "CALL"))
_STARTS = _f("starts_on", "Starts", "date", "The start date as YYYY-MM-DD.")
_ENDS = _f("ends_on", "Ends", "date", "The end date as YYYY-MM-DD, on or after the start date.")

_CAROUSEL_SLIDES = _f(
    "slides", "Pages", "slides",
    "One entry per page. \"headline\" is the few large words on the page (at most 60 characters), "
    "\"body\" is the one supporting line under it (at most 140 characters), \"image_prompt\" describes "
    "that page's background - a real scene, no text, no words, no letters. Page 1 is the hook and "
    "the last page is the call to action.",
    min_items=4, max_items=8, item_keys=("headline", "body", "image_prompt"),
    item_caps=(("headline", 60), ("body", 140)),
)


def _video_slides(count: int) -> Field:
    return _f(
        "slides", "Slides", "slides",
        f"Exactly {count} slides. \"narration\" is ONE spoken-style line of at most 90 characters that "
        "appears centred on screen; slide 1 is the hook (it has under 3 seconds to earn the watch), the "
        "middle slides carry the point, the last slide is the call to action. They must read as one "
        "continuous script. \"image_prompt\" describes that slide's background - a real scene, no text, "
        "no words, no letters.",
        min_items=count, max_items=count, item_keys=("narration", "image_prompt"),
        item_caps=(("narration", 90),),
    )


@dataclass(frozen=True)
class Surface:
    """One post object on one channel: its spec, its fields, its renderer."""

    key: str
    channel: str
    label: str
    summary: str
    media: str            # none | image | images | video | document
    render: str           # none | post_image | text_image | carousel | slideshow | document
    publish: str          # live | draft | manual
    body_max: int
    body_target: str
    hashtags_max: int
    width: int = 0
    height: int = 0
    aspect: str = ""
    seconds: float = 0.0
    fields: tuple[Field, ...] = ()
    notes: str = ""
    # For render="text_image": which drafted fields supply the words set on the
    # image. Empty means fall back to the piece's title.
    headline_field: str = ""
    subhead_field: str = ""

    @property
    def id(self) -> str:
        return f"{self.channel}.{self.key}"

    def field(self, key: str) -> Field | None:
        for entry in self.fields:
            if entry.key == key:
                return entry
        return None

    @property
    def slides_field(self) -> Field | None:
        return self.field("slides")

    def as_dict(self) -> dict:
        data = asdict(self)
        data["id"] = self.id
        data["fields"] = [f.as_dict() for f in self.fields]
        return data


def _s(**kw) -> Surface:
    return Surface(**kw)


# --------------------------------------------------------------- the catalog
#
# Ordered channel by channel. `publish` reflects services/channels/live.py as
# it stands - see docs/content-generation-all-channels.md for what each
# "manual" would take to become "live".

SURFACES: tuple[Surface, ...] = (
    # ------------------------------------------------------------- website
    _s(key="post", channel="website", label="Blog post",
       summary="A dated article on the site's blog.",
       media="image", render="post_image", publish="draft",
       body_max=4000, hashtags_max=0,
       body_target="180-350 words of safe HTML (<p>, <h2>, <h3>, <ul>, <li>, <strong>, <em>, <a> only)",
       width=1200, height=630, aspect="1.91:1",
       notes="Becomes a WordPress draft; the image becomes its featured image."),
    _s(key="page", channel="website", label="Standalone page",
       summary="An undated page - a service, an FAQ, a cornerstone resource.",
       media="image", render="post_image", publish="draft",
       body_max=8000, hashtags_max=0,
       body_target="400-900 words of safe HTML organised under <h2> sections",
       width=1200, height=630, aspect="1.91:1",
       notes="Pages carry search weight; write it to still be true in a year."),

    # ------------------------------------------------------ google business
    _s(key="whats_new", channel="google_business", label="What's new",
       summary="The standard Google Business update post.",
       media="image", render="post_image", publish="live",
       body_max=1500, hashtags_max=0,
       body_target="100-250 words of plain text with one clear call to action",
       width=1200, height=900, aspect="4:3",
       fields=(_CTA_LABEL, _CTA_URL),
       notes="topicType STANDARD. No hashtags - Google posts want a plain call to action."),
    _s(key="offer", channel="google_business", label="Offer",
       summary="A time-bound promotion with a coupon code and terms.",
       media="image", render="text_image", publish="manual",
       body_max=1500, hashtags_max=0,
       body_target="60-150 words naming the offer, who it's for, and how to claim it",
       width=1200, height=900, aspect="4:3",
       fields=(
           _f("offer_title", "Offer title", "text",
              "The offer itself in a few words, e.g. \"20% off first visit\".", max_chars=58),
           _STARTS, _ENDS,
           _f("coupon_code", "Coupon code", "text",
              "A short code the customer quotes, or \"\" if none applies.", max_chars=24, required=False),
           _f("terms", "Terms", "text",
              "The conditions in one plain sentence.", max_chars=200, required=False),
           _CTA_URL,
       ),
       headline_field="offer_title", subhead_field="terms",
       notes="topicType OFFER. Our adapter sends STANDARD today - see the docs."),
    _s(key="event", channel="google_business", label="Event",
       summary="A dated event on the business profile.",
       media="image", render="text_image", publish="manual",
       body_max=1500, hashtags_max=0,
       body_target="60-150 words: what it is, why come, and the practical detail",
       width=1200, height=900, aspect="4:3",
       fields=(
           _f("event_title", "Event title", "text", "The event name.", max_chars=58),
           _STARTS, _ENDS, _CTA_LABEL, _CTA_URL,
       ),
       headline_field="event_title", subhead_field="starts_on",
       notes="topicType EVENT."),
    _s(key="alert", channel="google_business", label="Update / alert",
       summary="A short notice - changed hours, a closure, an urgent update.",
       media="none", render="none", publish="manual",
       body_max=1500, hashtags_max=0,
       body_target="40-100 words, factual, leading with the change itself",
       fields=(_CTA_URL,),
       notes="topicType ALERT. Keep it factual - this surface is not for marketing."),

    # ------------------------------------------------------------- youtube
    _s(key="short", channel="youtube", label="Short",
       summary="A vertical Short - the format the algorithm pushes hardest.",
       media="video", render="slideshow", publish="live",
       body_max=900, hashtags_max=3,
       body_target="the description: two or three sentences, the first repeating the hook",
       width=720, height=1280, aspect="9:16", seconds=30.0,
       fields=(
           _f("video_title", "Title", "text",
              "The title shown on the Short. Front-load the hook.", max_chars=90),
           _video_slides(6),
       ),
       notes="Under 3 minutes to count as a Short. Uploads unlisted by default."),
    _s(key="video", channel="youtube", label="Standard video",
       summary="A landscape video for the main feed.",
       media="video", render="slideshow", publish="live",
       body_max=2000, hashtags_max=3,
       body_target="a description: a hook paragraph, then what the video covers, then the call to action",
       width=1280, height=720, aspect="16:9", seconds=45.0,
       fields=(
           _f("video_title", "Title", "text", "The video title.", max_chars=90),
           _video_slides(8),
       ),
       notes="Uploads unlisted by default - see the YouTube note in the docs."),
    _s(key="community_post", channel="youtube", label="Community post",
       summary="A text-and-image post to subscribers, between videos.",
       media="image", render="post_image", publish="manual",
       body_max=1200, hashtags_max=3,
       body_target="2-4 sentences addressed to existing subscribers",
       width=1280, height=720, aspect="16:9"),

    # ------------------------------------------------------------ facebook
    _s(key="text_post", channel="facebook", label="Text post",
       summary="A plain conversational post.",
       media="none", render="none", publish="live",
       body_max=1200, hashtags_max=3,
       body_target="2-5 short conversational sentences ending in a question or call to action"),
    _s(key="photo_post", channel="facebook", label="Photo post",
       summary="A photo with a caption - the everyday Page post.",
       media="image", render="post_image", publish="live",
       body_max=1200, hashtags_max=3,
       body_target="2-5 short conversational sentences ending in a question or call to action",
       width=1200, height=630, aspect="1.91:1"),
    _s(key="link_post", channel="facebook", label="Link post",
       summary="A post whose payload is a link with its preview card.",
       media="none", render="none", publish="live",
       body_max=1200, hashtags_max=2,
       body_target="2-3 sentences that give a reason to click, without repeating the headline",
       fields=(_f("link_url", "Link", "url",
                  "The URL being shared. Use [link] if the organization hasn't given one."),),
       notes="Facebook builds the preview card from the page's own metadata."),
    _s(key="reel", channel="facebook", label="Reel",
       summary="A vertical short video on the Page.",
       media="video", render="slideshow", publish="manual",
       body_max=1200, hashtags_max=3,
       body_target="a caption of 1-3 sentences, the first line carrying the hook",
       width=720, height=1280, aspect="9:16", seconds=30.0,
       fields=(_video_slides(6),),
       notes="4-60 seconds, at least 540x960, 23fps+. Pages only - not profiles or groups."),
    _s(key="story", channel="facebook", label="Story",
       summary="A full-screen frame that expires after 24 hours.",
       media="image", render="text_image", publish="manual",
       body_max=200, hashtags_max=0,
       body_target="one line of on-screen text, at most 100 characters",
       width=1080, height=1920, aspect="9:16",
       fields=(_f("overlay_headline", "On-screen text", "text",
                  "The words set on the frame. Short enough to read in two seconds.", max_chars=60),),
       headline_field="overlay_headline"),
    _s(key="event", channel="facebook", label="Event",
       summary="A Facebook event with a date, a place and a cover image.",
       media="image", render="text_image", publish="manual",
       body_max=2000, hashtags_max=3,
       body_target="the event description: what it is, who it's for, and the practical detail",
       width=1200, height=628, aspect="1.91:1",
       fields=(
           _f("event_title", "Event name", "text", "The event name.", max_chars=64),
           _STARTS, _ENDS,
           _f("location", "Location", "text",
              "Where it happens - a venue name and place, or \"Online\".", max_chars=120),
           _f("ticket_url", "Tickets / RSVP", "url",
              "Where to register. Use [link] if there isn't one yet.", required=False),
       ),
       headline_field="event_title", subhead_field="location"),

    # ----------------------------------------------------------- instagram
    _s(key="feed_image", channel="instagram", label="Feed post",
       summary="A single image in the grid.",
       media="image", render="post_image", publish="live",
       body_max=2200, hashtags_max=12,
       body_target="a strong first line, then 3-6 short lines",
       width=1080, height=1080, aspect="1:1",
       notes="First line is the hook - it's all that shows before 'more'."),
    _s(key="carousel", channel="instagram", label="Carousel",
       summary="Swipeable pages - the format people save and send on.",
       media="images", render="carousel", publish="manual",
       body_max=2200, hashtags_max=12,
       body_target="a caption that sets up the swipe, then the call to action",
       width=1080, height=1350, aspect="4:5",
       fields=(_CAROUSEL_SLIDES,),
       notes="Published as one post with child containers - 2 to 10 pages."),
    _s(key="reel", channel="instagram", label="Reel",
       summary="A vertical short video.",
       media="video", render="slideshow", publish="live",
       body_max=2200, hashtags_max=8,
       body_target="a caption of 1-3 lines, the first carrying the hook",
       width=720, height=1280, aspect="9:16", seconds=30.0,
       fields=(_video_slides(6),),
       notes="5-90 seconds and 9:16 to reach the Reels tab - outside that it posts as a plain video."),
    _s(key="story", channel="instagram", label="Story",
       summary="A full-screen frame that expires after 24 hours.",
       media="image", render="text_image", publish="manual",
       body_max=200, hashtags_max=2,
       body_target="one line of on-screen text, at most 100 characters",
       width=1080, height=1920, aspect="9:16",
       fields=(_f("overlay_headline", "On-screen text", "text",
                  "The words set on the frame. Short enough to read in two seconds.", max_chars=60),),
       headline_field="overlay_headline"),

    # ------------------------------------------------------------ linkedin
    _s(key="text_post", channel="linkedin", label="Text post",
       summary="The plain professional post.",
       media="none", render="none", publish="live",
       body_max=1800, hashtags_max=3,
       body_target="a hook line, a blank line, then 100-200 words, ending with a discussion prompt",
       notes="Professional tone; no hype."),
    _s(key="image_post", channel="linkedin", label="Image post",
       summary="A post with one supporting image.",
       media="image", render="post_image", publish="live",
       body_max=1800, hashtags_max=3,
       body_target="a hook line, a blank line, then 100-200 words, ending with a discussion prompt",
       width=1200, height=627, aspect="1.91:1"),
    _s(key="multi_image", channel="linkedin", label="Multi-image post",
       summary="Two to nine images in one post.",
       media="images", render="carousel", publish="manual",
       body_max=1800, hashtags_max=3,
       body_target="a hook line then 80-150 words explaining what the images show",
       width=1200, height=1200, aspect="1:1",
       fields=(_CAROUSEL_SLIDES,)),
    _s(key="video_post", channel="linkedin", label="Video post",
       summary="A native video - uploaded, not linked.",
       media="video", render="slideshow", publish="manual",
       body_max=1800, hashtags_max=3,
       body_target="a hook line then 80-150 words; assume it is watched with sound off",
       width=1080, height=1080, aspect="1:1", seconds=45.0,
       fields=(_video_slides(8),),
       notes="Uploaded via /rest/videos initializeUpload - adapter not built yet."),
    _s(key="document", channel="linkedin", label="Document (PDF carousel)",
       summary="A swipeable PDF - LinkedIn's highest-dwell format.",
       media="document", render="document", publish="manual",
       body_max=1800, hashtags_max=3,
       body_target="a hook line then 60-120 words on what the document delivers",
       width=1080, height=1350, aspect="4:5",
       fields=(
           _f("document_title", "Document title", "text",
              "The title shown on the document card.", max_chars=80),
           _CAROUSEL_SLIDES,
       ),
       notes="Rendered as a real multi-page PDF."),
    _s(key="article", channel="linkedin", label="Article",
       summary="Long-form published on LinkedIn itself.",
       media="image", render="post_image", publish="manual",
       body_max=12000, hashtags_max=3,
       body_target="600-1200 words of safe HTML under <h2> sections, written as one argument",
       width=1200, height=627, aspect="1.91:1",
       fields=(_f("article_title", "Article title", "text",
                  "The article headline.", max_chars=100),)),
    _s(key="poll", channel="linkedin", label="Poll",
       summary="A question with two to four options.",
       media="none", render="none", publish="manual",
       body_max=1800, hashtags_max=2,
       body_target="the framing above the poll: why you're asking, in 2-4 sentences",
       fields=(
           _f("question", "Question", "text",
              "The poll question. It must be genuinely open - a question you don't know the answer to.",
              max_chars=140),
           _f("options", "Options", "lines",
              "Two to four answer options, each at most 30 characters, mutually exclusive.",
              min_items=2, max_items=4, max_chars=30),
           _f("duration", "Runs for", "choice", "How long the poll stays open.",
              options=("1 day", "3 days", "1 week", "2 weeks")),
       )),

    # ------------------------------------------------------------ twitter/x
    _s(key="tweet", channel="twitter_x", label="Post",
       summary="A single post.",
       media="none", render="none", publish="live",
       body_max=270, hashtags_max=2,
       body_target="one post of at most 270 characters",
       notes="Hard character limit - the check enforces it. Media isn't attachable on our auth."),
    _s(key="thread", channel="twitter_x", label="Thread",
       summary="A sequence of posts that reads as one argument.",
       media="none", render="none", publish="manual",
       body_max=270, hashtags_max=2,
       body_target="the opening post - it has to work alone as well as open the thread",
       fields=(_f("parts", "Thread posts", "lines",
                  "The posts after the opener, in order, each at most 270 characters and each carrying "
                  "one idea. The last one is the call to action. Do not number them.",
                  min_items=2, max_items=8, max_chars=270),)),
    _s(key="poll", channel="twitter_x", label="Poll",
       summary="A question with two to four options.",
       media="none", render="none", publish="manual",
       body_max=270, hashtags_max=1,
       body_target="the question post, at most 270 characters",
       fields=(
           _f("options", "Options", "lines",
              "Two to four options, each at most 25 characters.",
              min_items=2, max_items=4, max_chars=25),
           _f("duration", "Runs for", "choice", "How long the poll stays open.",
              options=("1 day", "3 days", "1 week")),
       )),

    # -------------------------------------------------------------- tiktok
    _s(key="video", channel="tiktok", label="Video",
       summary="A vertical video. Generated here; not yet a scored channel.",
       media="video", render="slideshow", publish="manual",
       body_max=2200, hashtags_max=5,
       body_target="a caption of 80-100 characters - the video carries the message",
       width=720, height=1280, aspect="9:16", seconds=30.0,
       fields=(_video_slides(6),),
       notes="21-34 seconds is the engagement band. Posting needs the Content Posting API."),
)


_BY_ID: dict[str, Surface] = {s.id: s for s in SURFACES}

CHANNELS: tuple[str, ...] = tuple(dict.fromkeys(s.channel for s in SURFACES))

# Where a channel has no surface named, this is the one to use.
_DEFAULT_SURFACE: dict[str, str] = {
    "website": "post",
    "google_business": "whats_new",
    "youtube": "short",
    "facebook": "photo_post",
    "instagram": "feed_image",
    "linkedin": "text_post",
    "twitter_x": "tweet",
    "tiktok": "video",
}
DEFAULT_CHANNEL = "instagram"


def surfaces_for(channel: str) -> list[Surface]:
    return [s for s in SURFACES if s.channel == channel]


def surface_for(channel: str, key: str | None = None) -> Surface:
    """The surface for a (channel, key) pair, degrading to that channel's
    default and then to the global default - a bad picker value must never be
    able to fail a draft."""
    channel = channel if any(s.channel == channel for s in SURFACES) else DEFAULT_CHANNEL
    found = _BY_ID.get(f"{channel}.{key}") if key else None
    if found is not None:
        return found
    return _BY_ID[f"{channel}.{_DEFAULT_SURFACE[channel]}"]


def resolve(surface_id: str) -> Surface | None:
    """Look a surface up by its "channel.key" id."""
    return _BY_ID.get(surface_id or "")


def catalog() -> dict:
    """Everything a picker needs: channels, their surfaces, and each surface's
    full spec including the fields the copywriter will be asked for."""
    return {
        "channels": [
            {
                "key": channel,
                "label": _CHANNEL_LABELS.get(channel, channel.replace("_", " ").title()),
                "default_surface": _DEFAULT_SURFACE[channel],
                "surfaces": [s.as_dict() for s in surfaces_for(channel)],
            }
            for channel in CHANNELS
        ],
        "default_channel": DEFAULT_CHANNEL,
        "publish_states": {
            "live": "Engage AI can post this for you once the channel is connected.",
            "draft": "Lands as a draft on your site for you to approve.",
            "manual": "Engage AI produces the file and the copy; you post it yourself.",
        },
    }


_CHANNEL_LABELS: dict[str, str] = {
    "website": "Website",
    "google_business": "Google Business",
    "youtube": "YouTube",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "twitter_x": "X / Twitter",
    "tiktok": "TikTok",
}


def channel_label(channel: str) -> str:
    return _CHANNEL_LABELS.get(channel, channel.replace("_", " ").title())


# ------------------------------------------------------- prompt construction
#
# The drafting prompt is built from the surface, never hand-written per
# surface. That is the whole point: a new surface is one entry above, and its
# prompt, its validation and its render all follow from it.


def _field_instruction(f: Field) -> str:
    limit = ""
    if f.kind in ("text", "url", "date") and f.max_chars:
        limit = f" At most {f.max_chars} characters."
    elif f.kind == "lines":
        limit = f" Between {f.min_items} and {f.max_items} items, each at most {f.max_chars} characters."
    elif f.kind == "slides":
        limit = (f" Exactly {f.min_items} items." if f.min_items == f.max_items
                 else f" Between {f.min_items} and {f.max_items} items.")
    elif f.kind == "choice":
        limit = f" Exactly one of: {', '.join(f.options)}."
    optional = "" if f.required else " May be \"\" if it genuinely doesn't apply."
    return f'"{f.key}" ({f.label}): {f.describe}{limit}{optional}'


def _field_json_shape(f: Field) -> str:
    if f.kind == "lines":
        return f'"{f.key}": ["string"]'
    if f.kind == "slides":
        inner = ", ".join(f'"{k}": "string"' for k in f.item_keys)
        return f'"{f.key}": [{{{inner}}}]'
    return f'"{f.key}": "string"'


def draft_instructions(surface: Surface) -> str:
    """The surface-specific half of the drafting prompt."""
    article = "an" if surface.label[:1].lower() in "aeiou" else "a"
    lines = [
        f'You are writing {article} {surface.label} for {channel_label(surface.channel)}: {surface.summary}',
        f'"body" is the post copy: {surface.body_target}.',
    ]
    if surface.media in ("image", "images", "document"):
        lines.append(
            '"image_prompt" is a vivid, specific prompt for an image generator - a real scene, subject, '
            'lighting and mood. Never ask for text, words or letters in the image.'
        )
        lines.append('"image_alt" is concise alt text.')
    if surface.media == "video":
        lines.append('"image_alt" is concise alt text for the video thumbnail.')
    if surface.seconds:
        lines.append(
            f'The video runs about {surface.seconds:.0f} seconds at {surface.width}x{surface.height} '
            f'({surface.aspect}). The first slide has under three seconds to earn the watch: open on a '
            'pattern interrupt, a promise or a question - never on the organization\'s name.'
        )
    for f in surface.fields:
        lines.append(_field_instruction(f))
    if surface.hashtags_max:
        lines.append(f'Put {surface.hashtags_max} or fewer relevant hashtags in "hashtags" (no # needed).')
    else:
        lines.append('This surface takes no hashtags - return an empty "hashtags" list.')
    if surface.notes:
        lines.append(f'Constraint: {surface.notes}')
    return "\n".join(lines)


def json_shape(surface: Surface) -> str:
    """The exact JSON object the model must return for this surface."""
    parts = [
        # NOT an internal label, which is what this used to ask for - and got:
        # "Behind the Scenes: Scan to Plan (IG Carousel)". On a website surface
        # this string becomes the published WordPress post title, and on every
        # other surface it is what the operator reads in the library and the
        # calendar. There is no such thing as an internal title here.
        '"title": "the title a reader sees - never a label for us: no format, '
        'channel or campaign-role names in it"',
        '"body": "string"',
        '"hashtags": ["string"]',
    ]
    if surface.media != "none":
        parts.append('"image_prompt": "string"')
        parts.append('"image_alt": "string"')
    parts.extend(_field_json_shape(f) for f in surface.fields)
    return "{" + ",\n  ".join(parts) + "}"


def limits(surface: Surface) -> dict:
    """A compact machine-readable version of the same contract, passed to the
    model alongside the prose so the numbers are stated twice."""
    return {
        "body_max": surface.body_max,
        "hashtags_max": surface.hashtags_max,
        "canvas": {"width": surface.width, "height": surface.height, "aspect": surface.aspect}
        if surface.width else None,
        "seconds": surface.seconds or None,
        "fields": {
            f.key: {"kind": f.kind, "max_chars": f.max_chars or None,
                    "min_items": f.min_items or None, "max_items": f.max_items or None,
                    "options": list(f.options) or None, "required": f.required}
            for f in surface.fields
        },
    }
