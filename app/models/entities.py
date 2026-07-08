from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, JSON
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

    owner = relationship("User", back_populates="organizations")
    content_items = relationship("ContentItem", back_populates="organization")
    tickets = relationship("Ticket", back_populates="organization")
    agent_runs = relationship("AgentRun", back_populates="organization")


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
