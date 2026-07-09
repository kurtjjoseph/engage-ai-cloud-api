import json
from anthropic import Anthropic
from app.config import settings
from app.services.analytics_scoring import CHANNEL_KPI_SCHEMA
from app.services.claude_json import extract_citations, extract_json

KNOWN_CHANNELS = list(CHANNEL_KPI_SCHEMA.keys())


def _schema_block() -> str:
    lines = []
    for channel, fields in CHANNEL_KPI_SCHEMA.items():
        field_desc = ", ".join(f'"{name}": {typ}' for name, typ in fields.items())
        lines.append(f"  {channel}: {{{field_desc}}}")
    return "\n".join(lines)


BASE_PROTOCOL = f"""You are a digital-presence research analyst. Research the organization
described in the user message using web search, and report what you find, broken down per channel.

Rules:
- Only report what you can find real evidence for via search. For any field you cannot find real
  evidence for, use null (or "none"/false, matching that field's type) - never estimate, guess, or
  invent a number or an enum value.
- If the organization's context includes "channel_details" with a URL or handle for a channel,
  that is the organization's own confirmed profile for that channel - go to it directly and verify
  it (don't just search by org name and hope you land on the right profile among same-named ones).
  A channel with nothing in channel_details still gets researched via ordinary search.
- Every channel you report MUST use exactly this fixed set of fields - no extra fields, no renamed
  fields, so results are comparable across scans over time:
{_schema_block()}
- Where a field is an enum, you MUST use one of the exact listed values, nothing else.
- If you cite a number from a third-party estimator (e.g. SimilarWeb, a follower-count tracker site)
  rather than the platform itself, say so explicitly in that channel's "notes" (e.g. "SimilarWeb
  estimates ~50K monthly visits") - never present a third-party estimate as if it were first-party
  ground truth. Third-party estimates do NOT go in the fixed KPI fields above unless a field
  explicitly says otherwise (e.g. website's third_party_traffic_estimate) - mention them in notes.
- Write a short overall summary (2-4 sentences) of the organization's current public digital footprint.
- After you finish researching, your FINAL message must be ONLY the JSON object below - no leading
  "here is my report" sentence, no markdown fence, no trailing commentary - matching exactly:
{{
  "summary": "string",
  "channels": [
    {{"channel": "string (one of the channel names above)", "kpis": {{...exact fields for that channel...}}, "notes": "string"}}
  ],
  "sources": ["url", "url"]
}}
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
{"channel": "website", "kpis": {...}, "notes": "...", "pages": [
  {"url": "string", "visibility_rank": 1, "signals": {"indexed": true, "ranks_for": ["..."], "backlinks_signal": "...", "freshness_signal": "..."}, "notes": "string"}
]}
"""


def _build_system_prompt(channels: list[str] | None, include_pages: bool) -> str:
    prompt = BASE_PROTOCOL
    if channels:
        prompt += f"\nOnly research these channels, nothing else: {', '.join(channels)}.\n"
    else:
        prompt += f"\nCheck all of these channels: {', '.join(KNOWN_CHANNELS)}.\n"

    if include_pages and (not channels or "website" in channels):
        prompt += PAGE_RANKING_ADDENDUM

    return prompt


def _sanitize_channel_entry(entry: dict) -> dict:
    """Drops anything Claude reported that isn't in the fixed schema for
    that channel (extra chatty fields, renamed fields) - keeps only what
    the scorer knows how to read, plus notes/pages which are separate."""
    channel = entry.get("channel")
    schema = CHANNEL_KPI_SCHEMA.get(channel)
    raw_kpis = entry.get("kpis") or {}
    kpis = {key: raw_kpis.get(key) for key in schema} if schema else raw_kpis

    cleaned = {"channel": channel, "kpis": kpis, "notes": entry.get("notes", "")}
    if entry.get("pages"):
        cleaned["pages"] = entry["pages"]
    return cleaned


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
            result = extract_json(text)
        except json.JSONDecodeError:
            result = {"summary": "Scan returned non-JSON output; try again.", "channels": [], "sources": []}

        result["channels"] = [_sanitize_channel_entry(c) for c in result.get("channels", []) if c.get("channel") in CHANNEL_KPI_SCHEMA]

        cited = extract_citations(response)
        if cited:
            result["sources"] = sorted(set(result.get("sources") or []) | set(cited))

        return result
