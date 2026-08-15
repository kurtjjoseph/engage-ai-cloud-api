"""The idea cache: what to make, before it is anything in particular.

The Content Studio could always generate ideas (POST /studio/ideas), but they
were never kept - the plugin held a batch in a WordPress transient keyed by the
goal it was generated against, so a good idea expired, belonged to one site, and
was lost the moment the operator changed goal. This is where an idea lives
between "that's worth doing" and "that's written".

Deliberately not a second content table: an idea has no channel-shaped body, no
quality score and no media, and forcing it into ContentItem would mean every
library query learning to skip a kind of row that is not content yet. When an
idea is drafted, the resulting ContentItem is linked back via
Idea.content_item_id, which is what makes the trail from idea to published piece
answerable at all.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import Idea, User
from app.routers.organizations import get_owned_org
from app.schemas import IdeaCreate, IdeaOut, IdeaUpdate

router = APIRouter(prefix="/organizations/{org_id}/ideas", tags=["ideas"])

# Kept deliberately small. "kept" is the inbox, "drafted" is the archive of what
# was used, "dismissed" is remembered rather than deleted so the generator's
# next batch can avoid proposing the same thing back a week later.
STATUSES = ("kept", "drafted", "dismissed")


def _owned_idea(org_id: int, idea_id: int, db: Session) -> Idea:
    idea = db.query(Idea).filter(Idea.id == idea_id, Idea.organization_id == org_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    return idea


@router.get("", response_model=list[IdeaOut])
def list_ideas(
    org_id: int,
    status: str | None = Query(None, description="kept | drafted | dismissed"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Newest first. Without ?status the whole cache comes back, dismissed
    included - the caller decides what to show, rather than this quietly
    deciding that dismissed ideas are not worth seeing."""
    get_owned_org(org_id, db, user)

    query = db.query(Idea).filter(Idea.organization_id == org_id)
    if status is not None:
        if status not in STATUSES:
            raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(STATUSES)}")
        query = query.filter(Idea.status == status)
    return query.order_by(Idea.created_at.desc(), Idea.id.desc()).all()


@router.post("", response_model=list[IdeaOut], status_code=201)
def keep_ideas(
    org_id: int,
    payload: list[IdeaCreate],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Takes a list, because the thing this exists to serve is "keep these three
    of the five you just generated" - one round trip, not three. A single idea
    is just a list of one.

    Titles are de-duplicated against what is already cached (case-insensitively,
    any status): re-running the generator on the same goal returns overlapping
    ideas, and an operator pressing Keep twice should not end up with the same
    idea listed twice.
    """
    get_owned_org(org_id, db, user)

    if not payload:
        raise HTTPException(status_code=400, detail="Send at least one idea")

    existing = {
        (title or "").strip().lower()
        for (title,) in db.query(Idea.title).filter(Idea.organization_id == org_id).all()
    }

    created: list[Idea] = []
    for item in payload:
        title = (item.title or "").strip()
        if not title or title.lower() in existing:
            continue
        existing.add(title.lower())
        idea = Idea(
            organization_id=org_id,
            title=title[:255],
            angle=item.angle,
            rationale=item.rationale,
            goal=item.goal,
            channel=item.channel,
            source=item.source if item.source in ("ai", "operator") else "ai",
            status="kept",
        )
        db.add(idea)
        created.append(idea)

    db.commit()
    for idea in created:
        db.refresh(idea)
    return created


@router.patch("/{idea_id}", response_model=IdeaOut)
def update_idea(
    org_id: int,
    idea_id: int,
    payload: IdeaUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_owned_org(org_id, db, user)
    idea = _owned_idea(org_id, idea_id, db)

    fields = payload.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] not in STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(STATUSES)}")

    # Linking an idea to a piece means it has been written. Inferring the status
    # here rather than trusting every caller to send both keeps the two facts
    # from disagreeing: an idea with a content_item_id but still marked "kept"
    # would sit in the Ideas in-queue forever, which is precisely the silent
    # stall the queues exist to prevent.
    if fields.get("content_item_id") and "status" not in fields:
        fields["status"] = "drafted"

    for key, value in fields.items():
        setattr(idea, key, value)

    db.commit()
    db.refresh(idea)
    return idea


@router.delete("/{idea_id}", status_code=204)
def delete_idea(
    org_id: int,
    idea_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A real delete, for an idea that should not have been recorded at all.
    Dismissing is the ordinary "no" - it keeps the row so the same suggestion
    does not come back - so this is the rarer, deliberate one."""
    get_owned_org(org_id, db, user)
    idea = _owned_idea(org_id, idea_id, db)
    db.delete(idea)
    db.commit()
    return None
