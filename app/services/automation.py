"""Letting a stage's in-queue drain without an operator pressing the button.

services/pipeline.py answers "where is every piece of work, and what is each
stage waiting on". The honest reading of that board was always uncomfortable:
most of what it reports as waiting is waiting on a human to click the same
button, on the same item, for the same reason, again. Checking a draft, writing
a kept idea, building the pieces a campaign already planned, measuring a post
that has been live for a week - none of those are decisions. They are the queue
being carried by hand.

So each of those transitions is named here as a STEP, and an organization can
switch any of them on. When a step is on, the drainer takes items off that
step's in-queue and performs exactly the action an operator would have
performed - by calling the same function the endpoint calls, never a second
implementation of it. That is the whole design constraint: automation must not
be able to do a slightly different thing than the button does.

WHAT IS DELIBERATELY NOT AUTOMATABLE

`channels.publish` is in the registry, and it can never be switched on. It is
listed rather than omitted because an operator looking at the automation page
should be told that publishing is the step that stays theirs - a step that is
simply absent reads as an oversight, and the next person to ask "why can't the
rest be automated too" deserves the answer in the same list. Putting something
in front of the public is a decision, and this system has one rule about
decisions: they belong to a person.

That refusal is enforced in three places on purpose, because one is not enough:
`settings_for()` reads a gated step back as disabled whatever the stored config
says, `set_settings()` refuses to write it, and the drainer checks `step.gate`
again immediately before acting. A hand-edited database row, a stale config
written before a step was gated, and a future caller that skips the setter all
fail closed.

TWO LEVELS OF TRUST

A step's own toggle means "this may be done for me at all". The org-level
`enabled` means "and it may happen while nobody is watching". They are separate
because they are different amounts of trust, and an operator can reasonably want
the first without the second: run the checks when I press the button, but do not
touch anything at three in the morning. A manual run therefore honours the step
toggles and ignores the master switch - the person pressing Run now is here.
_skip_reason() is the only place that decides, shared by the preview and the
drainer, so what the preview promised is what happens.

WHAT A RUN MAY DO

Every step here only ever moves an item FORWARD into a draft, a check, a render
or a measurement. Nothing deletes, nothing sends, nothing spends. Each step is
capped per run (`max_per_run`), so switching everything on cannot turn one sweep
into an unbounded spend of model calls, and one item's failure is recorded
against that item and never ends the run.

Every run is written to AutomationRun with item-level detail, because the
operator was not there. "What did it do last night" has to be answerable from
storage, not reconstructed from side effects.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import SessionLocal
from app.models.entities import (
    AutomationRun,
    Campaign,
    ContentItem,
    Idea,
    Organization,
    Publication,
    PublicationSnapshot,
)
from app.services.pipeline import NEEDS_CHECK, NEEDS_MEDIA, READY, content_position

# No step may be talked into taking more than this in one sweep, whatever the
# org's config says. A cap the operator can raise is a convenience; a cap they
# cannot raise is what stops a bad config from becoming a bill.
MAX_PER_RUN_CEILING = 50

# How long a run may go without finishing an item before it is presumed dead.
#
# execute() runs on a background worker, and a worker does not survive a
# process restart - a deploy landing mid-sweep leaves the row saying "running"
# with nobody behind it. That is not hypothetical here: deploys happen several
# times a day, and a permanently "running" row would block every later manual
# run for that organization for good (see is_running).
#
# Measured against PROGRESS, not total duration: _save() writes after every
# item, and the slowest single item is a render, which the studio itself gives
# up on at 15 minutes. Half an hour of complete silence is dead, while a long
# but healthy sweep is left alone however long it takes.
STALE_AFTER_SECONDS = 30 * 60


class Skip(Exception):
    """Not now, and not an error.

    Raised by a step that meets an item it genuinely cannot advance yet - a
    piece with no image prompt to render, a publication on a channel that is
    not publicly visible. Counting these as failures would make a healthy run
    look broken every single sweep, and would bury the real failures under
    them.
    """


@dataclass(frozen=True)
class Pending:
    """One item waiting on a step. `ref` is a stable, human-readable handle
    ("idea:12", "campaign:3#1") so an AutomationRun row can be read months
    later without joining anything."""

    ref: str
    title: str
    payload: Any


@dataclass(frozen=True)
class Step:
    key: str
    stage: str  # which services/pipeline.py stage this drains - where the toggle belongs in the UI
    label: str
    description: str
    default_max_per_run: int
    pending: Callable[[Session, Organization], list[Pending]]
    run_one: Callable[[Session, Organization, Any], str]
    # Non-None means this step can NEVER be automated, and this is the reason
    # shown to the operator. See the module docblock.
    gate: str | None = None
    # What has to be configured for this step to be able to run at all, checked
    # per organization by blocked_reason(). Not a gate - a missing requirement
    # is a setup problem, not a decision reserved for a human.
    requires: tuple[str, ...] = field(default_factory=tuple)


# ----------------------------------------------------------------- the steps


def _pending_ideas(db: Session, org: Organization) -> list[Pending]:
    """Kept ideas, oldest first. The same set the Ideas page shows as "waiting
    to be written" - a queue drains from the front, so the idea that has been
    waiting longest is written first rather than the one kept most recently."""
    ideas = (
        db.query(Idea)
        .filter(Idea.organization_id == org.id, Idea.status == "kept")
        .order_by(Idea.created_at.asc(), Idea.id.asc())
        .all()
    )
    return [Pending(f"idea:{i.id}", i.title, i) for i in ideas]


def _draft_idea(db: Session, org: Organization, idea: Idea) -> str:
    """Writes one kept idea into a real, checked draft and links it back.

    Deliberately the same two calls the Studio's own draft endpoint makes
    (draft_surface then check_surface), against the same surface resolution, so
    an auto-written piece is indistinguishable in quality rules from a
    hand-written one. The one difference is recorded rather than hidden:
    input_payload.source says "automation", so the library can always tell who
    started a piece.

    Imported locally, the way services/scheduler.py already imports the
    analytics scan - the studio's write path lives in the router and importing
    it at module load would make services and routers import each other.
    """
    from app.routers.studio import _org_context, _site_type, _write_surface, studio
    from app.services.studio import WriteFailed, support_text
    from app.services.studio_formats import DEFAULT_GOAL
    from app.services.surfaces import surface_for

    # surface_for degrades an unknown or missing channel to a sensible default
    # rather than failing - an idea's channel was never enforced.
    surface = surface_for(idea.channel or "")
    goal = idea.goal or DEFAULT_GOAL
    context = _org_context(org)
    seed = {"headline": idea.title, "angle": idea.angle or "", "why": idea.rationale or ""}

    try:
        draft = studio.draft_surface(context, seed, surface, goal, _site_type(org))
    except WriteFailed as exc:
        raise RuntimeError(str(exc)[:300]) from exc
    if not draft or not draft.get("body"):
        raise RuntimeError("The copy came back empty.")

    draft, report = studio.check_surface(draft, surface, goal, sources=support_text(context, seed))
    state = {
        "version": 2,
        "goal": goal,
        "idea": seed,
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
        title=draft.get("title") or idea.title,
        input_payload={"source": "automation", "goal": goal, "channel": surface.channel,
                       "surface": surface.id, "idea": seed, "idea_id": idea.id,
                       "site_type": _site_type(org)},
        output_payload={},
    )
    _write_surface(item, draft, state)
    db.add(item)
    db.commit()
    db.refresh(item)

    # The link and the status together, as routers/ideas.py insists: an idea
    # with a content_item_id still marked "kept" sits in the in-queue forever.
    idea.content_item_id = item.id
    idea.status = "drafted"
    db.commit()
    return f"wrote {surface.label.lower()} #{item.id}"


def _pending_campaign_items(db: Session, org: Organization) -> list[Pending]:
    """Planned campaign pieces that have no draft, earliest scheduled first, so
    the piece that is due soonest is written first."""
    campaigns = (
        db.query(Campaign)
        .filter(Campaign.organization_id == org.id, Campaign.status != "archived")
        .all()
    )
    out: list[tuple[str, Pending]] = []
    for campaign in campaigns:
        for index, entry in enumerate(campaign.plan or []):
            if not isinstance(entry, dict) or entry.get("status") not in (None, "", "planned"):
                continue
            title = entry.get("headline") or entry.get("role") or f"piece {index + 1}"
            out.append((
                str(entry.get("scheduled_on") or "9999-12-31"),
                Pending(f"campaign:{campaign.id}#{index}", f"{campaign.name}: {title}",
                        (campaign.id, index)),
            ))
    return [p for _, p in sorted(out, key=lambda pair: pair[0])]


def _build_campaign_item(db: Session, org: Organization, target: tuple[int, int]) -> str:
    """Writes one planned campaign piece - literally routers/campaigns._build_one,
    the same function the Build button runs, including its "record the failure on
    the piece rather than raise" contract."""
    from app.routers.campaigns import _build_one, _set_item, _sync_status

    campaign_id, index = target
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if campaign is None or not 0 <= index < len(campaign.plan or []):
        raise Skip("the piece is no longer in the plan")

    item = dict((campaign.plan or [])[index])
    if item.get("status") == "drafted":
        raise Skip("already written")

    result = _build_one(db, campaign, org, item)
    _set_item(db, campaign, index, result)
    _sync_status(campaign)
    db.commit()
    if result.get("status") != "drafted":
        raise RuntimeError(result.get("error") or "The piece couldn't be written.")
    return f"wrote piece {index + 1} of {campaign.name} (#{result.get('content_id')})"


def _content_by_position(db: Session, org: Organization, position: str) -> list[ContentItem]:
    """Reuses services/pipeline.content_position so a step's queue is the same
    queue the board draws. Two definitions of "needs checking" is exactly the
    drift this codebase has been bitten by before."""
    items = db.query(ContentItem).filter(ContentItem.organization_id == org.id).all()
    published_ids = {
        pid for (pid,) in db.query(Publication.content_item_id)
        .filter(Publication.organization_id == org.id, Publication.content_item_id.isnot(None))
        .all()
    }
    return [i for i in items if content_position(i, published_ids) == position]


def _pending_checks(db: Session, org: Organization) -> list[Pending]:
    """Unchecked drafts that the check can actually be run against.

    A piece made by the older generators has no studio block, so there is no
    surface and no layout to measure it against - the endpoint refuses it with
    "this piece wasn't created in the Content Studio", and that will be just as
    true next sweep. Excluded here rather than attempted and failed nightly,
    for the same reason a private send is excluded from the scan queue.
    """
    return [
        Pending(f"content:{i.id}", i.title, i)
        for i in _content_by_position(db, org, NEEDS_CHECK)
        if isinstance((i.output_payload or {}).get("studio"), dict)
    ]


def _check_item(db: Session, org: Organization, item: ContentItem) -> str:
    """The Studio's pass 3, unrevised.

    revise=False on purpose: a revision is the AI rewriting copy the operator
    may already have edited by hand, and doing that unattended would silently
    overwrite their words. Automation measures and repairs mechanically; it does
    not rewrite behind someone's back.
    """
    from fastapi import HTTPException

    from app.routers.studio import recheck

    try:
        result = recheck(db, org, item)
    except HTTPException as exc:
        # Belt and braces with _pending_checks' filter: whatever the studio
        # refuses to measure is not this run's failure.
        raise Skip(str(exc.detail)) from exc
    issues = (result.get("quality") or {}).get("issues") or []
    return f"checked, {len(issues)} issue(s) found" if issues else "checked, clean"


def _pending_renders(db: Session, org: Organization) -> list[Pending]:
    return [Pending(f"content:{i.id}", i.title, i) for i in _content_by_position(db, org, NEEDS_MEDIA)]


def _render_item(db: Session, org: Organization, item: ContentItem) -> str:
    """Renders one piece's file, synchronously.

    The endpoint hands the render to a background task because an HTTP client
    cannot wait minutes for it. This caller already IS a background worker
    draining a queue, so it waits - which also means the run record reports
    what actually happened rather than what was started.
    """
    from fastapi import HTTPException

    from app.routers.studio import _execute_render, _render_state, _set_render, render_target

    try:
        media_kind = render_target(item)
    except HTTPException as exc:
        # "no slides yet", "no image prompt", "the copy is the whole post" - all
        # permanent-for-now, none of them this run's failure.
        raise Skip(str(exc.detail)) from exc

    if _render_state(item).get("status") == "running":
        raise Skip("a render is already running")

    _set_render(db, item, {"status": "running", "error": None, "asset_id": None,
                           "kind": media_kind, "started_at": datetime.utcnow().isoformat()})
    _execute_render(item.id, org.id)
    db.refresh(item)  # _execute_render committed on its own session

    render = _render_state(item)
    if render.get("status") != "done":
        raise RuntimeError(render.get("error") or "The render didn't finish.")
    return f"rendered the {media_kind}"


def _pending_scans(db: Session, org: Organization) -> list[Pending]:
    """Publications that have never been measured, on channels that can be.

    A private send (email, WhatsApp) is excluded rather than listed and skipped:
    it is not publicly visible, so there is nothing to search for and never will
    be. Leaving it in the queue would mean reporting the same permanent skip on
    every sweep forever, which trains an operator to ignore the report.
    """
    from app.services.analytics_scoring import PUBLICATION_SCANNABLE_CHANNELS

    publications = (
        db.query(Publication)
        .filter(Publication.organization_id == org.id)
        .order_by(Publication.id.asc())
        .all()
    )
    scanned = {
        pid for (pid,) in db.query(PublicationSnapshot.publication_id)
        .filter(PublicationSnapshot.publication_id.in_([p.id for p in publications] or [0]))
        .distinct()
        .all()
    }
    return [
        Pending(f"publication:{p.id}", p.label or p.url, p)
        for p in publications
        if p.id not in scanned and p.channel in PUBLICATION_SCANNABLE_CHANNELS
    ]


def _scan_publication(db: Session, org: Organization, pub: Publication) -> str:
    """The same scan POST /publications/{id}/scan runs, on the same service."""
    from app.routers.publications import search_service
    from app.services.analytics_scoring import score_publication

    result = search_service.scan(pub.channel, pub.url)
    score, breakdown = score_publication(pub.channel, result.get("kpis"))
    db.add(PublicationSnapshot(
        publication_id=pub.id,
        kpis=result.get("kpis"),
        notes=result.get("notes"),
        score=score,
        score_breakdown=breakdown,
        sources=result.get("sources", []),
    ))
    db.commit()
    return f"measured, scored {score}" if score is not None else "measured"


def _pending_publish(db: Session, org: Organization) -> list[Pending]:
    """Everything finished and waiting to go out. Reported so the operator can
    see the size of the decision that is theirs - never acted on."""
    return [Pending(f"content:{i.id}", i.title, i) for i in _content_by_position(db, org, READY)]


def _never(db: Session, org: Organization, item: Any) -> str:
    raise RuntimeError("This step is gated and must never be executed.")


STEPS: tuple[Step, ...] = (
    Step(
        key="ideas.draft",
        stage="ideas",
        label="Write kept ideas",
        description="Turns each kept idea into a checked draft on its suggested channel, and links the idea to the piece it became.",
        default_max_per_run=3,
        pending=_pending_ideas,
        run_one=_draft_idea,
        requires=("ai",),
    ),
    Step(
        key="campaigns.build",
        stage="campaigns",
        label="Build planned campaign pieces",
        description="Writes the pieces a campaign has already planned but not yet written, earliest scheduled date first.",
        default_max_per_run=3,
        pending=_pending_campaign_items,
        run_one=_build_campaign_item,
        requires=("ai",),
    ),
    Step(
        key="studio.check",
        stage="studio",
        label="Check new drafts",
        description="Runs the quality check on any draft that has never been measured. Never rewrites copy - a revision is the operator's call.",
        default_max_per_run=20,
        pending=_pending_checks,
        run_one=_check_item,
    ),
    Step(
        key="studio.render",
        stage="studio",
        label="Render waiting media",
        description="Renders the image, carousel or video for a checked piece that is waiting on its file.",
        default_max_per_run=2,
        pending=_pending_renders,
        run_one=_render_item,
    ),
    Step(
        key="performance.scan",
        stage="performance",
        label="Measure new publications",
        description="Takes the first performance measurement of anything published and never measured. Private sends are excluded - they are not publicly visible.",
        default_max_per_run=5,
        pending=_pending_scans,
        run_one=_scan_publication,
        requires=("analytics",),
    ),
    Step(
        key="channels.publish",
        stage="channels",
        label="Publish what is ready",
        description="Sends finished pieces out to their channels.",
        default_max_per_run=0,
        pending=_pending_publish,
        run_one=_never,
        gate="Publishing is a decision, not a step. Nothing goes in front of your audience without a person choosing to send it.",
    ),
)

STEPS_BY_KEY: dict[str, Step] = {s.key: s for s in STEPS}


# ------------------------------------------------------------- configuration


def settings_for(org: Organization) -> dict:
    """The org's automation config, normalized and complete.

    Every read goes through here rather than touching org.automation directly,
    so callers never have to cope with a missing key, an old shape, a bare bool
    written by an earlier version, or a cap someone set to a million. A gated
    step reads back disabled here no matter what is stored - the second of the
    three places that refusal is enforced.
    """
    raw = org.automation if isinstance(org.automation, dict) else {}
    stored = raw.get("steps") if isinstance(raw.get("steps"), dict) else {}

    steps: dict[str, dict] = {}
    for step in STEPS:
        entry = stored.get(step.key)
        if isinstance(entry, bool):  # the shorthand PATCH accepts, and an older shape
            entry = {"enabled": entry}
        if not isinstance(entry, dict):
            entry = {}
        cap = entry.get("max_per_run")
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
            cap = step.default_max_per_run
        steps[step.key] = {
            "enabled": bool(entry.get("enabled")) and step.gate is None,
            "max_per_run": min(cap, MAX_PER_RUN_CEILING),
        }

    return {"enabled": bool(raw.get("enabled")), "steps": steps}


def blocked_reason(step: Step, org: Organization) -> str | None:
    """Why this step could not run for this org right now, or None.

    Distinct from `gate`: a gate is a decision reserved for a person and never
    goes away; this is a setup problem with a fix. Reported next to the toggle
    so a step that is switched on but silently doing nothing is visible before
    the operator wonders why the queue is not moving.
    """
    if "ai" in step.requires and not settings.anthropic_api_key:
        return "No writing key is configured on the API, so nothing can be written."
    if "analytics" in step.requires and "analytics" not in (org.enabled_modules or []):
        return "The analytics module is switched off for this organization."
    return None


def set_settings(org: Organization, patch: dict) -> dict:
    """Applies a partial config change and returns the normalized result.

    Raises ValueError for an unknown step key or an attempt to enable a gated
    one - the first of the three places that refusal is enforced, and the only
    one that gets to explain itself to the caller.
    """
    current = settings_for(org)
    if "enabled" in patch:
        current["enabled"] = bool(patch["enabled"])

    for key, value in (patch.get("steps") or {}).items():
        step = STEPS_BY_KEY.get(key)
        if step is None:
            raise ValueError(f"Unknown automation step '{key}'.")
        if isinstance(value, bool):
            value = {"enabled": value}
        if not isinstance(value, dict):
            raise ValueError(f"Step '{key}' must be true/false or an object.")
        if value.get("enabled") and step.gate is not None:
            raise ValueError(f"'{step.label}' can't be automated. {step.gate}")
        if "enabled" in value:
            current["steps"][key]["enabled"] = bool(value["enabled"])
        if "max_per_run" in value:
            cap = value["max_per_run"]
            if not isinstance(cap, int) or isinstance(cap, bool) or not 1 <= cap <= MAX_PER_RUN_CEILING:
                raise ValueError(f"max_per_run for '{key}' must be a whole number from 1 to {MAX_PER_RUN_CEILING}.")
            current["steps"][key]["max_per_run"] = cap

    org.automation = current
    return current


def describe(db: Session, org: Organization) -> dict:
    """The automation page: every step, whether it may run, whether it is on,
    and how many items are actually waiting on it right now.

    The waiting count is computed from the same queues the drainer will read,
    so the number beside the toggle is the number that will be acted on - not a
    related-looking number from somewhere else.
    """
    config = settings_for(org)
    steps = []
    for step in STEPS:
        entry = config["steps"][step.key]
        steps.append({
            "key": step.key,
            "stage": step.stage,
            "label": step.label,
            "description": step.description,
            "automatable": step.gate is None,
            "gate": step.gate,
            "enabled": entry["enabled"],
            "max_per_run": entry["max_per_run"],
            "waiting": len(step.pending(db, org)),
            "blocked_by": blocked_reason(step, org) if step.gate is None else None,
        })

    last = (
        db.query(AutomationRun)
        .filter(AutomationRun.organization_id == org.id)
        .order_by(AutomationRun.started_at.desc(), AutomationRun.id.desc())
        .first()
    )
    return {
        # "may these steps run while nobody is watching". A step switched on
        # with this off still runs when the operator presses Run now.
        "enabled": config["enabled"],
        "max_per_run_ceiling": MAX_PER_RUN_CEILING,
        "interval_hours": settings.automation_interval_hours,
        "steps": steps,
        "last_run": _run_summary(last) if last else None,
    }


def _run_summary(run: AutomationRun) -> dict:
    return {
        "id": run.id,
        "trigger": run.trigger,
        "status": run.status,
        "processed": run.processed,
        "failed": run.failed,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


# -------------------------------------------------------------------- running


def _step_record(step: Step, entry: dict, waiting: int, skipped_reason: str | None) -> dict:
    return {
        "key": step.key,
        "label": step.label,
        "stage": step.stage,
        "enabled": entry["enabled"],
        "waiting": waiting,
        "attempted": 0,
        "processed": 0,
        "failed": 0,
        "skipped": 0,
        "skipped_reason": skipped_reason,
        "items": [],
    }


def _skip_reason(step: Step, config: dict, org: Organization, trigger: str) -> str | None:
    """Why this step will not run, or None. The single decision, used by the
    preview and by the drainer, so what the preview promised is what happens.

    The master switch is only consulted for a SCHEDULED run. That is the whole
    meaning of the two levels: `enabled` is "you may do this while I am not
    here", and the per-step toggles are "these steps may be done for me at all".
    An operator pressing Run now is here, so the first question does not apply -
    but the second still does, and a step they never switched on stays off.
    """
    if step.gate is not None:
        return step.gate
    if trigger == "scheduled" and not config["enabled"]:
        return "Unattended runs are switched off for this organization."
    if not config["steps"][step.key]["enabled"]:
        return "This step is switched off."
    return blocked_reason(step, org)


def plan(db: Session, org: Organization, trigger: str = "manual") -> list[dict]:
    """What a run would do, without doing any of it.

    Every step appears, including the ones that will do nothing and why - "the
    renders didn't happen" and "the renders were never attempted" are different
    problems, and a report that lists only the steps that acted cannot tell them
    apart.
    """
    config = settings_for(org)
    records = []
    for step in STEPS:
        entry = config["steps"][step.key]
        waiting = len(step.pending(db, org))
        reason = _skip_reason(step, config, org, trigger)
        record = _step_record(step, entry, waiting, reason)
        if reason is None:
            record["attempted"] = min(waiting, entry["max_per_run"])
        records.append(record)
    return records


def start_run(db: Session, org: Organization, trigger: str = "manual") -> AutomationRun:
    """Opens the run row before any work happens, so a worker that dies mid-way
    leaves a readable "running" row rather than no evidence at all."""
    run = AutomationRun(organization_id=org.id, trigger=trigger, status="running",
                        steps=[], processed=0, failed=0)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def reap_stale_runs(db: Session, org_id: int | None = None) -> int:
    """Marks runs that stopped making progress as failed, and says why.

    Called on startup (main.py), where by definition anything still "running" is
    orphaned, and again from is_running() so an organization is not locked out
    until the next deploy if a worker dies on its own. The same grace the
    analytics reaper uses applies: a young run may belong to an outgoing
    instance that is still finishing it, and its own later write simply wins.

    Returns how many were reaped, so the caller can log it.
    """
    cutoff = datetime.utcnow() - timedelta(seconds=STALE_AFTER_SECONDS)
    query = db.query(AutomationRun).filter(AutomationRun.status == "running")
    if org_id is not None:
        query = query.filter(AutomationRun.organization_id == org_id)

    reaped = 0
    for run in query.all():
        # A run that has not saved an item yet has no updated_at; its start is
        # the only progress it has made.
        last = run.updated_at or run.started_at
        if last is not None and last > cutoff:
            continue
        run.status = "failed"
        run.error = ("The run stopped partway through - most likely a deploy restarted the API. "
                     "Whatever it had already done is listed above; nothing was left half-written. "
                     "Run it again when you are ready.")
        run.finished_at = datetime.utcnow()
        reaped += 1
    if reaped:
        db.commit()
    return reaped


def is_running(db: Session, org_id: int) -> AutomationRun | None:
    """The run currently draining this org, or None.

    Reaps first, so a dead worker's abandoned row cannot block this
    organization's runs forever - which is exactly what it did before, since
    nothing else ever moved a "running" row out of that state.
    """
    reap_stale_runs(db, org_id)
    return (
        db.query(AutomationRun)
        .filter(AutomationRun.organization_id == org_id, AutomationRun.status == "running")
        .order_by(AutomationRun.id.desc())
        .first()
    )


def execute(run_id: int) -> None:
    """Drains every switched-on step, on its own session.

    Own session because this is called from a background task and from the
    scheduler, neither of which has a request's session to borrow. Everything
    below is wrapped so that the run row always ends in a terminal state: a
    crash that leaves a row saying "running" forever is the one outcome worse
    than a recorded failure.
    """
    db = SessionLocal()
    try:
        run = db.query(AutomationRun).filter(AutomationRun.id == run_id).first()
        if run is None:
            return
        org = db.query(Organization).filter(Organization.id == run.organization_id).first()
        if org is None:
            _finish(db, run, "failed", error="The organization no longer exists.")
            return

        config = settings_for(org)
        records: list[dict] = []
        processed = failed = 0

        for step in STEPS:
            entry = config["steps"][step.key]
            items = step.pending(db, org)

            # _skip_reason checks step.gate first - the third and last place
            # the gate is enforced, immediately before acting, so a config
            # written by anything that skipped set_settings still cannot
            # publish.
            reason = _skip_reason(step, config, org, run.trigger)

            record = _step_record(step, entry, len(items), reason)
            records.append(record)
            _save(db, run, records, processed, failed)
            if reason is not None:
                continue

            for pending in items[:entry["max_per_run"]]:
                record["attempted"] += 1
                try:
                    detail = step.run_one(db, org, pending.payload)
                    record["processed"] += 1
                    processed += 1
                    outcome, note = True, detail
                except Skip as exc:
                    record["skipped"] += 1
                    outcome, note = None, str(exc)[:300]
                except Exception as exc:  # noqa: BLE001 - one item must never end the run
                    db.rollback()
                    record["failed"] += 1
                    failed += 1
                    outcome, note = False, str(exc)[:300]
                record["items"].append({"ref": pending.ref, "title": (pending.title or "")[:120],
                                        "ok": outcome, "detail": note})
                _save(db, run, records, processed, failed)

        if run.trigger == "scheduled" and not config["enabled"]:
            status = "off"
        elif processed or failed:
            status = "done"
        else:
            status = "nothing_to_do"
        _finish(db, run, status, records=records, processed=processed, failed=failed)
    except Exception as exc:  # noqa: BLE001 - a crash must still leave a terminal row
        try:
            run = db.query(AutomationRun).filter(AutomationRun.id == run_id).first()
            if run is not None:
                _finish(db, run, "failed", error=str(exc)[:500])
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


def _save(db: Session, run: AutomationRun, records: list[dict], processed: int, failed: int) -> None:
    """Reassigns the list so SQLAlchemy sees the JSON column change - the same
    trick routers/campaigns.py needs for its plan."""
    run.steps = [dict(r) for r in records]
    run.processed = processed
    run.failed = failed
    run.updated_at = datetime.utcnow()
    db.commit()


def _finish(db: Session, run: AutomationRun, status: str, records: list[dict] | None = None,
            processed: int | None = None, failed: int | None = None, error: str | None = None) -> None:
    if records is not None:
        run.steps = [dict(r) for r in records]
    if processed is not None:
        run.processed = processed
    if failed is not None:
        run.failed = failed
    run.status = status
    run.error = error
    run.updated_at = datetime.utcnow()
    run.finished_at = datetime.utcnow()
    db.commit()


def run_now(db: Session, org: Organization, trigger: str = "scheduled") -> AutomationRun:
    """Start and drain in one call, for callers already off the request path
    (the scheduler). Returns the finished row.

    Honours the same one-drainer-per-organization rule the endpoint does. It did
    not, at first, and that was a real hole rather than an omission: the guard
    exists because two drainers read the same queues, and the scheduled sweep
    was the one caller able to walk straight past it and race a manual run the
    operator had just started. An in-flight run is returned untouched - this
    sweep simply leaves that org alone and picks it up next time.
    """
    existing = is_running(db, org.id)
    if existing is not None:
        return existing
    run = start_run(db, org, trigger)
    execute(run.id)
    db.refresh(run)
    return run
