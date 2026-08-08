"""The Campaign Creator: one goal and one big idea -> a scheduled run of pieces.

The Content Studio answers "what should this post say?". A campaign answers the
question above it - "what should the next three weeks say, and in what order?" -
and that is a different job, because the value of a campaign is the sequence:

    a hook piece earns attention,
    a teaching piece spends it on something useful,
    a proof piece makes the claim believable,
    an offer piece asks,
    a recap piece catches everyone who missed the first four.

Post any one of those alone and it underperforms; posted in that order against
one argument they compound. So the planner is asked for the ARC, not for five
independent ideas - that is the only thing it does that studio.ideas() doesn't.

One model call produces the whole plan. Everything after it is deterministic:
surfaces are resolved against the real catalog (an invented surface id degrades
to an allowed one rather than failing the plan), the run is spread across the
operator's own date window, and duplicates are dropped. Writing each piece is
NOT done here - each planned entry is handed back to StudioService's own
surface-first passes (see routers/campaigns.py), so a campaign piece is written,
checked and rendered by exactly the same code as a one-off piece.
"""
import json
from datetime import date, timedelta

from anthropic import Anthropic

from app.config import settings
from app.services.claude_json import extract_json
from app.services.content_ideas import guidance_for
from app.services.studio_formats import goal_guidance, goal_label
from app.services.surfaces import (
    SURFACES,
    channel_label,
    resolve as resolve_surface,
)

# What a piece is FOR inside the arc. The planner picks one per piece; the copy
# pass is told the role, so a "proof" piece doesn't come back written like an
# "offer". Kept here rather than in surfaces.py because a role is a property of
# the campaign, not of the channel.
ROLES: dict[str, str] = {
    "hook": "Earn attention from people who don't know the organization yet. No ask.",
    "teach": "Give something genuinely useful away, so the audience learns the organization is worth listening to.",
    "proof": "Make the claim believable - a result, a story, a number, a named person.",
    "offer": "Make the ask, once, plainly, with one next step and nothing competing with it.",
    "answer": "Answer the objection or question that stops people acting.",
    "behind_scenes": "Show the people and the work behind the offer - the trust piece.",
    "recap": "Catch everyone who missed the run, and close it.",
}
DEFAULT_ROLE = "hook"

# The roles that are meant to ask for something. The quality check normally
# demands a call to action whenever the GOAL is leads/sales/attendance - which
# is right for a one-off piece, and wrong inside a campaign: a hook that opens
# with an ask is a worse hook, and the brief tells it so explicitly. Without
# this, every campaign for those goals would flag warnings on the pieces that
# are correctly not asking, which is how people learn to ignore warnings.
ASKING_ROLES: frozenset[str] = frozenset({"offer", "answer", "recap"})

MIN_PIECES = 3
MAX_PIECES = 12
DEFAULT_PIECES = 5
DEFAULT_WINDOW_DAYS = 14


class PlanFailed(RuntimeError):
    """A campaign that couldn't be planned, and WHY.

    Four different things fail here - no API key, the model call itself, a
    reply that isn't parseable JSON, and a reply that parses to nothing usable -
    and they need four different fixes. Reporting them all as "is
    ANTHROPIC_API_KEY configured?" sent the operator to check an environment
    variable that was already fine. The message is shown verbatim to the
    operator, so it says what happened and what to do about it."""


def _clean(value) -> str:
    return str(value or "").strip()


def as_date(value, fallback: date | None = None) -> date | None:
    """An ISO date, or the fallback. Never raises - a bad date in a plan is the
    operator's typo, not a reason to lose the whole plan."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_clean(value))
    except (TypeError, ValueError):
        return fallback


def window(starts_on, ends_on, today: date | None = None) -> tuple[date, date]:
    """The date window a campaign runs over, defaulted and always forwards."""
    today = today or date.today()
    start = as_date(starts_on, today) or today
    end = as_date(ends_on, start + timedelta(days=DEFAULT_WINDOW_DAYS)) or start
    if end < start:
        end = start
    return start, end


def _spread(count: int, start: date, end: date) -> list[date]:
    """`count` dates spread evenly across the window, inclusive of both ends.

    Evenly, not daily: a five-piece campaign over three weeks should breathe,
    and a five-piece campaign over four days should not pretend it can't run
    twice on one day."""
    if count <= 1:
        return [start]
    span = (end - start).days
    return [start + timedelta(days=round(span * index / (count - 1))) for index in range(count)]


class CampaignPlanner:
    def __init__(self) -> None:
        self.client = Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None

    def plan(
        self,
        org_context: dict,
        goal: str,
        theme: str | None,
        channels: list[str] | None,
        count: int,
        starts_on,
        ends_on,
        site_type: str | None,
        today: date | None = None,
    ) -> dict:
        """Turns a goal plus a theme into a named campaign: one big idea and
        `count` pieces, each already carrying its surface, its role in the arc
        and the day it goes out.

        Raises PlanFailed, with the actual reason, rather than returning an
        empty campaign - every failure here is one the operator can act on, but
        only if they are told which one it was."""
        count = max(MIN_PIECES, min(int(count or DEFAULT_PIECES), MAX_PIECES))
        start, end = window(starts_on, ends_on, today)
        if not self.client:
            raise PlanFailed(
                "No ANTHROPIC_API_KEY is set on the API, so nothing can be written. "
                "Set it in the Render dashboard and redeploy."
            )

        allowed = [s for s in SURFACES if not channels or s.channel in channels]
        if not allowed:
            allowed = list(SURFACES)
        menu = "\n".join(f"- {s.id}: {channel_label(s.channel)} {s.label} - {s.summary}" for s in allowed)
        roles = "\n".join(f"- {key}: {description}" for key, description in ROLES.items())
        days = (end - start).days + 1

        system = f"""You are Engage AI's campaign director. Plan ONE campaign of exactly {count} pieces that work as a sequence, not as {count} separate posts.

Business goal: {goal_label(goal)}.
{goal_guidance(goal)}
{guidance_for(site_type)}

The campaign runs from {start.isoformat()} to {end.isoformat()} ({days} days).

First decide the ONE argument the whole campaign makes - the "big idea". Every piece must serve it from a different angle. If you cannot say the big idea in one sentence, it is too vague to build on.

Then order the pieces so they compound: earn attention before spending it, prove the claim before making the ask, and make the ask only once. Each piece has a role:
{roles}

Each piece is published on ONE surface, by its exact id, from:
{menu}

Vary the surface across the campaign - the same channel twice is fine when the arc calls for it, the same surface three times is not. Give each piece a "day", an integer from 0 ({start.isoformat()}) to {days - 1} ({end.isoformat()}), in ascending order, spaced so the run breathes.

Ground everything in the organization's real context (name, mission, tone, audience, locations) and in the operator's theme. No generic marketing filler, and never invent a fact - a date, a price or a result you weren't given is a defect, not a detail.

Return ONLY valid JSON, no markdown fences, matching exactly:
{{"name": "the campaign name, a few words the operator would recognise",
  "big_idea": "the one argument every piece serves, one sentence",
  "audience": "who this campaign is aimed at, one sentence",
  "pieces": [{{"headline": "the piece's idea in one compelling line",
              "angle": "the specific take, one sentence",
              "why": "one sentence on why this piece belongs at this point in the arc",
              "role": "one key from the role list",
              "surface": "one id from the surface list",
              "day": 0}}]}}"""

        user = {"organization": org_context, "goal": goal, "theme": _clean(theme) or None,
                "site_type": site_type, "pieces": count,
                "window": {"starts_on": start.isoformat(), "ends_on": end.isoformat(), "days": days}}
        data = self._json_call(system, user, max_tokens=8192)

        items = self.shape_items(data.get("pieces"), allowed, count, start, end)
        if not items:
            raise PlanFailed(
                "The plan came back with no usable pieces. Try again, or give the "
                "campaign a more specific subject to work from."
            )
        return {
            "name": _clean(data.get("name")) or f"{goal_label(goal)} campaign",
            "big_idea": _clean(data.get("big_idea")),
            "audience": _clean(data.get("audience")),
            "goal": goal,
            "theme": _clean(theme),
            "starts_on": start.isoformat(),
            "ends_on": end.isoformat(),
            "items": items,
        }

    # ------------------------------------------------------------ deterministic
    def shape_items(self, raw_pieces, allowed, count: int, start: date, end: date) -> list[dict]:
        """Everything the model returned, made safe: real surfaces, real roles,
        no duplicates, and dates inside the operator's own window.

        Separate from plan() and pure, so the same guarantees apply when an
        operator edits a plan by hand as when the model wrote it."""
        allowed_ids = {s.id for s in allowed}
        fallback = allowed[0]
        seen: set[tuple[str, str]] = set()
        shaped: list[dict] = []

        for raw in raw_pieces or []:
            if not isinstance(raw, dict):
                continue
            headline = _clean(raw.get("headline"))
            if not headline:
                continue
            surface_id = _clean(raw.get("surface"))
            surface = resolve_surface(surface_id) if surface_id in allowed_ids else fallback
            key = (surface.id, headline.lower())
            if key in seen:
                continue
            seen.add(key)
            role = _clean(raw.get("role")).lower()
            shaped.append({
                "headline": headline,
                "angle": _clean(raw.get("angle")),
                "why": _clean(raw.get("why")),
                "role": role if role in ROLES else DEFAULT_ROLE,
                "surface": surface.id,
                "channel": surface.channel,
                "_day": self._day_of(raw, start, end),
            })
            if len(shaped) == count:
                break

        if not shaped:
            return []

        # The model's own ordering is the arc it just argued for, so sort by day
        # and lay the run out on real dates. A plan that came back with every
        # piece on the same day gets spread instead of all landing at once -
        # but a plan that deliberately doubles up on one day keeps its pacing.
        shaped.sort(key=lambda item: item["_day"])
        if len(shaped) > 1 and len({item["_day"] for item in shaped}) == 1:
            dates = _spread(len(shaped), start, end)
        else:
            dates = [start + timedelta(days=item["_day"]) for item in shaped]

        for index, (item, when) in enumerate(zip(shaped, dates)):
            item.pop("_day", None)
            item.update({"index": index, "scheduled_on": when.isoformat(),
                         "status": "planned", "content_id": None, "error": None, "quality": None})
        return shaped

    @staticmethod
    def _day_of(raw: dict, start: date, end: date) -> int:
        """Which day of the window this piece lands on.

        An already-scheduled piece wins over a day offset: a plan comes back
        from the model with "day" and goes back in from the operator with
        "scheduled_on", and re-shaping a saved plan must not silently reschedule
        a run the operator already arranged."""
        span = (end - start).days
        scheduled = as_date(raw.get("scheduled_on"))
        if scheduled is not None:
            return max(0, min((scheduled - start).days, span))
        try:
            day = int(raw.get("day"))
        except (TypeError, ValueError):
            return 0
        return max(0, min(day, span))

    # ------------------------------------------------------------------- util
    def _json_call(self, system: str, user: dict, max_tokens: int) -> dict:
        """One Claude call returning parsed JSON.

        Raises PlanFailed naming what went wrong instead of returning {}: the
        model refusing, the account being out of credit, a rate limit and a
        truncated reply are four different problems with four different fixes,
        and the operator can only act on the one they are actually having."""
        try:
            response = self.client.messages.create(
                model=settings.anthropic_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": json.dumps(user)}],
            )
        except Exception as exc:  # noqa: BLE001 - reported verbatim, not swallowed
            raise PlanFailed(
                f"The model call failed ({type(exc).__name__}): {str(exc)[:300]}"
            ) from exc

        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        if not text.strip():
            raise PlanFailed("The model returned nothing. Try again.")
        # A reply cut off at the token limit is the one parse failure with an
        # obvious cause, so it is named rather than reported as bad JSON.
        if getattr(response, "stop_reason", None) == "max_tokens":
            raise PlanFailed(
                "The plan was cut off before it finished. Try again with fewer pieces."
            )
        try:
            data = extract_json(text)
        except (json.JSONDecodeError, ValueError):
            raise PlanFailed(
                f"The model's reply couldn't be read as a plan. It began: {text.strip()[:160]}"
            ) from None
        if not isinstance(data, dict):
            raise PlanFailed("The model's reply wasn't a plan object. Try again.")
        return data


def role_brief(role: str) -> str:
    """The line handed to the copy pass so a piece is written for its place in
    the arc - the one thing a campaign piece knows that a one-off doesn't."""
    return ROLES.get(role, ROLES[DEFAULT_ROLE])


def role_expects_cta(role: str) -> bool:
    """Whether the quality check should hold THIS piece to having a call to
    action, given its place in the arc rather than the campaign's goal."""
    return (role or DEFAULT_ROLE) in ASKING_ROLES


def roles_catalog() -> list[dict]:
    return [{"key": key, "description": description} for key, description in ROLES.items()]
