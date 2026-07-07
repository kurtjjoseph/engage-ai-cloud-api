from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import ContentItem, User
from app.routers.organizations import get_owned_org
from app.schemas import AnnouncementsRequest, ContentOut, EventCampaignRequest, SermonEngagementRequest
from app.services.ai import EngageAIService

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
ai = EngageAIService()


def org_to_memory(org) -> dict:
    return {
        "name": org.name,
        "org_type": org.org_type,
        "mission": org.mission,
        "tone": org.tone,
        "audience": org.audience,
        "colors": org.colors,
        "ministries": org.ministries,
        "recurring_schedule": org.recurring_schedule,
        "locations": org.locations,
        "speakers": org.speakers,
    }


def save_content(db: Session, org_id: int, content_type: str, title: str, input_payload: dict, output_payload: dict) -> ContentItem:
    item = ContentItem(
        organization_id=org_id,
        content_type=content_type,
        title=title,
        input_payload=input_payload,
        output_payload=output_payload,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.post("/event", response_model=ContentOut)
def generate_event_campaign(payload: EventCampaignRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = get_owned_org(payload.organization_id, db, user)
    output = ai.generate_structured("event_campaign", org_to_memory(org), payload.model_dump())
    return save_content(db, org.id, "event_campaign", payload.event_name, payload.model_dump(), output)


@router.post("/announcements", response_model=ContentOut)
def generate_announcements(payload: AnnouncementsRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = get_owned_org(payload.organization_id, db, user)
    title = f"Weekly announcements - {payload.service_date}"
    output = ai.generate_structured("weekly_announcements", org_to_memory(org), payload.model_dump())
    return save_content(db, org.id, "weekly_announcements", title, payload.model_dump(), output)


@router.post("/sermon", response_model=ContentOut)
def generate_sermon_engagement(payload: SermonEngagementRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org = get_owned_org(payload.organization_id, db, user)
    output = ai.generate_structured("sermon_engagement", org_to_memory(org), payload.model_dump())
    return save_content(db, org.id, "sermon_engagement", payload.title, payload.model_dump(), output)
