from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.db.migrate import sync_missing_columns
from app.db.session import Base, engine
from app.routers import agents, auth, campaigns, content, onboarding, organizations
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
app.include_router(agents.router)
app.include_router(onboarding.router)


@app.on_event("startup")
def on_startup():
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
    return {"status": "healthy"}
