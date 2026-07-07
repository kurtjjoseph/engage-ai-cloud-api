import json
from openai import OpenAI
from app.config import settings


def _fallback_response(task: str, payload: dict) -> dict:
    return {
        "status": "draft_generated_without_ai_key",
        "task": task,
        "message": "Set OPENAI_API_KEY to enable full AI generation.",
        "draft": payload,
    }


class EngageAIService:
    def __init__(self):
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    def generate_structured(self, task: str, organization: dict, payload: dict) -> dict:
        if not self.client:
            return _fallback_response(task, payload)

        system = """
You are Engage AI, an AI Engagement Director for churches and mission-driven organizations.
Your job is not merely to create content. Your job is to turn a message, sermon, or event into practical engagement.
Always create clear, warm, actionable outputs.
Return valid JSON only. No markdown fences.
"""
        user = {
            "task": task,
            "organization_memory": organization,
            "input": payload,
            "required_output_style": "structured JSON with ready-to-use content for church website, announcements, slides, social media, email, WhatsApp, and follow-up engagement actions",
        }

        response = self.client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
