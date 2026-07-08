from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import AgentRun, Organization, Ticket, User
from app.routers.organizations import get_owned_org
from app.schemas import AgentRunOut, TicketDecision, TicketOut
from app.services.cycle_engine import is_module_enabled, run_cycle_for_niche

router = APIRouter(prefix="/organizations/{org_id}/agents/{niche}", tags=["agents"])


def get_enabled_org(org_id: int, niche: str, db: Session, user: User) -> Organization:
    org = get_owned_org(org_id, db, user)
    if not is_module_enabled(org, niche):
        raise HTTPException(
            status_code=403,
            detail=f"Module 'agent:{niche}' is not enabled for this organization. Enable it via PATCH /organizations/{org_id}/modules first.",
        )
    return org


@router.post("/cycles/run", response_model=AgentRunOut)
def run_cycle_now(org_id: int, niche: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Manual trigger - the same function the scheduler calls automatically.
    Use this to test a niche's agent on demand before trusting the schedule."""
    org = get_enabled_org(org_id, niche, db, user)
    return run_cycle_for_niche(db, org, niche)


@router.get("/cycles", response_model=list[AgentRunOut])
def list_cycles(org_id: int, niche: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_enabled_org(org_id, niche, db, user)
    return (
        db.query(AgentRun)
        .filter(AgentRun.organization_id == org_id, AgentRun.niche == niche)
        .order_by(AgentRun.ran_at.desc())
        .all()
    )


@router.get("/tickets", response_model=list[TicketOut])
def list_tickets(
    org_id: int,
    niche: str,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_enabled_org(org_id, niche, db, user)
    query = db.query(Ticket).filter(Ticket.organization_id == org_id, Ticket.niche == niche)
    if status:
        query = query.filter(Ticket.status == status)
    return query.order_by(Ticket.created_at.desc()).all()


@router.post("/tickets/{ticket_id}/decision", response_model=TicketOut)
def decide_ticket(
    org_id: int,
    niche: str,
    ticket_id: int,
    payload: TicketDecision,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_enabled_org(org_id, niche, db, user)
    ticket = (
        db.query(Ticket)
        .filter(Ticket.id == ticket_id, Ticket.organization_id == org_id, Ticket.niche == niche)
        .first()
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if payload.decision == "approve":
        ticket.status = "approved"
    elif payload.decision == "reject":
        ticket.status = "rejected"
    elif payload.decision == "redirect":
        ticket.status = "backlog"
    else:
        raise HTTPException(status_code=400, detail="decision must be 'approve', 'reject', or 'redirect'")

    ticket.decision_note = payload.note
    ticket.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(ticket)
    return ticket


@router.patch("/profile")
def update_niche_profile(
    org_id: int,
    niche: str,
    profile: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Merges into this niche's slice of Organization.agent_profiles - how a
    clarifying-question ticket gets answered."""
    org = get_enabled_org(org_id, niche, db, user)
    profiles = org.agent_profiles or {}
    profiles[niche] = {**profiles.get(niche, {}), **profile}
    org.agent_profiles = profiles
    db.commit()
    db.refresh(org)
    return {"niche": niche, "profile": org.agent_profiles[niche]}
