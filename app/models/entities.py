from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    organizations = relationship("Organization", back_populates="owner")


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(255))
    org_type: Mapped[str] = mapped_column(String(100), default="church")
    mission: Mapped[str | None] = mapped_column(Text, nullable=True)
    tone: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    colors: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ministries: Mapped[list | None] = mapped_column(JSON, nullable=True)
    recurring_schedule: Mapped[list | None] = mapped_column(JSON, nullable=True)
    locations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    speakers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Anchors the analytics web search (services/analytics_search.py) to the
    # right organization instead of guessing from name alone - optional, but
    # search precision drops a lot without it for common org names.
    website_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Per-channel profile URL/handle the org has actually set up, e.g.
    # {"facebook": "https://facebook.com/...", "twitter_x": "@handle", ...}.
    # Same anchoring purpose as website_url above but per channel - passed
    # into the analytics search context (routers/analytics.py) so a channel
    # with a known handle gets verified/searched directly instead of guessed
    # from the org name. Optional; a channel with nothing set here just falls
    # back to name-based search like before this field existed.
    channel_details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Modular activation: which capabilities this org has turned on, e.g.
    # ["engagement", "agent:youtube_channel", "agent:coaching"]. "engagement"
    # gates the existing campaign generators below; each "agent:<niche>"
    # entry gates one autonomous-agent niche (see services/agent_ai.py).
    # An org can run several agent niches at once - each gets its own ticket
    # queue, distinguished by Ticket.niche / AgentRun.niche.
    enabled_modules: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Per-niche agent memory, keyed by niche string, e.g.
    # {"youtube_channel": {"topic": ..., "posting_cadence": ...}}. Kept
    # separate from the church-wide fields above since niches have
    # different, free-form profile shapes; tone/audience above are used as
    # fallback context if a niche's own profile is still thin.
    agent_profiles: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Goal-setting for the "engagement_growth" agent niche (services/agent_ai.py)
    # to work toward. None = no target set, that niche just reports state
    # without a gap to close.
    target_org_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # {"youtube": 80, "instagram": 60, ...} - per-channel targets, optional
    target_channel_scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    owner = relationship("User", back_populates="organizations")
    content_items = relationship("ContentItem", back_populates="organization")
    tickets = relationship("Ticket", back_populates="organization")
    agent_runs = relationship("AgentRun", back_populates="organization")
    analytics_snapshots = relationship("AnalyticsSnapshot", back_populates="organization")
    publications = relationship("Publication", back_populates="organization")


class ContentItem(Base):
    __tablename__ = "content_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    content_type: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(255))
    input_payload: Mapped[dict] = mapped_column(JSON)
    output_payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    organization = relationship("Organization", back_populates="content_items")
    publications = relationship("Publication", back_populates="content_item")


class Ticket(Base):
    """One proposed or completed unit of agent work, scoped to one niche
    within one organization. status flow: backlog -> proposed -> approved |
    rejected (redirect sends it back to backlog with a decision_note)."""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    niche: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(255))
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk: Mapped[str] = mapped_column(String(20), default="low")
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Finished deliverable the API generated after a "high risk" ticket got
    # approved (see decide_ticket in routers/agents.py) - "low" risk tickets
    # already carry their draft in payload per BASE_PROTOCOL, so this stays
    # null for those. Also null until an approval happens, and holds
    # {"error": "..."} instead of raising if generation itself failed, since
    # a generation hiccup shouldn't block the approval.
    generated_content: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    organization = relationship("Organization", back_populates="tickets")


class AgentRun(Base):
    """Log of one check-in cycle for one niche within one organization."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    niche: Mapped[str] = mapped_column(String(100), index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tickets_created: Mapped[int] = mapped_column(Integer, default=0)
    ran_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    organization = relationship("Organization", back_populates="agent_runs")


class AnalyticsSnapshot(Base):
    """One web-search-based scan of an organization's public digital
    footprint (services/analytics_search.py). The first snapshot for an org
    is flagged as its baseline - later snapshots are meant to be compared
    against it, so "is engagement actually improving" has a fixed reference
    point instead of just comparing against whatever the last scan happened
    to say."""

    __tablename__ = "analytics_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # list of {"channel": str, "kpis": {...fixed fields, see analytics_scoring.CHANNEL_KPI_SCHEMA...},
    # "notes": str, "score": int, "score_breakdown": [...], "pages": [...]? }
    # "pages" only appears on the "website" entry when include_pages was set -
    # see services/analytics_search.py's PAGE_RANKING_ADDENDUM. "score"/"score_breakdown"
    # are computed in code (services/analytics_scoring.py), never by the model, and are
    # stored at write time so historical scores stay reproducible even if the scoring
    # rubric changes later.
    channels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # {"score": int, "breakdown": [{"channel": str, "score": int}, ...]} - straight
    # average across every known channel, including 0 for channels with no presence.
    org_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    org_score_breakdown: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # list of URLs the search drew on, for the admin to verify claims against
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # None = full 8-channel sweep (the default); otherwise the specific
    # channels this scan was scoped to, e.g. ["website"].
    requested_channels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # "pending" while the scan runs in the background (see routers/analytics.py's
    # run_scan/_execute_scan), "failed" if it raised, "complete" once scored and
    # written. Old rows predating this column get NULL via sync_missing_columns() -
    # every reader treats NULL the same as "complete" (status not in the two
    # in-progress-or-broken values), so nothing needs a backfill.
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Set by the scan reconciliation (services/analytics_reconcile.py) when a
    # channel's value had to be carried forward (a not-found this scan that had
    # a real prior value - "hold last known" instead of a fabricated 0) or a
    # channel score swung beyond the anomaly threshold. Signals a human should
    # eyeball this report before it's trusted/sent. Old rows get NULL, treated
    # the same as "no review needed" (like status above).
    needs_review: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Wall-clock seconds the scan took (set in routers/analytics._execute_scan).
    # Powers the operator dashboard's "average measurement time". Null on old rows.
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Everything that went INTO this scan's request, captured at attempt time
    # (routers/analytics.run_scan and the scheduler): the org context sent to the
    # model (name, type, website_url, channel_details/handles, mission, audience,
    # locations), the model id, which channels were requested, the tool + mode.
    # Set at creation so even a failed/pending attempt has a full details page.
    request_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    organization = relationship("Organization", back_populates="analytics_snapshots")


class Publication(Base):
    """One specific published item on one specific channel - e.g. the
    WordPress post a campaign generated, or a social post the admin
    manually posted and pasted the URL back for. This is the anchor that
    lets performance be tracked per-item over its own lifecycle, not just
    folded into the channel-wide aggregate (AnalyticsSnapshot).

    content_item_id is nullable: a publication can be linked back to the
    ContentItem it came from (for the engagement-type ranking - see
    services/analytics_scoring.py) or stand alone if it wasn't generated by
    Engage AI's campaign generators."""

    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    content_item_id: Mapped[int | None] = mapped_column(ForeignKey("content_items.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(100), index=True)
    url: Mapped[str] = mapped_column(String(1000))
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # True when this was recorded by a SIMULATED distribution adapter (no real
    # post actually went out - see services/channels). The API surfaces this so
    # a simulated/draft publication is never mistaken for a real live post.
    simulated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    organization = relationship("Organization", back_populates="publications")
    content_item = relationship("ContentItem", back_populates="publications")
    snapshots = relationship("PublicationSnapshot", back_populates="publication")


class PublicationSnapshot(Base):
    """One performance check of one Publication, at one point in time -
    same score/breakdown/never-invent-a-number philosophy as
    AnalyticsSnapshot, just scoped to a single URL instead of a whole
    channel (services/publication_search.py, analytics_scoring.score_publication)."""

    __tablename__ = "publication_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"), index=True)
    kpis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # null for unscannable channels (email/whatsapp)
    score_breakdown: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    publication = relationship("Publication", back_populates="snapshots")


class EngagementCycleRun(Base):
    """Log of one full, autonomous "engagement cycle" run for one
    organization (services/engagement_cycle.py) - the seven-stage
    ANALYSE -> PLAN -> COPY -> GENERATE -> APPROVE -> DISTRIBUTE -> MEASURE
    orchestration that goes from the org's current analytics footprint to a
    fresh set of distributed engagements and a re-measured org_score.

    before/after/delta let "did this cycle actually move the needle" be a
    stored, auditable fact instead of something recomputed differently each
    time someone asks. status distinguishes a normal completed run from a
    dry run (planned but never distributed) or a run that couldn't start at
    all (no baseline analytics scan yet) or that had nothing to do.
    """

    __tablename__ = "engagement_cycle_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    before_org_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    after_org_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "simulate" (deterministic, offline projection) or "live" (best-effort
    # real web-search re-measurement) - see services/cycle_measurement.py.
    measure_mode: Mapped[str] = mapped_column(String(20), default="simulate")
    # "completed" | "dry_run" | "blocked_no_baseline" | "no_action"
    status: Mapped[str] = mapped_column(String(30), default="completed")
    # list of {"stage": int, "name": str, "detail": str, "count": int}, one
    # entry per stage actually executed, in order - an audit trail of what
    # this run did without needing to re-derive it from side effects.
    stages: Mapped[list | None] = mapped_column(JSON, nullable=True)
    engagement_count: Mapped[int] = mapped_column(Integer, default=0)
    # Publication.id list this run's DISTRIBUTE stage created, if any.
    publication_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Honest self-report of what in this run is stubbed/simulated vs real, so no
    # consumer mistakes a preview for real work. Shape:
    # {"is_simulated": bool, "content": "placeholder_templated"|"ai_generated",
    #  "distribution": "simulated"|"real"|"mixed"|"none",
    #  "measurement": "simulated_projection"|"live", "notes": str}
    simulation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
