"""Image generation for the content-design workflow, via OpenAI gpt-image-1.

Deliberately graceful: with no OPENAI_API_KEY set, generate_image() returns None
and the caller falls back to showing the image prompt + alt text (so the whole
workflow still works without the key - the pixels just aren't produced yet).
Video is assembled from these images + captions in a later step."""
import base64

from openai import OpenAI

from app.config import settings

IMAGE_MODEL = "gpt-image-1"


class ImageGenService:
    def __init__(self) -> None:
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def generate_image(self, prompt: str, size: str = "1024x1024") -> tuple[bytes, str] | None:
        """Returns (png_bytes, mime) for the prompt, or None when no key is set
        or generation fails. Never raises - a media hiccup must not sink the
        content workflow."""
        if not self.client or not (prompt or "").strip():
            return None
        try:
            resp = self.client.images.generate(model=IMAGE_MODEL, prompt=prompt.strip(), size=size, n=1)
            b64 = resp.data[0].b64_json
            if not b64:
                return None
            return base64.b64decode(b64), "image/png"
        except Exception:  # noqa: BLE001 - graceful: fall back to the prompt/alt
            return None
