from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import Organization, User
from app.schemas import OrganizationCreate, OrganizationOut

router = APIRouter(prefix="/organizations", tags=["organizations"])


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
