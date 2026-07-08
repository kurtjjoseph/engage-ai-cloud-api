from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import AnalyticsSnapshot, Organization, User
from app.routers.organizations import get_owned_org
from app.schemas import AnalyticsSnapshotOut
from app.services.analytics_search import AnalyticsSearchService

router = APIRouter(prefix="/organizations/{org_id}/analytics", tags=["analytics"])

search_service = AnalyticsSearchService()


def get_analytics_enabled_org(org_id: int, db: Session, user: User) -> Organization:
    org = get_owned_org(org_id, db, user)
    if "analytics" not in (org.enabled_modules or []):
        raise HTTPException(
            status_code=403,
            detail=f"The 'analytics' module is not enabled for this organization. Enable it via PATCH /organizations/{org_id}/modules first.",
        )
    return org


def _org_context(org: Organization) -> dict:
    return {
        "name": org.name,
        "org_type": org.org_type,
        "website_url": org.website_url,
        "mission": org.mission,
        "audience": org.audience,
        "locations": org.locations,
    }


@router.post("/scan", response_model=AnalyticsSnapshotOut)
def run_scan(org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Runs one web-search-based scan now. The first scan for an org is
    flagged as its baseline; every later scan is meant to be compared
    against that fixed reference point."""
    org = get_analytics_enabled_org(org_id, db, user)

    result = search_service.scan(_org_context(org))

    is_first = (
        db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.organization_id == org.id).first() is None
    )

    snapshot = AnalyticsSnapshot(
        organization_id=org.id,
        is_baseline=is_first,
        summary=result.get("summary"),
        channels=result.get("channels", []),
        sources=result.get("sources", []),
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


@router.get("", response_model=list[AnalyticsSnapshotOut])
def list_snapshots(org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_analytics_enabled_org(org_id, db, user)
    return (
        db.query(AnalyticsSnapshot)
        .filter(AnalyticsSnapshot.organization_id == org_id)
        .order_by(AnalyticsSnapshot.created_at.desc())
        .all()
    )
