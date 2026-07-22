"""VOM operator control dashboard - a live, single-owner overview of every site
the operator tracks: current scores, agent counts, install/measurement stats, a
cross-org activity log, and a per-site detail view. Same-origin with the API, so
the served HTML page (GET /dashboard) fetches these authed JSON endpoints
directly - no external host, unlike a published artifact.

This fills the "standalone web dashboard" ARCHITECTURE.md lists as deferred."""
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.deps import get_current_user
from app.db.session import get_db
from app.models.entities import AgentRun, AnalyticsSnapshot, Organization, Publication, User
from app.services.analytics_insights import compute_insights

router = APIRouter(tags=["dashboard"])

_DASHBOARD_HTML = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"


def _agent_niches(org: Organization) -> list[str]:
    return [m.split(":", 1)[1] for m in (org.enabled_modules or []) if m.startswith("agent:")]


def _links(website_url: str | None) -> tuple[str | None, str | None]:
    if not website_url:
        return None, None
    front = website_url.rstrip("/")
    return front, front + "/wp-admin"


def _latest_complete_snapshot(db: Session, org_id: int) -> AnalyticsSnapshot | None:
    rows = (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.organization_id == org_id)
        .order_by(AnalyticsSnapshot.created_at.desc())
        .limit(20)
        .all()
    )
    return next((s for s in rows if not s.requested_channels and s.status not in ("pending", "failed")), None)


@router.get("/admin/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Everything the main control dashboard renders, in one call. Scoped to the
    orgs this operator owns (organizations.owner_id == user.id)."""
    orgs = db.query(Organization).filter(Organization.owner_id == user.id).all()
    org_ids = [o.id for o in orgs]
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    sites = []
    scores, deltas, agent_total = [], [], 0
    for org in orgs:
        analytics_enabled = "analytics" in (org.enabled_modules or [])
        insights = compute_insights(db, org.id) if analytics_enabled else None
        latest = _latest_complete_snapshot(db, org.id)
        niches = _agent_niches(org)
        agent_total += len(niches)
        front, wp_admin = _links(org.website_url)

        org_score = insights["org_score"] if insights else None
        baseline = insights["baseline_org_score"] if insights else None
        delta = (org_score - baseline) if (org_score is not None and baseline is not None) else None
        if org_score is not None:
            scores.append(org_score)
        if delta is not None:
            deltas.append(delta)

        sites.append({
            "id": org.id,
            "name": org.name,
            "org_type": org.org_type,
            "website_url": org.website_url,
            "front_url": front,
            "wp_admin_url": wp_admin,
            "created_at": org.created_at.isoformat() if org.created_at else None,
            "analytics_enabled": analytics_enabled,
            "org_score": org_score,
            "baseline_org_score": baseline,
            "score_delta": delta,
            "channel_count_scored": sum(1 for r in insights["ranking"] if r["score"] > 0) if insights else 0,
            "last_scan_at": latest.created_at.isoformat() if latest else None,
            "last_scan_duration_seconds": latest.duration_seconds if latest else None,
            "needs_review": bool(insights["needs_review"]) if insights else False,
            "agent_count": len(niches),
            "agent_niches": niches,
        })

    # Average measurement time across recent completed scans (any owned org).
    recent_durations = []
    if org_ids:
        for s in (
            db.query(AnalyticsSnapshot)
            .filter(AnalyticsSnapshot.organization_id.in_(org_ids), AnalyticsSnapshot.status == "complete")
            .order_by(AnalyticsSnapshot.created_at.desc())
            .limit(50)
            .all()
        ):
            if s.duration_seconds is not None:
                recent_durations.append(s.duration_seconds)

    totals = {
        "total_sites": len(orgs),
        "analytics_sites": sum(1 for s in sites if s["analytics_enabled"]),
        "avg_org_score": round(sum(scores) / len(scores), 1) if scores else None,
        "total_score_improvement": sum(deltas) if deltas else 0,
        "installs_this_week": sum(1 for o in orgs if o.created_at and o.created_at >= week_ago),
        "avg_measurement_seconds": round(sum(recent_durations) / len(recent_durations), 1) if recent_durations else None,
        "total_agents": agent_total,
        "sites_needing_review": sum(1 for s in sites if s["needs_review"]),
    }

    return {"generated_at": now.isoformat(), "totals": totals, "sites": sites, "logs": _recent_logs(db, orgs)}


def _recent_logs(db: Session, orgs: list[Organization], limit: int = 40) -> list[dict]:
    """Newest-first activity across all owned orgs: scans and agent runs."""
    names = {o.id: o.name for o in orgs}
    org_ids = list(names.keys())
    events: list[dict] = []
    if not org_ids:
        return events

    for s in (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.organization_id.in_(org_ids))
        .order_by(AnalyticsSnapshot.created_at.desc())
        .limit(limit)
        .all()
    ):
        status = s.status or "complete"
        if status == "complete":
            detail = f"Scan complete - org score {s.org_score}" + (f" ({s.duration_seconds}s)" if s.duration_seconds else "")
        elif status == "pending":
            detail = "Scan running…"
        else:
            detail = f"Scan {status}"
        events.append({
            "ts": s.created_at.isoformat(), "org_id": s.organization_id, "org_name": names.get(s.organization_id),
            "kind": "scan", "status": status, "needs_review": bool(s.needs_review), "detail": detail,
        })

    for r in (
        db.query(AgentRun)
        .filter(AgentRun.organization_id.in_(org_ids))
        .order_by(AgentRun.ran_at.desc())
        .limit(limit)
        .all()
    ):
        events.append({
            "ts": r.ran_at.isoformat(), "org_id": r.organization_id, "org_name": names.get(r.organization_id),
            "kind": "agent", "status": None, "needs_review": False,
            "detail": (r.summary or f"{r.niche} check-in") + (f" - {r.tickets_created} tickets" if r.tickets_created else ""),
        })

    events.sort(key=lambda e: e["ts"], reverse=True)
    return events[:limit]


@router.get("/admin/site/{org_id}")
def site_detail(org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Everything the per-site detail view renders, in one call."""
    org = db.query(Organization).filter(Organization.id == org_id, Organization.owner_id == user.id).first()
    if org is None:
        raise HTTPException(status_code=404, detail="Site not found for this operator.")

    analytics_enabled = "analytics" in (org.enabled_modules or [])
    insights = compute_insights(db, org.id) if analytics_enabled else None
    front, wp_admin = _links(org.website_url)

    # Full-sweep score history, oldest-first, for the trend chart.
    snaps = (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.organization_id == org.id)
        .order_by(AnalyticsSnapshot.created_at.asc())
        .all()
    )
    history = [
        {
            "id": s.id,
            "created_at": s.created_at.isoformat(),
            "org_score": s.org_score,
            "duration_seconds": s.duration_seconds,
            "needs_review": bool(s.needs_review),
            "status": s.status or "complete",
            "is_baseline": bool(s.is_baseline),
        }
        for s in snaps
        if not s.requested_channels and s.status not in ("pending", "failed")
    ]

    # Every scan attempt (any status, scoped or full), newest first - each links
    # to its own details page (GET .../analytics/snapshot/{id}) showing all the
    # data used in that request.
    all_snaps = (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.organization_id == org.id)
        .order_by(AnalyticsSnapshot.created_at.desc())
        .limit(60)
        .all()
    )
    scans = [
        {
            "id": s.id,
            "created_at": s.created_at.isoformat(),
            "status": s.status or "complete",
            "org_score": s.org_score,
            "duration_seconds": s.duration_seconds,
            "needs_review": bool(s.needs_review),
            "is_baseline": bool(s.is_baseline),
            "scope": "full sweep" if not s.requested_channels else ", ".join(s.requested_channels),
            "channel_count": len(s.channels or []),
        }
        for s in all_snaps
    ]

    agent_runs = [
        {"niche": r.niche, "summary": r.summary, "tickets_created": r.tickets_created, "ran_at": r.ran_at.isoformat()}
        for r in (
            db.query(AgentRun)
            .filter(AgentRun.organization_id == org.id)
            .order_by(AgentRun.ran_at.desc())
            .limit(15)
            .all()
        )
    ]

    publication_count = db.query(Publication).filter(Publication.organization_id == org.id).count()

    return {
        "id": org.id,
        "name": org.name,
        "org_type": org.org_type,
        "mission": org.mission,
        "audience": org.audience,
        "website_url": org.website_url,
        "front_url": front,
        "wp_admin_url": wp_admin,
        "created_at": org.created_at.isoformat() if org.created_at else None,
        "enabled_modules": org.enabled_modules or [],
        "agent_niches": _agent_niches(org),
        "agent_count": len(_agent_niches(org)),
        "analytics_enabled": analytics_enabled,
        "publication_count": publication_count,
        "insights": insights,
        "history": history,
        "scans": scans,
        "agent_runs": agent_runs,
    }


@router.get("/dashboard")
def dashboard_page():
    """The operator control dashboard HTML (public shell; it logs in and fetches
    the authed JSON endpoints above same-origin)."""
    return FileResponse(str(_DASHBOARD_HTML), media_type="text/html")
