from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import Organization, User
from app.schemas import ModulesUpdate, OrganizationCreate, OrganizationOut

router = APIRouter(prefix="/organizations", tags=["organizations"])

# Recognized "agent:<niche>" values - kept here (not enforced server-side
# beyond documentation) so NICHE_PROMPTS in services/agent_ai.py stays the
# single source of truth for what a niche actually does.
KNOWN_AGENT_NICHES = [
    "physical_product", "reselling", "youtube_channel", "answer_man",
    "local_service", "app_builder", "ugc_creator", "coaching",
]


@router.post("", response_model=OrganizationOut)
def create_organization(payload: OrganizationCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = Organization(owner_id=user.id, **payload.model_dump())
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


@router.get("/me", response_model=list[OrganizationOut])
def my_organizations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Organization).filter(Organization.owner_id == user.id).all()


def get_owned_org(org_id: int, db: Session, user: User) -> Organization:
    org = db.query(Organization).filter(Organization.id == org_id, Organization.owner_id == user.id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.patch("/{org_id}", response_model=OrganizationOut)
def update_organization(org_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Partial update for org-wide fields (mission, tone, audience,
    website_url, etc.) - only OrganizationCreate's known fields are applied,
    so a caller can safely send just the one or two fields it's changing."""
    org = get_owned_org(org_id, db, user)
    allowed = set(OrganizationCreate.model_fields.keys())
    for key, value in payload.items():
        if key in allowed:
            setattr(org, key, value)
    db.commit()
    db.refresh(org)
    return org


@router.patch("/{org_id}/modules", response_model=OrganizationOut)
def update_modules(org_id: int, payload: ModulesUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Sets the org's full list of activated modules, e.g.
    ["engagement", "agent:youtube_channel", "agent:coaching"]. Replaces
    rather than merges, so the caller (the WordPress "Modules" checkboxes)
    can just send its current checked state."""
    org = get_owned_org(org_id, db, user)
    org.enabled_modules = payload.enabled_modules
    db.commit()
    db.refresh(org)
    return org
