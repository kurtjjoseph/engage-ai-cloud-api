# Engagement Cycle — Demo Run

Organization: **Vision Outreach Media**  ·  mode: **simulate** (deterministic, offline projection)

## Result: Engage AI score 33 → 39  (delta +6)

The success metric (org_score increases by >= 1 in one cycle) is met.

## Seven stages

| # | Stage | Detail | Count |
|---|-------|--------|-------|
| 1 | ANALYSE | Read current org_score=33 from snapshot #1. | 8 |
| 2 | PLAN | Selected 3 gap-closing engagement(s) from 8 ranked channels. | 3 |
| 3 | COPY | Ensured every planned engagement carries non-empty, written content. | 3 |
| 4 | GENERATE | Normalized and validated engagements by type: 1 channel_setup, 1 social_post, 1 website_post. | 3 |
| 5 | APPROVE | Auto-approved 3 engagement(s) (cycle_auto_approve=True). | 3 |
| 6 | DISTRIBUTE | Distributed 3 engagement(s) via channel adapters. | 3 |
| 7 | MEASURE | Simulated projection written as a new non-baseline AnalyticsSnapshot. after_org_score=39. | 3 |

Engagements distributed: **3** (publication ids: [1, 2, 3]).

> Simulate mode writes a projected post-cycle snapshot clearly labelled `[SIMULATED PROJECTION]` — it is a bounded, deterministic projection of each engagement's effect on the channel KPIs, not a live web-search measurement. In production, `measure_mode="live"` re-scans the real published items.
