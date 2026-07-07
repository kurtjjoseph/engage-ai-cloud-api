from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.db.session import Base, engine
from app.routers import auth, campaigns, content, organizations

Base.metadata.create_all(bind=engine)

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
