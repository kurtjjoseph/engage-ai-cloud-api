from apscheduler.schedulers.background import BackgroundScheduler
from app.db.session import SessionLocal
from app.models.entities import Organization
from app.services.cycle_engine import run_cycle_for_niche

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


def start_scheduler(interval_hours: int) -> None:
    scheduler.add_job(run_all_active_agent_modules, "interval", hours=interval_hours, id="agent_cycle", replace_existing=True)
    scheduler.start()
