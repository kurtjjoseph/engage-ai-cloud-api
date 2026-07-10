import json
from anthropic import Anthropic
from app.config import settings

ASSISTANT_PROTOCOL = """You are a helpful assistant embedded in Engage AI, answering one-off
questions for the admin of a church or mission-driven organization. Ground your answer in the
organization's context below where it's relevant - their actual mission, tone, audience,
ministries - rather than generic advice. If the context doesn't have enough to answer a specific
question well, say what's missing instead of guessing.

Answer directly and concisely - plain text, a few short paragraphs at most, no markdown headers.
This is a quick answer, not a report."""


class AssistantService:
    def __init__(self):
        self.client = Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None

    def ask(self, org_context: dict, question: str) -> str:
        if not self.client:
            return "ANTHROPIC_API_KEY is not set - the assistant is unavailable."

        user_message = "Organization context:\n" + json.dumps(org_context) + "\n\nQuestion: " + question

        response = self.client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
            system=ASSISTANT_PROTOCOL,
            messages=[{"role": "user", "content": user_message}],
        )
        return "".join(block.text for block in response.content if block.type == "text").strip()
