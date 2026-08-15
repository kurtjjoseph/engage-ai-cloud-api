from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.db.migrate import sync_missing_columns
from app.db.session import Base, engine
from app.routers import agents, analytics, assistant, auth, calendar, campaigns, channel_connections, chatbot, content, dashboard, engagement_cycle, ideas, onboarding, organizations, pipeline, plugin_updates, postiz, publications, studio
from app.services.scheduler import start_scheduler

Base.metadata.create_all(bind=engine)
sync_missing_columns()

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(campaigns.router)
app.include_router(content.router)
app.include_router(studio.router)
app.include_router(agents.router)
app.include_router(analytics.router)
app.include_router(engagement_cycle.router)
app.include_router(assistant.router)
app.include_router(onboarding.router)
app.include_router(plugin_updates.router)
app.include_router(publications.router)
app.include_router(channel_connections.router)
app.include_router(postiz.router)
app.include_router(dashboard.router)
app.include_router(ideas.router)
app.include_router(calendar.router)
app.include_router(pipeline.router)
app.include_router(chatbot.router)


@app.on_event("startup")
def on_startup():
    # Scans in flight when the previous deploy shut the process down died
    # with their snapshots stuck "pending" - mark those failed so the
    # plugin shows "run a new scan" instead of "in progress" forever.
    analytics.reap_stale_pending_snapshots()
    if settings.enable_scheduler:
        start_scheduler(settings.cycle_interval_hours)


@app.get("/")
def root():
    return {
        "name": settings.app_name,
        "status": "online",
        "message": "Engage AI is ready to turn messages into engagement.",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    """Liveness, plus whether the writing features can actually work.

    Every generator on this API needs an Anthropic key, and when one is missing
    the failure surfaces deep inside a feature ("the campaign couldn't be
    planned") where it reads like a bug in that feature. This says so up front,
    without exposing the key itself - a boolean and a public model name."""
    return {
        "status": "healthy",
        "ai": {
            "key_configured": bool(settings.anthropic_api_key),
            "model": settings.anthropic_model,
        },
    }
