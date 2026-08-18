"""Content Studio: the multi-pass content workflow.

One endpoint per pass, so the operator sees and can redirect the work between
each step rather than getting one opaque result:

    GET  /studio/catalog        the three formats, their channels and layouts
    POST /studio/ideas          pass 1: business goal      -> competing ideas
    POST /studio/draft          pass 2: chosen idea        -> copy (auto-checked)
    POST /studio/{id}/check     pass 3: re-check, optionally AI-revise
    POST /studio/{id}/edit      operator's own edits, re-checked on save
    POST /studio/{id}/render    pass 4: copy               -> the actual file

Everything is persisted on the ContentItem, so a piece can be left half-built
and picked up later, and so the existing Content library keeps working - the
studio writes the same output_payload fields the older workflow reads.
"""
from contextlib import contextmanager
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.deps import get_current_user
from app.models.entities import Campaign, ContentItem, MediaAsset, User
from app.routers.organizations import get_owned_org
from app.schemas import ContentOut
from app.services.content_ideas import DEFAULT_SITE_TYPE
from app.services.media_gen import ImageGenService, StudioRenderer
from app.services.studio import StudioService, WriteFailed, support_text
from app.services.studio_formats import (
    DEFAULT_CHANNEL,
    DEFAULT_FORMAT,
    DEFAULT_GOAL,
    FORMATS,
    VIDEO_SECONDS,
    catalog,
    goals_catalog,
    layout_for,
)
from app.services.surfaces import (
    catalog as surfaces_catalog,
    resolve as resolve_surface,
    surface_for,
)

router = APIRouter(prefix="/studio", tags=["studio"])

studio = StudioService()
renderer = StudioRenderer(ImageGenService())

# A render left "running" longer than this is treated as dead - background
# tasks don't survive a redeploy, and the operator needs a retry, not a
# permanent spinner.
_RENDER_TIMEOUT_SECONDS = 15 * 60


@contextmanager
def _writing(what: str):
    """Runs one studio pass and reports what actually went wrong with it.

    Every failure in here used to arrive as "is ANTHROPIC_API_KEY configured?",
    which sent the operator to check an environment variable that was already
    fine. A missing key and a model that fell over are two different sentences,
    and only one of them is about the environment."""
    if studio.client is None:
        raise HTTPException(
            status_code=503,
            detail=f"{what} No ANTHROPIC_API_KEY is set on the API, so nothing can be written.",
        )
    try:
        yield
    except WriteFailed as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class IdeasRequest(BaseModel):
    goal: str = DEFAULT_GOAL
    notes: str | None = None
    count: int = 3


class Idea(BaseModel):
    headline: str
    angle: str = ""
    why: str = ""


class DraftRequest(BaseModel):
    idea: Idea
    format: str = DEFAULT_FORMAT
    channel: str = DEFAULT_CHANNEL
    goal: str = DEFAULT_GOAL


class SurfaceIdeasRequest(BaseModel):
    goal: str = DEFAULT_GOAL
    channels: list[str] | None = None
    notes: str | None = None
    count: int = 3


class SurfaceDraftRequest(BaseModel):
    idea: Idea
    surface: str  # "channel.key", e.g. "instagram.carousel"
    goal: str = DEFAULT_GOAL


class EditRequest(BaseModel):
    body: str | None = None
    hashtags: list[str] | None = None
    headline: str | None = None
    subhead: str | None = None
    cta: str | None = None
    narrations: list[str] | None = None
    # Surface pieces: any declared field by name, e.g. {"options": [...],
    # "coupon_code": "SPRING20"}. Ignored on format-first pieces.
    fields: dict | None = None


def _org_context(org) -> dict:
    return {
        "name": org.name,
        "org_type": org.org_type,
        "mission": org.mission,
        "tone": org.tone,
        "audience": org.audience,
        "locations": org.locations,
        "website_url": org.website_url,
    }


def _site_type(org) -> str:
    return (org.site_facts or {}).get("site_type") or DEFAULT_SITE_TYPE


def _sources_for(item: ContentItem, org, db: Session) -> str:
    """The material this piece is allowed to take a fact from: the profile the
    owner filled in, plus the theme the operator typed if it came from a
    campaign.

    Wired through the EDIT path too, not just the draft path, so the warning
    doesn't quietly disappear the moment an operator changes the hashtags on a
    piece whose invented number they never touched. Their own edits get measured
    by the same rule - which is the point: the check reports what nothing on
    record supports, and asks them to confirm it, not to justify it."""
    theme = None
    if item.campaign_id:
        campaign = db.query(Campaign).filter(Campaign.id == item.campaign_id).first()
        theme = campaign.theme if campaign else None
    return support_text(_org_context(org), theme)


def _get_item(content_id: int, org, db: Session) -> ContentItem:
    item = (
        db.query(ContentItem)
        .filter(ContentItem.id == content_id, ContentItem.organization_id == org.id)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Content not found.")
    return item


def _studio_state(item: ContentItem) -> dict:
    state = (item.output_payload or {}).get("studio")
    if not isinstance(state, dict):
        raise HTTPException(status_code=400, detail="This piece wasn't created in the Content Studio.")
    return state


def _write(item: ContentItem, draft: dict, state: dict) -> dict:
    """Flattens a studio draft onto the ContentItem's output_payload, keeping
    the field names the rest of the plugin already reads (body, hashtags,
    image_prompt, website_post) so nothing else has to know about the studio."""
    channel, fmt = state["channel"], state["format"]
    output = dict(item.output_payload or {})
    output.update({
        "studio": state,
        "channel": channel,
        "content_type_key": fmt,
        "content_type_label": FORMATS[fmt]["label"],
        "media": FORMATS[fmt]["media"],
        "title": draft.get("title", ""),
        "body": draft.get("body", ""),
        "hashtags": draft.get("hashtags", []),
        "image_prompt": draft.get("image_prompt", ""),
        "image_alt": draft.get("image_alt", ""),
        "overlay": draft.get("overlay", {}),
        "slides": draft.get("slides", []),
        "angle": state.get("idea", {}).get("angle", ""),
    })
    if channel == "website":
        output["website_post"] = {"title": draft.get("title", ""), "body_html": draft.get("body", "")}
    item.output_payload = output
    item.title = draft.get("title") or item.title
    return output


# --------------------------------------------------------------- surface mode
#
# A studio piece is either format-first (the original three formats) or
# surface-first (a named post object on a channel). Which one it is, is decided
# at draft time and recorded in the studio state; every later pass reads it back
# with _surface_of(), so check / edit / render are shared and the plugin that
# only knows about formats keeps working untouched.


def _surface_of(state: dict):
    """The Surface this piece was drafted against, or None if it's a
    format-first piece from the original workflow."""
    return resolve_surface(state.get("surface") or "")


def _surface_draft(output: dict, surface) -> dict:
    """Reassembles the stored draft for a surface piece."""
    draft = {key: output.get(key) for key in ("title", "body", "hashtags", "image_prompt", "image_alt")}
    stored = output.get("fields") or {}
    for spec in surface.fields:
        draft[spec.key] = stored.get(spec.key)
    return draft


def _write_surface(item: ContentItem, draft: dict, state: dict) -> dict:
    """Flattens a surface draft onto the ContentItem. The declared fields live
    together under "fields" so the shape is self-describing, and body/hashtags/
    image_prompt/slides are mirrored at the top level so the existing Content
    library and the WordPress path keep reading what they already read."""
    surface = surface_for(state["channel"], state["surface_key"])
    output = dict(item.output_payload or {})
    output.update({
        "studio": state,
        "channel": surface.channel,
        "surface": surface.id,
        "content_type_key": surface.key,
        "content_type_label": surface.label,
        "media": surface.media,
        # The surface's render MODE ("none" for a text post), not the render
        # STATUS - that lives at output["studio"]["render"]. A caller needs this
        # to know a piece has no media pass at all, instead of offering a render
        # button and learning from a 400 that the copy is the whole post.
        "render": surface.render,
        "publish": surface.publish,
        "title": draft.get("title", ""),
        "body": draft.get("body", ""),
        "hashtags": draft.get("hashtags", []),
        "image_prompt": draft.get("image_prompt", ""),
        "image_alt": draft.get("image_alt", ""),
        "fields": {spec.key: draft.get(spec.key) for spec in surface.fields},
        "slides": draft.get("slides") or [],
        "angle": state.get("idea", {}).get("angle", ""),
    })
    if surface.channel == "website":
        output["website_post"] = {"title": draft.get("title", ""), "body_html": draft.get("body", "")}
    item.output_payload = output
    item.title = draft.get("title") or item.title
    return output


@router.get("/surfaces")
def studio_surfaces(user: User = Depends(get_current_user)):
    """Every post object every channel accepts, with the spec and the exact
    fields each one is drafted against - and, per surface, whether Engage AI
    can publish it for you today or hands you the file to post yourself."""
    return {"goals": goals_catalog(), **surfaces_catalog()}


@router.post("/surfaces/ideas")
def studio_surface_ideas(
    organization_id: int,
    payload: SurfaceIdeasRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Pass 1, surface-aware: a goal in, ideas out - each already naming the
    exact surface it should be published on. Nothing is saved."""
    org = get_owned_org(organization_id, db, user)
    with _writing("No ideas could be generated."):
        ideas = studio.surface_ideas(_org_context(org), payload.goal, _site_type(org),
                                     payload.channels, payload.notes, payload.count)
        if not ideas:
            raise HTTPException(
                status_code=503,
                detail="The ideas came back empty. Try again, or give the studio a note to work from.",
            )
    return {"goal": payload.goal, "ideas": ideas}


@router.post("/surfaces/draft", response_model=ContentOut)
def studio_surface_draft(
    organization_id: int,
    payload: SurfaceDraftRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Pass 2 (+ pass 3), surface-aware. Writes the copy and every field the
    surface declares - a Google Business offer gets its coupon code and expiry,
    an X thread gets its parts, a poll gets its options - then runs the same
    deterministic check over all of them before saving."""
    org = get_owned_org(organization_id, db, user)
    surface = resolve_surface(payload.surface)
    if surface is None:
        raise HTTPException(status_code=400, detail=f"Unknown surface '{payload.surface}'.")
    idea = payload.idea.model_dump()

    with _writing("The copy couldn't be written."):
        draft = studio.draft_surface(_org_context(org), idea, surface, payload.goal, _site_type(org))
        if not draft or not draft.get("body"):
            raise HTTPException(
                status_code=503,
                detail="The copy came back empty. Try writing this piece again.",
            )
    draft, report = studio.check_surface(draft, surface, payload.goal,
                                         sources=support_text(_org_context(org), idea))

    state = {
        "version": 2,
        "goal": payload.goal,
        "idea": idea,
        "surface": surface.id,
        "surface_key": surface.key,
        "channel": surface.channel,
        "spec": surface.as_dict(),
        "step": "checked",
        "quality": report,
    }
    item = ContentItem(
        organization_id=org.id,
        content_type=surface.channel,
        title=draft.get("title") or idea["headline"],
        input_payload={"source": "studio", "goal": payload.goal, "channel": surface.channel,
                       "surface": surface.id, "idea": idea, "site_type": _site_type(org)},
        output_payload={},
    )
    _write_surface(item, draft, state)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/catalog")
def studio_catalog(user: User = Depends(get_current_user)):
    """Everything the studio UI needs to build its pickers: the goals it starts
    from, the three formats, and the layout for every channel/format pair."""
    return {"goals": goals_catalog(), **catalog()}


@router.post("/ideas")
def studio_ideas(
    organization_id: int,
    payload: IdeasRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Pass 1. A business goal in, a few competing ideas out - each already
    carrying the format and channel that would serve it. Nothing is saved yet;
    the operator picks one and it becomes a draft."""
    org = get_owned_org(organization_id, db, user)
    with _writing("No ideas could be generated."):
        ideas = studio.ideas(_org_context(org), payload.goal, _site_type(org), payload.notes, payload.count)
        if not ideas:
            raise HTTPException(
                status_code=503,
                detail="The ideas came back empty. Try again, or give the studio a note to work from.",
            )
    return {"goal": payload.goal, "ideas": ideas}


@router.post("/draft", response_model=ContentOut)
def studio_draft(
    organization_id: int,
    payload: DraftRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Pass 2 (+ pass 3). Writes the copy for one idea against its (channel,
    format) layout, then immediately runs the quality check so the operator
    never has to look at a draft whose mechanical problems weren't already
    fixed. Saves it as a tracked ContentItem."""
    org = get_owned_org(organization_id, db, user)
    layout = layout_for(payload.channel, payload.format)
    idea = payload.idea.model_dump()

    with _writing("The copy couldn't be written."):
        draft = studio.draft(_org_context(org), idea, layout, payload.goal, _site_type(org))
        if not draft or not draft.get("body"):
            raise HTTPException(
                status_code=503,
                detail="The copy came back empty. Try writing this piece again.",
            )
    draft, report = studio.check(draft, layout, payload.goal)

    state = {
        "version": 1,
        "goal": payload.goal,
        "idea": idea,
        "format": layout.format,
        "channel": layout.channel,
        "layout": layout.as_dict(),
        "step": "checked",
        "quality": report,
    }
    item = ContentItem(
        organization_id=org.id,
        content_type=layout.channel,
        title=draft.get("title") or idea["headline"],
        input_payload={"source": "studio", "goal": payload.goal, "channel": layout.channel,
                       "format": layout.format, "idea": idea, "site_type": _site_type(org)},
        output_payload={},
    )
    _write(item, draft, state)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def recheck(db: Session, org, item: ContentItem, revise: bool = False) -> dict:
    """Pass 3, without a request around it.

    Lifted out of the endpoint below so the queue-drainer
    (services/automation.py) runs the same check an operator would, rather than
    a second implementation of it that could quietly drift into measuring
    something else. The endpoint is now this function plus authorization.
    """
    state = _studio_state(item)
    goal = state.get("goal", DEFAULT_GOAL)
    output = item.output_payload or {}

    surface = _surface_of(state)
    if surface is not None:
        # A campaign piece records what its role in the arc expects of it
        # (routers/campaigns.py); a one-off piece has no such key and falls
        # back to the goal-derived rule.
        expects_cta = state.get("expects_cta")
        sources = _sources_for(item, org, db)
        draft, report = studio.check_surface(_surface_draft(output, surface), surface, goal,
                                             expects_cta, sources=sources)
        if revise and report["issues"]:
            revised = studio.revise_surface(draft, surface, report, _org_context(org))
            draft, report = studio.check_surface(revised, surface, goal, expects_cta, sources=sources)
            report["revised"] = True
        state["quality"] = report
        state["step"] = "checked"
        _write_surface(item, draft, state)
        db.commit()
        return {"content_id": item.id, "quality": report}

    layout = layout_for(state["channel"], state["format"])
    draft = {k: output.get(k) for k in ("title", "body", "hashtags", "image_prompt", "image_alt", "overlay", "slides")}

    draft, report = studio.check(draft, layout, state.get("goal", DEFAULT_GOAL))
    if revise and report["issues"]:
        revised = studio.revise(draft, layout, report, _org_context(org))
        draft, report = studio.check(revised, layout, state.get("goal", DEFAULT_GOAL))
        report["revised"] = True

    state["quality"] = report
    state["step"] = "checked"
    _write(item, draft, state)
    db.commit()
    return {"content_id": item.id, "quality": report}


@router.post("/{content_id}/check")
def studio_check(
    content_id: int,
    organization_id: int,
    revise: bool = Query(False, description="Have the AI rewrite against the issues found"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Pass 3. Re-measures the draft against its layout and repairs what can be
    repaired mechanically. With revise=true, anything left over (missing call to
    action, placeholder text, too few slides) is sent back for a rewrite and
    then re-checked, so the report always describes what is actually stored."""
    org = get_owned_org(organization_id, db, user)
    item = _get_item(content_id, org, db)
    return recheck(db, org, item, revise)


@router.post("/{content_id}/edit", response_model=ContentOut)
def studio_edit(
    content_id: int,
    organization_id: int,
    payload: EditRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The operator's own edits. Saved and re-checked in one step, so hand-edited
    copy is held to the same channel limits the AI's was."""
    org = get_owned_org(organization_id, db, user)
    item = _get_item(content_id, org, db)
    state = _studio_state(item)
    output = item.output_payload or {}

    surface = _surface_of(state)
    if surface is not None:
        draft = _surface_draft(output, surface)
        if payload.body is not None:
            draft["body"] = payload.body
        if payload.hashtags is not None:
            draft["hashtags"] = payload.hashtags
        # Only fields this surface actually declares - an edit can't smuggle in
        # a key the check knows nothing about.
        declared = {spec.key for spec in surface.fields}
        for key, value in (payload.fields or {}).items():
            if key in declared:
                draft[key] = value
        draft, report = studio.check_surface(draft, surface, state.get("goal", DEFAULT_GOAL),
                                             state.get("expects_cta"), sources=_sources_for(item, org, db))
        state["quality"] = report
        state["step"] = "checked"
        _write_surface(item, draft, state)
        db.commit()
        db.refresh(item)
        return item

    layout = layout_for(state["channel"], state["format"])
    draft = {k: output.get(k) for k in ("title", "body", "hashtags", "image_prompt", "image_alt", "overlay", "slides")}
    if payload.body is not None:
        draft["body"] = payload.body
    if payload.hashtags is not None:
        draft["hashtags"] = payload.hashtags
    overlay = dict(draft.get("overlay") or {})
    for field, value in (("headline", payload.headline), ("subhead", payload.subhead), ("cta", payload.cta)):
        if value is not None:
            overlay[field] = value
    draft["overlay"] = overlay
    if payload.narrations is not None:
        slides = [dict(s) for s in (draft.get("slides") or []) if isinstance(s, dict)]
        for index, text in enumerate(payload.narrations):
            if index < len(slides):
                slides[index]["narration"] = text
        draft["slides"] = slides

    draft, report = studio.check(draft, layout, state.get("goal", DEFAULT_GOAL))
    state["quality"] = report
    state["step"] = "checked"
    _write(item, draft, state)
    db.commit()
    db.refresh(item)
    return item


def _set_render(db: Session, item: ContentItem, patch: dict) -> dict:
    """Merges into studio.render and reassigns the payload, so SQLAlchemy sees
    the JSON column change."""
    output = dict(item.output_payload or {})
    state = dict(output.get("studio") or {})
    render = dict(state.get("render") or {})
    render.update(patch)
    state["render"] = render
    output["studio"] = state
    item.output_payload = output
    db.commit()
    return render


def _render_surface(output: dict, surface, fallback_title: str) -> tuple[list[tuple[bytes, str]], str, str]:
    """Produces the file(s) for a surface piece.

    Returns (results, kind, prompt). `results` is a list because a carousel is
    genuinely N files - everything else returns one, or none if the render
    couldn't produce anything."""
    fields = output.get("fields") or {}
    slides = output.get("slides") or []
    prompt = str(output.get("image_prompt") or "").strip()
    width = surface.width or 1080
    height = surface.height or 1080

    if surface.render == "slideshow":
        narration = " ".join(str(s.get("narration") or "") for s in slides)[:500]
        result = renderer.render_slideshow(slides, width, height,
                                           surface.seconds or VIDEO_SECONDS,
                                           max_slides=max(4, len(slides)))
        return ([result] if result else []), "video", narration

    if surface.render == "carousel":
        return renderer.render_carousel(slides, width, height), "image", prompt

    if surface.render == "document":
        result = renderer.render_document(slides, width, height)
        return ([result] if result else []), "document", prompt

    if surface.render == "text_image":
        headline = str(fields.get(surface.headline_field) or "") if surface.headline_field else ""
        subhead = str(fields.get(surface.subhead_field) or "") if surface.subhead_field else ""
        cta = str(fields.get("cta_label") or "").replace("_", " ").title()
        return ([renderer.render_text_image(prompt, headline or fallback_title, subhead, cta, width, height)],
                "image", prompt)

    return [renderer.render_post_image(prompt, width, height)], "image", prompt


def _execute_render(content_id: int, organization_id: int) -> None:
    """The actual render, on a background worker with its own session.

    Runs out of band because a background image takes tens of seconds to come
    back and the generator only serves one request at a time - an 8-second
    video needs several, which is far longer than any sensible HTTP timeout.
    The plugin polls GET /studio/{id}/render instead of holding a connection
    open, the same way analytics scans already work."""
    db = SessionLocal()
    try:
        item = db.query(ContentItem).filter(ContentItem.id == content_id).first()
        if item is None:
            return
        output = item.output_payload or {}
        state = output.get("studio") or {}

        surface = resolve_surface(state.get("surface") or "")
        if surface is not None:
            results, kind, prompt = _render_surface(output, surface, item.title or "")
            if not results:
                _set_render(db, item, {"status": "failed",
                                       "error": "The media couldn't be produced. Try again."})
                return
            asset_ids = []
            for data, mime in results:
                asset = MediaAsset(organization_id=organization_id, content_item_id=item.id,
                                   kind=kind, mime=mime, prompt=prompt, data=data)
                db.add(asset)
                db.commit()
                db.refresh(asset)
                asset_ids.append(asset.id)

            output = dict(item.output_payload or {})
            state = dict(output.get("studio") or {})
            state["step"] = "rendered"
            state["render"] = {"status": "done", "kind": kind, "asset_id": asset_ids[0],
                               "asset_ids": asset_ids, "mime": results[0][1],
                               "width": surface.width, "height": surface.height,
                               "pages": len(asset_ids) if surface.render == "carousel" else None,
                               "seconds": surface.seconds if kind == "video" else None,
                               "finished_at": datetime.utcnow().isoformat()}
            output["studio"] = state
            output[f"{kind}_asset_id"] = asset_ids[0]
            output[f"{kind}_asset_ids"] = asset_ids
            item.output_payload = output
            db.commit()
            return

        layout = layout_for(state.get("channel", ""), state.get("format", ""))

        if layout.format == "video_slideshow":
            slides = output.get("slides") or []
            result = renderer.render_slideshow(slides, layout.width, layout.height, VIDEO_SECONDS)
            kind = "video"
            prompt = " ".join(str(s.get("narration") or "") for s in slides)[:500]
        else:
            prompt = str(output.get("image_prompt") or "").strip()
            if layout.format == "image_text":
                overlay = output.get("overlay") or {}
                result = renderer.render_text_image(
                    prompt,
                    str(overlay.get("headline") or item.title),
                    str(overlay.get("subhead") or ""),
                    str(overlay.get("cta") or ""),
                    layout.width, layout.height,
                )
            else:
                result = renderer.render_post_image(prompt, layout.width, layout.height)
            kind = "image"

        if not result:
            _set_render(db, item, {"status": "failed",
                                   "error": "The media couldn't be produced. Try again."})
            return

        data, mime = result
        asset = MediaAsset(organization_id=organization_id, content_item_id=item.id,
                           kind=kind, mime=mime, prompt=prompt, data=data)
        db.add(asset)
        db.commit()
        db.refresh(asset)

        output = dict(item.output_payload or {})
        state = dict(output.get("studio") or {})
        state["step"] = "rendered"
        state["render"] = {"status": "done", "kind": kind, "asset_id": asset.id, "mime": mime,
                           "width": layout.width, "height": layout.height,
                           "seconds": VIDEO_SECONDS if kind == "video" else None,
                           "finished_at": datetime.utcnow().isoformat()}
        output["studio"] = state
        output[f"{kind}_asset_id"] = asset.id
        item.output_payload = output
        db.commit()
    except Exception as exc:  # noqa: BLE001 - a worker crash must leave a readable state
        try:
            item = db.query(ContentItem).filter(ContentItem.id == content_id).first()
            if item is not None:
                _set_render(db, item, {"status": "failed", "error": str(exc)[:300]})
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


def render_target(item: ContentItem) -> str:
    """The kind of file this piece renders to - "image", "video" or "document" -
    or an HTTPException(400) saying why it has nothing to render.

    One definition of "can this be rendered at all", shared by the endpoint
    below and by the queue-drainer (services/automation.py). A piece with no
    slides, no image prompt, or on a surface where the copy IS the whole post
    must be refused the same way whoever asked - otherwise the drainer retries
    a text post's impossible render on every sweep, forever.
    """
    state = _studio_state(item)
    output = item.output_payload or {}

    surface = _surface_of(state)
    if surface is not None:
        if surface.render == "none":
            raise HTTPException(
                status_code=400,
                detail=f"A {surface.label.lower()} has no file to render - the copy is the whole post.",
            )
        if surface.render in ("slideshow", "carousel", "document") and not (output.get("slides") or []):
            raise HTTPException(status_code=400, detail="There are no slides to render yet.")
        if surface.render in ("post_image", "text_image") and not str(output.get("image_prompt") or "").strip():
            raise HTTPException(status_code=400, detail="There is no image prompt to render.")
        return {"slideshow": "video", "document": "document"}.get(surface.render, "image")

    layout = layout_for(state["channel"], state["format"])
    if layout.format == "video_slideshow" and not (output.get("slides") or []):
        raise HTTPException(status_code=400, detail="There are no slides to render yet.")
    if layout.format != "video_slideshow" and not str(output.get("image_prompt") or "").strip():
        raise HTTPException(status_code=400, detail="There is no image prompt to render.")
    return FORMATS[layout.format]["media"]


@router.post("/{content_id}/render")
def studio_render(
    content_id: int,
    organization_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Pass 4. Starts the render of the piece's actual file at its channel's
    canvas:

    post_image      an illustrative image
    image_text      the headline composited onto the image
    video_slideshow an 8-second vertical video, narration centred

    Returns immediately with status "running" - poll GET /studio/{id}/render.
    Every path is keyless and falls back to a locally-built background, so a
    render finishes with a usable file rather than an error the operator can't
    act on."""
    org = get_owned_org(organization_id, db, user)
    item = _get_item(content_id, org, db)
    media_kind = render_target(item)

    current = _render_state(item)
    if current.get("status") == "running":
        return {"content_id": item.id, **current}

    render = _set_render(db, item, {"status": "running", "error": None, "asset_id": None,
                                    "kind": media_kind,
                                    "started_at": datetime.utcnow().isoformat()})
    background_tasks.add_task(_execute_render, item.id, org.id)
    return {"content_id": item.id, **render}


@router.get("/{content_id}/render")
def studio_render_status(
    content_id: int,
    organization_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Where the render got to. A render still marked running long after it
    started is reported failed - background tasks don't survive a redeploy, and
    a stuck spinner is worse than a retry button."""
    org = get_owned_org(organization_id, db, user)
    item = _get_item(content_id, org, db)
    render = _render_state(item)
    if render.get("status") == "done" and render.get("asset_id"):
        render["url"] = f"/content/asset/{render['asset_id']}"
        # A carousel is several files; the list is the real answer and "url" is
        # kept as its first page so older callers don't break.
        if render.get("asset_ids"):
            render["urls"] = [f"/content/asset/{asset_id}" for asset_id in render["asset_ids"]]
    return {"content_id": item.id, **render}


def _render_state(item: ContentItem) -> dict:
    render = dict(((item.output_payload or {}).get("studio") or {}).get("render") or {})
    if render.get("status") == "running":
        started = render.get("started_at")
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(started)).total_seconds() if started else 0
        except (TypeError, ValueError):
            age = 0
        if age > _RENDER_TIMEOUT_SECONDS:
            return {**render, "status": "failed",
                    "error": "The render didn't finish (the service may have restarted). Try again."}
    return render or {"status": "none"}
