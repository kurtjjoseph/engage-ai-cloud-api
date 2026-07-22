# Full Engagement Cycle: User Stories & Acceptance Criteria

## Feature Summary

The Full Engagement Cycle orchestrates a complete, end-to-end campaign lifecycle for organizations using Engage AI's engagement_growth module. Triggered on-demand or on a configured schedule, the feature runs seven autonomous stages—(1) scoring current analytics, (2) planning target gaps, (3) generating copy, (4) creating channel-specific content, (5) auto-approving low-risk items, (6) distributing to channels via reversible drafts (website) or pluggable adapters (social), and (7) re-measuring performance—in a single `run_full_cycle()` call. With a "simulate" mode for testing and a "live" mode for real distribution, it enables organizations to demonstrate measurable engagement improvement (org_score delta >= +1) between cycle runs, while respecting module gating, auditable decision logs, and safety defaults (WordPress drafts, simulated social posts).

---

## User Stories

### Story 1: Trigger Full Cycle On Demand
**As a** marketing manager,  
**I want** to run a complete engagement cycle with a single API call,  
**so that** I can test and verify the full workflow before scheduling it to run autonomously.

**Acceptance Criteria:**
- POST `/organizations/{org_id}/cycles/run` endpoint accepts optional `mode` param: "simulate" (default) or "live"
- Endpoint checks `is_module_enabled(org, "engagement_growth")` before starting; returns 403 if disabled
- Returns immediately with a `CycleRun` object (id, org_id, status="running", started_at, mode, stage_reports=[])
- Cycle executes in background; client polls GET `/organizations/{org_id}/cycles/{cycle_id}` for progress
- Given a valid org with engagement_growth enabled, when POST is called, then status 200 and cycle starts
- Given an org without engagement_growth enabled, when POST is called, then status 403 with clear error message

---

### Story 2: Schedule Autonomous Cycle Runs
**As a** system administrator,  
**I want** the cycle to run automatically on a configured schedule (e.g., daily at 9 AM),  
**so that** engagement measurement and content distribution are continuous without manual intervention.

**Acceptance Criteria:**
- Configuration flag `"cycle_enabled": true` in org's `enabled_modules` gates whether scheduler includes this org
- Scheduler calls `run_full_cycle(db, org, mode="simulate")` at configured interval (defined in Organization or settings)
- Each run creates a `CycleRun` record with auto-set created_at; no manual trigger required
- If previous cycle is still running, new trigger is queued (not dropped); queue max depth is configurable, defaults to 3
- Given a scheduled org with cycle_enabled=true, when interval fires, then new CycleRun starts automatically
- Given two back-to-back cycles, each produces separate records with distinct run timestamps

---

### Story 3: View Per-Stage Visibility & Reports
**As a** marketing manager,  
**I want** to see each stage of the cycle as it completes—what was analyzed, what was planned, what was approved—with timestamps and decision details,  
**so that** I understand where the cycle excels and where bottlenecks or failures occur.

**Acceptance Criteria:**
- GET `/organizations/{org_id}/cycles/{cycle_id}` returns:
  ```json
  {
    "id": 123,
    "org_id": 1,
    "started_at": "2026-07-17T14:00:00Z",
    "completed_at": "2026-07-17T14:15:00Z",
    "status": "complete|running|failed",
    "mode": "simulate|live",
    "stage_reports": [
      {
        "stage": "analyze_score",
        "status": "complete",
        "timestamp": "2026-07-17T14:00:15Z",
        "payload": { "org_score": 45, "org_score_previous": 42, ... }
      },
      {
        "stage": "plan_gaps",
        "status": "complete",
        "timestamp": "2026-07-17T14:00:45Z",
        "payload": { "gaps_identified": [...], "top_gap_channel": "facebook", "gap_size": 15 }
      },
      ...
    ]
  }
  ```
- Each stage report includes stage name, completion status, timestamp, and structured payload (payload shape varies by stage)
- Stage transitions are logged (e.g., "analyze_score" -> "plan_gaps") so pipeline order is auditable
- Given a completed cycle, when GET is called, then all 7 stage_reports are present with timestamps and payloads
- When a stage fails (e.g., network error in distribution), that stage shows status="failed" with error details; later stages do not run

---

### Story 4: Display Before/After Score & Delta
**As a** marketing manager,  
**I want** to see the org_score before the cycle started and after it completed, plus the delta between them,  
**so that** I can quantify whether the cycle actually improved engagement.

**Acceptance Criteria:**
- CycleRun includes fields: `org_score_before`, `org_score_after`, `score_delta` (computed as after - before)
- Score delta is immediately visible in the cycle report summary and in a historical list view
- GET `/organizations/{org_id}/cycles` returns list of all cycles with (org_id, started_at, org_score_before, org_score_after, score_delta, mode, status) for each
- score_before is captured from `compute_insights(db, org_id)["org_score"]` at cycle start (stage 1)
- score_after is captured from `compute_insights(db, org_id)["org_score"]` at cycle end (stage 7)
- Given a cycle that starts at score 42 and ends at score 44, then score_delta = +2 is displayed
- If score_after < score_before, delta is negative and displayed honestly (no hiding)

---

### Story 5: Simulate vs. Live Distribution Modes
**As a** marketing manager,  
**I want** to test the full cycle in "simulate" mode (no real posts created) before running it "live" with actual distribution,  
**so that** I can verify quality and strategy without committing to public posts.

**Acceptance Criteria:**
- All cycles run in either "simulate" (default) or "live" mode (specified at trigger time)
- In "simulate" mode:
  - Stage 6 (distribute) does not post to real channels; instead, it records would-be posts to a local `Publication` with channel="test_simulation" for audit
  - WordPress draft creation is skipped (no actual draft); distribution reports the intended post content in stage_report.payload without touching WordPress
  - Social adapter receives the payload but runs in dry-run mode, recording intent only
  - No external network calls occur; all operations are local
- In "live" mode:
  - Website content is published as a WordPress draft (reversible—admin can discard or publish later)
  - Social posts go through pluggable adapters (default: a safe simulated adapter that records a Publication; real adapters can be swapped in)
  - All posts are recorded as Publication records with channel and url; Publication.published_at is set
- Given a cycle run in "simulate" mode, when stage 6 completes, then no real posts exist and dry-run records exist only in the cycle report
- Given a cycle run in "live" mode, when stage 6 completes, then WordPress drafts exist and social Publication records exist with real URLs (or adapter-specific equivalent)
- Mode is set at trigger time and included in CycleRun.mode for audit

---

### Story 6: Module Gating & Safe Defaults
**As a** system operator,  
**I want** only organizations that have explicitly enabled the engagement_growth cycle to run it,  
**so that** no organization accidentally gets autonomous posting without opting in.

**Acceptance Criteria:**
- A new org has `"engagement_growth" in org.enabled_modules` default to `false`
- Only when `is_module_enabled(org, "engagement_growth")` returns `true` does the cycle start
- If cycle is triggered on a disabled org, POST `/organizations/{org_id}/cycles/run` returns 403 with detail: "Module 'engagement_growth' is not enabled for this organization. Enable it via PATCH /organizations/{org_id}/modules first."
- Scheduler skips orgs where engagement_growth is not in enabled_modules; no error, no retry, silent skip
- Given an org with engagement_growth not enabled, when scheduler fires, then no cycle starts for that org
- PATCH `/organizations/{org_id}/modules` can toggle engagement_growth on or off; enabling it does not auto-start a cycle (next schedule or manual trigger starts it)

---

### Story 7: Auto-Approve Low-Risk Tickets
**As a** system designer,  
**I want** stage 5 to automatically approve "low-risk" tickets (like website content_idea tickets) without human review,  
**so that** the cycle can demonstrate end-to-end automation without being gated by manual approval.

**Acceptance Criteria:**
- Stage 5 (auto-approve) queries all tickets created in stage 3 for this cycle's run
- For each ticket with risk="low":
  - Set ticket.status = "approved" immediately (no decision_note required)
  - Increment auto_approved counter in stage_report.payload
- For each ticket with risk="high":
  - Leave ticket.status = "proposed" for human review later
  - Increment awaiting_approval counter in stage_report.payload
- stage_report.payload includes { "auto_approved": int, "awaiting_approval": int, "total_tickets": int }
- Given stage 3 produces 2 low-risk tickets, when stage 5 runs, then both are marked approved and auto_approved=2
- Given stage 3 produces 1 high-risk ticket, when stage 5 runs, then it stays proposed and awaiting_approval=1

---

### Story 8: Distribute via Channel Adapters
**As a** marketing manager,  
**I want** stage 6 to send approved content to each channel using a pluggable adapter pattern,  
**so that** I can use the default simulated adapter, swap in a real social media posting adapter, or add future custom adapters.

**Acceptance Criteria:**
- Distribute stage loads the channel adapter for each approved ticket's channel (e.g., "facebook_adapter", "wordpress_adapter")
- Adapter interface: `adapter.post(org_id, content_payload, mode="simulate"|"live") -> { "url": str, "success": bool, "notes": str }`
- Default adapters:
  - `wordpress_adapter`: writes drafts to configured WordPress site (live mode); records intent only (simulate mode)
  - `facebook_adapter`, `instagram_adapter`, `linkedin_adapter`, `twitter_x_adapter`: all default to simulated mode (records intent, no real posting)
- Each successful post creates a Publication record (organization_id, channel, url, label, published_at)
- stage_report.payload includes { "posted": [{"channel": str, "url": str, "success": true}], "failed": [{"channel": str, "reason": str, "success": false}] }
- Given a ticket with payload {"action_type": "content_idea", "channel": "facebook", "content": "..."}, when stage 6 runs in live mode, then facebook_adapter.post() is called and a Publication is created
- Given simulate mode, when stage 6 runs, then no real posts are created; Publications are recorded only with note="simulated" or dry-run flag

---

### Story 9: Re-Measure Performance & Close Cycle
**As a** marketing manager,  
**I want** stage 7 to re-scan analytics, recompute the org_score, and confirm whether the cycle achieved the goal (org_score delta >= +1),  
**so that** I can see quantified proof of engagement improvement before the cycle ends.

**Acceptance Criteria:**
- Stage 7 (measure_performance) calls `compute_insights(db, org_id)` to get the new org_score
- Compares org_score_after to org_score_before (captured in stage 1)
- stage_report.payload includes:
  ```json
  {
    "org_score_before": 42,
    "org_score_after": 44,
    "score_delta": 2,
    "delta_goal": 1,
    "goal_achieved": true,
    "channel_scores": [{"channel": "facebook", "score_before": 30, "score_after": 35, "delta": 5}, ...]
  }
  ```
- If goal_achieved is false, cycle still completes (stage_report.status = "complete"); delta is auditable even if < 1
- CycleRun.org_score_after and .score_delta are set from stage 7 results
- Given a cycle that achieves +2 delta, then goal_achieved=true and score_delta=2 are recorded
- Given a cycle that achieves +0 delta, then goal_achieved=false; cycle still completes and delta is visible

---

## Acceptance Criteria (Cross-Cutting)

### Testability & Measurement

- **Simulate-mode demonstration run:** A test can call `POST /cycles/run?mode=simulate`, poll until completion, and assert `score_delta >= 1` (or `score_delta > 0` depending on test data). This is the primary measurable success criterion; it must be demonstrable offline without hitting real web APIs.
- **No network calls in tests:** Stage 1 (analyze_score) uses cached or mocked `compute_insights()` in tests; stage 6 (distribute) uses a mock adapter (simulate mode by default) that records intent without posting; stage 7 (measure_performance) uses a mock re-scan or fixed test data.
- **All 7 stages run:** POST `/cycles/run` starts and completes all 7 stages (analyze_score, plan_gaps, create_copy, generate_content, auto_approve, distribute, measure_performance) in order, with no stages skipped.
- **Stage order is enforced:** Each stage writes its results to the CycleRun.stage_reports array in order; if stage N fails, stages N+1 onward do not run.
- **Idempotency:** Running the same cycle twice (same org_id, mode, timestamp) should be safe; re-running does not double-post or corrupt state. Use cycle_run_id as idempotency key in distribution stage.

### Database & Audit Trail

- **CycleRun table:** Tracks one full cycle per row; columns: id, org_id, started_at, completed_at, status, mode, org_score_before, org_score_after, score_delta, stage_reports (JSON)
- **Stage reports stored as JSON:** stage_reports is a list of dicts, each with keys: stage, status, timestamp, payload; stored in CycleRun.stage_reports for full audit history
- **Tickets created during cycle:** Each ticket created in stage 3 (create_copy) has a new Ticket record; ticket.niche = "engagement_growth"; ticket.risk = "low" or "high" per agent proposal; tickets are auto-approved in stage 5 if risk="low"
- **Publications created during cycle:** Each post in stage 6 creates a Publication record (organization_id, channel, url, label, published_at); Publications link back to the CycleRun for traceability (or via created_at timestamp within the cycle window)

### Error Handling & Safety

- **Partial cycle failure is auditable:** If stage 4 fails (e.g., AI generation timeout), CycleRun.status = "failed", that stage's status = "failed" with error_details in payload, and all prior stage reports are preserved. Admin can inspect what succeeded and what failed.
- **High-risk tickets are never auto-posted in simulate mode:** If stage 5 marks a high-risk ticket approved in simulate mode, stage 6 still does not post it (it stays in the queue for human review in a real workflow).
- **WordPress drafts are reversible:** Website content posted in live mode is always a draft, never published automatically; admin can review and publish or discard.
- **Social adapters default to safe:** Default social adapters (Facebook, Instagram, LinkedIn, Twitter/X) do not post in live mode unless explicitly swapped for a real posting adapter; recorded Publications use placeholder URLs ("simulated://facebook/...") in default mode.

### Performance & Limits

- **Cycle timeout:** A full cycle run must complete within a configured timeout (default: 15 minutes); if timeout is exceeded, the cycle is marked failed and stages after the timeout are skipped.
- **Concurrent cycle limit:** An org can have at most 1 cycle running at a time; if a new run is triggered while the previous one is still running, the new request is queued (up to 3 queued; 4th is rejected with 429 Too Many Requests).
- **No network calls in simulate mode:** Network calls during stage 6 in simulate mode are forbidden; any attempt raises an error and fails the stage.

---

## Definition of Done

- [ ] `run_full_cycle(db, org, mode="simulate"|"live")` function implemented in `services/cycle_engine.py`, calling all 7 stages in order
- [ ] `CycleRun` model added to `models/entities.py` with fields: id, org_id, started_at, completed_at, status, mode, org_score_before, org_score_after, score_delta, stage_reports (JSON)
- [ ] POST `/organizations/{org_id}/cycles/run?mode=simulate|live` endpoint implemented in a new `routers/cycles.py`
- [ ] GET `/organizations/{org_id}/cycles` and GET `/organizations/{org_id}/cycles/{cycle_id}` endpoints return cycle list and detail with stage_reports
- [ ] All 7 stages implemented:
  - Stage 1 (analyze_score): calls `compute_insights()`, captures org_score_before, returns in stage_report
  - Stage 2 (plan_gaps): calls `_engagement_growth_profile()`, identifies gaps, returns channel_gaps in stage_report
  - Stage 3 (create_copy): calls agent cycle for engagement_growth niche, creates Tickets, returns tickets in stage_report
  - Stage 4 (generate_content): iterates approved low-risk tickets, ensures payload has finished content (none missing), returns in stage_report
  - Stage 5 (auto_approve): auto-approves all low-risk tickets created in stage 3, increments auto_approved counter
  - Stage 6 (distribute): for each approved ticket, calls the channel adapter, creates Publications, returns posted/failed in stage_report
  - Stage 7 (measure_performance): calls `compute_insights()`, captures org_score_after, computes score_delta, returns goal_achieved in stage_report
- [ ] Module gating: `is_module_enabled(org, "engagement_growth")` check applied to all cycle endpoints and scheduler
- [ ] Simulate mode: no real posts created, no external network calls (except cached/mocked insights), dry-run Publications recorded with test markers
- [ ] Live mode: WordPress drafts created, Publications recorded with real URLs, adapters configured to use defaults (safe simulated social)
- [ ] Background execution: cycle runs asynchronously after endpoint returns; client polls GET endpoint for completion
- [ ] Scheduler integration: `scheduler.add_job()` calls `run_full_cycle()` at configured interval (environment-configurable, default: daily)
- [ ] Error handling: failed stages set status="failed" with error details; later stages skipped; CycleRun marked failed
- [ ] Audit trail: all stage_reports stored in CycleRun.stage_reports (JSON); timestamps and payloads preserved for inspection
- [ ] Tests pass (no real network calls in test suite):
  - Test `run_full_cycle()` in simulate mode, assert org_score delta captured correctly
  - Test stage order enforced (stage 2 does not run if stage 1 fails)
  - Test module gating (disabled org returns 403)
  - Test auto-approve logic (low-risk approved, high-risk stays proposed)
  - Test simulate mode does not create real Publications
- [ ] Documentation: `ENGAGEMENT_CYCLE_STORIES.md` (this file) included in repo; architecture notes added to ARCHITECTURE.md if needed
