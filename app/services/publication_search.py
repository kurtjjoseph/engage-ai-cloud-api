import json
from anthropic import Anthropic
from app.config import settings
from app.services.analytics_scoring import PUBLICATION_KPI_SCHEMA
from app.services.claude_json import extract_citations, extract_json


def _schema_for(channel: str) -> str:
    fields = PUBLICATION_KPI_SCHEMA.get(channel, {})
    return ", ".join(f'"{name}": {typ}' for name, typ in fields.items())


PROTOCOL_TEMPLATE = """You are checking the public performance of ONE specific published item, not a whole
channel. You will be given its exact URL and channel.

Rules:
- Search for that exact URL/post. Only report what you can find real evidence for. For any field you
  cannot find real evidence for, use null (or false, matching that field's type) - never estimate,
  guess, or invent a number.
- Report exactly these fields for this channel, no extra/renamed fields:
  {schema}
- Write one short note (1-2 sentences) on what you found (or didn't - a brand-new post may simply not
  be indexed/crawled yet, which is itself useful information, not a failure).
- Your FINAL message must be ONLY this JSON, no leading commentary, no markdown fence:
{{
  "kpis": {{...exact fields above...}},
  "notes": "string",
  "sources": ["url"]
}}
"""


class PublicationSearchService:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None

    def scan(self, channel: str, url: str) -> dict:
        if not self.client:
            return {"kpis": {}, "notes": "ANTHROPIC_API_KEY is not set - no scan was run.", "sources": []}

        system = PROTOCOL_TEMPLATE.format(schema=_schema_for(channel))
        user_message = f"Channel: {channel}\nURL: {url}"

        response = self.client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
            system=system,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": user_message}],
        )

        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            result = extract_json(text)
        except json.JSONDecodeError:
            result = {"kpis": {}, "notes": "Scan returned non-JSON output; try again.", "sources": []}

        schema = PUBLICATION_KPI_SCHEMA.get(channel, {})
        raw_kpis = result.get("kpis") or {}
        result["kpis"] = {key: raw_kpis.get(key) for key in schema}

        cited = extract_citations(response)
        if cited:
            result["sources"] = sorted(set(result.get("sources") or []) | set(cited))

        return result
