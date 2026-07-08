import json
from anthropic import Anthropic
from app.config import settings

ANALYTICS_PROTOCOL = """You are a digital-presence research analyst. Research the organization
described in the user message using web search, and report what you find, broken down per channel.

Rules:
- Only report what you can find real evidence for via search. If a channel isn't found or has no
  public data, leave it out entirely - never estimate, guess, or invent a number.
- Check for these channels where evidence exists: website, google_business (rating/review count),
  facebook, instagram, youtube, linkedin, twitter_x, news_mentions. Skip any channel with nothing
  findable rather than fabricating a placeholder entry for it.
- For each channel found, report whatever concrete numbers are publicly visible (follower count,
  review count/rating, subscriber count, video count, how recently they posted, etc.) plus one
  short qualitative note.
- Write a short overall summary (2-4 sentences) of the organization's current public digital footprint.
- Return ONLY valid JSON, no markdown fences, no commentary outside the JSON, matching exactly:
{
  "summary": "string",
  "channels": [
    {"channel": "string", "metrics": {}, "notes": "string"}
  ],
  "sources": ["url", "url"]
}
"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
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


class AnalyticsSearchService:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None

    def scan(self, org_context: dict) -> dict:
        if not self.client:
            return {
                "summary": "ANTHROPIC_API_KEY is not set - no scan was run.",
                "channels": [],
                "sources": [],
            }

        user_message = "Research this organization's public digital presence:\n" + json.dumps(org_context)

        response = self.client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            system=ANALYTICS_PROTOCOL,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
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
