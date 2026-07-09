import json
import re
from anthropic import Anthropic
from app.config import settings

KNOWN_CHANNELS = [
    "website", "google_business", "facebook", "instagram",
    "youtube", "linkedin", "twitter_x", "news_mentions",
]

BASE_PROTOCOL = """You are a digital-presence research analyst. Research the organization
described in the user message using web search, and report what you find, broken down per channel.

Rules:
- Only report what you can find real evidence for via search. If a channel isn't found or has no
  public data, leave it out entirely - never estimate, guess, or invent a number.
- For each channel found, report whatever concrete numbers are publicly visible (follower count,
  review count/rating, subscriber count, video count, how recently they posted, etc.) plus one
  short qualitative note.
- If you cite a number from a third-party estimator (e.g. SimilarWeb, a follower-count tracker site)
  rather than the platform itself, say so explicitly in the note (e.g. "SimilarWeb estimates ~50K
  monthly visits") - never present a third-party estimate as if it were first-party ground truth.
- Write a short overall summary (2-4 sentences) of the organization's current public digital footprint.
- After you finish researching, your FINAL message must be ONLY the JSON object below - no leading
  "here is my report" sentence, no markdown fence, no trailing commentary - matching exactly:
{
  "summary": "string",
  "channels": [
    {"channel": "string", "metrics": {}, "notes": "string"}
  ],
  "sources": ["url", "url"]
}
"""

# Per-page website visibility ranking (opt-in - see AnalyticsSearchService.scan's
# include_pages param). This is NOT real traffic data - web search has no access to
# a site's actual Google Analytics/Search Console numbers, which are private. It's a
# visibility/discoverability proxy built entirely from what's publicly searchable.
PAGE_RANKING_ADDENDUM = """
## Per-page website visibility ranking

In addition to the general "website" channel entry, discover individual pages on this
organization's website (e.g. via "site:domain.com" searches) and rank up to 12 of the most
significant ones by PUBLIC VISIBILITY - not real traffic, which web search cannot see.

For each page found, assess:
- Whether it's indexed and turns up in search results at all
- What it appears to rank for / get found for (specific keywords or topics, if apparent)
- Any backlink or "mentioned by" signal (other sites linking to or citing it)
- Freshness signal (recently updated vs. stale, if determinable)
- Any third-party traffic estimate if one turns up (attributed to its source, per the rule above)

Assign each page a "visibility_rank" (1 = most publicly visible/discoverable of the pages found,
increasing from there). Be explicit that this is a visibility/discoverability proxy, not measured
traffic - do not imply these numbers are real analytics.

Add a "pages" array to the "website" channel's entry in your JSON output:
{"channel": "website", "metrics": {...}, "notes": "...", "pages": [
  {"url": "string", "visibility_rank": 1, "signals": {"indexed": true, "ranks_for": ["..."], "backlinks_signal": "...", "freshness_signal": "..."}, "notes": "string"}
]}
"""


def _extract_json(text: str) -> dict:
    """Web-search-augmented responses reliably ignore "no commentary" and
    prepend a sentence like "Based on my research, here's the report" before
    the JSON (sometimes still fenced, sometimes not) - so this looks for a
    fenced block first, then falls back to the outermost {...} span, rather
    than assuming the response starts with the JSON."""
    text = text.strip()

    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1))

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])

    return json.loads(text)


def _extract_citations(response) -> list[str]:
    """Supplements whatever the model self-reports in "sources" with the
    actual citation URLs the web search tool attached to its text output,
    when the SDK exposes them."""
    urls: list[str] = []
    for block in response.content:
        if block.type == "text":
            for citation in getattr(block, "citations", None) or []:
                url = getattr(citation, "url", None)
                if url:
                    urls.append(url)
    return urls


def _build_system_prompt(channels: list[str] | None, include_pages: bool) -> str:
    prompt = BASE_PROTOCOL
    if channels:
        prompt += f"\nOnly research these channels, nothing else: {', '.join(channels)}.\n"
    else:
        prompt += f"\nCheck all of these channels where evidence exists: {', '.join(KNOWN_CHANNELS)}.\n"

    if include_pages and (not channels or "website" in channels):
        prompt += PAGE_RANKING_ADDENDUM

    return prompt


class AnalyticsSearchService:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None

    def scan(self, org_context: dict, channels: list[str] | None = None, include_pages: bool = False) -> dict:
        if not self.client:
            return {
                "summary": "ANTHROPIC_API_KEY is not set - no scan was run.",
                "channels": [],
                "sources": [],
            }

        system = _build_system_prompt(channels, include_pages)
        user_message = "Research this organization's public digital presence:\n" + json.dumps(org_context)

        # Page-level discovery needs more searches (one per candidate page,
        # roughly) and a bigger response budget for up to 12 page entries.
        max_uses = 16 if include_pages else 8
        max_tokens = 8192 if include_pages else 4096

        response = self.client.messages.create(
            model=settings.anthropic_model,
            max_tokens=max_tokens,
            system=system,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses}],
            messages=[{"role": "user", "content": user_message}],
        )

        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            result = _extract_json(text)
        except json.JSONDecodeError:
            result = {"summary": "Scan returned non-JSON output; try again.", "channels": [], "sources": []}

        cited = _extract_citations(response)
        if cited:
            result["sources"] = sorted(set(result.get("sources") or []) | set(cited))

        return result
