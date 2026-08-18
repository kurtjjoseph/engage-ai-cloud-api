"""The automation page: which workflow steps may run themselves, and what they did.

    GET   /organizations/{id}/automation          every step, its toggle, what is waiting on it
    PATCH /organizations/{id}/automation          switch steps on or off, set per-step caps
    GET   /organizations/{id}/automation/preview  what a run right now would do, doing none of it
    POST  /organizations/{id}/automation/run      drain the switched-on queues now
    GET   /organizations/{id}/automation/runs     what it has done, newest first

The interesting endpoint is the preview. Automation that cannot be inspected
before it is switched on is a thing an operator has to trust rather than
understand, and the first surprise loses that trust permanently - so the same
function that decides what a run does also answers, ahead of time, what it
would do.
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import AutomationRun, User
from app.routers.organizations import get_owned_org
from app.services import automation

router = APIRouter(prefix="/organizations/{org_id}/automation", tags=["automation"])


class AutomationPatch(BaseModel):
    """Partial by design - a page with six toggles should be able to send the
    one that changed. `steps` accepts either the full object per step or a bare
    true/false for the common case:

        {"enabled": true,
         "steps": {"studio.check": true, "studio.render": {"enabled": true, "max_per_run": 1}}}
    """

    enabled: bool | None = None
    steps: dict[str, bool | dict] | None = None


def _run_out(run: AutomationRun) -> dict:
    return {
        "id": run.id,
        "organization_id": run.organization_id,
        "trigger": run.trigger,
        "status": run.status,
        "processed": run.processed,
        "failed": run.failed,
        "error": run.error,
        "steps": run.steps or [],
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("")
def get_automation(org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Every step the workflow has, whether it can be automated at all, whether
    this org has it switched on, and how many items are waiting on it right
    now. The gated step is listed too, with the reason it will never move."""
    org = get_owned_org(org_id, db, user)
    return automation.describe(db, org)


@router.patch("")
def update_automation(
    org_id: int,
    payload: AutomationPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    org = get_owned_org(org_id, db, user)
    try:
        automation.set_settings(org, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        # 422 rather than 400: the request was understood and refused on its
        # content - trying to automate publishing is the main way to get here,
        # and the message is the explanation, not a validation nicety.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(org)
    return automation.describe(db, org)


@router.get("/preview")
def preview_automation(
    org_id: int,
    trigger: str = Query("manual", description="manual (Run now) | scheduled (the unattended sweep)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """What a run would do, without doing any of it. Ask as "scheduled" to see
    what tonight's unattended sweep would do instead."""
    org = get_owned_org(org_id, db, user)
    if trigger not in ("manual", "scheduled"):
        raise HTTPException(status_code=422, detail="trigger must be 'manual' or 'scheduled'")
    return {"trigger": trigger, "steps": automation.plan(db, org, trigger)}


@router.post("/run")
def run_automation(
    org_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Drains every switched-on step now, in the background.

    Returns immediately with status "running" - poll GET
    /organizations/{id}/automation/runs/{run_id}. Out of band for the same
    reason a campaign build is: writing a piece is a model call and rendering
    one is minutes, so a run is far past any sensible HTTP timeout.

    A run already in flight is returned as-is rather than a second one started:
    two drainers on one org would race for the same queue items.
    """
    org = get_owned_org(org_id, db, user)

    existing = automation.is_running(db, org.id)
    if existing is not None:
        return _run_out(existing)

    run = automation.start_run(db, org, trigger="manual")
    background_tasks.add_task(automation.execute, run.id)
    return _run_out(run)


@router.get("/runs")
def list_automation_runs(
    org_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_owned_org(org_id, db, user)
    runs = (
        db.query(AutomationRun)
        .filter(AutomationRun.organization_id == org_id)
        .order_by(AutomationRun.started_at.desc(), AutomationRun.id.desc())
        .limit(limit)
        .all()
    )
    return [_run_out(r) for r in runs]


@router.get("/runs/{run_id}")
def get_automation_run(
    org_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_owned_org(org_id, db, user)
    run = (
        db.query(AutomationRun)
        .filter(AutomationRun.id == run_id, AutomationRun.organization_id == org_id)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Automation run not found")
    return _run_out(run)
