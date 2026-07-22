from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import EngagementCycleRun, Organization, User
from app.routers.organizations import get_owned_org
from app.schemas import EngagementCycleRunOut, RunCycleRequest
from app.services.cycle_measurement import is_cycle_enabled
from app.services.engagement_cycle import run_full_cycle

router = APIRouter(prefix="/organizations/{org_id}/engagement-cycle", tags=["engagement-cycle"])

VALID_MEASURE_MODES = {None, "simulate", "live"}


def get_cycle_enabled_org(org_id: int, db: Session, user: User) -> Organization:
    org = get_owned_org(org_id, db, user)
    if not is_cycle_enabled(org):
        raise HTTPException(
            status_code=403,
            detail=f"Module 'engagement_cycle' is not enabled for this organization. Enable it via PATCH /organizations/{org_id}/modules first.",
        )
    return org


@router.post("/run", response_model=EngagementCycleRunOut)
def run_cycle_now(
    org_id: int,
    payload: RunCycleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Manual trigger for the full seven-stage engagement cycle - the same
    function the scheduler calls automatically on an interval. Use this to
    run/preview a cycle on demand."""
    org = get_cycle_enabled_org(org_id, db, user)

    if payload.measure_mode not in VALID_MEASURE_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"measure_mode must be one of {sorted(m for m in VALID_MEASURE_MODES if m)} or omitted, got {payload.measure_mode!r}",
        )

    return run_full_cycle(
        db,
        org,
        auto_approve=payload.auto_approve,
        measure_mode=payload.measure_mode,
        dry_run=payload.dry_run,
    )


@router.get("/runs", response_model=list[EngagementCycleRunOut])
def list_runs(org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_cycle_enabled_org(org_id, db, user)
    return (
        db.query(EngagementCycleRun)
        .filter(EngagementCycleRun.organization_id == org_id)
        .order_by(EngagementCycleRun.created_at.desc())
        .all()
    )


@router.get("/runs/{run_id}", response_model=EngagementCycleRunOut)
def get_run(org_id: int, run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_cycle_enabled_org(org_id, db, user)
    run = (
        db.query(EngagementCycleRun)
        .filter(EngagementCycleRun.id == run_id, EngagementCycleRun.organization_id == org_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Engagement cycle run not found")
    return run
