from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import ContentItem, MediaAsset, User
from app.routers.organizations import get_owned_org
from app.schemas import ContentOut
from app.services.content_ideas import (
    CHANNEL_CONTENT_TYPES,
    ContentIdeaService,
    DEFAULT_SITE_TYPE,
    content_types_catalog,
    default_type_for,
)
from app.services.media_gen import ImageGenService, VideoGenService

router = APIRouter(prefix="/content", tags=["content"])

content_ideas = ContentIdeaService()
image_gen = ImageGenService()
video_gen = VideoGenService(image_gen)


class PackRequest(BaseModel):
    topic: str | None = None
    channels: list[str] | None = None


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


@router.post("/pack", response_model=list[ContentOut])
def generate_pack(
    organization_id: int,
    payload: PackRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The content-design agent: one topic turned into a coordinated post for
    each chosen channel, each with the media it needs - an image prompt + alt
    for image posts, a full video storyboard for video posts. Saves each piece
    as a tracked ContentItem; the plugin then generates the image (POST
    /content/{id}/image) and renders/publishes per channel."""
    org = get_owned_org(organization_id, db, user)
    site_type = (org.site_facts or {}).get("site_type") or DEFAULT_SITE_TYPE

    channels = [c for c in (payload.channels or ["website", "instagram", "facebook"]) if c in CHANNEL_CONTENT_TYPES][:6]
    selections: list[tuple[str, str]] = []
    for channel in channels:
        default = default_type_for(channel)
        if default:
            selections.append((channel, default["key"]))
    if not selections:
        raise HTTPException(status_code=400, detail="No valid channels selected.")

    pack = content_ideas.generate_pack(_org_content_context(org), site_type, payload.topic, selections)
    pieces = pack.get("pieces") or []
    if not pieces:
        raise HTTPException(status_code=503, detail="No content could be generated (is ANTHROPIC_API_KEY configured?). Try again.")

    saved: list[ContentItem] = []
    for piece in pieces:
        output = {**piece, "topic": pack.get("topic", "")}
        if piece["channel"] == "website":
            output["website_post"] = {"title": piece["title"], "body_html": piece["body"]}
        item = ContentItem(
            organization_id=org.id,
            content_type=piece["channel"],
            title=piece["title"],
            input_payload={"source": "campaign", "topic": pack.get("topic", ""),
                           "channel": piece["channel"], "content_type": piece["content_type"], "site_type": site_type},
            output_payload=output,
        )
        db.add(item)
        saved.append(item)
    db.commit()
    for item in saved:
        db.refresh(item)
    return saved


@router.post("/{content_id}/image")
def generate_content_image(
    content_id: int,
    organization_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generates the image for a content piece from its stored image_prompt (via
    gpt-image-1), stores it, and links it back to the item. 503 if no image
    provider key is configured yet - the prompt + alt are always available to
    paste into any tool in the meantime."""
    org = get_owned_org(organization_id, db, user)
    item = db.query(ContentItem).filter(ContentItem.id == content_id, ContentItem.organization_id == org.id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Content not found.")
    prompt = (item.output_payload or {}).get("image_prompt")
    if not prompt:
        raise HTTPException(status_code=400, detail="This content has no image to generate.")
    if not image_gen.enabled:
        raise HTTPException(status_code=503, detail="Image generation isn't configured yet - set OPENAI_API_KEY. Use the image prompt below in any image tool for now.")
    result = image_gen.generate_image(prompt)
    if not result:
        raise HTTPException(status_code=502, detail="Image generation failed - try again.")
    data, mime = result
    asset = MediaAsset(organization_id=org.id, content_item_id=item.id, kind="image", mime=mime, prompt=prompt, data=data)
    db.add(asset)
    db.commit()
    db.refresh(asset)
    output = dict(item.output_payload or {})
    output["image_asset_id"] = asset.id
    item.output_payload = output
    db.commit()
    return {"asset_id": asset.id, "url": f"/content/asset/{asset.id}", "mime": mime}


@router.post("/{content_id}/video")
def generate_content_video(
    content_id: int,
    organization_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Assembles a short captioned video from a piece's stored video_plan -
    generates a still per scene, burns in the caption, and stitches to MP4.
    Keyless and reliable (no premium video model)."""
    org = get_owned_org(organization_id, db, user)
    item = db.query(ContentItem).filter(ContentItem.id == content_id, ContentItem.organization_id == org.id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Content not found.")
    plan = (item.output_payload or {}).get("video_plan")
    if not isinstance(plan, dict) or not plan.get("scenes"):
        raise HTTPException(status_code=400, detail="This content has no video plan to assemble.")
    result = video_gen.assemble(plan)
    if not result:
        raise HTTPException(status_code=502, detail="Video assembly failed - try again.")
    data, mime = result
    asset = MediaAsset(organization_id=org.id, content_item_id=item.id, kind="video", mime=mime,
                       prompt=(plan.get("voiceover") or item.title), data=data)
    db.add(asset)
    db.commit()
    db.refresh(asset)
    output = dict(item.output_payload or {})
    output["video_asset_id"] = asset.id
    item.output_payload = output
    db.commit()
    return {"asset_id": asset.id, "url": f"/content/asset/{asset.id}", "mime": mime}


@router.get("/asset/{asset_id}")
def get_asset(asset_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Serves a generated media file's bytes (owner-scoped)."""
    asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    get_owned_org(asset.organization_id, db, user)  # 404s if not the caller's org
    return Response(content=asset.data, media_type=asset.mime)
