"""Tests for the scan reconciliation (app/services/analytics_reconcile.py) - the
"hold last known instead of a fabricated 0" reliability fix. Pure function, no
DB or network, so these are fast and deterministic.
"""

from datetime import datetime

from app.services.analytics_reconcile import KNOWN_CHANNELS, reconcile_channels

PRIOR_DATE = datetime(2026, 6, 1, 12, 0, 0)
DELTA = 25


def _entry(channel, score, **kw):
    return {"channel": channel, "kpis": kw.get("kpis", {"found": score > 0}), "notes": kw.get("notes", ""),
            "score": score, "score_breakdown": kw.get("breakdown", [])}


def test_holds_last_known_when_channel_not_found_this_scan():
    # youtube really existed last month (40); this scan failed to find it (0).
    fresh = [_entry("youtube", 0)] + [_entry(c, 30) for c in KNOWN_CHANNELS if c != "youtube"]
    prior = {"youtube": _entry("youtube", 40)}

    final, needs_review = reconcile_channels(fresh, prior, PRIOR_DATE, True, DELTA)
    by = {e["channel"]: e for e in final}

    assert by["youtube"]["score"] == 40, "held the prior value instead of the fabricated 0"
    assert by["youtube"]["stale"] is True
    assert by["youtube"]["needs_review"] is True
    assert by["youtube"]["last_measured_at"] == PRIOR_DATE.isoformat()
    assert needs_review is True


def test_org_average_does_not_cliff_on_a_transient_miss():
    # All 8 at 40 last time; this scan misses youtube (0). Held -> average stays 40.
    fresh = [_entry("youtube", 0)] + [_entry(c, 40) for c in KNOWN_CHANNELS if c != "youtube"]
    prior = {c: _entry(c, 40) for c in KNOWN_CHANNELS}

    final, _ = reconcile_channels(fresh, prior, PRIOR_DATE, True, DELTA)
    avg = sum(e["score"] for e in final) / len(final)
    assert avg == 40, "held value keeps the org average from dropping"


def test_genuine_whitespace_stays_zero():
    # Never had a linkedin; still none. Real 0, not held, not flagged.
    fresh = [_entry(c, 30) for c in KNOWN_CHANNELS if c != "linkedin"] + [_entry("linkedin", 0)]
    prior = {"linkedin": _entry("linkedin", 0)}  # prior also 0

    final, needs_review = reconcile_channels(fresh, prior, PRIOR_DATE, True, DELTA)
    by = {e["channel"]: e for e in final}
    assert by["linkedin"]["score"] == 0
    assert not by["linkedin"].get("stale")
    assert needs_review is False


def test_flags_a_large_swing_but_keeps_the_fresh_value():
    # facebook dropped 60 -> 20 (found, so not held) - suspicious, flag for review.
    fresh = [_entry("facebook", 20)] + [_entry(c, 30) for c in KNOWN_CHANNELS if c != "facebook"]
    prior = {"facebook": _entry("facebook", 60)}

    final, needs_review = reconcile_channels(fresh, prior, PRIOR_DATE, True, DELTA)
    by = {e["channel"]: e for e in final}
    assert by["facebook"]["score"] == 20, "kept the fresh value (it was found)"
    assert by["facebook"]["needs_review"] is True
    assert "60" in by["facebook"]["review_reason"] and "20" in by["facebook"]["review_reason"]
    assert needs_review is True


def test_small_move_is_not_flagged():
    fresh = [_entry("facebook", 55)] + [_entry(c, 30) for c in KNOWN_CHANNELS if c != "facebook"]
    prior = {"facebook": _entry("facebook", 60)}  # 5-point move < delta
    final, needs_review = reconcile_channels(fresh, prior, PRIOR_DATE, True, DELTA)
    by = {e["channel"]: e for e in final}
    assert not by["facebook"].get("needs_review")
    assert needs_review is False


def test_full_sweep_synthesizes_omitted_channels_as_zero():
    # Model only returned website; a full sweep must still cover all 8.
    fresh = [_entry("website", 50)]
    final, _ = reconcile_channels(fresh, {}, None, True, DELTA)
    assert len(final) == len(KNOWN_CHANNELS)
    by = {e["channel"]: e for e in final}
    assert by["website"]["score"] == 50
    assert all(by[c]["score"] == 0 for c in KNOWN_CHANNELS if c != "website")


def test_scoped_scan_only_touches_requested_channels():
    # A youtube-only rescan that missed youtube, with a prior full sweep value.
    fresh = [_entry("youtube", 0)]
    prior = {"youtube": _entry("youtube", 40)}
    final, needs_review = reconcile_channels(fresh, prior, PRIOR_DATE, False, DELTA)
    assert len(final) == 1, "scoped scan does not invent the other channels"
    assert final[0]["score"] == 40 and final[0]["stale"] is True
    assert needs_review is True


def test_chained_hold_preserves_original_measurement_date():
    # A held entry from last month is held again; last_measured_at keeps pointing
    # to the ORIGINAL measurement, not just the prior (already-stale) snapshot.
    original_iso = datetime(2026, 5, 1).isoformat()
    prior_held = {**_entry("youtube", 40), "stale": True, "last_measured_at": original_iso}
    fresh = [_entry("youtube", 0)] + [_entry(c, 30) for c in KNOWN_CHANNELS if c != "youtube"]
    final, _ = reconcile_channels(fresh, {"youtube": prior_held}, PRIOR_DATE, True, DELTA)
    by = {e["channel"]: e for e in final}
    assert by["youtube"]["last_measured_at"] == original_iso
