import json
import random
import time
from anthropic import Anthropic, APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
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
- The organization's context may include "website_url" and "channel_details" (a per-channel URL or
  handle the organization has confirmed). Treat these as ground truth, not hints. For any channel
  with a given URL, use the web_fetch tool DIRECTLY on that exact URL first - it is already present
  in this message, so this does not depend on a search engine having indexed it. Only fall back to
  web_search (using the exact domain as a "site:example.com" query, or the literal handle as the
  query) if the direct fetch fails or the handle isn't a fetchable URL.
- web_fetch rules that matter here: it only accepts URLs that appear VERBATIM in this conversation.
  Fetch each URL as the EXACT character-for-character string given in the context or in a search
  result - never a variant you constructed (no added/removed trailing slash, no www. added or
  dropped, no http/https swap). A constructed variant returns an "url_not_allowed" error; that
  error means YOUR URL string was modified, not that the site is unreachable - retry with the
  exact original string instead of concluding the fetch failed. And when a fetch returns page
  content, that IS a successful verification of that channel - report what the page shows; do not
  dismiss content you actually retrieved. Only count a channel as
  verified if you actually retrieved or located that specific URL/handle - not merely an
  organization with a similar name. Similarly-named unrelated organizations are common (a generic
  name search often surfaces the wrong company entirely); a name-based search turning up a
  same-named but different organization is not evidence about this one either way. Only report a
  channel as unfound after you've tried both the direct fetch and the direct domain/handle search
  above and neither surfaced it - don't fall back to "couldn't distinguish it from similarly-named
  results" as if that were the same as actually trying the exact URL/handle and coming up empty.
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


def _web_search_failures(response) -> list[str]:
    """A `web_search_tool_result` content block's `content` is either a list
    of real results or a WebSearchToolResultError (type
    "web_search_tool_result_error", carrying an `error_code` like
    "unavailable"/"too_many_requests"/"invalid_tool_input"). A broken or
    unverified tool version/config returns an error on every single call -
    the old code never looked at this, so the model just wrote null/0 into
    every KPI field and the scan was still recorded as status="complete"
    with a clean-looking but entirely empty result. Only treat this as a
    scan failure when EVERY web_search call this turn errored - a mix of
    some errors and some real results means the tool works and the model
    still had genuine data to report on."""
    result_blocks = [b for b in response.content if getattr(b, "type", None) == "web_search_tool_result"]
    if not result_blocks:
        return []
    errors = []
    saw_real_results = False
    for block in result_blocks:
        error_code = getattr(block.content, "error_code", None)
        if error_code is not None:
            errors.append(error_code)
        else:
            saw_real_results = True
    return [] if saw_real_results else errors


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

    def _create_with_retry(self, **kwargs):
        """A scan is a single expensive call; a transient overload/timeout/rate
        limit used to fail the whole scan (one-shot, no retry), which the caller
        then records as a "failed" snapshot - a self-inflicted reliability hit on
        top of the acquisition variance. Retry a few times with exponential
        backoff + jitter on transient errors only; a real 4xx (bad request, auth)
        still raises immediately."""
        attempts = 3
        last_exc = None
        for attempt in range(1, attempts + 1):
            try:
                return self.client.messages.create(**kwargs)
            except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
                last_exc = exc
            except APIStatusError as exc:
                # Retry only transient statuses (429 rate limit, 408/409, 5xx
                # incl. 529 overloaded); a genuine 4xx is a bug, not bad luck.
                if not (exc.status_code in (408, 409, 429) or exc.status_code >= 500):
                    raise
                last_exc = exc
            if attempt < attempts:
                delay = min(2 ** attempt, 20) + random.uniform(0, 1)
                print(
                    f"[analytics_search] transient Anthropic error (attempt {attempt}/{attempts}), "
                    f"retrying in {delay:.1f}s: {last_exc}",
                    flush=True,
                )
                time.sleep(delay)
        raise last_exc

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
        # max_tokens covers ALL output in the turn - tool-use blocks and the
        # model's review of search results, not just the final JSON - so a
        # full 8-channel sweep routinely needs far more than the final
        # JSON's own size. A live scan hit stop_reason=max_tokens at the old
        # 4096 budget after 146s of real research, silently truncating its
        # own output.
        #
        # Budget scales with how many channels this call actually covers, so a
        # per-channel scan (the parallel fast path in routers/analytics.py) uses
        # a small, quick budget (~3 searches / 2048 tokens) instead of the full
        # sweep's - that's what makes each parallel call finish in a few seconds.
        n = len(channels) if channels else len(KNOWN_CHANNELS)
        if include_pages:
            max_uses, max_tokens = 16, 16384
        else:
            max_uses = min(8, max(3, n))
            max_tokens = min(8192, max(2048, n * 1024))

        tools = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": max_uses},
        ]
        # Print (not logging - nothing else in this codebase configures a
        # logging handler, and uvicorn's own INFO lines go straight to
        # stdout/Render logs the same way) the exact request right before it's
        # sent, so a slow or wrong scan can be diagnosed from Render logs
        # without needing to reproduce it.
        print(
            "[analytics_search] calling Claude: "
            + json.dumps({
                "model": settings.anthropic_model,
                "max_tokens": max_tokens,
                "tools": tools,
                "user_message": user_message,
            }),
            flush=True,
        )
        started_at = time.monotonic()

        response = self._create_with_retry(
            model=settings.anthropic_model,
            max_tokens=max_tokens,
            system=system,
            # web_search_20250305 - the same verified-working tool version
            # publication_search.py uses. The web_fetch_20260209 tool and the
            # web_search_20260209 variant were removed here: that combination
            # was never verified against this account/model and was silently
            # returning a tool_result error on every single call - the scan
            # still finished normally and was recorded as status="complete",
            # but every KPI came back null/0 because the model never got a
            # real search or fetch result back to report on.
            tools=tools,
            messages=[{"role": "user", "content": user_message}],
        )

        elapsed = time.monotonic() - started_at
        print(f"[analytics_search] Claude call finished in {elapsed:.1f}s, stop_reason={response.stop_reason}", flush=True)

        if response.stop_reason == "max_tokens":
            # The response was cut off mid-turn - whatever text made it out is
            # not reliably valid/complete JSON (may parse "successfully" into
            # garbage if it happens to look balanced). Treat this as an
            # explicit failure so the caller marks the snapshot "failed"
            # instead of "complete" with silently wrong/empty data.
            raise RuntimeError(
                f"Claude ran out of its {max_tokens}-token response budget before finishing "
                "(stop_reason=max_tokens) - try again, or scope the scan to fewer channels."
            )

        tool_errors = _web_search_failures(response)
        if tool_errors:
            # Every web_search call this turn came back as an error, not
            # results - whatever JSON the model produced anyway is not real
            # research (see _web_search_failures docstring). Fail loudly
            # instead of letting routers/analytics.py record this as a
            # clean status="complete" snapshot full of null/0 KPIs.
            raise RuntimeError(
                "web_search tool failed on every call this turn (error codes: "
                f"{', '.join(sorted(set(tool_errors)))}) - no real search results came back, "
                "so this scan's findings cannot be trusted; not recording it as complete."
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
