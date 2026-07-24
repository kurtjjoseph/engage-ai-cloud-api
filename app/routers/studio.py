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
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.deps import get_current_user
from app.models.entities import ContentItem, MediaAsset, User
from app.routers.organizations import get_owned_org
from app.schemas import ContentOut
from app.services.content_ideas import DEFAULT_SITE_TYPE
from app.services.media_gen import ImageGenService, StudioRenderer
from app.services.studio import StudioService
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

router = APIRouter(prefix="/studio", tags=["studio"])

studio = StudioService()
renderer = StudioRenderer(ImageGenService())

# A render left "running" longer than this is treated as dead - background
# tasks don't survive a redeploy, and the operator needs a retry, not a
# permanent spinner.
_RENDER_TIMEOUT_SECONDS = 15 * 60


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


class EditRequest(BaseModel):
    body: str | None = None
    hashtags: list[str] | None = None
    headline: str | None = None
    subhead: str | None = None
    cta: str | None = None
    narrations: list[str] | None = None


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
    ideas = studio.ideas(_org_context(org), payload.goal, _site_type(org), payload.notes, payload.count)
    if not ideas:
        raise HTTPException(
            status_code=503,
            detail="No ideas could be generated (is ANTHROPIC_API_KEY configured?). Try again.",
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

    draft = studio.draft(_org_context(org), idea, layout, payload.goal, _site_type(org))
    if not draft or not draft.get("body"):
        raise HTTPException(
            status_code=503,
            detail="The copy couldn't be written (is ANTHROPIC_API_KEY configured?). Try again.",
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
    state = _studio_state(item)
    layout = layout_for(state["channel"], state["format"])
    output = item.output_payload or {}
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
    layout = layout_for(state["channel"], state["format"])
    output = item.output_payload or {}

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
    state = _studio_state(item)
    layout = layout_for(state["channel"], state["format"])
    output = item.output_payload or {}

    if layout.format == "video_slideshow" and not (output.get("slides") or []):
        raise HTTPException(status_code=400, detail="There are no slides to render yet.")
    if layout.format != "video_slideshow" and not str(output.get("image_prompt") or "").strip():
        raise HTTPException(status_code=400, detail="There is no image prompt to render.")

    current = _render_state(item)
    if current.get("status") == "running":
        return {"content_id": item.id, **current}

    render = _set_render(db, item, {"status": "running", "error": None, "asset_id": None,
                                    "kind": FORMATS[layout.format]["media"],
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
