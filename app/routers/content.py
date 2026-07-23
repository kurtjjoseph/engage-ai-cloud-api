from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import ContentItem, User
from app.routers.organizations import get_owned_org
from app.schemas import ContentOut
from app.services.content_ideas import ContentIdeaService, DEFAULT_SITE_TYPE, content_types_catalog

router = APIRouter(prefix="/content", tags=["content"])

content_ideas = ContentIdeaService()


def _org_content_context(org) -> dict:
    return {
        "name": org.name,
        "org_type": org.org_type,
        "mission": org.mission,
        "tone": org.tone,
        "audience": org.audience,
        "locations": org.locations,
        "website_url": org.website_url,
    }


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


@router.get("/types")
def content_types(user: User = Depends(get_current_user)):
    """Per-channel content-type catalog: 5 types per channel, each with what
    engagement lever it raises. Powers the plugin's channel/type picker."""
    return content_types_catalog()


@router.post("/suggest", response_model=list[ContentOut])
def suggest_content(
    organization_id: int,
    count: int = Query(3, ge=1, le=6),
    channel: str | None = Query(None, description="Target channel, e.g. instagram (with content_type)"),
    content_type: str | None = Query(None, description="Content-type key from GET /content/types"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Drafts content tailored to this org and saved as tracked ContentItems the
    plugin can review. Two modes:

    - channel + content_type given: drafts that specific content type for that
      channel, shaped to raise that channel's engagement score (services/
      content_ideas.CHANNEL_CONTENT_TYPES).
    - neither given: a few website posts tailored to the site type (the default).

    site_type (Organization.site_facts["site_type"], church/ecommerce/business)
    and the org's mission/tone/audience keep the drafts on-brand."""
    org = get_owned_org(organization_id, db, user)
    site_type = (org.site_facts or {}).get("site_type") or DEFAULT_SITE_TYPE

    saved: list[ContentItem] = []
    if channel and content_type:
        ideas = content_ideas.suggest_for_channel(_org_content_context(org), channel, content_type, site_type, count)
        if not ideas:
            raise HTTPException(
                status_code=503,
                detail="No content could be generated (check ANTHROPIC_API_KEY and that the channel/content type are valid).",
            )
        for idea in ideas:
            output = {
                "channel": channel,
                "content_type_key": content_type,
                "content_type_label": idea.get("label", ""),
                "title": idea["title"],
                "body": idea["body"],
                "hashtags": idea.get("hashtags", []),
                "angle": idea.get("angle", ""),
            }
            if channel == "website":  # website drafts can become WordPress posts
                output["website_post"] = {"title": idea["title"], "body_html": idea["body"]}
            item = ContentItem(
                organization_id=org.id,
                content_type=channel,
                title=idea["title"],
                input_payload={"source": "suggested", "channel": channel, "content_type": content_type, "site_type": site_type},
                output_payload=output,
            )
            db.add(item)
            saved.append(item)
    else:
        ideas = content_ideas.suggest(_org_content_context(org), site_type, count)
        if not ideas:
            raise HTTPException(
                status_code=503,
                detail="No content could be generated (is ANTHROPIC_API_KEY configured?). Try again.",
            )
        for idea in ideas:
            item = ContentItem(
                organization_id=org.id,
                content_type="website_post",
                title=idea["title"],
                input_payload={"source": "suggested", "site_type": site_type, "angle": idea.get("angle", "")},
                output_payload={
                    "website_post": {"title": idea["title"], "body_html": idea["body_html"]},
                    "angle": idea.get("angle", ""),
                },
            )
            db.add(item)
            saved.append(item)

    db.commit()
    for item in saved:
        db.refresh(item)
    return saved
