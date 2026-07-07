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

## WordPress plugin integration

The WordPress plugin should authenticate once, store the JWT securely, then call:

```text
POST /campaigns/event
POST /campaigns/announcements
POST /campaigns/sermon
```

The API returns structured JSON content that can be inserted into WordPress posts, pages, custom post types, Elementor templates, email tools, or social media workflows.
