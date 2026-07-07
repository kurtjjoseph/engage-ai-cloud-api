# Engage AI — Architecture & Product Decisions

**Status as of this doc:** working FastAPI backend, not yet version-controlled, not yet deployed, no plugin/frontend built.

## 1. What Engage AI is

An AI Engagement Director for churches and mission-driven organizations. Not just a content generator — it turns a message, sermon, or event into practical engagement (website copy, slides, social, email, WhatsApp, follow-up actions), using a stored "organization memory" (mission, tone, audience, ministries, recurring schedule, speakers) so output matches each church's voice without re-explaining context every time.

## 2. What's already built (`app/`)

- **Auth** — JWT register/login (`routers/auth.py`, `services/security.py`)
- **Organizations** — per-user church/org profiles with mission, tone, audience, colors, ministries, recurring schedule, locations, speakers (`routers/organizations.py`, `models/entities.py`)
- **Campaigns** — three generators, each calling OpenAI (`gpt-4.1-mini`) with the org's memory + task-specific input, returning structured JSON (`routers/campaigns.py`, `services/ai.py`):
  - `POST /campaigns/event` — event campaigns
  - `POST /campaigns/announcements` — weekly announcements
  - `POST /campaigns/sermon` — sermon engagement content
- **Content library** — every generated item is saved and retrievable per org (`routers/content.py`)
- **Deployment scaffolding** — `Dockerfile`, `docker-compose.yml` (API + Postgres together), `render.yaml` (unused now, see §4)

## 3. Product decisions (locked in 2026-07)

### 3.1 Distribution: WordPress plugin first, API stays generic
Build the WordPress plugin as the first client — it reaches churches already in the VOM WordPress client base with the least new build effort (no separate frontend needed yet). The cloud API itself stays a plain JSON API (not WP-coupled) so a standalone web dashboard can be added later without rework. The plugin authenticates once (JWT), stores the token, and calls the three `/campaigns/*` endpoints, inserting results into WP posts/pages/Elementor templates per the existing README plan.

**Not yet built:** the actual WordPress plugin code. This is the next real implementation task.

### 3.2 Billing: bundled into existing VOM pricing
Engage AI launches as a feature/add-on within the current €69/month VOM service, not as its own self-serve subscription product. No Stripe integration work needed at launch — `stripe_secret_key`/`stripe_webhook_secret` stay in config unused for now.

**Revisit when:** client volume or support load makes manual bundling impractical, or Kurt wants to sell Engage AI standalone outside existing VOM relationships. At that point, wire Stripe subscriptions using the keys already scaffolded in `config.py`.

### 3.3 Hosting: self-managed VPS (existing One.com/Bluehost infrastructure)
Chosen over Render.com to keep everything on infrastructure already owned/paid for, rather than adding a new vendor. This is a deliberate trade: Render would have handled all ops automatically; a VPS means Kurt owns OS updates, process supervision, SSL, and backups. Given variable energy/capacity (see personal AI strategy notes), the setup below is chosen specifically to minimize ongoing hands-on maintenance.

**Deploy plan:**
1. Provision the VPS plan on One.com/Bluehost with Docker support (confirm the plan allows installing Docker — not all shared-tier VPS plans do; if it turns out to be container-restricted, fall back to Render for the API only, keeping WordPress/VOM sites on the VPS as-is).
2. Install Docker + Docker Compose on the VPS.
3. Put **Caddy** in front of the existing `docker-compose.yml` stack as a reverse proxy — chosen over Nginx specifically because it auto-issues and auto-renews SSL certs with a ~5-line Caddyfile, removing a recurring manual task.
4. Set a nightly cron job running `pg_dump` against the `db` container to a backup file (and ideally copied off-VPS, e.g. to existing VOM storage) — the single most important safety net given this will hold real church data.
5. Enable unattended OS security updates on the VPS (e.g. `unattended-upgrades` on Debian/Ubuntu) so patching isn't a manual recurring task either.
6. Point DNS (e.g. `api.engageai.<domain>` or similar, TBD) at the VPS.

**Not yet decided:** exact subdomain/DNS name, and confirmation the specific One.com/Bluehost plan supports Docker.

## 4. Deployment scaffolding to retire or keep

`render.yaml` was written for a Render.com deploy and is no longer the plan — keep it in the repo for now (cheap fallback option if the VPS plan turns out not to support Docker) but it's not the active path.

## 5. Outstanding implementation gaps (not decisions — just not built yet)

- **No git repo** — nothing here is version-controlled. Do this before further changes.
- **No WordPress plugin code** — the next concrete build task per §3.1.
- **No tests** — none exist yet for auth/organizations/campaigns/content.
- **No Alembic migrations in use** — `alembic` is in `requirements.txt` but no migration files exist; currently relies on `Base.metadata.create_all` in `main.py`, which won't handle schema changes cleanly once there's real data in production.
- **No Caddyfile/backup script/unattended-upgrades config** yet — to be written as part of the VPS deploy work.

## 6. Deferred for later (explicitly not now)

- Standalone web dashboard (self-service beyond WordPress) — architecture supports it later since the API is generic, but not being built now.
- Self-serve Stripe billing — deferred until manual/bundled billing becomes a bottleneck.
