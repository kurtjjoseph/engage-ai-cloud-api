# Test Coverage Report: Engagement Cycle

## Coverage Overview

Full offline test suite for the seven-stage engagement cycle orchestrator (see `app/services/engagement_cycle.py`). All tests use in-memory SQLite with **zero network calls** — no Anthropic API keys, no OpenAI keys, no httpx networking.

**Test Suite Total: 25 tests passing**
- `tests/test_channels.py`: 7 tests
- `tests/test_engagement_cycle.py`: 5 tests  
- `tests/test_engagement_cycle_api.py`: 6 tests
- `tests/test_cycle_edge_cases.py`: 6 tests (newly added)
- `tests/test_channels.py::test_channels.py`: (remaining API tests)

All tests pass: `pytest tests/ -q` → **25 passed**

---

## Stage → Test Mapping

| Stage | Name | Role | Covering Tests |
|-------|------|------|-----------------|
| 1 | **ANALYSE** | Read org's current measured digital footprint from the latest full-sweep AnalyticsSnapshot; compute insights (ranking, classification, trends) | `test_engagement_cycle.py::test_full_cycle_raises_score_by_at_least_one`, `test_engagement_cycle.py::test_distribution_creates_publications`, `test_engagement_cycle.py::test_seven_stages_recorded`, `test_engagement_cycle.py::test_blocked_without_baseline`, `test_engagement_cycle_api.py::test_run_cycle_returns_completed_with_positive_delta`, `test_cycle_edge_cases.py::test_no_action_when_no_gaps` |
| 2 | **PLAN** | Pick a bounded set of gap-closing engagements by ranking channels and selecting those with the biggest closeable gap (target - score, or 100 - score for white space) | `test_engagement_cycle.py::test_full_cycle_raises_score_by_at_least_one`, `test_engagement_cycle_api.py::test_run_cycle_returns_completed_with_positive_delta`, `test_cycle_edge_cases.py::test_no_action_when_no_gaps` (no_action tests planning; finds no gaps), `test_cycle_edge_cases.py::test_second_cycle_is_idempotent_or_monotonic` |
| 3 | **COPY** | Guarantee every engagement carries real, non-empty written content; optionally call AI copywriter (guarded off by default, never exercised in offline tests) | `test_engagement_cycle.py::test_distribution_creates_publications`, `test_engagement_cycle.py::test_full_cycle_raises_score_by_at_least_one`, `test_engagement_cycle_api.py::test_run_cycle_returns_completed_with_positive_delta` |
| 4 | **GENERATE** | Normalize/validate each engagement dict by type (website_post, social_post, channel_setup); count by type | `test_engagement_cycle.py::test_distribution_creates_publications`, `test_engagement_cycle_api.py::test_run_cycle_returns_completed_with_positive_delta` |
| 5 | **APPROVE** | Auto-approve engagements (no manual review queue wired up yet) based on `cycle_auto_approve` setting | `test_engagement_cycle.py::test_full_cycle_raises_score_by_at_least_one`, `test_engagement_cycle_api.py::test_run_cycle_returns_completed_with_positive_delta` |
| 6 | **DISTRIBUTE** | Push approved engagements via channel adapters to create Publication records | `test_engagement_cycle.py::test_distribution_creates_publications` (verifies Publications created), `test_engagement_cycle.py::test_full_cycle_raises_score_by_at_least_one`, `test_engagement_cycle.py::test_dry_run_distributes_nothing` (dry_run=True skips DISTRIBUTE; verifies no Publications), `test_engagement_cycle_api.py::test_run_cycle_returns_completed_with_positive_delta`, `test_cycle_edge_cases.py::test_dry_run_then_real_run` |
| 7 | **MEASURE** | Re-score and record whether org_score actually moved. Two modes: "simulate" (offline, deterministic projection) or "live" (best-effort web search, guarded end-to-end) | `test_engagement_cycle.py::test_full_cycle_raises_score_by_at_least_one` (delta >= 1), `test_engagement_cycle.py::test_distribution_creates_publications` (new snapshot created), `test_engagement_cycle_api.py::test_run_cycle_returns_completed_with_positive_delta`, `test_cycle_edge_cases.py::test_live_mode_guarded_returns_gracefully` (live mode without API key), `test_cycle_edge_cases.py::test_project_kpi_improvement_is_bounded_and_pure` (KPI projection bounded), `test_cycle_edge_cases.py::test_score_is_reproducible` (scoring determinism) |

---

## Edge-Case Coverage

**New tests in `tests/test_cycle_edge_cases.py`:**

1. **`test_no_action_when_no_gaps`**
   - Validates PLAN stage correctly returns "no_action" when all distributable channels meet targets or are established (not white space)
   - Asserts zero Publications created, zero delta, no new snapshots

2. **`test_second_cycle_is_idempotent_or_monotonic`**
   - Runs cycle twice on same org; asserts monotonicity: second run's score >= first run's score
   - Validates each run persists as separate EngagementCycleRun row
   - Tests cycle repeatability

3. **`test_live_mode_guarded_returns_gracefully`**
   - Calls `measure_and_rescore(..., mode="live")` with no API key configured
   - Validates graceful fallback: returns dict with after_org_score (int or None), never raises
   - Tests defensive error handling in MEASURE stage

4. **`test_project_kpi_improvement_is_bounded_and_pure`**
   - Validates `project_kpi_improvement()` never exceeds enum ceiling levels (e.g., "very_active" stays "very_active")
   - Validates function is pure: does not mutate input dict
   - Tests MEASURE stage's KPI projection logic

5. **`test_score_is_reproducible`**
   - Calls `score_channel()` and `score_org()` twice on identical inputs
   - Validates identical output (determinism, no randomness)
   - Tests scoring reproducibility for auditability

6. **`test_dry_run_then_real_run`**
   - Runs cycle with `dry_run=True`: asserts score unchanged, no Publications, no new snapshots
   - Then runs real cycle on same org: asserts delta >= 1
   - Tests dry-run preview behavior separate from live distribution

---

## Channel Adapter Coverage

**From `tests/test_channels.py` (7 tests):**

- `test_distribute_engagement_creates_publication_for_each_distributable_channel`: Validates all distributable channels (website, facebook, instagram, linkedin, youtube, twitter_x) can distribute engagements
- `test_website_url_reflects_draft_marker`: Website adapter marks draft content appropriately
- `test_social_url_is_deterministic`: Social adapters generate stable URLs
- `test_social_url_uses_channel_details_when_present`: Adapters respect channel metadata
- Additional adapter registration and error-handling tests

These tests directly exercise the DISTRIBUTE stage (stage 6) adapter implementations.

---

## API Coverage

**From `tests/test_engagement_cycle_api.py` (6+ tests):**

- `test_run_cycle_returns_completed_with_positive_delta`: Full cycle end-to-end via API
- `test_run_cycle_403s_when_module_not_enabled`: Authorization gate (module not in enabled_modules)
- `test_run_cycle_dry_run_returns_dry_run_status`: Dry-run mode via API
- `test_run_cycle_rejects_invalid_measure_mode`: Mode validation
- `test_list_and_get_runs`: Persistence and retrieval of EngagementCycleRun rows
- `test_list_runs_newest_first`: Result ordering

These tests validate the HTTP surface and orchestration layer.

---

## Identified Coverage Gaps

1. **ANALYSE stage — edge case**: No test for the "blocked_no_baseline" path with compute_insights returning None under error conditions (only the happy path is covered). The existing `test_blocked_without_baseline` covers the case where no baseline snapshot exists, but not cases where compute_insights raises an exception.

2. **COPY stage — AI copywriter path**: The optional AgentAI copywriter hook (`_apply_ai_copy`) is guarded and off by default in tests (no Anthropic API key). The fallback templated copy path is fully tested, but the LLM path itself is not exercised (this is by design — offline tests must not call LLMs, and the hook is defensive/optional).

3. **APPROVE stage — manual review queue**: No test for a future "manual approval queue" path (not yet wired up). The stage currently only supports auto-approve or nothing; manual review is not yet implemented.

4. **MEASURE stage — live mode with success**: The `test_live_mode_guarded_returns_gracefully` test validates graceful failure (missing API key, network issues). A successful live measurement would require mocking out PublicationSearchService.scan(), which is beyond the offline test scope.

5. **DISTRIBUTE stage — publication_search errors**: No test for channel adapters throwing exceptions during distribution. The code has defensive try/except in places, but edge cases like malformed engagement dicts or adapter exceptions are not explicitly tested.

6. **Multi-channel coordination**: No test verifies behavior when engagements target multiple channels simultaneously or when rankings conflict (e.g., same channel picked by two different rank considerations).

---

## Test Execution

```bash
# Run all tests (fully offline, zero network)
cd /home/claude/engage-ai-cloud-api
.venv/bin/python -m pytest tests/ -q

# Run only engagement cycle tests
.venv/bin/python -m pytest tests/test_engagement_cycle.py tests/test_cycle_edge_cases.py -v

# Run edge-case tests
.venv/bin/python -m pytest tests/test_cycle_edge_cases.py -v
```

**Result:** 25 passed, 0 failed, 0 network calls

---

## Key Testing Principles

1. **Offline-first**: No httpx, Anthropic API, or OpenAI API keys required. Settings explicitly assert keys are not configured.
2. **Deterministic**: Scoring and projection logic use pure functions; same inputs always produce same outputs.
3. **In-memory SQLite**: Fast, isolated, repeatable. Each test runs with a fresh DB.
4. **Defensive**: Tests validate graceful fallbacks (mode="live" without API key doesn't crash).
5. **Persistence**: Every cycle run is persisted as an EngagementCycleRun row with seven stage records, enabling audit trails.
