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
