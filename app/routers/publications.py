from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import Publication, PublicationSnapshot, User
from app.routers.analytics import get_analytics_enabled_org
from app.schemas import PublicationCreate, PublicationOut, PublicationSnapshotOut, PublicationWithLatestOut
from app.services.analytics_scoring import PUBLICATION_SCANNABLE_CHANNELS, PUBLICATION_UNSCANNABLE_CHANNELS, score_publication
from app.services.publication_search import PublicationSearchService

router = APIRouter(prefix="/organizations/{org_id}/publications", tags=["publications"])

search_service = PublicationSearchService()


def _get_owned_publication(org_id: int, pub_id: int, db: Session) -> Publication:
    pub = db.query(Publication).filter(Publication.id == pub_id, Publication.organization_id == org_id).first()
    if not pub:
        raise HTTPException(status_code=404, detail="Publication not found")
    return pub


@router.post("", response_model=PublicationOut)
def register_publication(org_id: int, payload: PublicationCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Records where something was actually published - the WordPress
    plugin's "mark as published" step calls this once the admin pastes back
    a URL. Not the same as generating the content (ContentItem already
    exists by then); this is the missing piece that makes that content
    trackable, since Engage AI can't otherwise know what happened after
    social/email/WhatsApp copy was handed off for manual posting."""
    org = get_analytics_enabled_org(org_id, db, user)
    pub = Publication(organization_id=org.id, **payload.model_dump())
    db.add(pub)
    db.commit()
    db.refresh(pub)
    return pub


@router.get("", response_model=list[PublicationWithLatestOut])
def list_publications(org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_analytics_enabled_org(org_id, db, user)
    pubs = (
        db.query(Publication)
        .filter(Publication.organization_id == org_id)
        .order_by(Publication.created_at.desc())
        .all()
    )
    result = []
    for pub in pubs:
        latest = (
            db.query(PublicationSnapshot)
            .filter(PublicationSnapshot.publication_id == pub.id)
            .order_by(PublicationSnapshot.scanned_at.desc())
            .first()
        )
        result.append(PublicationWithLatestOut(
            **PublicationOut.model_validate(pub).model_dump(),
            latest_snapshot=PublicationSnapshotOut.model_validate(latest) if latest else None,
        ))
    return result


@router.get("/{pub_id}", response_model=list[PublicationSnapshotOut])
def get_publication_history(org_id: int, pub_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_analytics_enabled_org(org_id, db, user)
    _get_owned_publication(org_id, pub_id, db)
    return (
        db.query(PublicationSnapshot)
        .filter(PublicationSnapshot.publication_id == pub_id)
        .order_by(PublicationSnapshot.scanned_at.desc())
        .all()
    )


@router.post("/{pub_id}/scan", response_model=PublicationSnapshotOut)
def scan_publication(org_id: int, pub_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = get_analytics_enabled_org(org_id, db, user)
    pub = _get_owned_publication(org_id, pub_id, db)

    if pub.channel in PUBLICATION_UNSCANNABLE_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"'{pub.channel}' publications aren't publicly visible (it's a private send, not a public post), so there's genuinely nothing to search for - not a limitation that can be fixed, just how {pub.channel} works.",
        )
    if pub.channel not in PUBLICATION_SCANNABLE_CHANNELS:
        raise HTTPException(status_code=400, detail=f"Unrecognized channel '{pub.channel}'. Scannable channels: {', '.join(PUBLICATION_SCANNABLE_CHANNELS)}")

    result = search_service.scan(pub.channel, pub.url)
    score, breakdown = score_publication(pub.channel, result.get("kpis"))

    snapshot = PublicationSnapshot(
        publication_id=pub.id,
        kpis=result.get("kpis"),
        notes=result.get("notes"),
        score=score,
        score_breakdown=breakdown,
        sources=result.get("sources", []),
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot
