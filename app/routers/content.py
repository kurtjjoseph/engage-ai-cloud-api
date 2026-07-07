from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import ContentItem, User
from app.routers.organizations import get_owned_org
from app.schemas import ContentOut

router = APIRouter(prefix="/content", tags=["content"])


@router.get("", response_model=list[ContentOut])
def list_content(organization_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = get_owned_org(organization_id, db, user)
    return (
        db.query(ContentItem)
        .filter(ContentItem.organization_id == org.id)
        .order_by(ContentItem.created_at.desc())
        .limit(100)
        .all()
    )
