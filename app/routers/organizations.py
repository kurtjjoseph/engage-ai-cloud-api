from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.config import settings
from app.db.session import get_db
from app.deps import get_current_user
from app.models.entities import (
    AgentRun,
    AnalyticsSnapshot,
    ContentItem,
    EngagementCycleRun,
    Organization,
    Publication,
    PublicationSnapshot,
    Ticket,
    User,
)
from app.schemas import ModulesUpdate, OrganizationCreate, OrganizationOut
from app.services.plugin_packager import build_personalized_zip
from app.services.security import create_long_lived_token

router = APIRouter(prefix="/organizations", tags=["organizations"])

# Recognized "agent:<niche>" values - kept here (not enforced server-side
# beyond documentation) so NICHE_PROMPTS in services/agent_ai.py stays the
# single source of truth for what a niche actually does.
KNOWN_AGENT_NICHES = [
    "physical_product", "reselling", "youtube_channel", "answer_man",
    "local_service", "app_builder", "ugc_creator", "coaching",
]


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


# Every table that hangs off an organization, so a delete or a merge touches
# all of them and never orphans a row (there are no DB-level cascades - these
# FKs are plain integer columns).
_ORG_CHILD_MODELS = (ContentItem, Ticket, AgentRun, AnalyticsSnapshot, Publication, EngagementCycleRun)


def _normalize_domain(url: str | None) -> str | None:
    """Bare lowercase host for same-site comparison: strips scheme, leading
    'www.', any path/port, so https://www.Foo.org/ and http://foo.org match."""
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    host = (urlparse(raw).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _delete_org_cascade(db: Session, org: Organization) -> None:
    """Deletes an org and every row that references it. Grandchildren
    (PublicationSnapshot -> Publication) go first, then the direct children,
    then the org itself. Caller commits."""
    pub_ids = [p.id for p in db.query(Publication.id).filter(Publication.organization_id == org.id).all()]
    if pub_ids:
        db.query(PublicationSnapshot).filter(PublicationSnapshot.publication_id.in_(pub_ids)).delete(synchronize_session=False)
    for model in _ORG_CHILD_MODELS:
        db.query(model).filter(model.organization_id == org.id).delete(synchronize_session=False)
    db.delete(org)


def _merge_org_into(db: Session, source: Organization, target: Organization) -> None:
    """Reassigns every child row of `source` to `target`, then deletes the now
    empty `source`. Used when the same site turns out to have two org records
    (see site_hello). Caller commits."""
    for model in _ORG_CHILD_MODELS:
        db.query(model).filter(model.organization_id == source.id).update(
            {model.organization_id: target.id}, synchronize_session=False
        )
    db.delete(source)


@router.patch("/{org_id}", response_model=OrganizationOut)
def update_organization(org_id: int, payload: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Partial update for org-wide fields (mission, tone, audience,
    website_url, etc.) - only OrganizationCreate's known fields are applied,
    so a caller can safely send just the one or two fields it's changing."""
    org = get_owned_org(org_id, db, user)
    allowed = set(OrganizationCreate.model_fields.keys())
    for key, value in payload.items():
        if key in allowed:
            setattr(org, key, value)
    db.commit()
    db.refresh(org)
    return org


@router.patch("/{org_id}/modules", response_model=OrganizationOut)
def update_modules(org_id: int, payload: ModulesUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Sets the org's full list of activated modules, e.g.
    ["engagement", "agent:youtube_channel", "agent:coaching"]. Replaces
    rather than merges, so the caller (the WordPress "Modules" checkboxes)
    can just send its current checked state."""
    org = get_owned_org(org_id, db, user)
    org.enabled_modules = payload.enabled_modules
    db.commit()
    db.refresh(org)
    return org


@router.delete("/{org_id}")
def delete_organization(org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Permanently removes a site the operator owns, along with all of its
    scans, agent runs, tickets, publications and content. Backs the console's
    "Delete site" control."""
    org = get_owned_org(org_id, db, user)
    name = org.name
    _delete_org_cascade(db, org)
    db.commit()
    return {"deleted": True, "id": org_id, "name": name}


@router.get("/{org_id}/plugin.zip")
def download_site_plugin(org_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """A ready-to-install plugin zip pre-connected to THIS site (base URL +
    long-lived token + org id baked in), so a site the operator added in the
    console can be wired up just by installing and activating - the same
    personalized package the public onboarding flow hands out, but scoped to
    an existing owned org. Fetched with the operator's bearer token by the
    console detail page, so it stays private (no public per-org link)."""
    org = get_owned_org(org_id, db, user)
    token = create_long_lived_token(str(user.id))
    zip_bytes = build_personalized_zip(settings.api_base_url, token, org.id)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="engage-ai.zip"'},
    )


class SiteHello(BaseModel):
    home_url: str
    admin_url: str | None = None


@router.post("/{org_id}/site-hello")
def site_hello(org_id: int, payload: SiteHello, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Called once by the plugin on its first run. Records where the plugin is
    actually installed (home_url) and, if the operator had already created a
    separate org record for the same site, merges the two so the site isn't
    tracked twice.

    Returns the canonical org id the plugin should use from now on - usually
    the one it called with, but the id of the surviving org if a merge happened
    (the plugin then repoints itself, see class-engageai-api-client.php)."""
    org = get_owned_org(org_id, db, user)
    domain = _normalize_domain(payload.home_url)

    # Is this site already known under a different org record (same owner)?
    duplicate = None
    if domain:
        for other in db.query(Organization).filter(
            Organization.owner_id == user.id, Organization.id != org.id
        ).all():
            if _normalize_domain(other.website_url) == domain:
                duplicate = other
                break

    if duplicate is None:
        if not org.website_url:
            org.website_url = payload.home_url.strip()
        db.commit()
        db.refresh(org)
        return {"organization_id": org.id, "merged": False, "merged_from": None}

    # Keep the record with the richer history as canonical (more analytics
    # snapshots wins; older id breaks a tie), fold the other into it, and make
    # sure the survivor carries the confirmed live URL.
    def _weight(o: Organization) -> tuple[int, int]:
        snaps = db.query(AnalyticsSnapshot).filter(AnalyticsSnapshot.organization_id == o.id).count()
        return (snaps, -o.id)  # more snapshots first, then smaller (older) id

    keep, drop = (org, duplicate) if _weight(org) >= _weight(duplicate) else (duplicate, org)
    if not keep.website_url:
        keep.website_url = payload.home_url.strip()
    _merge_org_into(db, drop, keep)
    db.commit()
    db.refresh(keep)
    return {"organization_id": keep.id, "merged": True, "merged_from": drop.id}
