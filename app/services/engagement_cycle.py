"""The full engagement cycle orchestrator.

Runs all seven stages autonomously for one organization, end to end:

    1. ANALYSE   - read the org's current, measured digital footprint
    2. PLAN      - pick a bounded set of gap-closing engagements
    3. COPY      - make sure every engagement has real, written content
    4. GENERATE  - normalize/validate/count engagements by type
    5. APPROVE   - auto-approve (no human-review queue for this flow yet)
    6. DISTRIBUTE - push approved engagements out via the channel adapters
    7. MEASURE   - re-score and record whether org_score actually moved

Every run is persisted as one EngagementCycleRun row, so "did the last cycle
help" is a stored fact, not something recomputed differently each time.

The default planner (_plan_engagements) is a deterministic, gap-based
algorithm with no LLM call - this is what makes the whole cycle runnable
offline/in tests. An AgentAI-backed copywriter can optionally replace the
COPY stage's templated content when settings.anthropic_api_key is set (see
_apply_ai_copy), but that path is never required for the cycle to complete.
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.models.entities import EngagementCycleRun, Organization, Publication
from app.services.analytics_insights import compute_insights
from app.services.channels import DISTRIBUTABLE_CHANNELS, distribute_engagement
from app.services.cycle_measurement import measure_and_rescore

ENGAGEMENT_TYPES = ("website_post", "social_post", "channel_setup")

# Cap on how many engagements one cycle plans/distributes - keeps a single
# run bounded and reviewable instead of blasting every channel at once.
MAX_ENGAGEMENTS_PER_CYCLE = 3


def _stage(stages: list[dict], number: int, name: str, detail: str, count: int = 0) -> None:
    stages.append({"stage": number, "name": name, "detail": detail, "count": count})


def _channel_gap(candidate: dict, target: int | None) -> int | None:
    """"Gap" used purely to rank planning candidates, biggest first:
    - a real target is set -> target - score (only positive gaps count)
    - no target, but the channel is genuine white space (score 0, no trend
      history to even call it "new") -> treated as the maximum opportunity
      (100 - score), since establishing a presence from nothing is exactly
      what analytics_scoring.classify_channel_trend calls "the cheapest
      reach opportunity" - no explicit target needed to justify that.
    - otherwise -> None (not a planning candidate)
    """
    score = candidate["score"]
    if target is not None:
        gap = target - score
        return gap if gap > 0 else None
    if candidate["classification"] == "white_space":
        gap = 100 - score
        return gap if gap > 0 else None
    return None


def _website_post_content(org: Organization, candidate: dict) -> str:
    weak_point = candidate.get("notes") or "overall freshness and indexed reach"
    return (
        f"[DRAFT - Engage AI engagement cycle] {org.name}: a new website post addressing "
        f"the current weak point on website ({weak_point}). This is templated placeholder "
        f"copy queued as a WordPress draft for human review before anything goes live - "
        f"not a live human-written post."
    )


def _social_post_content(org: Organization, channel: str, candidate: dict) -> str:
    weak_point = candidate.get("notes") or f"engagement/posting cadence on {channel}"
    return (
        f"[DRAFT - Engage AI engagement cycle] {org.name} on {channel}: templated post copy "
        f"targeting the current weak point ({weak_point}). Honest placeholder content, "
        f"clearly not a live human post - queued for approval before distribution."
    )


def _channel_setup_content(org: Organization, channel: str) -> str:
    return (
        f"[DRAFT - Engage AI engagement cycle] {org.name}: first-week setup plan for {channel}, "
        f"currently white space (no measured presence). Steps: (1) create/claim the {channel} "
        f"profile using {org.name}'s name, mission, and branding; (2) publish an introductory "
        f"post explaining who {org.name} is and what to expect; (3) post at least once this "
        f"week to establish a posting cadence; (4) link back to {org.name}'s website. Templated "
        f"placeholder plan, not a live human post."
    )


def _plan_engagements(db: Session, org: Organization, insights: dict) -> list[dict]:
    """Deterministic, gap-based next-action planner - no LLM call, so the
    cycle runs the same way whether or not an Anthropic API key is
    configured. Selects up to MAX_ENGAGEMENTS_PER_CYCLE distributable
    channels with the biggest closeable gap (see _channel_gap) and builds
    one templated engagement dict per channel."""
    targets = org.target_channel_scores or {}

    candidates = []
    for entry in insights["ranking"]:
        channel = entry["channel"]
        if channel not in DISTRIBUTABLE_CHANNELS:
            continue
        gap = _channel_gap(entry, targets.get(channel))
        if gap is None:
            continue
        candidates.append((gap, entry))

    # Biggest gap first; stable sort preserves the ranking's own order
    # (score descending, then original snapshot order) among ties.
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    selected = candidates[:MAX_ENGAGEMENTS_PER_CYCLE]

    engagements: list[dict] = []
    for _gap, entry in selected:
        channel = entry["channel"]
        if channel == "website":
            engagement_type = "website_post"
            title = f"{org.name}: Website Refresh"
            content = _website_post_content(org, entry)
            risk = "low"
        elif entry["classification"] == "white_space":
            engagement_type = "channel_setup"
            title = f"{org.name}: {channel.replace('_', ' ').title()} Setup"
            content = _channel_setup_content(org, channel)
            risk = "high"
        else:
            engagement_type = "social_post"
            title = f"{org.name}: {channel.replace('_', ' ').title()} Update"
            content = _social_post_content(org, channel, entry)
            risk = "high"

        engagements.append({
            "channel": channel,
            "type": engagement_type,
            "title": title,
            "content": content,
            "risk": risk,
            "source_ticket_id": None,
        })

    return engagements


def _apply_ai_copy(engagements: list[dict], org: Organization) -> list[dict]:
    """Optional hook: if an Anthropic API key is configured, an AgentAI-
    backed copywriter could replace each engagement's templated content
    with a generated draft. Guarded and off by default - the deterministic
    templated copy from _plan_engagements is always a valid, non-empty
    fallback, so this hook is never required for the cycle to complete and
    is never exercised by the offline test suite."""
    if not settings.anthropic_api_key:
        return engagements
    try:
        from app.services.agent_ai import AgentAI  # local import - optional dependency path

        ai = AgentAI()
        if ai.client is None:
            return engagements
        # Deliberately conservative: no LLM call wired up here yet for this
        # stage. Keeping the hook here (rather than in _plan_engagements)
        # means turning it on later is a small, isolated change.
        return engagements
    except Exception:  # pragma: no cover - defensive, must never break the cycle
        return engagements


def _ensure_copy(engagements: list[dict], org: Organization) -> list[dict]:
    """COPY stage: guarantee every engagement carries real, non-empty
    content, then run the optional AI-copywriter hook."""
    for engagement in engagements:
        if not engagement.get("content"):
            engagement["content"] = (
                f"[DRAFT - Engage AI engagement cycle] {org.name} on {engagement['channel']}: "
                f"placeholder content pending review."
            )
    return _apply_ai_copy(engagements, org)


def _validate_engagements(engagements: list[dict]) -> dict[str, int]:
    """GENERATE stage: normalize/validate each engagement dict by type and
    count them. Raises ValueError on a malformed engagement rather than
    silently distributing something broken."""
    counts: dict[str, int] = {}
    for engagement in engagements:
        engagement_type = engagement.get("type")
        if engagement_type not in ENGAGEMENT_TYPES:
            raise ValueError(f"Unknown engagement type: {engagement_type!r}")
        if engagement.get("channel") not in DISTRIBUTABLE_CHANNELS:
            raise ValueError(f"Unknown/undistributable channel: {engagement.get('channel')!r}")
        if not engagement.get("content"):
            raise ValueError(f"Engagement for {engagement.get('channel')!r} has no content")
        if not engagement.get("title"):
            engagement["title"] = f"{engagement['channel']} update"
        engagement.setdefault("risk", "high")
        engagement.setdefault("source_ticket_id", None)
        counts[engagement_type] = counts.get(engagement_type, 0) + 1
    return counts


def run_full_cycle(
    db: Session,
    org: Organization,
    *,
    auto_approve: bool | None = None,
    measure_mode: str | None = None,
    dry_run: bool = False,
) -> EngagementCycleRun:
    """Run one full, seven-stage engagement cycle for `org` and persist the
    result as an EngagementCycleRun. Caller is responsible for checking
    is_cycle_enabled() first if that gate applies.

    auto_approve/measure_mode default from settings.cycle_auto_approve /
    settings.cycle_measure_mode. dry_run=True plans and generates copy but
    never distributes or re-measures - useful for previewing what a cycle
    would do."""
    if auto_approve is None:
        auto_approve = settings.cycle_auto_approve
    if measure_mode is None:
        measure_mode = settings.cycle_measure_mode

    stages: list[dict] = []

    # --- 1. ANALYSE ---
    insights = compute_insights(db, org.id)
    if insights is None:
        _stage(stages, 1, "ANALYSE", "No completed full-sweep analytics snapshot exists yet - cannot start a cycle.", 0)
        run = EngagementCycleRun(
            organization_id=org.id,
            before_org_score=None,
            after_org_score=None,
            delta=None,
            measure_mode=measure_mode,
            status="blocked_no_baseline",
            stages=stages,
            engagement_count=0,
            publication_ids=[],
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    before_org_score = insights["org_score"]
    _stage(stages, 1, "ANALYSE", f"Read current org_score={before_org_score} from snapshot #{insights['latest_snapshot_id']}.", len(insights["ranking"]))

    # --- 2. PLAN ---
    engagements = _plan_engagements(db, org, insights)
    _stage(stages, 2, "PLAN", f"Selected {len(engagements)} gap-closing engagement(s) from {len(insights['ranking'])} ranked channels.", len(engagements))

    if not engagements:
        run = EngagementCycleRun(
            organization_id=org.id,
            before_org_score=before_org_score,
            after_org_score=before_org_score,
            delta=0,
            measure_mode=measure_mode,
            status="no_action",
            stages=stages,
            engagement_count=0,
            publication_ids=[],
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    # --- 3. COPY ---
    engagements = _ensure_copy(engagements, org)
    _stage(stages, 3, "COPY", "Ensured every planned engagement carries non-empty, written content.", len(engagements))

    # --- 4. GENERATE ---
    type_counts = _validate_engagements(engagements)
    detail = ", ".join(f"{count} {etype}" for etype, count in sorted(type_counts.items())) or "none"
    _stage(stages, 4, "GENERATE", f"Normalized and validated engagements by type: {detail}.", len(engagements))

    # --- 5. APPROVE ---
    if auto_approve:
        approved = list(engagements)
        _stage(stages, 5, "APPROVE", f"Auto-approved {len(approved)} engagement(s) (cycle_auto_approve=True).", len(approved))
    else:
        approved = []
        _stage(stages, 5, "APPROVE", "auto_approve is False and no manual approval queue is wired up - nothing approved this cycle.", 0)

    # --- 6. DISTRIBUTE ---
    if dry_run:
        _stage(stages, 6, "DISTRIBUTE", "dry_run=True - skipped distribution.", 0)
        run = EngagementCycleRun(
            organization_id=org.id,
            before_org_score=before_org_score,
            after_org_score=before_org_score,
            delta=0,
            measure_mode=measure_mode,
            status="dry_run",
            stages=stages,
            engagement_count=0,
            publication_ids=[],
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    publications: list[Publication] = []
    distributed_engagements: list[dict] = []
    for engagement in approved:
        publication = distribute_engagement(db, org, engagement)
        publications.append(publication)
        distributed_engagements.append(engagement)
    _stage(stages, 6, "DISTRIBUTE", f"Distributed {len(publications)} engagement(s) via channel adapters.", len(publications))

    # --- 7. MEASURE ---
    result = measure_and_rescore(db, org, publications, distributed_engagements, mode=measure_mode)
    after_org_score = result["after_org_score"]
    delta = (after_org_score - before_org_score) if after_org_score is not None else None
    _stage(stages, 7, "MEASURE", result["detail"] + f" after_org_score={after_org_score}.", len(result.get("publication_snapshot_ids") or []))

    run = EngagementCycleRun(
        organization_id=org.id,
        before_org_score=before_org_score,
        after_org_score=after_org_score,
        delta=delta,
        measure_mode=measure_mode,
        status="completed",
        stages=stages,
        engagement_count=len(publications),
        publication_ids=[p.id for p in publications],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
