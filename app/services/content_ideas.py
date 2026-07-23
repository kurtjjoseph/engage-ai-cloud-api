"""Suggests and drafts website content tailored to the kind of site it is.

Uses Claude (the same working backend as services/agent_ai.py - the OpenAI path
in services/ai.py is dormant because OPENAI_API_KEY isn't set). The plugin
detects the site's type (ecommerce / church / business) from the WordPress
install and reports it (Organization.site_facts["site_type"]); the drafts are
shaped to that type so a shop gets product-led posts and a church gets sermon/
event content, not generic filler."""
import json

from anthropic import Anthropic

from app.config import settings
from app.services.claude_json import extract_json

# Per-site-type guidance for what content actually serves that kind of site.
# Unknown/missing type falls back to "business".
SITE_TYPE_GUIDANCE: dict[str, str] = {
    "church": (
        "This is a church / ministry site. Good posts: sermon recaps with a takeaway, "
        "upcoming service or event invitations, short devotionals, ministry or volunteer "
        "spotlights, testimonies. Warm, faith-centered, inviting - never salesy."
    ),
    "ecommerce": (
        "This is an online shop (WooCommerce). Good posts: product spotlights and how-to-use "
        "guides, buying guides and comparisons, seasonal or gift roundups, customer stories, "
        "care/maintenance tips. Helpful first, commercial second; end with a soft call to action."
    ),
    "business": (
        "This is a business / service or creator site. Good posts: practical how-to and "
        "educational articles, service explainers, case studies or results, industry tips and "
        "FAQs, behind-the-scenes. Establish expertise and answer real customer questions."
    ),
}

DEFAULT_SITE_TYPE = "business"


def guidance_for(site_type: str | None) -> str:
    return SITE_TYPE_GUIDANCE.get((site_type or "").lower(), SITE_TYPE_GUIDANCE[DEFAULT_SITE_TYPE])


# Five content types per channel, each chosen to move that channel's specific
# score levers (see services/analytics_scoring.py CHANNEL_KPI_SCHEMA): website
# = freshness/indexed-pages/backlinks; google_business = reviews/activity;
# youtube = video_count/frequency; social = post_count/frequency/engagement;
# news_mentions = mention count/recency. "raises" is shown to the operator so
# they know why a type helps; "guidance" steers the draft.
CHANNEL_CONTENT_TYPES: dict[str, list[dict]] = {
    "website": [
        {"key": "blog_post", "label": "Blog article", "raises": "freshness + indexed pages",
         "guidance": "A timely blog article on a topic the audience searches for."},
        {"key": "pillar_page", "label": "Pillar / cornerstone page", "raises": "indexed pages + backlinks",
         "guidance": "A comprehensive cornerstone page that other pages and sites can link to."},
        {"key": "faq_page", "label": "FAQ page", "raises": "indexed pages + search visibility",
         "guidance": "A clear FAQ answering the real questions this audience asks, each as its own heading."},
        {"key": "case_study", "label": "Case study / testimonial", "raises": "trust + backlinks",
         "guidance": "A short outcome-focused case study or testimonial that builds trust."},
        {"key": "resource_guide", "label": "Guide / linkable resource", "raises": "backlinks",
         "guidance": "A genuinely useful how-to guide or checklist worth linking to and sharing."},
    ],
    "google_business": [
        {"key": "gbp_offer", "label": "Offer / promotion post", "raises": "activity + clicks",
         "guidance": "A Google Business 'offer' post with a clear, time-bound call to action."},
        {"key": "gbp_event", "label": "Event post", "raises": "recent activity",
         "guidance": "A Google Business event post: what, when, where, and why to come."},
        {"key": "gbp_update", "label": "What's-new update", "raises": "freshness",
         "guidance": "A short 'what's new' update that keeps the profile active."},
        {"key": "review_request", "label": "Review request message", "raises": "review count + rating",
         "guidance": "A short, warm message the org can send a happy customer asking for a Google review; include a [review link] placeholder."},
        {"key": "gbp_qa", "label": "Q&A seed", "raises": "profile completeness",
         "guidance": "A common customer question and a clear answer to seed the profile's Q&A."},
    ],
    "youtube": [
        {"key": "short_script", "label": "Short (vertical) script", "raises": "video count + frequency",
         "guidance": "A 30-60s vertical Short script."},
        {"key": "howto_script", "label": "How-to / tutorial script", "raises": "watch time + subscribers",
         "guidance": "A concise how-to/tutorial video script that teaches one thing well."},
        {"key": "story_script", "label": "Story / testimonial script", "raises": "engagement",
         "guidance": "A short story or testimonial video script with a clear arc."},
        {"key": "bts_script", "label": "Behind-the-scenes script", "raises": "posting frequency",
         "guidance": "A light behind-the-scenes video script."},
        {"key": "announce_script", "label": "Announcement / promo script", "raises": "posting frequency",
         "guidance": "A short announcement or promo video script."},
    ],
    "facebook": [
        {"key": "fb_question", "label": "Question / poll post", "raises": "engagement",
         "guidance": "A conversational post that ends in a question to spark comments."},
        {"key": "fb_event", "label": "Event promotion", "raises": "engagement + reach",
         "guidance": "An inviting event promotion post."},
        {"key": "fb_story", "label": "Story / testimonial", "raises": "engagement",
         "guidance": "A short human story or testimonial post."},
        {"key": "fb_tip", "label": "Tip / value post", "raises": "posting frequency",
         "guidance": "A quick, useful tip the audience can act on."},
        {"key": "fb_bts", "label": "Behind-the-scenes", "raises": "posting frequency",
         "guidance": "A behind-the-scenes glimpse post."},
    ],
    "instagram": [
        {"key": "ig_carousel", "label": "Educational carousel", "raises": "engagement + saves",
         "guidance": "An educational carousel: slide-by-slide points people will save."},
        {"key": "ig_reel", "label": "Reel script", "raises": "reach + frequency",
         "guidance": "A 15-30s Reel script with a strong hook."},
        {"key": "ig_photo", "label": "Photo caption", "raises": "posting frequency",
         "guidance": "An engaging single-photo caption."},
        {"key": "ig_story", "label": "Story sequence", "raises": "engagement",
         "guidance": "A 3-5 frame Story sequence (one line per frame, with a poll or question)."},
        {"key": "ig_quote", "label": "Quote / inspiration", "raises": "engagement",
         "guidance": "A shareable quote or inspiration post caption."},
    ],
    "linkedin": [
        {"key": "li_insight", "label": "Industry insight post", "raises": "engagement + followers",
         "guidance": "A professional insight post with a hook, a point, and a discussion prompt."},
        {"key": "li_article", "label": "Thought-leadership article", "raises": "authority",
         "guidance": "A short thought-leadership article establishing expertise."},
        {"key": "li_milestone", "label": "Company milestone", "raises": "engagement",
         "guidance": "A milestone/announcement post that invites congratulations and shares."},
        {"key": "li_case", "label": "How-we-did-it case study", "raises": "authority",
         "guidance": "A concise 'how we did it' case study post."},
        {"key": "li_poll", "label": "Poll / discussion starter", "raises": "engagement",
         "guidance": "A discussion-starter post posing a poll-style question with a few options."},
    ],
    "twitter_x": [
        {"key": "x_thread", "label": "Educational thread", "raises": "engagement + reach",
         "guidance": "An educational thread: numbered tweets, each one idea, each <=270 chars."},
        {"key": "x_take", "label": "Opinion / take", "raises": "engagement",
         "guidance": "A single sharp, defensible opinion tweet (<=270 chars)."},
        {"key": "x_tip", "label": "Quick tip", "raises": "posting frequency",
         "guidance": "A single quick-tip tweet (<=270 chars)."},
        {"key": "x_question", "label": "Question / poll", "raises": "engagement",
         "guidance": "A single question or poll-style tweet that invites replies."},
        {"key": "x_announce", "label": "Announcement", "raises": "posting frequency",
         "guidance": "A single announcement tweet (<=270 chars)."},
    ],
    "news_mentions": [
        {"key": "press_release", "label": "Press release", "raises": "mentions + recency",
         "guidance": "A standard press release: headline, dateline, lead answering who/what/when/where/why, 2-3 body paragraphs, an 'About' boilerplate, and a media contact line."},
        {"key": "story_pitch", "label": "Story pitch to media", "raises": "mentions",
         "guidance": "A short, personalized pitch email to a journalist proposing a newsworthy angle."},
        {"key": "milestone_announce", "label": "Milestone announcement", "raises": "mentions",
         "guidance": "A milestone announcement written to be picked up by local media."},
        {"key": "expert_oped", "label": "Expert op-ed / commentary", "raises": "mentions + authority",
         "guidance": "A short op-ed offering expert commentary on a current topic in the field."},
        {"key": "event_angle", "label": "Community event angle", "raises": "mentions",
         "guidance": "A community-interest event angle a local outlet would want to cover."},
    ],
}

# How the draft body should be shaped per channel, so a Google post is short, a
# YouTube script is a script, an Instagram caption carries hashtags, etc.
_CHANNEL_FORMAT: dict[str, str] = {
    "website": "Each body is safe HTML (<p>, <h2>, <h3>, <ul>, <li>, <strong>, <em>, <a> only - never <script>/<style>), 180-350 words.",
    "google_business": "Each body is a Google Business post: 100-250 words of plain text with one clear call to action. No hashtags.",
    "youtube": "Each body is a short video script in plain text with labeled sections: HOOK (first 3 seconds), BODY (the key points), CTA (what to do next).",
    "facebook": "Each body is a Facebook post: 2-5 short, conversational sentences of plain text ending in a question or call to action.",
    "instagram": "Each body is an Instagram caption: a strong first line then 3-6 short lines of plain text. Put 6-12 relevant hashtags in 'hashtags' (without the # is fine).",
    "linkedin": "Each body is a LinkedIn post: a hook first line, then 100-200 words of professional plain text, ending with a prompt. Up to 3 hashtags in 'hashtags'.",
    "twitter_x": "For a thread, body is numbered tweets ('1/ ...', '2/ ...') each <=270 characters. For a single post, body is one tweet <=270 characters. Up to 3 hashtags in 'hashtags'.",
    "news_mentions": "Each body is plain text in the requested press/pitch format. 'title' is the headline or subject line.",
}


def content_types_catalog() -> dict:
    """The full per-channel content-type catalog for the plugin's picker."""
    return {
        channel: [{"key": t["key"], "label": t["label"], "raises": t["raises"]} for t in types]
        for channel, types in CHANNEL_CONTENT_TYPES.items()
    }


def _content_type_entry(channel: str, content_type_key: str) -> dict | None:
    for entry in CHANNEL_CONTENT_TYPES.get(channel, []):
        if entry["key"] == content_type_key:
            return entry
    return None


class ContentIdeaService:
    def __init__(self) -> None:
        self.client = Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None

    def suggest(self, org_context: dict, site_type: str | None, count: int = 3) -> list[dict]:
        """Returns a list of ready-to-review drafts, each:
        {"title": str, "angle": str (one line on why this post), "body_html": str}.
        Empty list if no API key is configured (the caller surfaces that)."""
        count = max(1, min(count, 6))
        if not self.client:
            return []

        system = f"""You are Engage AI, a content director drafting website posts for one organization.
{guidance_for(site_type)}

Propose {count} DISTINCT post ideas, each fully written out (not described). Use the organization's real
context (name, mission, tone, audience, locations) so the drafts sound like them. Keep each body focused
and usable as-is: 180-350 words of safe HTML (<p>, <h2>, <h3>, <ul>, <li>, <strong>, <em>, <a> only -
never <script>/<style>). Vary the angles across the {count} ideas.

Return ONLY valid JSON, no markdown fences, matching exactly:
{{"ideas": [{{"title": "string", "angle": "one sentence on why this post helps", "body_html": "string"}}]}}"""

        user = {"organization": org_context, "site_type": (site_type or DEFAULT_SITE_TYPE), "count": count}
        response = self.client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": json.dumps(user)}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        try:
            data = extract_json(text)
        except (json.JSONDecodeError, ValueError):
            return []
        ideas = data.get("ideas") if isinstance(data, dict) else None
        out: list[dict] = []
        for idea in (ideas or [])[:count]:
            if not isinstance(idea, dict):
                continue
            title = str(idea.get("title") or "").strip()
            body = str(idea.get("body_html") or "").strip()
            if not title or not body:
                continue
            out.append({"title": title, "angle": str(idea.get("angle") or "").strip(), "body_html": body})
        return out

    def suggest_for_channel(self, org_context: dict, channel: str, content_type_key: str,
                            site_type: str | None, count: int = 3) -> list[dict]:
        """Drafts content of a specific type for a specific channel, shaped so it
        actually raises that channel's engagement score. Returns items:
        {"title", "body", "hashtags": [..], "angle"}. Empty list on no key / no
        matching content type / no output."""
        count = max(1, min(count, 6))
        entry = _content_type_entry(channel, content_type_key)
        if not self.client or entry is None:
            return []

        fmt = _CHANNEL_FORMAT.get(channel, _CHANNEL_FORMAT["facebook"])
        system = f"""You are Engage AI, a content director creating a {entry['label']} for the {channel.replace('_', ' ')} channel of one organization.
Purpose: content that raises this channel's engagement score by improving {entry['raises']}.
{entry['guidance']}
{guidance_for(site_type)}
{fmt}

Write {count} DISTINCT, ready-to-use items using the organization's real context (name, mission, tone,
audience, locations) so they sound like them. Vary the angle across items.

Return ONLY valid JSON, no markdown fences, matching exactly:
{{"items": [{{"title": "string", "body": "string", "hashtags": ["string"]}}]}}
Include "hashtags" only where the format above asks for them; otherwise use an empty list."""

        user = {"organization": org_context, "channel": channel, "content_type": entry["label"], "count": count}
        response = self.client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": json.dumps(user)}],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        try:
            data = extract_json(text)
        except (json.JSONDecodeError, ValueError):
            return []
        out: list[dict] = []
        for item in (data.get("items") if isinstance(data, dict) else None) or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            body = str(item.get("body") or "").strip()
            if not title or not body:
                continue
            hashtags = [str(h).lstrip("#").strip() for h in (item.get("hashtags") or []) if str(h).strip()]
            out.append({"title": title, "body": body, "hashtags": hashtags[:12],
                        "label": entry["label"], "angle": f"Raises {entry['raises']}"})
        return out
