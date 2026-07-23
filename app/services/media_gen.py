"""Reliable image + video generation for the content workflow.

Design goal: work out of the box with NO extra keys. Images use OpenAI
gpt-image-1 when OPENAI_API_KEY is set (best quality), otherwise a keyless
public generator (Pollinations) - so "Generate image" always produces a real
image. Video is assembled locally: generate a still per scene, burn in the
caption, and stitch to an MP4 with ffmpeg (via imageio-ffmpeg / system ffmpeg).
Everything is best-effort and never raises into the request."""
import base64
import io
import os
import tempfile
import textwrap
import urllib.parse

import httpx
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont

from app.config import settings

IMAGE_MODEL = "gpt-image-1"
_POLLINATIONS = "https://image.pollinations.ai/prompt/"
_UA = "EngageAI/1.0 (+https://engage-ai-api.onrender.com)"


class ImageGenService:
    def __init__(self) -> None:
        self.openai = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    @property
    def enabled(self) -> bool:
        # Always available: even with no key, the keyless fallback works.
        return True

    def generate_image(self, prompt: str, width: int = 1024, height: int = 1024) -> tuple[bytes, str] | None:
        """(bytes, mime) for the prompt, or None on total failure. Tries OpenAI
        first when configured, then the keyless provider."""
        prompt = (prompt or "").strip()
        if not prompt:
            return None
        if self.openai:
            try:
                resp = self.openai.images.generate(model=IMAGE_MODEL, prompt=prompt, size=f"{width}x{height}", n=1)
                b64 = resp.data[0].b64_json
                if b64:
                    return base64.b64decode(b64), "image/png"
            except Exception:  # noqa: BLE001 - fall through to the keyless provider
                pass
        return self._pollinations(prompt, width, height)

    def _pollinations(self, prompt: str, width: int, height: int) -> tuple[bytes, str] | None:
        try:
            url = (_POLLINATIONS + urllib.parse.quote(prompt)
                   + f"?width={int(width)}&height={int(height)}&nologo=true&model=flux")
            r = httpx.get(url, timeout=90.0, follow_redirects=True, headers={"User-Agent": _UA})
            ctype = r.headers.get("content-type", "")
            if r.status_code < 400 and ctype.startswith("image/") and r.content:
                return r.content, ctype
        except Exception:  # noqa: BLE001
            pass
        return None

    def generate_pil(self, prompt: str, width: int, height: int) -> Image.Image:
        """A PIL image for a prompt at the exact size - never fails: a plain
        branded placeholder is returned if generation doesn't work."""
        result = self.generate_image(prompt, width, height)
        if result:
            try:
                return Image.open(io.BytesIO(result[0])).convert("RGB").resize((width, height))
            except Exception:  # noqa: BLE001
                pass
        img = Image.new("RGB", (width, height), (17, 24, 39))
        return img


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow 10.1+ scalable default
    except TypeError:
        return ImageFont.load_default()


def _draw_caption(img: Image.Image, text: str, font: ImageFont.FreeTypeFont) -> None:
    text = (text or "").strip()
    if not text:
        return
    w, h = img.size
    draw = ImageDraw.Draw(img)
    lines = textwrap.wrap(text, width=max(16, int(w / (font.size * 0.55))))[:4]
    line_h = int(font.size * 1.3)
    bar_h = line_h * len(lines) + 40
    draw.rectangle([0, h - bar_h, w, h], fill=(0, 0, 0))
    y = h - bar_h + 20
    for line in lines:
        draw.text((28, y), line, fill=(255, 255, 255), font=font)
        y += line_h


class VideoGenService:
    """Assembles a short captioned video (Reel/Shorts-style, vertical) from a
    video_plan's scenes: one generated still per scene with its caption burned
    in, held a few seconds, stitched to MP4."""

    def __init__(self, image_gen: ImageGenService | None = None) -> None:
        self.image_gen = image_gen or ImageGenService()

    def assemble(self, video_plan: dict | None, width: int = 720, height: int = 1280,
                 seconds_per_scene: float = 3.0, fps: int = 24) -> tuple[bytes, str] | None:
        scenes = [s for s in ((video_plan or {}).get("scenes") or []) if isinstance(s, dict)][:6]
        if not scenes:
            return None
        font = _load_font(46)
        frames: list[Image.Image] = []
        for scene in scenes:
            prompt = (scene.get("image_prompt") or scene.get("caption") or "").strip()
            img = self.image_gen.generate_pil(prompt, width, height)
            _draw_caption(img, scene.get("caption") or "", font)
            frames.append(img)
        if not frames:
            return None

        import numpy as np  # local import: only needed when a video is actually built
        import imageio.v2 as imageio

        hold = max(1, int(seconds_per_scene * fps))
        path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        try:
            writer = imageio.get_writer(path, format="FFMPEG", mode="I", fps=fps,
                                        codec="libx264", quality=7, macro_block_size=8,
                                        ffmpeg_log_level="error", pixelformat="yuv420p")
            try:
                for img in frames:
                    arr = np.asarray(img.convert("RGB"))
                    for _ in range(hold):
                        writer.append_data(arr)
            finally:
                writer.close()
            with open(path, "rb") as f:
                data = f.read()
        except Exception:  # noqa: BLE001 - a render failure must not sink the request
            return None
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        return (data, "video/mp4") if data else None
