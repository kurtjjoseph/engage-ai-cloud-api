from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, JSON
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
    # How often this org intends to post per channel, as posts per week, e.g.
    # {"instagram": 3, "facebook": 2, "website": 1}. Intent, not a schedule -
    # nothing publishes from it. The Calendar compares it against what is
    # actually planned so "Instagram is two short this week" is answerable;
    # without a target there is nothing to be short of. A channel absent from
    # this map simply has no target and is reported as such, never as zero.
    posting_targets: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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

    # Ground-truth facts the installed Engage AI plugin reports about its own
    # WordPress site (POST /organizations/{id}/site-report), e.g.
    # {"website_present": true, "published_posts": 42, "published_pages": 6,
    #  "reported_at": "..."}. A site running the plugin definitely exists and
    # knows its own real published-content count, so the analytics scan uses
    # these as authoritative for the "website" channel instead of a web-search
    # guess - a small/new site the search engine hasn't indexed (and that
    # web_fetch's SSRF gate refuses) otherwise scores 0 despite being live.
    site_facts: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Which workflow steps this org lets run without an operator pressing the
    # button, and how many items each may take per run (services/automation.py):
    #
    #   {"enabled": true,
    #    "steps": {"studio.check": {"enabled": true, "max_per_run": 20}, ...}}
    #
    # NULL means fully manual, which is what every org that existed before this
    # field means and what a new org gets - automation is opt-in per step, never
    # inherited and never on by default. The stored value is advisory only: it is
    # normalized through services/automation.settings_for() on every read, and a
    # gated step (anything that publishes, sends or spends) reads back disabled
    # no matter what this column says.
    automation: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    owner = relationship("User", back_populates="organizations")
    content_items = relationship("ContentItem", back_populates="organization")
    campaigns = relationship("Campaign", back_populates="organization")
    tickets = relationship("Ticket", back_populates="organization")
    agent_runs = relationship("AgentRun", back_populates="organization")
    analytics_snapshots = relationship("AnalyticsSnapshot", back_populates="organization")
    publications = relationship("Publication", back_populates="organization")


class PasswordResetToken(Base):
    """One single-use, time-limited password-reset token. Created by
    POST /auth/password-reset/request (emailed to the user via Brevo), consumed
    by POST /auth/password-reset/confirm. Random + unguessable + short-lived so
    a leaked/old link can't reset an account."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Campaign(Base):
    """One multi-piece campaign: a single goal and one big idea, executed as a
    scheduled set of pieces across several channels.

    The Content Studio builds ONE piece at a time. A campaign is the layer
    above it: it decides what a whole run should say, on which surfaces, on
    which days - and then hands each entry back to the studio's own passes to
    actually be written. So the pieces a campaign produces are ordinary
    ContentItems (see ContentItem.campaign_id): the Content Library, the
    render, the quality check and every publish path keep working on them
    untouched, and a campaign is only the plan that grouped them.

    The plan itself lives in `plan` rather than a second table, because an
    entry is edited (its date moved, its surface swapped, the whole entry
    dropped) far more often before it is built than after - and until it is
    built there is nothing to relate to. Shape, one dict per piece:

        {"index": 0, "surface": "instagram.carousel", "channel": "instagram",
         "role": "hook", "headline": ..., "angle": ..., "why": ...,
         "scheduled_on": "2026-08-12",
         "status": "planned"|"building"|"drafted"|"failed",
         "content_id": int|None, "error": str|None, "quality": {...}|None}
    """

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    # One of services/studio_formats.GOALS - the whole campaign shares it, which
    # is what makes the pieces read as one run rather than a pile of posts.
    goal: Mapped[str] = mapped_column(String(50), default="awareness")
    # The operator's own steer ("the autumn open evening", "we have 3 slots
    # left") - what the campaign is actually about.
    theme: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The single argument every piece serves, written by the planner.
    big_idea: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The channels the operator allowed the planner to use. None = all of them.
    channels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # "planned" (nothing written yet) | "building" | "ready" (every piece has a
    # draft) | "partial" (built, but something failed) | "archived".
    status: Mapped[str] = mapped_column(String(20), default="planned", index=True)
    plan: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # {"status": "running"|"done"|"failed"|"none", "built": int, "failed": int,
    #  "total": int, "started_at": iso, "finished_at": iso, "error": str|None}
    # Written by the background builder; a run still "running" long after it
    # started is reported failed, since background tasks don't survive a
    # redeploy and a stuck spinner is worse than a retry button.
    build: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    organization = relationship("Organization", back_populates="campaigns")
    content_items = relationship("ContentItem", back_populates="campaign")


class ContentItem(Base):
    __tablename__ = "content_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    content_type: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(255))
    input_payload: Mapped[dict] = mapped_column(JSON)
    output_payload: Mapped[dict] = mapped_column(JSON)
    # Set when this piece was built as part of a Campaign (routers/campaigns.py).
    # Null for everything made one-off in the Content Studio, which is most of
    # the library - so every reader must treat null as "not in a campaign"
    # rather than as an error.
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    organization = relationship("Organization", back_populates="content_items")
    campaign = relationship("Campaign", back_populates="content_items")
    publications = relationship("Publication", back_populates="content_item")


class MediaAsset(Base):
    """A generated media file (currently images from gpt-image-1) attached to a
    piece of content. Bytes are stored inline and served by GET
    /content/asset/{id}. Kept small/on-demand - only generated when the operator
    asks, and only when an image provider key is configured."""

    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    content_item_id: Mapped[int | None] = mapped_column(ForeignKey("content_items.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="image")  # image | video
    mime: Mapped[str] = mapped_column(String(100), default="image/png")
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
    # How this went out: "direct" (the org's own OAuth/token connection),
    # "postiz" (relayed through the org's Postiz workspace), or NULL for
    # everything recorded before this distinction existed.
    delivery: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # The relay's own id for this post, when it has one (a Postiz post id).
    # Keeps a queued post identifiable for reconciliation and cancellation.
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    # "published" | "queued" | "scheduled". A relayed post is accepted into a
    # queue and released afterwards, so "we sent it" and "it is live" are two
    # different facts and are not collapsed into one. NULL means published -
    # every direct adapter posts synchronously and only records on success.
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
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


class ChannelConnection(Base):
    """One organization's authenticated connection to one posting channel.

    This is what turns Engage AI from "drafts you copy-paste" into "drafts it
    can actually publish": the org's admin authorizes Engage AI once per
    channel (OAuth, or a pasted long-lived token where no provider app is
    registered yet), and the resulting credentials live here so the live
    adapters in services/channels/live.py can post on that channel's API.

    Tokens are ENCRYPTED at rest (services/crypto.py) and are never returned
    by any endpoint - the API only ever reports *that* a channel is connected,
    which account it is connected as, and when the token expires.

    Connecting a channel is not the same as consenting to autonomous posting:
    `auto_post` stays False until the admin explicitly turns it on, and the
    live adapters refuse a channel whose status isn't "connected".
    """

    __tablename__ = "channel_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    # One of services/channels/providers.PROVIDERS - "facebook", "instagram",
    # "linkedin", "youtube", "twitter_x", "google_business".
    channel: Mapped[str] = mapped_column(String(50), index=True)
    # The identity provider behind the channel ("facebook" backs both the
    # facebook and instagram channels; "google" backs youtube and
    # google_business), kept so a token's origin is unambiguous.
    provider: Mapped[str] = mapped_column(String(50))
    # "connected" | "expired" | "error" | "revoked". Only "connected" posts.
    status: Mapped[str] = mapped_column(String(20), default="connected", index=True)
    # "oauth" (authorization-code flow) or "manual_token" (admin pasted a
    # long-lived token, for providers whose app isn't registered yet).
    auth_method: Mapped[str] = mapped_column(String(20), default="oauth")
    # Who Engage AI is posting AS on this channel, for display + audit.
    account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scopes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Provider-specific posting target resolved at connect time, e.g.
    # {"page_id": ...} for facebook, {"ig_user_id": ...} for instagram,
    # {"author_urn": ...} for linkedin, {"location": "accounts/1/locations/2"}
    # for google_business. Kept out of the channel-agnostic columns above
    # because no two providers agree on what "where to post" means.
    target: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Explicit opt-in to autonomous posting on this channel. False = Engage AI
    # may only post when a human presses publish for a specific piece.
    auto_post: Mapped[bool] = mapped_column(Boolean, default=False)
    connected_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Last provider/refresh failure, surfaced verbatim so an admin can see why
    # a channel stopped working instead of a generic "disconnected".
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChannelAuthRequest(Base):
    """One in-flight OAuth authorization for one channel.

    Holds the CSRF `state` and the PKCE code_verifier server-side (rather than
    round-tripping them through the browser) so the callback can prove the code
    it receives belongs to an authorization this API actually started, for this
    organization, on behalf of this user. Single-use and short-lived.
    """

    __tablename__ = "channel_auth_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(50))
    state: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    code_verifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Where to send the admin back to once the callback completes (the WP
    # plugin's Channels page). Only http(s) URLs are accepted at creation.
    return_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PostizWorkspace(Base):
    """One organization's link to a Postiz workspace.

    The alternative to authorizing every platform directly (ChannelConnection):
    the org's accounts are connected inside Postiz, and Engage AI holds a single
    API key that can post to all of them. One workspace per organization - a key
    already scopes to exactly one Postiz workspace, so a second row would only
    ever be ambiguity about which one is meant.

    The key is ENCRYPTED at rest (services/crypto.py) and is never returned by
    any endpoint, exactly like a ChannelConnection token.
    """

    __tablename__ = "postiz_workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), index=True, unique=True
    )
    # Public-API root, normalized by services/channels/postiz.normalize_base_url.
    # Hosted Postiz is https://api.postiz.com/public/v1.
    base_url: Mapped[str] = mapped_column(String(500))
    # Where the workspace's own UI lives, when the operator supplied it - used
    # only to link an admin to a queued post. Optional: a missing app URL costs
    # a convenience link, never a publish.
    app_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "connected" | "error" | "revoked". Only "connected" posts.
    status: Mapped[str] = mapped_column(String(20), default="connected", index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    connected_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    channels = relationship(
        "PostizChannel", back_populates="workspace", cascade="all, delete-orphan"
    )


class PostizChannel(Base):
    """One account inside a Postiz workspace, mapped to an Engage AI channel.

    Rows are created by syncing the workspace (`GET /integrations`), not by
    hand: what Postiz has connected is the source of truth for what can be
    posted to. Two fields are Engage AI's own and survive a re-sync, because
    they are decisions a person made rather than facts Postiz reports:

    * `auto_post` - the same explicit opt-in ChannelConnection carries. Off by
      default; the engagement cycle will not touch a channel without it.
    * `settings` - provider settings the operator had to choose (a subreddit,
      a non-default YouTube visibility). Merged over the defaults in
      services/channels/postiz.default_settings.
    """

    __tablename__ = "postiz_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("postiz_workspaces.id"), index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    # The Engage AI channel this account posts to ("facebook", "tiktok", ...).
    # Not unique: an org can have two Instagram accounts in one workspace.
    channel: Mapped[str] = mapped_column(String(50), index=True)
    # Postiz's own ids: the integration id posts are addressed to, and the
    # platform identifier that becomes settings.__type on every post.
    integration_id: Mapped[str] = mapped_column(String(120), index=True)
    identifier: Mapped[str] = mapped_column(String(50))
    account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_picture: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Disabled inside Postiz (expired token, over plan limit). Refreshed on
    # every sync; a disabled channel is never posted to.
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Whether this is the account used when a post names only the channel.
    # Exactly one per channel per workspace; the first synced account wins
    # until an admin picks another.
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_post: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    workspace = relationship("PostizWorkspace", back_populates="channels")


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


class Idea(Base):
    """A kept idea, before it is any particular piece of content.

    The Content Studio has always been able to generate ideas, but nothing held
    on to them: the plugin cached a batch in a WordPress transient keyed by
    goal, so they expired, belonged to one site, and vanished the moment the
    operator changed their mind about the goal. An idea an operator liked on
    Tuesday could not be found again on Thursday.

    Kept here instead, so an idea outlives the transient, survives a site
    rebuild, and belongs to the organization rather than to one WordPress
    install. `content_item_id` is set when an idea is finally drafted, which is
    what makes "which ideas did we actually use?" answerable.
    """

    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    angle: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Why this idea is worth doing - the "so what", kept separate from the
    # angle so a list can show the pitch without the whole rationale.
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The business goal it was generated against (studio_formats.goals_catalog),
    # e.g. "attract". Free text rather than an enum: the catalog is allowed to
    # grow without a migration, and an unknown goal is displayable either way.
    goal: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Suggested channel, when the generator had an opinion. Never enforced -
    # the operator can draft any idea to any channel.
    channel: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # "kept" (default) | "drafted" | "dismissed". Dismissed ideas are retained
    # rather than deleted so the same idea is not proposed back a week later.
    status: Mapped[str] = mapped_column(String(20), default="kept", index=True)
    # "ai" when it came from the generator, "operator" when typed in by hand.
    source: Mapped[str] = mapped_column(String(20), default="ai")
    # Set once this idea has been turned into a draft, so the trail from idea
    # to published piece is not guesswork.
    content_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_items.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class AutomationRun(Base):
    """One pass of the queue-drainer (services/automation.py): what the workflow
    did on its own, when, and to which items.

    Kept for the same reason EngagementCycleRun is kept, only more so. When an
    operator presses a button they know what they asked for; when the workflow
    advances items by itself, this row is the only place the answer lives. So it
    records the item-level detail rather than a count - "3 processed" is not an
    answer to "what did it write last night", and a run that drafted the wrong
    idea has to be traceable back to the idea, not just to the hour.

    `steps` is a list, one entry per step considered - including the steps that
    were off and the ones that had nothing waiting. A step that did nothing is a
    fact worth storing: "the renders didn't happen" and "the renders were never
    attempted" are different problems and the operator can only tell them apart
    if the run says which one it was. Shape:

        {"key": "studio.check", "label": ..., "enabled": bool,
         "waiting": int, "attempted": int, "processed": int, "failed": int,
         "skipped_reason": str|None,
         "items": [{"ref": "content:12", "title": ..., "ok": bool,
                    "detail": str|None}]}
    """

    __tablename__ = "automation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    # "manual" (an operator pressed Run now) or "scheduled" (the interval job).
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    # "running" | "done" | "failed" | "dry_run" | "nothing_to_do" | "off"
    # "off" is a completed run that did nothing because automation is switched
    # off for the org - recorded rather than refused, so a scheduled sweep that
    # finds everything off still leaves evidence that it ran.
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    steps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    # Touched after every single item, so "is this run still alive" is answered
    # by whether it is still making PROGRESS rather than by how long it has been
    # going. A healthy sweep can legitimately run for a long time - a render is
    # minutes, and the caps allow several - but it is never quiet for long. Total
    # duration would either kill honest long runs or never catch a dead one.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
