"""Campaigns: a whole run of content, planned as one arc and built as pieces.

Two things live here, and they are not the same feature:

  the Campaign Creator   POST /campaigns/plan   -> a plan, nothing saved
                         POST /campaigns        -> save it
                         POST /campaigns/{id}/build -> write every piece
    One goal and one big idea become a scheduled set of pieces across channels.
    The plan is deliberately a separate step from the build: a plan costs one
    model call and is cheap to throw away, a build costs one call per piece and
    produces real drafts, so the operator gets to look, move dates, swap
    surfaces and drop pieces in between.

  the original generators POST /campaigns/event | /announcements | /sermon
    The church-specific one-shot generators this router started as. Untouched
    and still routed, because installed plugins call them.

A campaign never publishes anything. It produces the same ContentItems the
Content Studio produces, each carrying campaign_id, and every existing path -
quality check, edit, render, publish to a channel - works on them unchanged.
"""
import re
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.deps import get_current_user
from app.models.entities import Campaign, ContentItem, User
from app.routers.organizations import get_owned_org
from app.routers.studio import _write_surface
from app.schemas import AnnouncementsRequest, ContentOut, EventCampaignRequest, SermonEngagementRequest
from app.services.ai import EngageAIService
from app.services.campaign import (
    CampaignPlanner,
    DEFAULT_PIECES,
    MAX_PIECES,
    MIN_PIECES,
    ROLES,
    PlanFailed,
    as_date,
    role_brief,
    role_expects_cta,
    roles_catalog,
    window,
)
from app.services.content_ideas import DEFAULT_SITE_TYPE
from app.services.studio import StudioService
from app.services.studio_formats import DEFAULT_GOAL, goals_catalog
from app.services.surfaces import (
    SURFACES,
    catalog as surfaces_catalog,
    resolve as resolve_surface,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
ai = EngageAIService()
planner = CampaignPlanner()
studio = StudioService()

# A build left "running" longer than this is treated as dead - background tasks
# don't survive a redeploy, and the operator needs a retry, not a permanent
# spinner. Generous, because a twelve-piece build is twelve model calls.
_BUILD_TIMEOUT_SECONDS = 30 * 60


def org_to_memory(org) -> dict:
    return {
        "name": org.name,
        "org_type": org.org_type,
        "mission": org.mission,
        "tone": org.tone,
        "audience": org.audience,
        "colors": org.colors,
        "ministries": org.ministries,
        "recurring_schedule": org.recurring_schedule,
        "locations": org.locations,
        "speakers": org.speakers,
    }


def save_content(db: Session, org_id: int, content_type: str, title: str, input_payload: dict, output_payload: dict) -> ContentItem:
    item = ContentItem(
        organization_id=org_id,
        content_type=content_type,
        title=title,
        input_payload=input_payload,
        output_payload=output_payload,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# ------------------------------------------------------------ the campaign API


class PlanRequest(BaseModel):
    goal: str = DEFAULT_GOAL
    theme: str | None = None
    channels: list[str] | None = None
    pieces: int = DEFAULT_PIECES
    starts_on: str | None = None
    ends_on: str | None = None


class SaveRequest(BaseModel):
    """A plan the operator accepted - usually the one POST /campaigns/plan just
    returned, with their own edits applied to the items."""

    name: str
    goal: str = DEFAULT_GOAL
    theme: str | None = None
    big_idea: str | None = None
    audience: str | None = None
    channels: list[str] | None = None
    starts_on: str | None = None
    ends_on: str | None = None
    items: list[dict]


class ItemPatch(BaseModel):
    """What an operator may change on a planned piece before it is written.
    The copy itself is edited afterwards, in the studio, on the real draft."""

    headline: str | None = None
    angle: str | None = None
    role: str | None = None
    surface: str | None = None
    scheduled_on: str | None = None


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


def _get_campaign(campaign_id: int, org, db: Session) -> Campaign:
    campaign = (
        db.query(Campaign)
        .filter(Campaign.id == campaign_id, Campaign.organization_id == org.id)
        .first()
    )
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return campaign


def _build_state(campaign: Campaign) -> dict:
    """Where the build got to. A build still marked running long after it
    started is reported failed rather than left spinning forever."""
    build = dict(campaign.build or {})
    if build.get("status") == "running":
        try:
            started = datetime.fromisoformat(build.get("started_at") or "")
            age = (datetime.utcnow() - started).total_seconds()
        except (TypeError, ValueError):
            age = 0
        if age > _BUILD_TIMEOUT_SECONDS:
            return {**build, "status": "failed",
                    "error": "The build didn't finish (the service may have restarted). Try again."}
    return build or {"status": "none"}


def _as_out(campaign: Campaign) -> dict:
    items = campaign.plan or []
    return {
        "id": campaign.id,
        "organization_id": campaign.organization_id,
        "name": campaign.name,
        "goal": campaign.goal,
        "theme": campaign.theme,
        "big_idea": campaign.big_idea,
        "audience": campaign.audience,
        "channels": campaign.channels,
        "starts_on": campaign.starts_on.isoformat() if campaign.starts_on else None,
        "ends_on": campaign.ends_on.isoformat() if campaign.ends_on else None,
        "status": campaign.status,
        "items": items,
        "counts": {
            "total": len(items),
            "drafted": sum(1 for i in items if i.get("status") == "drafted"),
            "failed": sum(1 for i in items if i.get("status") == "failed"),
        },
        "build": _build_state(campaign),
        "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
    }


def _renumber(items: list[dict]) -> list[dict]:
    """Indexes are positions, so they are reassigned whenever the list changes -
    an item's identity is its position in the plan, and nothing outside the plan
    refers to it by index once it has a content_id."""
    for index, item in enumerate(items):
        item["index"] = index
    return items


@router.get("/options")
def campaign_options(user: User = Depends(get_current_user)):
    """Everything the campaign UI needs to build its pickers: the goals a
    campaign can be aimed at, the roles a piece can play in the arc, and every
    channel/surface the planner is allowed to choose from."""
    return {
        "goals": goals_catalog(),
        "roles": roles_catalog(),
        "pieces": {"min": MIN_PIECES, "max": MAX_PIECES, "default": DEFAULT_PIECES},
        **surfaces_catalog(),
    }


@router.post("/plan")
def plan_campaign(
    organization_id: int,
    payload: PlanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Plans a campaign without saving it: one big idea, and one entry per
    piece carrying its surface, its role in the arc and its date. Cheap enough
    to run again with a different theme until the shape is right."""
    org = get_owned_org(organization_id, db, user)
    try:
        return planner.plan(
            _org_context(org), payload.goal, payload.theme, payload.channels,
            payload.pieces, payload.starts_on, payload.ends_on, _site_type(org),
        )
    except PlanFailed as exc:
        # Reported verbatim: the operator sees this string in the plugin, and a
        # missing key, an exhausted account and an unreadable reply need three
        # different actions from them.
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("")
def create_campaign(
    organization_id: int,
    payload: SaveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Saves an accepted plan. The items are re-shaped on the way in with the
    same rules the planner applies, so a hand-edited plan can't save a surface
    that doesn't exist or a date outside its own window."""
    org = get_owned_org(organization_id, db, user)
    start, end = window(payload.starts_on, payload.ends_on)
    allowed = _allowed_surfaces(payload.channels)
    items = planner.shape_items(payload.items, allowed, MAX_PIECES, start, end)
    if not items:
        raise HTTPException(status_code=400, detail="A campaign needs at least one piece.")

    campaign = Campaign(
        organization_id=org.id,
        name=(payload.name or "").strip() or "Untitled campaign",
        goal=payload.goal,
        theme=payload.theme,
        big_idea=payload.big_idea,
        audience=payload.audience,
        channels=payload.channels,
        starts_on=start,
        ends_on=end,
        status="planned",
        plan=items,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return _as_out(campaign)


def _allowed_surfaces(channels: list[str] | None):
    allowed = [s for s in SURFACES if not channels or s.channel in channels]
    return allowed or list(SURFACES)


@router.get("")
def list_campaigns(
    organization_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org = get_owned_org(organization_id, db, user)
    campaigns = (
        db.query(Campaign)
        .filter(Campaign.organization_id == org.id)
        .order_by(Campaign.created_at.desc())
        .all()
    )
    return [_as_out(c) for c in campaigns]


@router.get("/{campaign_id}")
def get_campaign(
    campaign_id: int,
    organization_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org = get_owned_org(organization_id, db, user)
    return _as_out(_get_campaign(campaign_id, org, db))


@router.delete("/{campaign_id}")
def delete_campaign(
    campaign_id: int,
    organization_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Deletes the plan. The pieces it already produced are NOT deleted - they
    are real drafts in the library, some possibly already published, and losing
    them because a plan was tidied away would be a data-loss bug. They are
    unlinked from the campaign instead."""
    org = get_owned_org(organization_id, db, user)
    campaign = _get_campaign(campaign_id, org, db)
    (db.query(ContentItem)
       .filter(ContentItem.campaign_id == campaign.id)
       .update({ContentItem.campaign_id: None}, synchronize_session=False))
    db.delete(campaign)
    db.commit()
    return {"deleted": campaign_id}


@router.patch("/{campaign_id}/items/{index}")
def patch_item(
    campaign_id: int,
    index: int,
    organization_id: int,
    payload: ItemPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Moves a planned piece's date, swaps its surface, or rewrites its idea -
    before it is written. A piece that already has a draft is left alone: its
    copy is edited in the studio, on the real draft, not here."""
    org = get_owned_org(organization_id, db, user)
    campaign = _get_campaign(campaign_id, org, db)
    items = [dict(i) for i in (campaign.plan or [])]
    if not 0 <= index < len(items):
        raise HTTPException(status_code=404, detail="No such piece in this campaign.")
    item = items[index]
    if item.get("status") == "drafted":
        raise HTTPException(
            status_code=400,
            detail="This piece is already written - edit the draft in the Content Studio instead.",
        )

    if payload.headline is not None:
        item["headline"] = payload.headline.strip() or item["headline"]
    if payload.angle is not None:
        item["angle"] = payload.angle.strip()
    if payload.role is not None:
        if payload.role not in ROLES:
            raise HTTPException(status_code=400, detail=f"Unknown role '{payload.role}'.")
        item["role"] = payload.role
    if payload.surface is not None:
        surface = resolve_surface(payload.surface)
        if surface is None:
            raise HTTPException(status_code=400, detail=f"Unknown surface '{payload.surface}'.")
        item["surface"] = surface.id
        item["channel"] = surface.channel
    if payload.scheduled_on is not None:
        when = as_date(payload.scheduled_on)
        if when is None:
            raise HTTPException(status_code=400, detail="The date must be YYYY-MM-DD.")
        item["scheduled_on"] = when.isoformat()

    items[index] = item
    items.sort(key=lambda i: i.get("scheduled_on") or "")
    campaign.plan = _renumber(items)
    _sync_window(campaign)
    db.commit()
    db.refresh(campaign)
    return _as_out(campaign)


@router.delete("/{campaign_id}/items/{index}")
def delete_item(
    campaign_id: int,
    index: int,
    organization_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Drops a piece from the plan. Its draft, if it already has one, stays in
    the library - see delete_campaign for why."""
    org = get_owned_org(organization_id, db, user)
    campaign = _get_campaign(campaign_id, org, db)
    items = [dict(i) for i in (campaign.plan or [])]
    if not 0 <= index < len(items):
        raise HTTPException(status_code=404, detail="No such piece in this campaign.")
    removed = items.pop(index)
    if removed.get("content_id"):
        (db.query(ContentItem)
           .filter(ContentItem.id == removed["content_id"])
           .update({ContentItem.campaign_id: None}, synchronize_session=False))
    campaign.plan = _renumber(items)
    _sync_status(campaign)
    db.commit()
    db.refresh(campaign)
    return _as_out(campaign)


def _sync_window(campaign: Campaign) -> None:
    """Keeps the campaign's own window around whatever its pieces are actually
    scheduled for, so a piece moved past the end date doesn't fall outside its
    own campaign."""
    dates = [as_date(i.get("scheduled_on")) for i in (campaign.plan or [])]
    dates = [d for d in dates if d]
    if not dates:
        return
    campaign.starts_on = min(dates)
    campaign.ends_on = max(dates)


def _sync_status(campaign: Campaign) -> None:
    """"partial" means something FAILED, not "not finished yet" - a run half
    written with nothing wrong is still just a plan being worked through, and
    the counts say how far it got."""
    items = campaign.plan or []
    drafted = sum(1 for i in items if i.get("status") == "drafted")
    failed = sum(1 for i in items if i.get("status") == "failed")
    if failed:
        campaign.status = "partial"
    elif items and drafted == len(items):
        campaign.status = "ready"
    else:
        campaign.status = "planned"


# ------------------------------------------------------------------ the build
#
# Writing a piece is the studio's job, not this router's: draft_surface writes
# it against the surface contract and check_surface measures it, exactly as for
# a one-off piece. The only thing a campaign adds is the brief - the big idea
# and this piece's role in the arc - so the copy knows what run it belongs to.


def _brief(campaign: Campaign, item: dict) -> str:
    lines = [f"This piece is part of a campaign: {campaign.name}."]
    if campaign.big_idea:
        lines.append(f"The one argument the whole campaign makes: {campaign.big_idea}")
    if campaign.theme:
        lines.append(f"What the campaign is about: {campaign.theme}")
    lines.append(f"This piece's job in the run ({item.get('role')}): {role_brief(item.get('role', ''))}")
    lines.append("Write it so it stands alone, but don't restate the whole campaign - it is one move in a sequence.")
    # The campaign, the role and the arc are how WE talk about the work. The
    # reader never sees any of it, and neither should the title.
    lines.append("Never mention the campaign, its role or its sequence anywhere in the copy or the title.")
    return "\n".join(lines)


def _strip_role_tag(title: str, role: str) -> str:
    """Drops a trailing "(Hook)"-style tag from a piece's title.

    Telling the copywriter its role makes the copy better and makes it label
    the title with it - asking the prompt not to only shortened the label. So
    it is removed here instead, and narrowly: only a bracketed tag, only at the
    end, and only when it names THIS piece's own role, so a real title ending
    in "(Offer)" on an offer piece is the only false positive available and a
    title like "Our Autumn Offer" is untouched."""
    pattern = rf"\s*[\(\[]\s*(?:campaign\s+)?{re.escape(role.replace('_', ' '))}\s*[\)\]]\s*$"
    return re.sub(pattern, "", title, flags=re.IGNORECASE).strip() or title


def _build_one(db: Session, campaign: Campaign, org, item: dict) -> dict:
    """Writes one planned piece into a real, checked ContentItem.

    Returns the updated item. Never raises for a content reason - a piece that
    couldn't be written is recorded as failed with the reason on it, so one bad
    piece doesn't abandon the rest of the run."""
    surface = resolve_surface(item.get("surface") or "")
    if surface is None:
        return {**item, "status": "failed", "error": f"Unknown surface '{item.get('surface')}'."}

    if studio.client is None:
        return {**item, "status": "failed",
                "error": "No ANTHROPIC_API_KEY is set on the API, so nothing can be written."}

    idea = {"headline": item.get("headline", ""), "angle": item.get("angle", ""),
            "why": item.get("why", "")}
    draft = studio.draft_surface(_org_context(org), idea, surface, campaign.goal,
                                 _site_type(org), _brief(campaign, item))
    if not draft or not draft.get("body"):
        return {**item, "status": "failed",
                "error": "The copy came back empty. Try writing this piece again."}
    # Held to its role, not to the campaign's goal: a hook is briefed not to
    # ask, so the goal-derived call-to-action rule would flag it for obeying.
    expects_cta = role_expects_cta(item.get("role", ""))
    draft["title"] = _strip_role_tag(str(draft.get("title") or ""), str(item.get("role") or ""))
    draft, report = studio.check_surface(draft, surface, campaign.goal, expects_cta)

    state = {
        "version": 2,
        "goal": campaign.goal,
        "idea": idea,
        "surface": surface.id,
        "surface_key": surface.key,
        "channel": surface.channel,
        "spec": surface.as_dict(),
        "step": "checked",
        "expects_cta": expects_cta,
        "quality": report,
        "campaign": {"id": campaign.id, "name": campaign.name, "role": item.get("role"),
                     "scheduled_on": item.get("scheduled_on")},
    }
    content = ContentItem(
        organization_id=org.id,
        campaign_id=campaign.id,
        content_type=surface.channel,
        title=draft.get("title") or idea["headline"],
        input_payload={"source": "campaign", "campaign_id": campaign.id, "goal": campaign.goal,
                       "channel": surface.channel, "surface": surface.id, "idea": idea,
                       "role": item.get("role"), "scheduled_on": item.get("scheduled_on"),
                       "site_type": _site_type(org)},
        output_payload={},
    )
    _write_surface(content, draft, state)
    db.add(content)
    db.commit()
    db.refresh(content)
    return {**item, "status": "drafted", "content_id": content.id, "error": None,
            "quality": report, "title": content.title}


def _set_item(db: Session, campaign: Campaign, index: int, item: dict) -> None:
    """Writes one item back into the plan. Reassigns the list so SQLAlchemy sees
    the JSON column change."""
    items = [dict(i) for i in (campaign.plan or [])]
    if 0 <= index < len(items):
        items[index] = item
    campaign.plan = items
    db.commit()


def _set_build(db: Session, campaign: Campaign, patch: dict) -> dict:
    build = dict(campaign.build or {})
    build.update(patch)
    campaign.build = build
    db.commit()
    return build


def _execute_build(campaign_id: int) -> None:
    """The whole run, on a background worker with its own session.

    Out of band because each piece is its own model call - a five-piece build is
    minutes, far past any sensible HTTP timeout. The client polls
    GET /campaigns/{id} instead, the same way studio renders and analytics scans
    already work."""
    db = SessionLocal()
    try:
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if campaign is None:
            return
        org = campaign.organization
        built = failed = 0
        for index, raw in enumerate(list(campaign.plan or [])):
            if raw.get("status") == "drafted":
                built += 1
                continue
            _set_item(db, campaign, index, {**raw, "status": "building", "error": None})
            try:
                result = _build_one(db, campaign, org, dict(raw))
            except Exception as exc:  # noqa: BLE001 - one bad piece must not end the run
                db.rollback()
                result = {**raw, "status": "failed", "error": str(exc)[:300]}
            _set_item(db, campaign, index, result)
            if result.get("status") == "drafted":
                built += 1
            else:
                failed += 1
            _set_build(db, campaign, {"built": built, "failed": failed})

        _sync_status(campaign)
        _set_build(db, campaign, {
            "status": "done" if not failed else "failed",
            "built": built, "failed": failed,
            "error": None if not failed else f"{failed} of {built + failed} pieces couldn't be written.",
            "finished_at": datetime.utcnow().isoformat(),
        })
    except Exception as exc:  # noqa: BLE001 - a worker crash must leave a readable state
        try:
            campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
            if campaign is not None:
                _set_build(db, campaign, {"status": "failed", "error": str(exc)[:300],
                                          "finished_at": datetime.utcnow().isoformat()})
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


@router.post("/{campaign_id}/build")
def build_campaign(
    campaign_id: int,
    organization_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Writes every piece that doesn't have a draft yet, in plan order.

    Returns immediately with status "running" - poll GET /campaigns/{id}. Each
    piece is written and quality-checked exactly as a studio piece is; the media
    is NOT rendered here, because a render is another minute per piece and most
    pieces get their copy edited first."""
    org = get_owned_org(organization_id, db, user)
    campaign = _get_campaign(campaign_id, org, db)
    pending = [i for i in (campaign.plan or []) if i.get("status") != "drafted"]
    if not pending:
        return {"campaign_id": campaign.id, **_build_state(campaign), "status": "done"}

    current = _build_state(campaign)
    if current.get("status") == "running":
        return {"campaign_id": campaign.id, **current}

    campaign.status = "building"
    build = _set_build(db, campaign, {"status": "running", "error": None, "built": 0, "failed": 0,
                                      "total": len(campaign.plan or []),
                                      "started_at": datetime.utcnow().isoformat(),
                                      "finished_at": None})
    background_tasks.add_task(_execute_build, campaign.id)
    return {"campaign_id": campaign.id, **build}


@router.post("/{campaign_id}/items/{index}/build")
def build_item(
    campaign_id: int,
    index: int,
    organization_id: int,
    force: bool = Query(False, description="Rewrite a piece that already has a draft"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Writes ONE piece, synchronously - for retrying a piece the run failed on,
    or rewriting one whose draft missed. The old draft is left in the library
    when a piece is rewritten; only the campaign's link moves to the new one."""
    org = get_owned_org(organization_id, db, user)
    campaign = _get_campaign(campaign_id, org, db)
    items = campaign.plan or []
    if not 0 <= index < len(items):
        raise HTTPException(status_code=404, detail="No such piece in this campaign.")
    item = dict(items[index])
    if item.get("status") == "drafted" and not force:
        raise HTTPException(status_code=400,
                            detail="This piece is already written. Pass force=true to rewrite it.")

    if item.get("content_id"):
        (db.query(ContentItem)
           .filter(ContentItem.id == item["content_id"])
           .update({ContentItem.campaign_id: None}, synchronize_session=False))
        item["content_id"] = None

    result = _build_one(db, campaign, org, item)
    _set_item(db, campaign, index, result)
    _sync_status(campaign)
    db.commit()
    db.refresh(campaign)
    if result.get("status") == "failed":
        raise HTTPException(status_code=503, detail=result.get("error") or "The piece couldn't be written.")
    return {"campaign_id": campaign.id, "item": result}


# --------------------------------------------------- the original generators


@router.post("/event", response_model=ContentOut)
def generate_event_campaign(payload: EventCampaignRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = get_owned_org(payload.organization_id, db, user)
    output = ai.generate_structured("event_campaign", org_to_memory(org), payload.model_dump())
    return save_content(db, org.id, "event_campaign", payload.event_name, payload.model_dump(), output)


@router.post("/announcements", response_model=ContentOut)
def generate_announcements(payload: AnnouncementsRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = get_owned_org(payload.organization_id, db, user)
    title = f"Weekly announcements - {payload.service_date}"
    output = ai.generate_structured("weekly_announcements", org_to_memory(org), payload.model_dump())
    return save_content(db, org.id, "weekly_announcements", title, payload.model_dump(), output)


@router.post("/sermon", response_model=ContentOut)
def generate_sermon_engagement(payload: SermonEngagementRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = get_owned_org(payload.organization_id, db, user)
    output = ai.generate_structured("sermon_engagement", org_to_memory(org), payload.model_dump())
    return save_content(db, org.id, "sermon_engagement", payload.title, payload.model_dump(), output)
