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


class OrganizationOut(OrganizationCreate):
    id: int
    enabled_modules: list[str] | None = None
    agent_profiles: dict | None = None

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
    decision_note: str | None
    created_at: datetime
    decided_at: datetime | None

    class Config:
        from_attributes = True


class TicketDecision(BaseModel):
    decision: str = Field(description="approve | reject | redirect")
    note: str | None = None


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
    output_payload: dict

    class Config:
        from_attributes = True


class AnalyticsSnapshotOut(BaseModel):
    id: int
    is_baseline: bool
    summary: str | None
    channels: list[dict] | None
    sources: list[str] | None
    requested_channels: list[str] | None
    created_at: datetime

    class Config:
        from_attributes = True
