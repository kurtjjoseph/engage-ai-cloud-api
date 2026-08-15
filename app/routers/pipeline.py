"""GET /organizations/{id}/pipeline - every stage's in-queue and out-queue.

One call returns the whole board rather than one endpoint per stage: each page
in the plugin needs its own stage plus the neighbouring counts to say where work
came from and where it went, and seven round trips to draw one strip would put
the API on the critical path of every admin page.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import User
from app.routers.organizations import get_owned_org
from app.services.pipeline import build_pipeline

router = APIRouter(prefix="/organizations/{org_id}", tags=["pipeline"])


@router.get("/pipeline")
def get_pipeline(
    org_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_owned_org(org_id, db, user)
    return build_pipeline(db, org_id)
