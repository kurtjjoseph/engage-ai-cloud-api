"""Reconcile a fresh scan's channel results against the org's prior full-sweep,
so a non-deterministic web-search miss can't fabricate a trend cliff.

The scoring engine (analytics_scoring.py) is deterministic and correct; the
unreliability lives entirely upstream in acquisition - the single web-search
research pass can fail to *find* a channel that genuinely exists (a platform
blocked the fetch that day, a search snippet varied, the model missed it). The
old behaviour scored that as a hard 0 (every channel's rubric returns 0 unless
`found`/`indexed` is true), indistinguishable from real white space, and it
pulled the org average down - so a client's monthly trend line dropped for a
measurement reason, not a real one.

This module is a pure function (no DB, no network) so it's trivially testable:
given this scan's channels and the prior snapshot's channels, it decides per
channel whether to keep the fresh value, HOLD the last-known value (marking it
`stale`), or flag a suspicious swing - and reports whether the whole snapshot
`needs_review`.

Key fact the "found == score>0" logic relies on: every channel's rubric awards
a non-zero base the moment it's found (website indexed=25, google found=30,
youtube/socials/news found=20). So a channel score of exactly 0 means "not
found this scan" - never "found but empty". That makes `score == 0` a reliable
not-found signal without threading a separate flag through the model output.
"""

from datetime import datetime

from app.services.analytics_scoring import CHANNEL_KPI_SCHEMA, score_channel

KNOWN_CHANNELS = list(CHANNEL_KPI_SCHEMA.keys())


def _friendly(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d")
    return str(dt) if dt else "a previous scan"


def _iso(dt) -> str | None:
    if isinstance(dt, datetime):
        return dt.isoformat()
    return dt if dt else None


def reconcile_channels(
    fresh_channels: list[dict],
    prior_by_channel: dict[str, dict],
    prior_created_at,
    is_full_sweep: bool,
    anomaly_delta: int,
) -> tuple[list[dict], bool]:
    """Return (final_channels, needs_review).

    - fresh_channels: this scan's scored entries ({channel, kpis, notes, score, score_breakdown, ...}).
    - prior_by_channel: {channel: entry} from the most recent prior COMPLETE full-sweep snapshot (or {}).
    - prior_created_at: that prior snapshot's created_at (for stamping last_measured_at); may be None.
    - is_full_sweep: True for an 8-channel sweep (synthesize not-found 0s for channels the model omitted
      entirely, so the org average is over all channels); False for a scoped scan (only touch the
      channels actually requested/returned).
    - anomaly_delta: flag a channel whose score moved at least this much vs prior.

    Rules per channel:
      * fresh score 0 (not found) AND prior score > 0  -> HOLD the prior entry, mark stale + needs_review.
        (last_measured_at chains back to the original measurement date, not just the prior snapshot's.)
      * otherwise keep the fresh entry; if |fresh - prior| >= anomaly_delta, flag needs_review on it.
      * full sweep, channel missing from fresh AND no prior>0 -> synthesize a genuine not-found 0.
    """
    fresh_by_channel = {e.get("channel"): e for e in fresh_channels if e.get("channel")}

    if is_full_sweep:
        channels_to_consider = KNOWN_CHANNELS
    else:
        # A scoped scan only speaks to the channels it actually checked - don't
        # invent the other seven as 0s (that's score_org's job on a full sweep).
        channels_to_consider = [c for c in KNOWN_CHANNELS if c in fresh_by_channel]

    final: list[dict] = []
    needs_review = False

    for channel in channels_to_consider:
        fresh = fresh_by_channel.get(channel)
        fresh_score = (fresh or {}).get("score", 0) or 0
        prior = prior_by_channel.get(channel)
        prior_score = (prior or {}).get("score")

        if fresh_score == 0 and prior_score and prior_score > 0:
            # HOLD last known - a not-found on a channel that really existed last
            # time is almost always a transient acquisition miss, not the channel
            # vanishing. Carry the prior value so the org score doesn't cliff, but
            # mark it stale + needs_review so it's honest and gets eyeballed.
            held = dict(prior)
            held["stale"] = True
            held["last_measured_at"] = prior.get("last_measured_at") or _iso(prior_created_at)
            held["needs_review"] = True
            held["review_reason"] = (
                f"Not found in this scan; showing the last measured value "
                f"(from {_friendly(prior_created_at)}). Likely a transient lookup miss - verify."
            )
            final.append(held)
            needs_review = True
            continue

        if fresh is None:
            # Full sweep, channel omitted entirely and no prior to hold: a real 0.
            score, breakdown = score_channel(channel, {})
            final.append({"channel": channel, "kpis": {}, "notes": "", "score": score, "score_breakdown": breakdown})
            continue

        entry = dict(fresh)
        entry.pop("stale", None)
        entry.pop("last_measured_at", None)
        if prior_score is not None and abs(fresh_score - prior_score) >= anomaly_delta:
            entry["needs_review"] = True
            entry["review_reason"] = (
                f"Score moved {prior_score} -> {fresh_score} since the last scan "
                f"({_friendly(prior_created_at)}); verify before trusting this trend."
            )
            needs_review = True
        final.append(entry)

    return final, needs_review
