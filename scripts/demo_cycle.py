"""End-to-end demo of the full engagement cycle on a realistic Vision Outreach
Media organization. Offline / deterministic (simulate mode). Proves the success
metric: org_score increases by >= 1 in one cycle run.

Run: .venv/bin/python scripts/demo_cycle.py
"""
import json
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///./engage_ai_demo.db")
os.environ["ENABLE_SCHEDULER"] = "false"
# Ensure no network/LLM path is taken.
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models.entities import AnalyticsSnapshot, Organization, User
from app.services.analytics_scoring import score_channel, score_org
from app.services.engagement_cycle import run_full_cycle

# Fresh in-memory DB so the demo is self-contained and repeatable.
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine)
db = Session()

# --- Seed Vision Outreach Media, with a realistic public footprint ---
user = User(email="info@visionoutreachmedia.nl", hashed_password="x")
db.add(user)
db.flush()

org = Organization(
    owner_id=user.id,
    name="Vision Outreach Media",
    org_type="business",
    mission="Done-for-you websites and media for churches and mission-driven organizations.",
    tone="warm, mission-literate, faith-aligned",
    audience="churches, ministries, nonprofits",
    website_url="https://www.visionoutreachmedia.nl",
    channel_details={
        "facebook": "https://facebook.com/visionoutreachmedia",
        "instagram": "https://instagram.com/visionoutreachmedia",
        "youtube": "https://youtube.com/@visionoutreachmedia9417",
        "linkedin": "https://linkedin.com/company/136115225",
        "twitter_x": "@visionomedia",
    },
    enabled_modules=["analytics", "engagement_cycle"],
    target_org_score=70,
    target_channel_scores={
        "website": 80, "youtube": 60, "instagram": 60,
        "facebook": 60, "linkedin": 55, "news_mentions": 40,
    },
)
db.add(org)
db.flush()

# Baseline KPIs — a plausible early-stage footprint: solid website, a real
# Google Business presence, but thin/young social channels and no press.
baseline_kpis = {
    "website": {"indexed": True, "pages_indexed_estimate": 9, "backlink_signal": "low", "freshness": "occasional"},
    "google_business": {"found": True, "rating": 5.0, "review_count": 4},
    "youtube": {"found": True, "subscriber_count": 40, "video_count": 6, "posting_frequency": "monthly"},
    "facebook": {"found": True, "follower_count": 120, "posting_frequency": "monthly", "engagement_level": "low"},
    "instagram": {"found": True, "follower_count": 80, "posting_frequency": "rare", "engagement_level": "low"},
    "linkedin": {"found": True, "follower_count": 30, "posting_frequency": "rare", "engagement_level": "low"},
    "twitter_x": {"found": False},
    "news_mentions": {"found": False},
}
channels = []
for ch, kpis in baseline_kpis.items():
    score, breakdown = score_channel(ch, kpis)
    channels.append({"channel": ch, "kpis": kpis, "notes": None, "score": score, "score_breakdown": breakdown})
org_score, org_breakdown = score_org({c["channel"]: c["score"] for c in channels})

db.add(AnalyticsSnapshot(
    organization_id=org.id, is_baseline=True, summary="Baseline footprint scan (demo seed).",
    channels=channels, org_score=org_score, org_score_breakdown=org_breakdown,
    sources=[], requested_channels=None, status="complete",
))
db.commit()

print(f"BASELINE org_score = {org_score}")
print("  per-channel:", {c["channel"]: c["score"] for c in channels})

# --- Run one full engagement cycle (simulate mode) ---
run = run_full_cycle(db, org, measure_mode="simulate")

print("\n=== ENGAGEMENT CYCLE RUN ===")
print(f"status={run.status}  measure_mode={run.measure_mode}")
print(f"before_org_score={run.before_org_score}  after_org_score={run.after_org_score}  delta={run.delta}")
print(f"engagements distributed={run.engagement_count}  publication_ids={run.publication_ids}")
print("\nSeven stages:")
for s in run.stages:
    print(f"  {s['stage']}. {s['name']:9s} — {s['detail']} (count={s['count']})")

assert run.status == "completed", run.status
assert run.delta is not None and run.delta >= 1, f"GOAL NOT MET: delta={run.delta}"
print(f"\n✅ SUCCESS METRIC MET: org_score rose by {run.delta} (>= 1).")

# --- Write report artifacts ---
report = {
    "organization": org.name,
    "measure_mode": run.measure_mode,
    "before_org_score": run.before_org_score,
    "after_org_score": run.after_org_score,
    "delta": run.delta,
    "engagements_distributed": run.engagement_count,
    "publication_ids": run.publication_ids,
    "stages": run.stages,
}
os.makedirs("docs", exist_ok=True)
with open("docs/DEMO_RUN.json", "w") as f:
    json.dump(report, f, indent=2)

lines = [
    "# Engagement Cycle — Demo Run",
    "",
    f"Organization: **{org.name}**  ·  mode: **{run.measure_mode}** (deterministic, offline projection)",
    "",
    f"## Result: Engage AI score {run.before_org_score} → {run.after_org_score}  (delta +{run.delta})",
    "",
    "The success metric (org_score increases by >= 1 in one cycle) is met.",
    "",
    "## Seven stages",
    "",
    "| # | Stage | Detail | Count |",
    "|---|-------|--------|-------|",
]
for s in run.stages:
    detail = s["detail"].replace("|", "\\|")
    lines.append(f"| {s['stage']} | {s['name']} | {detail} | {s['count']} |")
lines += [
    "",
    f"Engagements distributed: **{run.engagement_count}** (publication ids: {run.publication_ids}).",
    "",
    "> Simulate mode writes a projected post-cycle snapshot clearly labelled "
    "`[SIMULATED PROJECTION]` — it is a bounded, deterministic projection of each "
    "engagement's effect on the channel KPIs, not a live web-search measurement. "
    "In production, `measure_mode=\"live\"` re-scans the real published items.",
]
with open("docs/DEMO_RUN.md", "w") as f:
    f.write("\n".join(lines) + "\n")

print("\nWrote docs/DEMO_RUN.md and docs/DEMO_RUN.json")
db.close()
