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

from anthropic import Anthropic

from app.config import settings
from app.services.claude_json import extract_json
from app.services.content_ideas import guidance_for
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
