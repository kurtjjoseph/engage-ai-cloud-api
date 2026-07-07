import json
from openai import OpenAI
from app.config import settings


# Fixed output contract every generator must return. Clients (e.g. the WordPress
# plugin) rely on these exact keys to auto-publish without guessing at LLM output shape.
OUTPUT_SCHEMA = {
    "website_post": {"title": "string", "body_html": "string (safe HTML, no script tags)"},
    "social_media": {"caption": "string", "hashtags": ["string"]},
    "email": {"subject": "string", "body_html": "string"},
    "whatsapp": {"message": "string"},
    "slides": [{"title": "string", "body": "string"}],
    "follow_up_actions": ["string"],
}


def _empty_output() -> dict:
    return {
        "website_post": {"title": "", "body_html": ""},
        "social_media": {"caption": "", "hashtags": []},
        "email": {"subject": "", "body_html": ""},
        "whatsapp": {"message": ""},
        "slides": [],
        "follow_up_actions": [],
    }


def _fallback_response(task: str, payload: dict) -> dict:
    output = _empty_output()
    output["status"] = "draft_generated_without_ai_key"
    output["task"] = task
    output["message"] = "Set OPENAI_API_KEY to enable full AI generation."
    return output


def _normalize(raw: dict) -> dict:
    """Fill in any keys the model omitted so callers can rely on the full schema always being present."""
    output = _empty_output()
    for key, default in output.items():
        value = raw.get(key)
        if isinstance(default, dict) and isinstance(value, dict):
            output[key] = {**default, **value}
        elif value is not None:
            output[key] = value
    return output


class EngageAIService:
    def __init__(self):
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    def generate_structured(self, task: str, organization: dict, payload: dict) -> dict:
        if not self.client:
            return _fallback_response(task, payload)

        system = f"""
You are Engage AI, an AI Engagement Director for churches and mission-driven organizations.
Your job is not merely to create content. Your job is to turn a message, sermon, or event into practical engagement.
Always create clear, warm, actionable outputs.
Return valid JSON only, matching exactly this schema (no extra top-level keys, no markdown fences):
{json.dumps(OUTPUT_SCHEMA, indent=2)}
"""
        user = {
            "task": task,
            "organization_memory": organization,
            "input": payload,
        }

        response = self.client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
            response_format={"type": "json_object"},
        )
        return _normalize(json.loads(response.choices[0].message.content))
