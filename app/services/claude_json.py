"""Shared helpers for parsing structured JSON out of Claude responses that
used the web_search tool - factored out because both analytics_search.py
(channel scans) and publication_search.py (single-URL scans) need the exact
same "the model prepends commentary before the JSON" workaround."""
import json
import re


def extract_json(text: str) -> dict:
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


def extract_citations(response) -> list[str]:
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
