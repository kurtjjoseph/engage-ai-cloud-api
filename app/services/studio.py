"""The Content Studio pipeline: business goal -> idea -> copy -> quality check.

Four passes, each a separate step so the operator can stop, look, and redirect
between them instead of getting one opaque blob back:

    pass 1  ideas()   goal + audience     -> a few competing ideas, each with
                                             the format and channel that suits it
    pass 2  draft()   one chosen idea     -> the actual copy, shaped by the
                                             (channel, format) layout contract
    pass 3  check()   the draft           -> a deterministic quality report, with
                                             the mechanical problems auto-fixed
            revise()  the failing draft   -> an AI rewrite aimed at the issues
                                             the check found (only when needed)
    pass 4  rendering lives in services/media_gen.py

Passes 1, 2 and 4 are the expensive ones. Pass 3 is deliberately deterministic
first: length, hashtag count, missing alt text and placeholder text are all
measurable, so they get measured (and mostly repaired) without spending a model
call, and the model is only asked to rewrite what actually needs judgement.
"""
import json
import re
from datetime import date

from anthropic import Anthropic

from app.config import settings
from app.services.claude_json import extract_json
from app.services.content_ideas import guidance_for
from app.services.surfaces import (
    SURFACES,
    Field,
    Surface,
    channel_label as surface_channel_label,
    draft_instructions,
    json_shape,
    limits as surface_limits,
    resolve as resolve_surface,
)
from app.services.studio_formats import (
    CHANNELS,
    DEFAULT_CHANNEL,
    DEFAULT_FORMAT,
    FORMATS,
    Layout,
    VIDEO_SECONDS,
    VIDEO_SLIDES,
    channel_label,
    goal_guidance,
    goal_label,
    layout_for,
)

# Text that means the model left a hole in the draft rather than writing it.
_PLACEHOLDER = re.compile(
    r"(lorem ipsum|\[insert[^\]]*\]|\byour (?:company|business|organization|org) name\b|\bTBD\b|\bTODO\b|xxx+)",
    re.IGNORECASE,
)
# A call to action, loosely: an imperative link/visit/book/call, or a question.
_CTA_HINT = re.compile(
    r"\b(visit|book|call|message|dm|comment|click|shop|order|sign up|subscribe|join|register|"
    r"download|learn more|get in touch|contact|reply|share|save this|come|rsvp)\b",
    re.IGNORECASE,
)
_GOALS_NEEDING_CTA = {"leads", "sales", "attendance"}

_NARRATION_MAX = 90  # chars per slide - what fits legibly, centred, on a phone


def _clean(value) -> str:
    return str(value or "").strip()


def _truncate_words(text: str, limit: int) -> str:
    """Cut to `limit` characters on a word boundary, keeping it readable."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[int(limit * 0.6):]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-") + "…"


def _hashtags(raw) -> list[str]:
    return [_clean(h).lstrip("#") for h in (raw or []) if _clean(h)]


def _as_date(value) -> date | None:
    try:
        return date.fromisoformat(_clean(value))
    except (TypeError, ValueError):
        return None


# "[link]", "[review link]" - a hole the owner is meant to fill, which is a
# legitimate answer for a URL the organization hasn't given us.
_URL_PLACEHOLDER = re.compile(r"^\[[^\]]+\]$")


def _shape_surface_draft(data: dict, surface: Surface, idea: dict) -> dict:
    """Keeps exactly the fields this surface declares and nothing else, so a
    draft never carries leftovers from another surface into the check or the
    render."""
    draft = {
        "title": _clean(data.get("title")) or _clean(idea.get("headline")) or "Untitled",
        "body": _clean(data.get("body")),
        "hashtags": _hashtags(data.get("hashtags")),
    }
    if surface.media != "none":
        draft["image_prompt"] = _clean(data.get("image_prompt"))
        draft["image_alt"] = _clean(data.get("image_alt"))

    for spec in surface.fields:
        raw = data.get(spec.key)
        if spec.kind == "lines":
            items = [_clean(v) for v in (raw or []) if _clean(v)]
            draft[spec.key] = items[: spec.max_items] if spec.max_items else items
        elif spec.kind == "slides":
            items = []
            for entry in raw or []:
                # The first item_key is the one that makes an item real - a
                # slide with no narration, or a page with no headline, is not
                # a slide with a missing field, it's not a slide.
                if isinstance(entry, dict) and _clean(entry.get(spec.item_keys[0])):
                    items.append({key: _clean(entry.get(key)) for key in spec.item_keys})
            draft[spec.key] = items[: spec.max_items] if spec.max_items else items
        else:
            draft[spec.key] = _clean(raw)

    slides = draft.get("slides") or []
    if slides and surface.media != "none" and not draft.get("image_prompt"):
        draft["image_prompt"] = slides[0].get("image_prompt") or ""
    return draft


def _check_field(draft: dict, spec: Field, issue, fixed: list[str]) -> None:
    """One declared field, measured against its own declaration. Mechanical
    problems are repaired in place; only what needs judgement becomes an issue.

    This is the whole reason surfaces are data: every rule below is generic
    over the field kind, so a new surface brings new validation for free."""
    value = draft.get(spec.key)
    name = spec.label.lower()

    if spec.kind == "lines":
        items = [_clean(v) for v in (value or []) if _clean(v)]
        if spec.max_items and len(items) > spec.max_items:
            items = items[: spec.max_items]
            fixed.append(f"Kept the first {spec.max_items} {name}.")
        for index, item in enumerate(items):
            if spec.max_chars and len(item) > spec.max_chars:
                items[index] = _truncate_words(item, spec.max_chars)
                fixed.append(f"Shortened {name} {index + 1} to {spec.max_chars} characters.")
        draft[spec.key] = items
        if len(items) < spec.min_items:
            issue(spec.key, "error",
                  f"{spec.label} needs at least {spec.min_items} entries - there are {len(items)}.")
        return

    if spec.kind == "slides":
        items = [e for e in (value or []) if isinstance(e, dict) and _clean(e.get(spec.item_keys[0]))]
        if spec.max_items and len(items) > spec.max_items:
            items = items[: spec.max_items]
            fixed.append(f"Kept the first {spec.max_items} {name}.")
        for index, item in enumerate(items, start=1):
            for key in spec.item_keys:
                cap = spec.cap_for(key)
                text = _clean(item.get(key))
                if cap and len(text) > cap:
                    item[key] = _truncate_words(text, cap)
                    fixed.append(f"Shortened {name} {index}'s {key.replace('_', ' ')} so it fits on screen.")
            if "image_prompt" in spec.item_keys and not _clean(item.get("image_prompt")):
                item["image_prompt"] = _clean(item.get(spec.item_keys[0]))
                fixed.append(f"Derived {name} {index}'s background from its text.")
        draft[spec.key] = items
        if len(items) < spec.min_items:
            issue(spec.key, "error",
                  f"{spec.label} needs {spec.min_items} entries - there are {len(items)}.")
        return

    text = _clean(value)
    if not text:
        if spec.required:
            issue(spec.key, "error", f"{spec.label} is missing.")
        else:
            draft[spec.key] = ""
        return

    if spec.kind == "choice":
        match = next((option for option in spec.options if option.lower() == text.lower()), None)
        if match is None:
            draft[spec.key] = spec.options[0]
            fixed.append(f'{spec.label} was "{text}", which isn\'t an option - set it to {spec.options[0]}.')
        else:
            draft[spec.key] = match
        return

    if spec.kind == "date":
        if _as_date(text) is None:
            issue(spec.key, "error", f'{spec.label} isn\'t a real date (expected YYYY-MM-DD, got "{text}").')
        return

    if spec.kind == "url":
        if not (text.startswith(("http://", "https://")) or _URL_PLACEHOLDER.match(text)):
            draft[spec.key] = "[link]"
            fixed.append(f"{spec.label} wasn't a usable URL - left [link] for the owner to fill in.")
        return

    if spec.max_chars and len(text) > spec.max_chars:
        draft[spec.key] = _truncate_words(text, spec.max_chars)
        fixed.append(f"Shortened {name} to {spec.max_chars} characters.")


class StudioService:
    def __init__(self) -> None:
        self.client = Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None

    # ---------------------------------------------------------------- pass 1
    def ideas(self, org_context: dict, goal: str, site_type: str | None,
              notes: str | None = None, count: int = 3) -> list[dict]:
        """Turns a business goal into competing content ideas. Each idea names
        the format and channel that would serve it best, so the operator picks
        an idea rather than having to know the format taxonomy first.

        Returns [{"headline", "angle", "why", "format", "channel"}]."""
        count = max(1, min(count, 5))
        if not self.client:
            return []

        format_menu = "\n".join(
            f"- {key}: {spec['label']} - {spec['summary']} Best for: {spec['best_for']}"
            for key, spec in FORMATS.items()
        )
        system = f"""You are Engage AI's content director. The operator has one business goal; propose {count} DISTINCT content ideas that would actually move it.

Business goal: {goal_label(goal)}.
{goal_guidance(goal)}
{guidance_for(site_type)}

For each idea choose the format that suits it, from exactly these three:
{format_menu}

And choose ONE channel from: website, instagram, facebook, linkedin, twitter_x, google_business, youtube.
video_slideshow suits instagram, youtube and facebook; website and google_business prefer post_image.

Ground every idea in the organization's real context (name, mission, tone, audience, locations) - no generic marketing filler. Vary the angle, format and channel across the {count} ideas.

Return ONLY valid JSON, no markdown fences, matching exactly:
{{"ideas": [{{"headline": "the idea in one compelling line",
              "angle": "the specific take, one sentence",
              "why": "one sentence on why this moves the stated goal",
              "format": "post_image|image_text|video_slideshow",
              "channel": "string"}}]}}"""

        user = {"organization": org_context, "goal": goal, "site_type": site_type,
                "operator_notes": _clean(notes) or None, "count": count}
        data = self._json_call(system, user, max_tokens=2048)
        out: list[dict] = []
        for raw in (data.get("ideas") if isinstance(data, dict) else None) or []:
            if not isinstance(raw, dict):
                continue
            headline = _clean(raw.get("headline"))
            if not headline:
                continue
            fmt = _clean(raw.get("format"))
            channel = _clean(raw.get("channel"))
            out.append({
                "headline": headline,
                "angle": _clean(raw.get("angle")),
                "why": _clean(raw.get("why")),
                # An unrecognised format or channel degrades to the default
                # rather than failing the pass - the operator can change both
                # on the next step anyway.
                "format": fmt if fmt in FORMATS else DEFAULT_FORMAT,
                "channel": channel if channel in CHANNELS else DEFAULT_CHANNEL,
            })
        return out[:count]

    # ---------------------------------------------------------------- pass 2
    def draft(self, org_context: dict, idea: dict, layout: Layout, goal: str,
              site_type: str | None) -> dict:
        """Writes the real copy for one idea, shaped by its layout contract.

        Returns a format-specific draft:
          post_image      {title, body, hashtags, image_prompt, image_alt}
          image_text      + overlay {headline, subhead, cta}
          video_slideshow + slides [{narration, image_prompt}] and caption body
        Empty dict when no model is configured."""
        if not self.client:
            return {}

        shape = {
            "post_image": (
                'Write the post copy and describe the image that goes with it.\n'
                f'"body" is the post itself: {layout.body_target}.\n'
                '"image_prompt" is a vivid, specific prompt for an image generator - describe a real scene, '
                'subject, lighting and mood. Never ask for text, words or letters in the image.\n'
                '"image_alt" is concise alt text.'
            ),
            "image_text": (
                'This is a graphic: the words are set ON the image, and a caption goes beside it.\n'
                f'"overlay.headline" is the line that appears large on the image - at most {layout.headline_max} '
                'characters, punchy, no trailing period.\n'
                f'"overlay.subhead" supports it in at most {layout.subhead_max} characters (may be empty).\n'
                '"overlay.cta" is a short call to action of at most 30 characters (may be empty).\n'
                '"image_prompt" describes the BACKGROUND only - an atmospheric, uncluttered scene with room for '
                'text: simple composition, soft depth of field, no text, no words, no letters, no signage.\n'
                f'"body" is the caption posted with the graphic: {layout.body_target}.\n'
                '"image_alt" is concise alt text that includes the headline wording.'
            ),
            "video_slideshow": (
                f'This is a {VIDEO_SECONDS:.0f}-second vertical video of exactly {VIDEO_SLIDES} slides '
                f'({VIDEO_SECONDS / VIDEO_SLIDES:.0f} seconds each).\n'
                f'"slides" has exactly {VIDEO_SLIDES} items. Each "narration" is ONE spoken-style line of at most '
                f'{_NARRATION_MAX} characters that appears centred on screen - slide 1 is the hook, the middle '
                'slides carry the point, the last slide is the call to action. They must read as one continuous '
                'sentence-by-sentence script.\n'
                '"slides[].image_prompt" describes that slide\'s background image - a real scene, no text, no '
                'words, no letters.\n'
                f'"body" is the caption posted with the video: {layout.body_target}.\n'
                '"image_alt" is concise alt text for the video thumbnail.'
            ),
        }[layout.format]

        hashtag_rule = (
            f'Put {min(layout.hashtags_max, 8)} or fewer relevant hashtags in "hashtags" (no # needed).'
            if layout.hashtags_max else 'This channel takes no hashtags - return an empty "hashtags" list.'
        )
        cta_rule = ('The goal demands one clear next step - make the call to action explicit and specific.'
                    if goal in _GOALS_NEEDING_CTA else
                    'End on something that invites a response rather than a hard sell.')

        system = f"""You are Engage AI's copywriter. Write ONE ready-to-publish piece of content. It must be usable exactly as written - never describe what could be written.

Business goal: {goal_label(goal)}. {goal_guidance(goal)}
{guidance_for(site_type)}

The idea to execute:
  headline: {idea.get('headline', '')}
  angle: {idea.get('angle', '')}

Channel: {channel_label(layout.channel)}. {layout.notes}
Format: {FORMATS[layout.format]['label']}. Canvas {layout.width}x{layout.height} ({layout.aspect}).

{shape}

{hashtag_rule}
{cta_rule}
Use the organization's real context (name, mission, tone, audience, locations) so it sounds like them, and never exceed a stated character limit.

Return ONLY valid JSON, no markdown fences, matching exactly:
{{"title": "short internal title for this piece",
  "body": "string",
  "hashtags": ["string"],
  "image_prompt": "string",
  "image_alt": "string",
  "overlay": {{"headline": "string", "subhead": "string", "cta": "string"}},
  "slides": [{{"narration": "string", "image_prompt": "string"}}]}}
Use "" or [] or {{}} for the fields this format doesn't use."""

        user = {"organization": org_context, "idea": idea, "goal": goal,
                "channel": layout.channel, "format": layout.format,
                "limits": {"body_max": layout.body_max, "hashtags_max": layout.hashtags_max,
                           "headline_max": layout.headline_max, "slides": VIDEO_SLIDES}}
        data = self._json_call(system, user, max_tokens=4096)
        if not isinstance(data, dict):
            return {}
        return self._shape_draft(data, layout, idea)

    def _shape_draft(self, data: dict, layout: Layout, idea: dict) -> dict:
        """Keeps only the fields this format uses, so a draft never carries
        half-filled leftovers from another format into the check or the render."""
        draft = {
            "title": _clean(data.get("title")) or _clean(idea.get("headline")) or "Untitled",
            "body": _clean(data.get("body")),
            "hashtags": _hashtags(data.get("hashtags")),
            "image_prompt": _clean(data.get("image_prompt")),
            "image_alt": _clean(data.get("image_alt")),
            "overlay": {},
            "slides": [],
        }
        if layout.format == "image_text":
            overlay = data.get("overlay") if isinstance(data.get("overlay"), dict) else {}
            draft["overlay"] = {
                "headline": _clean(overlay.get("headline")) or draft["title"],
                "subhead": _clean(overlay.get("subhead")),
                "cta": _clean(overlay.get("cta")),
            }
        if layout.format == "video_slideshow":
            slides = []
            for raw in (data.get("slides") or [])[:VIDEO_SLIDES]:
                if not isinstance(raw, dict):
                    continue
                narration = _clean(raw.get("narration")) or _clean(raw.get("caption"))
                if not narration:
                    continue
                slides.append({"narration": narration, "image_prompt": _clean(raw.get("image_prompt"))})
            draft["slides"] = slides
            draft["image_prompt"] = draft["image_prompt"] or (slides[0]["image_prompt"] if slides else "")
        return draft

    # ---------------------------------------------------------------- pass 3
    def check(self, draft: dict, layout: Layout, goal: str) -> tuple[dict, dict]:
        """Measures a draft against its layout contract and repairs what can be
        repaired mechanically. Returns (repaired_draft, report), where report is
        {"score": 0-100, "passed": bool, "issues": [...], "fixed": [...]}.

        Runs with no API key - this is arithmetic and string handling, not
        judgement. Issues left in the report are the ones that genuinely need a
        rewrite (see revise())."""
        draft = json.loads(json.dumps(draft or {}))  # don't mutate the caller's dict
        issues: list[dict] = []
        fixed: list[str] = []

        def issue(field: str, severity: str, message: str) -> None:
            issues.append({"field": field, "severity": severity, "message": message})

        body = _clean(draft.get("body"))
        if not body:
            issue("body", "error", "The post copy is empty.")
        elif len(body) > layout.body_max:
            draft["body"] = _truncate_words(body, layout.body_max)
            fixed.append(f"Trimmed the copy to {layout.body_max} characters for {channel_label(layout.channel)}.")
            body = draft["body"]

        tags = _hashtags(draft.get("hashtags"))
        if layout.hashtags_max == 0 and tags:
            draft["hashtags"] = []
            fixed.append(f"Removed hashtags - {channel_label(layout.channel)} posts don't use them.")
        elif len(tags) > layout.hashtags_max:
            draft["hashtags"] = tags[: layout.hashtags_max]
            fixed.append(f"Kept the first {layout.hashtags_max} hashtags.")

        if _PLACEHOLDER.search(body):
            issue("body", "error", "The copy still contains placeholder text - it needs the real detail.")

        if goal in _GOALS_NEEDING_CTA and body and not (_CTA_HINT.search(body) or "?" in body):
            issue("body", "warning", "No clear call to action, and this goal needs one.")

        if layout.format in ("post_image", "image_text"):
            if not _clean(draft.get("image_prompt")):
                issue("image_prompt", "error", "No image prompt, so there is nothing to render.")
            if not _clean(draft.get("image_alt")):
                draft["image_alt"] = _truncate_words(_clean(draft.get("title")), 120)
                fixed.append("Filled in alt text from the title (accessibility).")

        if layout.format == "image_text":
            overlay = draft.get("overlay") if isinstance(draft.get("overlay"), dict) else {}
            headline = _clean(overlay.get("headline"))
            if not headline:
                issue("overlay.headline", "error", "No headline to set on the image.")
            elif len(headline) > layout.headline_max:
                overlay["headline"] = _truncate_words(headline, layout.headline_max)
                fixed.append(f"Shortened the on-image headline to {layout.headline_max} characters so it stays legible.")
            if len(_clean(overlay.get("subhead"))) > layout.subhead_max:
                overlay["subhead"] = _truncate_words(_clean(overlay.get("subhead")), layout.subhead_max)
                fixed.append("Shortened the sub-headline.")
            draft["overlay"] = overlay

        if layout.format == "video_slideshow":
            slides = [s for s in (draft.get("slides") or []) if isinstance(s, dict) and _clean(s.get("narration"))]
            if len(slides) < VIDEO_SLIDES:
                issue("slides", "error",
                      f"Only {len(slides)} of {VIDEO_SLIDES} slides - an {VIDEO_SECONDS:.0f}-second video needs all {VIDEO_SLIDES}.")
            for index, slide in enumerate(slides[:VIDEO_SLIDES], start=1):
                narration = _clean(slide.get("narration"))
                if len(narration) > _NARRATION_MAX:
                    slide["narration"] = _truncate_words(narration, _NARRATION_MAX)
                    fixed.append(f"Shortened slide {index}'s narration so it fits on screen.")
                if not _clean(slide.get("image_prompt")):
                    slide["image_prompt"] = slide["narration"]
                    fixed.append(f"Derived slide {index}'s background from its narration.")
            draft["slides"] = slides[:VIDEO_SLIDES]

        errors = sum(1 for i in issues if i["severity"] == "error")
        warnings = len(issues) - errors
        score = max(0, 100 - errors * 30 - warnings * 10)
        return draft, {"score": score, "passed": errors == 0, "issues": issues, "fixed": fixed}

    def revise(self, draft: dict, layout: Layout, report: dict, org_context: dict) -> dict:
        """Asks the model to rewrite a draft against the specific issues the
        check found. Returns the revised draft, or the original unchanged if
        there's no model or the rewrite doesn't come back usable."""
        issues = report.get("issues") or []
        if not self.client or not issues:
            return draft
        problems = "\n".join(f"- {i['field']}: {i['message']}" for i in issues)
        system = f"""You are Engage AI's editor. Revise the draft below so every problem is fixed. Change only what the problems require - keep the voice, the idea and everything that already works.

Problems to fix:
{problems}

Constraints: body at most {layout.body_max} characters ({layout.body_target}); at most {layout.hashtags_max} hashtags; on-image headline at most {layout.headline_max} characters; exactly {VIDEO_SLIDES} slides for a video, each narration at most {_NARRATION_MAX} characters. Image prompts must never ask for text or letters in the image.

Return ONLY valid JSON, no markdown fences, in the same shape as the draft you were given."""
        user = {"organization": org_context, "format": layout.format, "channel": layout.channel, "draft": draft}
        data = self._json_call(system, user, max_tokens=4096)
        if not isinstance(data, dict) or not _clean(data.get("body")):
            return draft
        return self._shape_draft(data, layout, {"headline": draft.get("title", "")})

    # ---------------------------------------------------- surface-aware passes
    #
    # The three passes above are format-first: post_image / image_text /
    # video_slideshow. These are surface-first - the same goal -> idea -> copy
    # -> check pipeline, but the contract is read from services/surfaces.py.
    #
    # That is the difference that matters: there is no branch here per surface.
    # A Google Business offer gets asked for its coupon code and expiry, an X
    # thread for its parts, a LinkedIn poll for its options and duration, all
    # because the surface declares those fields - not because this file knows
    # what an offer is. Adding a surface adds no code here.

    def surface_ideas(self, org_context: dict, goal: str, site_type: str | None,
                      channels: list[str] | None = None, notes: str | None = None,
                      count: int = 3) -> list[dict]:
        """Pass 1, surface-aware. Each idea names the exact surface to publish
        it on ("instagram.carousel"), not just a channel.

        Returns [{"headline", "angle", "why", "channel", "surface"}]."""
        count = max(1, min(count, 5))
        if not self.client:
            return []

        allowed = [s for s in SURFACES if not channels or s.channel in channels]
        if not allowed:
            allowed = list(SURFACES)
        menu = "\n".join(f"- {s.id}: {surface_channel_label(s.channel)} {s.label} - {s.summary}"
                         for s in allowed)

        system = f"""You are Engage AI's content director. The operator has one business goal; propose {count} DISTINCT content ideas that would actually move it.

Business goal: {goal_label(goal)}.
{goal_guidance(goal)}
{guidance_for(site_type)}

For each idea pick the ONE surface that suits it best, by its exact id, from:
{menu}

Vary the surface and the angle across the {count} ideas - don't propose three variations of the same post. Ground every idea in the organization's real context (name, mission, tone, audience, locations); no generic marketing filler.

Return ONLY valid JSON, no markdown fences, matching exactly:
{{"ideas": [{{"headline": "the idea in one compelling line",
              "angle": "the specific take, one sentence",
              "why": "one sentence on why this moves the stated goal",
              "surface": "one id from the list above"}}]}}"""

        user = {"organization": org_context, "goal": goal, "site_type": site_type,
                "operator_notes": _clean(notes) or None, "count": count}
        data = self._json_call(system, user, max_tokens=2048)
        allowed_ids = {s.id for s in allowed}
        out: list[dict] = []
        for raw in (data.get("ideas") if isinstance(data, dict) else None) or []:
            if not isinstance(raw, dict):
                continue
            headline = _clean(raw.get("headline"))
            if not headline:
                continue
            # An unrecognised surface degrades to the first allowed one rather
            # than failing the pass; the operator can change it on the next step.
            surface_id = _clean(raw.get("surface"))
            surface = resolve_surface(surface_id) if surface_id in allowed_ids else allowed[0]
            out.append({
                "headline": headline,
                "angle": _clean(raw.get("angle")),
                "why": _clean(raw.get("why")),
                "channel": surface.channel,
                "surface": surface.id,
            })
        return out[:count]

    def draft_surface(self, org_context: dict, idea: dict, surface: Surface, goal: str,
                      site_type: str | None, brief: str = "") -> dict:
        """Pass 2, surface-aware. Writes the copy plus every field the surface
        declares. Empty dict when no model is configured.

        `brief` is optional extra standing instruction for this one piece - the
        Campaign Creator passes the campaign's big idea and this piece's role in
        the arc, so a piece written as part of a run knows what the run is
        arguing and where it sits in it. Empty for a one-off studio piece, which
        is the normal case."""
        if not self.client:
            return {}

        cta_rule = ('The goal demands one clear next step - make the call to action explicit and specific.'
                    if goal in _GOALS_NEEDING_CTA else
                    'End on something that invites a response rather than a hard sell.')
        brief_block = f"\n{_clean(brief)}\n" if _clean(brief) else ""

        system = f"""You are Engage AI's copywriter. Write ONE ready-to-publish piece of content. It must be usable exactly as written - never describe what could be written, and never leave a placeholder.

Business goal: {goal_label(goal)}. {goal_guidance(goal)}
{guidance_for(site_type)}

The idea to execute:
  headline: {idea.get('headline', '')}
  angle: {idea.get('angle', '')}
{brief_block}
{draft_instructions(surface)}

{cta_rule}
Use the organization's real context (name, mission, tone, audience, locations) so it sounds like them, and never exceed a stated character limit.

Return ONLY valid JSON, no markdown fences, matching exactly:
{json_shape(surface)}"""

        user = {"organization": org_context, "idea": idea, "goal": goal,
                "surface": surface.id, "limits": surface_limits(surface)}
        data = self._json_call(system, user, max_tokens=8192)
        if not isinstance(data, dict):
            return {}
        return _shape_surface_draft(data, surface, idea)

    def check_surface(self, draft: dict, surface: Surface, goal: str,
                      expects_cta: bool | None = None) -> tuple[dict, dict]:
        """Pass 3, surface-aware. Measures the draft against the surface
        contract and repairs everything mechanically repairable.

        Runs with no API key - this is arithmetic and string handling. Only
        issues that genuinely need judgement are left for revise_surface().

        `expects_cta` overrides the goal-derived call-to-action rule for one
        piece. The Campaign Creator sets it from the piece's role in the arc:
        a hook is briefed NOT to ask, so holding it to the campaign's
        leads/sales goal would flag a warning for doing exactly what it was
        told. None (the normal case) keeps the goal-derived rule."""
        draft = json.loads(json.dumps(draft or {}))
        issues: list[dict] = []
        fixed: list[str] = []

        def issue(field: str, severity: str, message: str) -> None:
            issues.append({"field": field, "severity": severity, "message": message})

        label = surface_channel_label(surface.channel)

        body = _clean(draft.get("body"))
        if not body:
            issue("body", "error", "The post copy is empty.")
        elif len(body) > surface.body_max:
            draft["body"] = _truncate_words(body, surface.body_max)
            fixed.append(f"Trimmed the copy to {surface.body_max} characters for {label} {surface.label.lower()}.")
            body = draft["body"]

        tags = _hashtags(draft.get("hashtags"))
        if surface.hashtags_max == 0 and tags:
            draft["hashtags"] = []
            fixed.append(f"Removed hashtags - a {label} {surface.label.lower()} doesn't use them.")
        elif len(tags) > surface.hashtags_max:
            draft["hashtags"] = tags[: surface.hashtags_max]
            fixed.append(f"Kept the first {surface.hashtags_max} hashtags.")

        if _PLACEHOLDER.search(body):
            issue("body", "error", "The copy still contains placeholder text - it needs the real detail.")

        needs_cta = goal in _GOALS_NEEDING_CTA if expects_cta is None else expects_cta
        if needs_cta and body and not (_CTA_HINT.search(body) or "?" in body):
            issue("body", "warning",
                  "No clear call to action, and this piece is the one that asks." if expects_cta
                  else "No clear call to action, and this goal needs one.")

        if surface.media in ("image", "images", "document"):
            if not _clean(draft.get("image_prompt")) and not draft.get("slides"):
                issue("image_prompt", "error", "No image prompt, so there is nothing to render.")
            if not _clean(draft.get("image_alt")):
                draft["image_alt"] = _truncate_words(_clean(draft.get("title")), 120)
                fixed.append("Filled in alt text from the title (accessibility).")

        for spec in surface.fields:
            _check_field(draft, spec, issue, fixed)

        # The one cross-field rule: a date range has to run forwards.
        start, end = _as_date(draft.get("starts_on")), _as_date(draft.get("ends_on"))
        if start and end and end < start:
            draft["ends_on"] = draft["starts_on"]
            fixed.append("The end date was before the start date - set them to the same day.")

        errors = sum(1 for i in issues if i["severity"] == "error")
        warnings = len(issues) - errors
        return draft, {"score": max(0, 100 - errors * 30 - warnings * 10),
                       "passed": errors == 0, "issues": issues, "fixed": fixed}

    def revise_surface(self, draft: dict, surface: Surface, report: dict, org_context: dict) -> dict:
        """Asks for a rewrite against the specific issues the check found.
        Returns the original unchanged if there's no model or the rewrite
        doesn't come back usable."""
        issues = report.get("issues") or []
        if not self.client or not issues:
            return draft
        problems = "\n".join(f"- {i['field']}: {i['message']}" for i in issues)
        system = f"""You are Engage AI's editor. Revise the draft below so every problem is fixed. Change only what the problems require - keep the voice, the idea and everything that already works.

Problems to fix:
{problems}

The contract this piece must satisfy:
{draft_instructions(surface)}

Return ONLY valid JSON, no markdown fences, matching exactly:
{json_shape(surface)}"""
        user = {"organization": org_context, "surface": surface.id, "draft": draft,
                "limits": surface_limits(surface)}
        data = self._json_call(system, user, max_tokens=8192)
        if not isinstance(data, dict) or not _clean(data.get("body")):
            return draft
        return _shape_surface_draft(data, surface, {"headline": draft.get("title", "")})

    # ------------------------------------------------------------------ util
    def _json_call(self, system: str, user: dict, max_tokens: int) -> dict:
        """One Claude call returning parsed JSON, or {} on any failure - a pass
        that can't parse its own output must not 500 the request."""
        try:
            response = self.client.messages.create(
                model=settings.anthropic_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": json.dumps(user)}],
            )
        except Exception:  # noqa: BLE001 - surfaced to the operator as "try again"
            return {}
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        try:
            data = extract_json(text)
        except (json.JSONDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
