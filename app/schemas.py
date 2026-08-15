from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class OrganizationCreate(BaseModel):
    name: str
    org_type: str = "church"
    mission: str | None = None
    tone: str | None = "warm, clear, inviting, faith-centered"
    audience: str | None = None
    colors: dict | None = None
    ministries: list[dict] | None = None
    recurring_schedule: list[dict] | None = None
    locations: list[dict] | None = None
    speakers: list[dict] | None = None
    website_url: str | None = None
    # Per-channel profile URL/handle, e.g. {"facebook": "https://...", "twitter_x": "@handle"}.
    channel_details: dict | None = None
    # Goal-setting for the engagement_growth agent niche - included here (not
    # a separate schema) so the existing generic PATCH /organizations/{id}
    # can set them like any other org field.
    target_org_score: int | None = None
    target_channel_scores: dict | None = None


class OrganizationOut(OrganizationCreate):
    id: int
    enabled_modules: list[str] | None = None
    agent_profiles: dict | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class ModulesUpdate(BaseModel):
    enabled_modules: list[str]


class TicketOut(BaseModel):
    id: int
    niche: str
    title: str
    rationale: str | None
    risk: str
    status: str
    payload: dict | None
    generated_content: dict | None
    decision_note: str | None
    created_at: datetime
    decided_at: datetime | None

    class Config:
        from_attributes = True


class TicketDecision(BaseModel):
    decision: str = Field(description="approve | reject | redirect")
    note: str | None = None


class AssistantAskIn(BaseModel):
    question: str = Field(min_length=1)


class AssistantAskOut(BaseModel):
    question: str
    answer: str


class AgentRunOut(BaseModel):
    id: int
    niche: str
    summary: str | None
    tickets_created: int
    ran_at: datetime

    class Config:
        from_attributes = True


class EventCampaignRequest(BaseModel):
    organization_id: int
    event_name: str
    date: str
    time: str | None = None
    location: str | None = None
    speaker: str | None = None
    description: str | None = None
    target_audience: str | None = None
    desired_action: str | None = "Attend the event"


class AnnouncementsRequest(BaseModel):
    organization_id: int
    service_date: str
    speaker: str | None = None
    events: list[dict] = []
    birthdays: list[dict] = []
    special_notes: list[str] = []


class SermonEngagementRequest(BaseModel):
    organization_id: int
    title: str
    sermon_text: str
    bible_translation: str | None = "HSV"
    target_audience: str | None = "church members and visitors"


class ContentOut(BaseModel):
    id: int
    content_type: str
    title: str
    input_payload: dict | None = None
    output_payload: dict
    # The Campaign this piece was built as part of, or null - which is most of
    # the library, since anything made one-off in the Content Studio has none.
    campaign_id: int | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class AnalyticsSnapshotOut(BaseModel):
    id: int
    is_baseline: bool
    summary: str | None
    channels: list[dict] | None
    org_score: int | None
    org_score_breakdown: list[dict] | None
    sources: list[str] | None
    requested_channels: list[str] | None
    # "pending" while the scan runs in the background, "failed" if it raised,
    # null/"complete" once real data is written - see AnalyticsSnapshot.status.
    status: str | None
    # True when reconciliation held a channel forward or flagged a swing - a
    # human should eyeball before this snapshot is trusted/sent (see
    # AnalyticsSnapshot.needs_review). Null on old rows = no review needed.
    needs_review: bool | None = None
    # Wall-clock seconds the scan took (operator dashboard's measurement time).
    duration_seconds: float | None = None
    # All data that went into this scan's request (org context sent to the model,
    # pinned channel handles, model id, channels, tool) - for the scan-details page.
    request_context: dict | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ChannelRankingEntry(BaseModel):
    rank: int
    channel: str
    score: int
    classification: str  # white_space | new | growing | saturated | healthy
    score_breakdown: list[dict]
    notes: str | None = None
    # Reliability flags (services/analytics_reconcile.py): stale = value carried
    # forward because the web search didn't re-find the channel this run;
    # last_measured_at = when it was really last measured; needs_review +
    # review_reason = why a human should verify this channel.
    stale: bool | None = None
    last_measured_at: datetime | None = None
    needs_review: bool | None = None
    review_reason: str | None = None


class AnalyticsInsightsOut(BaseModel):
    latest_snapshot_id: int
    latest_created_at: datetime
    org_score: int | None
    org_score_breakdown: list[dict] | None
    baseline_org_score: int | None
    ranking: list[ChannelRankingEntry]
    summary: str | None
    # Roll-up: true if the latest sweep or any channel needs a human check
    # before this report is trusted/sent unattended.
    needs_review: bool | None = None


class PublicationCreate(BaseModel):
    channel: str
    url: str
    label: str | None = None
    content_item_id: int | None = None
    published_at: datetime | None = None


class PublicationOut(PublicationCreate):
    id: int
    organization_id: int
    # True if recorded by a simulated distribution adapter (no real post went
    # out). Null on manually-registered publications and old rows.
    simulated: bool | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class PublicationSnapshotOut(BaseModel):
    id: int
    publication_id: int
    kpis: dict | None
    notes: str | None
    score: int | None
    score_breakdown: list[dict] | None
    sources: list[str] | None
    scanned_at: datetime

    class Config:
        from_attributes = True


class PublicationWithLatestOut(PublicationOut):
    """Publication + its most recent snapshot, if any - what the WordPress
    'mark as published' list actually needs to render without a second
    round-trip per item."""
    latest_snapshot: PublicationSnapshotOut | None = None


class EngagementTypeRankingEntry(BaseModel):
    content_type: str
    avg_score: float
    publication_count: int
    scanned_publication_count: int


class RunCycleRequest(BaseModel):
    # "simulate" (deterministic offline projection) or "live" (best-effort
    # real re-measurement) - None defers to settings.cycle_measure_mode.
    measure_mode: str | None = None
    # True plans and generates copy but never distributes or re-measures.
    dry_run: bool = False
    # None defers to settings.cycle_auto_approve.
    auto_approve: bool | None = None


class EngagementCycleRunOut(BaseModel):
    id: int
    organization_id: int
    before_org_score: int | None
    after_org_score: int | None
    delta: int | None
    measure_mode: str
    status: str
    stages: list[dict] | None
    engagement_count: int
    publication_ids: list[int] | None
    # Honest self-report of what in this run is stubbed/simulated vs real (see
    # EngagementCycleRun.simulation) - so a preview cycle is never read as real
    # posting or a real measured score.
    simulation: dict | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class IdeaCreate(BaseModel):
    title: str
    angle: str | None = None
    rationale: str | None = None
    goal: str | None = None
    channel: str | None = None
    # "ai" for a generated idea the operator kept, "operator" for one typed in.
    source: str = "ai"


class IdeaUpdate(BaseModel):
    """Every field optional: this backs both the small edits (retitle, retarget
    a channel) and the status moves (keep -> dismissed, keep -> drafted), and a
    caller should never have to resend fields it is not changing."""

    title: str | None = None
    angle: str | None = None
    rationale: str | None = None
    goal: str | None = None
    channel: str | None = None
    status: str | None = None


class IdeaOut(BaseModel):
    id: int
    organization_id: int
    title: str
    angle: str | None = None
    rationale: str | None = None
    goal: str | None = None
    channel: str | None = None
    status: str
    source: str
    content_item_id: int | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class PostingTargets(BaseModel):
    """Posts per week per channel, e.g. {"instagram": 3}. A channel left out
    has no target rather than a target of zero - the Calendar reports those
    differently, and conflating them would invent a shortfall nobody set."""

    targets: dict[str, int]


class ChatbotPassage(BaseModel):
    """One retrieved passage from the site's own Site Brain index."""

    title: str = ""
    heading: str = ""
    url: str = ""
    passage: str = ""


class ChatbotFaq(BaseModel):
    question: str
    answer: str


class ChatbotGrounding(BaseModel):
    """Everything the website knows about itself for this one turn.

    Retrieval happens on the WordPress side, where the Site Brain index lives
    in-process. The site sends what it found; the protocol that decides how the
    model may use it stays here, so a compromised site cannot rewrite the rules
    the assistant answers under."""

    persona: str = ""
    facts: dict[str, str] = Field(default_factory=dict)
    faqs: list[ChatbotFaq] = Field(default_factory=list)
    passages: list[ChatbotPassage] = Field(default_factory=list)
    escalation: str = ""
    block_pricing: bool = False


class ChatbotTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=4000)


class ChatbotReplyIn(BaseModel):
    messages: list[ChatbotTurn] = Field(min_length=1, max_length=16)
    language: str = "en"
    grounding: ChatbotGrounding = Field(default_factory=ChatbotGrounding)


class ChatbotReplyOut(BaseModel):
    reply: str
