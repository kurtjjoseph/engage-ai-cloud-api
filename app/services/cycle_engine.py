from sqlalchemy.orm import Session
from app.models.entities import AgentRun, Organization, Ticket
from app.services.agent_ai import AgentAI
from app.services.analytics_insights import compute_insights

ai = AgentAI()

ENGAGEMENT_GROWTH_NICHE = "engagement_growth"


def module_key(niche: str) -> str:
    return f"agent:{niche}"


def is_module_enabled(org: Organization, niche: str) -> bool:
    return module_key(niche) in (org.enabled_modules or [])


def _org_context(org: Organization) -> dict:
    """Shared church/business-wide context, used as fallback if a niche's own profile is thin."""
    return {
        "name": org.name,
        "org_type": org.org_type,
        "mission": org.mission,
        "tone": org.tone,
        "audience": org.audience,
    }


def _engagement_growth_profile(db: Session, org: Organization) -> dict:
    """Builds this niche's "profile" from live analytics data instead of the
    free-form agent_profiles JSON every other niche uses - the whole point
    of this niche is to act on numbers Engage AI already measured
    (services/analytics_insights.py), not on facts the client typed in.
    Every gap/target number here is computed in Python, never invented by
    the model - the agent's only job is turning a known gap into a concrete
    next action."""
    targets = org.target_channel_scores or {}
    profile: dict = {
        "target_org_score": org.target_org_score,
        "target_channel_scores": targets,
    }

    insights = compute_insights(db, org.id)
    if insights is None:
        profile["status"] = "no_baseline_scan_yet"
        return profile

    gaps = []
    for r in insights["ranking"]:
        target = targets.get(r["channel"])
        gap = (target - r["score"]) if target is not None else None
        gaps.append({**r, "target": target, "gap": gap})
    # Biggest gap first; channels with no target set sort last (nothing to close).
    gaps.sort(key=lambda g: (g["gap"] is None, -(g["gap"] or 0)))

    org_score = insights["org_score"]
    org_gap = (org.target_org_score - org_score) if org.target_org_score is not None and org_score is not None else None

    profile.update({
        "org_score": org_score,
        "baseline_org_score": insights["baseline_org_score"],
        "org_score_gap": org_gap,
        "channel_gaps": gaps,
        "latest_scan_summary": insights["summary"],
    })
    return profile


def _niche_profile(db: Session, org: Organization, niche: str) -> dict:
    if niche == ENGAGEMENT_GROWTH_NICHE:
        return _engagement_growth_profile(db, org)
    return (org.agent_profiles or {}).get(niche, {})


def _recent_runs(db: Session, organization_id: int, niche: str, limit: int = 5) -> list[dict]:
    runs = (
        db.query(AgentRun)
        .filter(AgentRun.organization_id == organization_id, AgentRun.niche == niche)
        .order_by(AgentRun.ran_at.desc())
        .limit(limit)
        .all()
    )
    return [{"ran_at": r.ran_at.isoformat(), "summary": r.summary} for r in reversed(runs)]


def _open_tickets(db: Session, organization_id: int, niche: str) -> list[dict]:
    tickets = (
        db.query(Ticket)
        .filter(
            Ticket.organization_id == organization_id,
            Ticket.niche == niche,
            Ticket.status.in_(["backlog", "proposed", "approved"]),
        )
        .order_by(Ticket.created_at.desc())
        .all()
    )
    return [
        {"id": t.id, "title": t.title, "status": t.status, "risk": t.risk, "decision_note": t.decision_note}
        for t in tickets
    ]


def run_cycle_for_niche(db: Session, org: Organization, niche: str) -> AgentRun:
    """The one function both the manual '/organizations/{id}/agents/{niche}/cycles/run'
    endpoint and the scheduler call. Runs exactly one check-in cycle for one
    niche module on one organization end to end. Caller is responsible for
    checking is_module_enabled() first."""
    result = ai.run_cycle(
        niche=niche,
        org_context=_org_context(org),
        niche_profile=_niche_profile(db, org, niche),
        recent_runs=_recent_runs(db, org.id, niche),
        open_tickets=_open_tickets(db, org.id, niche),
    )

    tickets_created = 0
    for t in result.get("tickets", []):
        title = (t.get("title") or "Untitled")[:255]
        db.add(Ticket(
            organization_id=org.id,
            niche=niche,
            title=title,
            rationale=t.get("rationale"),
            risk=t.get("risk", "low"),
            status="proposed",
            payload=t.get("payload"),
        ))
        tickets_created += 1

    run = AgentRun(
        organization_id=org.id,
        niche=niche,
        summary=result.get("summary", ""),
        tickets_created=tickets_created,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
