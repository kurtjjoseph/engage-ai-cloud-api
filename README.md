# Engage AI Cloud API

A deployment-ready FastAPI backend for **Engage AI** — an AI Engagement Director for churches and mission-driven organizations.

## Core features

- Church / organization profile memory
- Event campaign generator
- Weekly announcement generator
- Sermon engagement generator
- Content library
- JWT authentication
- Docker-ready deployment
- WordPress plugin integration-ready API

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open:

```text
http://localhost:8000/docs
```

## Docker setup

```bash
docker build -t engage-ai-api .
docker run --env-file .env -p 8000:8000 engage-ai-api
```

## Main endpoints

- `POST /auth/register`
- `POST /auth/login`
- `POST /organizations`
- `GET /organizations/me`
- `POST /campaigns/event`
- `POST /campaigns/announcements`
- `POST /campaigns/sermon`
- `GET /content`

## Production deployment

Set these environment variables on your host:

- `DATABASE_URL`
- `JWT_SECRET`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

Use PostgreSQL in production.

## Testing

The Python API:

```bash
python -m pytest tests/ -q
```

The WordPress plugin has no test coverage from that suite — not one test imports
it — so it gets its own check, which boots a real WordPress with
`app/plugin_template/engage-ai` mounted and opens every admin page:

```bash
node scripts/smoke_plugin.mjs
```

It asserts each page returns 200, is not refused by WordPress, and emits no PHP
fatal, warning or deprecation, and that pages meant to be hidden from the sidebar
are still reachable by URL. Exits non-zero on the first failure, so it works as a
release gate. Needs Node and network on the first run (it fetches WordPress and a
PHP build via `@wp-playground/cli`); no PHP installation required.

Run it before pushing anything that touches the plugin. **0.27.0 shipped a page
WordPress refused to serve** — the code parsed cleanly and the Python suite was
green, because neither one runs WordPress. This does.

## WordPress plugin integration

The WordPress plugin should authenticate once, store the JWT securely, then call:

```text
POST /campaigns/event
POST /campaigns/announcements
POST /campaigns/sermon
```

The API returns structured JSON content that can be inserted into WordPress posts, pages, custom post types, Elementor templates, email tools, or social media workflows.
