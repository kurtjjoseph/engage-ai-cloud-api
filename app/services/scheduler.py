import time

from apscheduler.schedulers.background import BackgroundScheduler
from app.config import settings
from app.db.session import SessionLocal
from app.models.entities import AnalyticsSnapshot, Organization
from app.services.cycle_engine import run_cycle_for_niche
from app.services.cycle_measurement import is_cycle_enabled
from app.services.engagement_cycle import run_full_cycle

# In-process scheduler, not Celery/Redis - one fewer moving part to operate,
# consistent with the low-maintenance hosting goal documented in ARCHITECTURE.md.
scheduler = BackgroundScheduler()


def run_all_active_agent_modules() -> None:
    db = SessionLocal()
    try:
        orgs = db.query(Organization).all()
        for org in orgs:
            for module in (org.enabled_modules or []):
                if module.startswith("agent:"):
                    niche = module.split(":", 1)[1]
                    run_cycle_for_niche(db, org, niche)
    finally:
        db.close()


def run_all_engagement_cycles() -> None:
    """Runs the full seven-stage engagement cycle (services/engagement_cycle.py)
    for every organization with the 'engagement_cycle' module enabled. Each
    org is wrapped in its own try/except so one org's failure (e.g. no
    baseline analytics snapshot yet, or a distribution error) never stops the
    rest of the batch from running."""
    db = SessionLocal()
    try:
        orgs = db.query(Organization).all()
        for org in orgs:
            if not is_cycle_enabled(org):
                continue
            try:
                run_full_cycle(db, org)
            except Exception as exc:  # noqa: BLE001 - one org's failure must not sink the whole scheduled batch
                print(f"[scheduler] engagement cycle failed for org {org.id}: {exc}", flush=True)
    finally:
        db.close()


def run_all_scheduled_analytics_scans() -> None:
    """Runs a full-sweep analytics scan for every org with the 'analytics'
    module enabled, on a schedule (settings.analytics_scan_interval_hours,
    ~monthly). This is what makes the trend line accrue on its own - before
    this, every scan was manual, so there was no automatic monthly report
    cadence at all.

    Scans run SYNCHRONOUSLY inside this job (not via FastAPI BackgroundTasks,
    which don't survive a restart - see routers/analytics.reap_stale_pending_snapshots).
    Orgs are staggered (settings.analytics_scan_stagger_seconds) so N orgs don't
    fire N simultaneous web-search calls, and each is isolated so one failure
    doesn't sink the batch. Imports are local to avoid a router<->service import
    cycle at module load."""
    from app.routers.analytics import _execute_scan, _org_context, build_request_context

    db = SessionLocal()
    try:
        org_ids = [o.id for o in db.query(Organization).all() if "analytics" in (o.enabled_modules or [])]
    finally:
        db.close()

    stagger = max(0, settings.analytics_scan_stagger_seconds)
    for index, org_id in enumerate(org_ids):
        try:
            db = SessionLocal()
            try:
                org = db.query(Organization).filter(Organization.id == org_id).first()
                if org is None:
                    continue
                is_first = (
                    db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.organization_id == org_id).first() is None
                )
                snapshot = AnalyticsSnapshot(
                    organization_id=org_id, is_baseline=is_first, requested_channels=None, status="pending",
                    request_context=build_request_context(org, None, False),
                )
                db.add(snapshot)
                db.commit()
                db.refresh(snapshot)
                snapshot_id = snapshot.id
                org_context = _org_context(org)
                site_facts = org.site_facts
            finally:
                db.close()
            print(f"[scheduler] scheduled analytics scan starting for org {org_id} (snapshot {snapshot_id})", flush=True)
            _execute_scan(snapshot_id, org_context, None, False, site_facts)  # opens its own session, scores + writes
        except Exception as exc:  # noqa: BLE001 - one org's failure must not sink the whole scheduled batch
            print(f"[scheduler] scheduled analytics scan failed for org {org_id}: {exc}", flush=True)
        if stagger and index < len(org_ids) - 1:
            time.sleep(stagger)


def start_scheduler(interval_hours: int) -> None:
    scheduler.add_job(run_all_active_agent_modules, "interval", hours=interval_hours, id="agent_cycle", replace_existing=True)
    scheduler.add_job(run_all_engagement_cycles, "interval", hours=interval_hours, id="engagement_cycle", replace_existing=True)
    scheduler.add_job(
        run_all_scheduled_analytics_scans,
        "interval",
        hours=settings.analytics_scan_interval_hours,
        id="analytics_scan",
        replace_existing=True,
    )
    scheduler.start()
