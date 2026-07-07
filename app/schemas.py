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


class OrganizationOut(OrganizationCreate):
    id: int

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
