from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import ContentItem, User
from app.routers.organizations import get_owned_org
from app.schemas import ContentOut
from app.services.content_ideas import ContentIdeaService, DEFAULT_SITE_TYPE

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


@router.post("/suggest", response_model=list[ContentOut])
def suggest_content(
    organization_id: int,
    count: int = Query(3, ge=1, le=6),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Drafts a few website posts tailored to this site's type (church /
    ecommerce / business, from Organization.site_facts["site_type"] the plugin
    reports) and saves each as a tracked ContentItem the plugin can review and
    turn into a WordPress draft. Uses the org's own mission/tone/audience so the
    drafts sound like them."""
    org = get_owned_org(organization_id, db, user)
    site_type = (org.site_facts or {}).get("site_type") or DEFAULT_SITE_TYPE
    ideas = content_ideas.suggest(_org_content_context(org), site_type, count)
    if not ideas:
        raise HTTPException(
            status_code=503,
            detail="No content could be generated (is ANTHROPIC_API_KEY configured?). Try again.",
        )

    saved: list[ContentItem] = []
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
