# Engage AI analytics — reliability: current vs. authenticated

**Why this doc exists:** the monthly per-org analytics report is the retention
artifact of the Vision Outreach Media (VOM) model — clients stay because they see
recurring value. So an analysis that swings for measurement reasons rather than
real change is a business risk, not just a bug. This doc records how the analysis
is acquired today, where it's unreliable, the authenticated strategy that fixes it
long-term, and the Phase-1 hardening already shipped.

## 1. How it works today (file-cited)

- **Acquisition — one mechanism for all 8 channels:** a single Anthropic
  (`claude-sonnet-5`) call with the `web_search` tool, in
  `app/services/analytics_search.py` (`AnalyticsSearchService.scan`). The model
  must *publicly rediscover* each channel every scan. There is **no** OAuth, **no**
  official-platform API, **no** scraping, **no** third-party data provider (no such
  SDKs in `requirements.txt`). `channel_details` on the org (a per-channel URL/handle)
  is injected as ground truth to anchor the search, but a channel still has to be
  found publicly.
- **Scoring — deterministic and correct:** `app/services/analytics_scoring.py` is
  pure code (no LLM, no randomness, no time dependency). Given the same KPIs it
  always produces the same score. **This is not the problem and is left untouched.**
- **Storage:** `AnalyticsSnapshot` (`app/models/entities.py`) freezes each scan's
  KPIs *and* computed scores per org over time, so history/trends are possible.
- **Delivery:** the WordPress plugin (`app/plugin_template/engage-ai/`) renders a
  radar chart + score/trend tables; the CLI prints JSON. No hosted dashboard.

## 2. Where it's unreliable — acquisition, not scoring

1. **Non-determinism.** The web-search research pass isn't repeatable: the same org
   scanned twice can yield different KPIs (varying search snippets, a platform
   blocking the fetch that day, the model finding or missing a channel), so the
   score moves for measurement reasons. Corrosive for a *trend*.
2. **Fabricated cliffs (the worst one).** A not-found/blocked channel was scored a
   hard **0** — every channel's rubric returns 0 unless `found`/`indexed` is true —
   with no last-known fallback, so a transient miss looked identical to real white
   space and pulled the org average down.
3. **Identity guessing.** Without a pinned handle the model can attribute a
   same-named but different org's data (a documented recurring failure).
4. **No cadence.** There was **no scheduled analytics scan at all** — the 24h
   scheduler only ran agent/engagement-cycle jobs, so the "monthly report" had to be
   triggered by hand.
5. **One-shot.** No retry; a transient overload/timeout failed the whole scan.

The correct read: **web-search acquisition is right for prospects** (you can't
authenticate into a stranger's channels) and as a fallback — the mistake was using
that same best-effort tool for a *client's recurring, trend-based report*.

## 3. Current vs. authenticated

| | Current (web-search) | Authenticated strategy |
|---|---|---|
| Source, all 8 channels | one `web_search` LLM call | official APIs on channels the client owns; web-search only as fallback |
| Input determinism | non-deterministic | deterministic (same endpoint each time) |
| Not-found handling | hard 0 (cliff) → **now held (Phase 1)** | real 0 only when truly absent; hold + stale flag on a miss |
| Identity | guessed unless `channel_details` set | pinned per client at onboarding |
| Cost per scan | a live LLM web-search call | ~free API calls; LLM only for narrative |
| Prospects (no access) | ✓ correct use | ✗ — web-search stays for prospects |
| Scoring | deterministic ✓ | same scorer, unchanged |
| Build cost | ~0 (exists) | OAuth per platform + API keys; Meta/LinkedIn app review |

## 4. Phased roadmap

**Phase 1 — harden the existing engine (SHIPPED, no new integrations).** See §5.

**Phase 2 — authenticated per-channel adapters.** Introduce an acquisition-adapter
interface (`authenticated → web-search fallback` per channel), mirroring the
existing distribution `register_adapter` pattern in
`app/services/channels/registry.py`. Priority order by ease + value:
- **Website** — VOM hosts it; use the site itself + the analytics already stood up.
- **Google Business Profile API**, **YouTube Data API** — open, reliable, exact numbers.
- **Meta Graph API** (Facebook/Instagram) — client connects their Page during onboarding.
- **LinkedIn / X** — restrictive/paid; keep best-effort and lightly weighted so their
  flakiness can't swing the org average.
The deterministic scorer is unchanged; each adapter just fills the same KPI fields
from an authenticated source instead of web search.

## 5. Phase 1 changes (shipped)

- **Hold-last-known instead of a fabricated 0** — `app/services/analytics_reconcile.py`
  (pure, unit-tested). A channel not found this scan that had a real prior value is
  carried forward, marked `stale` + `last_measured_at`, so the org average no longer
  cliffs on a transient miss. Wired into `app/routers/analytics.py:_execute_scan`,
  which recomputes `org_score` from the reconciled channels.
- **Anomaly flag** — a channel score swinging ≥ `analytics_anomaly_delta` (default 25)
  sets `needs_review` on the snapshot; surfaced so a monthly send can hold for a quick
  human check.
- **Freshness surfaced** — `app/services/analytics_insights.py` (and the schemas)
  return per-channel `stale`/`last_measured_at`/`needs_review`/`review_reason` and a
  snapshot-level `needs_review`, so plugin/CLI can show an honest "not refreshed" /
  "verify" badge.
- **Retry + backoff** — `AnalyticsSearchService._create_with_retry` retries transient
  Anthropic errors (429/5xx/timeout/connection) instead of failing the whole scan.
- **Scheduled monthly scan (the missing cadence)** — `app/services/scheduler.py`'s
  `analytics_scan` job runs a staggered full sweep over analytics-enabled orgs every
  `analytics_scan_interval_hours` (default 720h ≈ monthly). This is what makes the
  trend accrue on its own.
- **Identity anchoring** — reuses the existing `channel_details` field (already
  injected as ground truth); no new field added.

## 6. Making it instant (perceived + real)

The analysis should feel instant. Two layers, both now supported by the API:

**Real speed (shipped, this repo).** A full sweep no longer runs as one 30-90s
sequential call. `_execute_scan` fans out **one parallel web-search call per
channel** (a thread pool over the existing scoped-scan primitive) and **streams**
each scored channel onto the pending snapshot as it lands. Wall-clock drops to
roughly the slowest single channel (a few seconds), and per-channel budgets are
smaller (`analytics_search.py` scales `max_uses`/`max_tokens` by channel count).
The summary is now built deterministically in code (no extra LLM call).

**Perceived-instant (spec for the WordPress plugin, `~/Downloads/engage-ai-wordpress`).**
The dashboard must never show a blank "scan in progress" screen:
1. **Always render the last COMPLETE snapshot immediately** from `GET .../analytics/insights`
   (a DB read - already instant). This is the default view; data is on screen in one round-trip.
2. **Keep it warm.** The scheduled monthly scan (`services/scheduler.py`) means a
   recent snapshot is always waiting, so the client basically never triggers a scan themselves.
3. **Refresh in place, don't block.** On "Run new scan", keep the current numbers
   visible with a subtle per-channel "updating…" state; poll
   `GET .../analytics/snapshot/{id}` (new endpoint) and swap each channel's value in
   as its parallel sub-scan lands - the radar fills in live. Only channels still
   in flight show the spinner.
4. **Show freshness.** Add an "as of {created_at}" line, and render the per-channel
   `stale` / `last_measured_at` / `needs_review` flags (a small "not refreshed" /
   "verify" badge) already returned by `/insights`.

Net effect: opening the dashboard is instant (cached read); the rare manual refresh
is fast (parallel) and progressive (streamed), never a frozen 30-90s spinner.

## 7. Security note (action required)

The working-tree `.env` holds a **live Anthropic API key** (and OpenAI/JWT/Stripe
values) in plaintext. Good news: `.env` **is** gitignored and **not** tracked by git,
so it was never committed to history. But the key is real and sat in a local file
that surfaced in test output (the pre-existing
`tests/test_engagement_cycle.py::test_offline_no_network_dependency` fails precisely
because a real key is present) and could leak via a backup or a zipped working tree.
**Prudent action: rotate that Anthropic key (and the other secrets) as hygiene**, and
keep secrets out of any shipped artifact. No git-history scrub is needed since it was
never committed.

Also note (separate from analytics reliability): social distribution
(`app/services/channels/social.py`), engagement-cycle copy
(`app/services/engagement_cycle.py`), and `cycle_measure_mode` default to
**simulated/placeholder** — a demo must not present a simulated projection as a
measured result.
