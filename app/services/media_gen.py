"""Rendering for the Content Studio's three formats.

The hard requirement is reliability: every format must produce a usable file on
every run, with no API key and no external service that is allowed to be a
single point of failure. So:

* Backgrounds try OpenAI (when a key is set), then a keyless public generator,
  then a retry on a shortened prompt, and finally a deterministic gradient
  built locally from the prompt's hash. The last step cannot fail, and it is
  designed to look intentional rather than broken - the same prompt always
  yields the same palette.
* All typography is composited locally with Pillow, so text on an image is
  exact, legible and never subject to a model's inability to spell.
* Video is stitched locally with ffmpeg (baked into the image), never fetched.

Three renderers, one per studio format:
    render_post_image  - a plain illustrative image at the channel's canvas
    render_text_image  - headline/subhead/CTA composited onto a background
    render_slideshow   - an 8s vertical video, narration centred, cross-faded
"""
import base64
import hashlib
import io
import os
import tempfile
import textwrap
import time
import urllib.parse

import httpx
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config import settings

IMAGE_MODEL = "gpt-image-1"
_POLLINATIONS = "https://image.pollinations.ai/prompt/"
_UA = "EngageAI/1.0 (+https://engage-ai-api.onrender.com)"
# How long to wait on the keyless generator before falling back to the local
# gradient. Bounded deliberately: the whole render has to finish inside the
# WordPress plugin's HTTP timeout, and a background that never arrives is worth
# less than a render that comes back.
IMAGE_TIMEOUT = 55.0
# Total time a multi-background render (the video) will spend fetching. Renders
# run as background jobs, so this can be generous - but it still has to end.
BATCH_BUDGET = 150.0
# How far a slide's background is oversized so the slow zoom has room to move.
_ZOOM = 1.14

# Curated background palettes for the deterministic fallback: pairs that stay
# dark enough for white text to sit on them at full contrast.
_PALETTES: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = [
    ((17, 24, 39), (55, 65, 81)),      # slate
    ((12, 34, 56), (14, 78, 105)),     # deep ocean
    ((40, 20, 60), (94, 42, 108)),     # plum
    ((20, 40, 30), (26, 92, 63)),      # forest
    ((60, 25, 20), (140, 62, 38)),     # ember
    ((25, 25, 45), (72, 61, 139)),     # indigo
    ((10, 40, 45), (17, 94, 89)),      # teal
    ((45, 30, 15), (122, 82, 38)),     # amber earth
]


def _palette_for(seed: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    digest = hashlib.sha256((seed or "engage").encode("utf-8")).digest()
    return _PALETTES[digest[0] % len(_PALETTES)]


def _gradient(width: int, height: int, seed: str) -> Image.Image:
    """A smooth diagonal two-tone gradient - the always-works background. Built
    tiny and upscaled, which is both fast and naturally soft."""
    top, bottom = _palette_for(seed)
    small = Image.new("RGB", (16, 16))
    pixels = small.load()
    for y in range(16):
        for x in range(16):
            t = (y * 0.75 + x * 0.25) / 15.0
            pixels[x, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return small.resize((width, height), Image.BICUBIC)


class ImageGenService:
    def __init__(self) -> None:
        self.openai = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    @property
    def enabled(self) -> bool:
        # Always: with no key the keyless provider runs, and if that fails the
        # local gradient still yields a real, presentable image.
        return True

    def generate_image(self, prompt: str, width: int = 1024, height: int = 1024,
                       timeout: float = IMAGE_TIMEOUT) -> tuple[bytes, str] | None:
        """(bytes, mime) for the prompt, or None only when the prompt is empty.
        Tries OpenAI, the keyless provider, a shortened retry, then falls back
        to the local gradient. `timeout` bounds the wait on the keyless
        provider, whose latency varies a lot - a slow generator must degrade to
        the gradient rather than hold the request open past the plugin's own
        HTTP timeout."""
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
        result = self._pollinations(prompt, width, height, timeout)
        if result is None and len(prompt) > 180:
            # Over-long prompts are the most common keyless failure; the first
            # sentence carries the subject, so retry with just that.
            result = self._pollinations(prompt[:180].rsplit(" ", 1)[0], width, height, timeout)
        if result is not None:
            return result
        buffer = io.BytesIO()
        _gradient(width, height, prompt).save(buffer, format="JPEG", quality=88)
        return buffer.getvalue(), "image/jpeg"

    def _pollinations(self, prompt: str, width: int, height: int,
                      timeout: float = IMAGE_TIMEOUT) -> tuple[bytes, str] | None:
        try:
            url = (_POLLINATIONS + urllib.parse.quote(prompt)
                   + f"?width={int(width)}&height={int(height)}&nologo=true&model=flux"
                   + f"&seed={int(hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:8], 16) % 100000}")
            r = httpx.get(url, timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA})
            ctype = r.headers.get("content-type", "")
            if r.status_code < 400 and ctype.startswith("image/") and r.content:
                return r.content, ctype
        except Exception:  # noqa: BLE001
            pass
        return None

    def generate_pil(self, prompt: str, width: int, height: int,
                     timeout: float = IMAGE_TIMEOUT) -> Image.Image:
        """A PIL background at exactly (width, height). Never fails."""
        result = self.generate_image(prompt, width, height, timeout)
        if result:
            try:
                return _cover(Image.open(io.BytesIO(result[0])).convert("RGB"), width, height)
            except Exception:  # noqa: BLE001
                pass
        return _gradient(width, height, prompt)

    def generate_many(self, prompts: list[str], width: int, height: int,
                      budget: float = BATCH_BUDGET) -> list[Image.Image]:
        """Backgrounds for several prompts, in order, within a total time
        budget.

        Deliberately serial: the keyless generator answers one request at a
        time and rejects the rest outright (HTTP 429), so firing them in
        parallel returns three placeholders and one image. Its latency is queue
        time - roughly the same for any size or model - so the only lever left
        is how many images a render is willing to wait for. Once the budget is
        spent the remaining slides reuse an already-fetched background, which
        the renderer then varies with its own framing rather than showing the
        same shot twice."""
        images: list[Image.Image | None] = []
        deadline = time.monotonic() + budget
        for prompt in prompts:
            remaining = deadline - time.monotonic()
            if remaining < 10:  # not enough left to be worth waiting on
                images.append(None)
                continue
            images.append(self.generate_pil(prompt, width, height, timeout=min(IMAGE_TIMEOUT, remaining)))
        fetched = [img for img in images if img is not None]
        if not fetched:
            return [_gradient(width, height, p) for p in prompts]
        return [img if img is not None else fetched[index % len(fetched)] for index, img in enumerate(images)]


def _cover(img: Image.Image, width: int, height: int) -> Image.Image:
    """Resize to fill the canvas and centre-crop - never squashes the subject
    the way a plain resize to a different aspect ratio does."""
    src_w, src_h = img.size
    if src_w == width and src_h == height:
        return img
    scale = max(width / src_w, height / src_h)
    resized = img.resize((max(1, round(src_w * scale)), max(1, round(src_h * scale))), Image.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


# --------------------------------------------------------------------- type
_FONT_PATHS = {
    True: (  # bold
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ),
    False: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ),
}


def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for path in _FONT_PATHS[bold] + _FONT_PATHS[not bold]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow 10.1+ scalable default
    except TypeError:
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if not current or draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, max_height: int,
              size_from: int, size_to: int, bold: bool = True) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """Largest font size at which `text` wraps inside the box. Returns
    (font, lines, line_height) - always something, even if it has to settle for
    the smallest size."""
    font = _load_font(size_to, bold)
    lines = _wrap(draw, text, font, max_width)
    for size in range(size_from, size_to - 1, -2):
        candidate = _load_font(size, bold)
        wrapped = _wrap(draw, text, candidate, max_width)
        if len(wrapped) * int(size * 1.24) <= max_height:
            return candidate, wrapped, int(size * 1.24)
    return font, lines, int(size_to * 1.24)


def _draw_centered(img: Image.Image, lines: list[str], font: ImageFont.FreeTypeFont,
                   line_height: int, top: int, fill=(255, 255, 255)) -> int:
    """Draws centre-aligned lines with a soft shadow for legibility on any
    background. Returns the y just below the block."""
    draw = ImageDraw.Draw(img)
    y = top
    for line in lines:
        x = (img.width - draw.textlength(line, font=font)) / 2
        draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _scrim(img: Image.Image, strength: float = 0.42, blur: float = 0.0) -> Image.Image:
    """Darkens a background so white type reads against it, optionally blurring
    it first so the type is what the eye lands on."""
    base = img.filter(ImageFilter.GaussianBlur(blur)) if blur else img
    overlay = Image.new("RGB", base.size, (0, 0, 0))
    return Image.blend(base, overlay, max(0.0, min(strength, 0.9)))


# ---------------------------------------------------------------- renderers
def _encode(img: Image.Image) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    img.convert("RGB").save(buffer, format="JPEG", quality=90, optimize=True)
    return buffer.getvalue(), "image/jpeg"


class StudioRenderer:
    """Renders the studio's three formats. Everything here returns a real file
    or raises nothing - failure modes degrade to the gradient background."""

    def __init__(self, image_gen: ImageGenService | None = None) -> None:
        self.image_gen = image_gen or ImageGenService()

    def render_post_image(self, prompt: str, width: int, height: int) -> tuple[bytes, str]:
        """Format 1: a plain illustrative image at the channel's canvas."""
        return _encode(self.image_gen.generate_pil(prompt, width, height))

    def render_text_image(self, prompt: str, headline: str, subhead: str = "", cta: str = "",
                          width: int = 1080, height: int = 1350) -> tuple[bytes, str]:
        """Format 2: the headline set ON the image. The background is darkened
        and gently blurred, the headline is centred and auto-sized to fill the
        space without ever overflowing it, and the CTA sits in a pill below."""
        img = _scrim(self.image_gen.generate_pil(prompt, width, height), strength=0.46, blur=width / 360)
        draw = ImageDraw.Draw(img)
        margin = int(width * 0.10)
        box_width = width - margin * 2

        headline = (headline or "").strip()
        subhead = (subhead or "").strip()
        cta = (cta or "").strip()

        # Budget: the headline gets most of the canvas, the rest is reserved so
        # the block always lands optically centred.
        head_font, head_lines, head_lh = _fit_text(
            draw, headline or " ", box_width, int(height * 0.46),
            size_from=int(height * 0.115), size_to=int(height * 0.035), bold=True,
        )
        sub_font, sub_lines, sub_lh = (None, [], 0)
        if subhead:
            sub_font, sub_lines, sub_lh = _fit_text(
                draw, subhead, box_width, int(height * 0.16),
                size_from=int(height * 0.048), size_to=int(height * 0.024), bold=False,
            )
        cta_font = _load_font(max(16, int(height * 0.028)), bold=True)
        cta_height = int(cta_font.size * 2.6) if cta else 0

        block = len(head_lines) * head_lh + (int(height * 0.03) + len(sub_lines) * sub_lh if sub_lines else 0)
        block += (int(height * 0.05) + cta_height) if cta else 0
        y = max(int(height * 0.08), (height - block) // 2)

        # A short accent rule above the headline - a small piece of design that
        # makes the graphic read as deliberate rather than as a stock overlay.
        rule_w = int(width * 0.09)
        draw.rounded_rectangle(
            [(width - rule_w) // 2, y - int(height * 0.045), (width + rule_w) // 2, y - int(height * 0.045) + 6],
            radius=3, fill=(255, 255, 255),
        )

        y = _draw_centered(img, head_lines, head_font, head_lh, y)
        if sub_lines:
            y += int(height * 0.03)
            y = _draw_centered(img, sub_lines, sub_font, sub_lh, y, fill=(232, 232, 236))
        if cta:
            y += int(height * 0.05)
            text_w = draw.textlength(cta, font=cta_font)
            pad_x = int(cta_font.size * 1.1)
            x0 = (width - (text_w + pad_x * 2)) / 2
            draw.rounded_rectangle(
                [x0, y, x0 + text_w + pad_x * 2, y + cta_height],
                radius=cta_height // 2, fill=(255, 255, 255),
            )
            draw.text((x0 + pad_x, y + (cta_height - cta_font.size * 1.25) / 2), cta,
                      font=cta_font, fill=(17, 24, 39))
        return _encode(img)

    def render_slideshow(self, slides: list[dict], width: int = 720, height: int = 1280,
                         total_seconds: float = 8.0, fps: int = 24) -> tuple[bytes, str] | None:
        """Format 3: an 8-second vertical video.

        Each slide is a background under a slow zoom, with its narration line
        set dead-centre on screen, a progress bar across the top, and a short
        cross-fade into the next slide. The zoom is what lets a render reuse a
        background when the generator is slow (see generate_many) without the
        result looking like a repeat - each slide gets its own framing and
        direction of travel.

        Returns (mp4_bytes, mime), or None if there are no slides or ffmpeg is
        unavailable."""
        slides = [s for s in (slides or []) if isinstance(s, dict) and str(s.get("narration") or "").strip()]
        if not slides:
            return None
        slides = slides[:6]

        frames_total = max(fps, int(round(total_seconds * fps)))
        per_slide = max(1, frames_total // len(slides))
        fade = min(int(fps * 0.4), max(1, per_slide // 3))

        backgrounds = self.image_gen.generate_many(
            [str(s.get("image_prompt") or s.get("narration") or "").strip() for s in slides],
            width, height,
        )

        try:
            import numpy as np  # local imports: only needed when a video is built
            import imageio.v2 as imageio
        except Exception:  # noqa: BLE001
            return None

        # Per slide: an oversized, scrimmed plate to pan across, and the
        # typography pre-rendered once as a transparent layer to paste onto
        # every frame (drawing text 192 times would be pure waste).
        plates, layers = [], []
        for index, (slide, background) in enumerate(zip(slides, backgrounds)):
            plates.append(_scrim(_cover(background, int(width * _ZOOM), int(height * _ZOOM)),
                                 strength=0.5, blur=width / 480))
            layers.append(self._narration_layer(str(slide.get("narration") or ""), width, height))

        path = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        try:
            writer = imageio.get_writer(
                path, format="FFMPEG", mode="I", fps=fps, codec="libx264",
                quality=7, macro_block_size=8, ffmpeg_log_level="error", pixelformat="yuv420p",
            )
            try:
                emitted = 0
                previous_last: Image.Image | None = None
                for index in range(len(slides)):
                    count = max(1, per_slide if index < len(slides) - 1 else frames_total - emitted)
                    for frame_index in range(count):
                        progress = frame_index / max(1, count - 1)
                        frame = self._zoom_frame(plates[index], width, height, progress, index)
                        frame.paste(layers[index], (0, 0), layers[index])
                        if previous_last is not None and frame_index < fade:
                            frame = Image.blend(previous_last, frame, (frame_index + 1) / (fade + 1))
                        self._draw_progress(frame, (emitted + frame_index + 1) / frames_total)
                        writer.append_data(np.asarray(frame))
                        if frame_index == count - 1:
                            previous_last = frame
                    emitted += count
            finally:
                writer.close()
            with open(path, "rb") as handle:
                data = handle.read()
        except Exception:  # noqa: BLE001 - a render failure must not sink the request
            return None
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        return (data, "video/mp4") if data else None

    def _zoom_frame(self, plate: Image.Image, width: int, height: int,
                    progress: float, index: int) -> Image.Image:
        """One frame of the slow zoom. Odd slides zoom out instead of in, and
        each slide is anchored a little differently, so a reused background
        never reads as the same shot twice."""
        scale = _ZOOM - (_ZOOM - 1.0) * (progress if index % 2 == 0 else 1.0 - progress)
        crop_w, crop_h = int(width * scale), int(height * scale)
        drift = (index % 3) - 1  # -1, 0, or 1: left, centre, right
        left = int((plate.width - crop_w) / 2 + drift * (plate.width - crop_w) / 3)
        top = int((plate.height - crop_h) / 2)
        left = max(0, min(left, plate.width - crop_w))
        return plate.crop((left, top, left + crop_w, top + crop_h)).resize((width, height), Image.BILINEAR)

    def _narration_layer(self, text: str, width: int, height: int) -> Image.Image:
        """The narration, centred both ways, as a transparent layer.

        Behind the type sits a blurred dark pool. A flat scrim over the whole
        frame either isn't enough where the background is bright or is so heavy
        it kills the image everywhere else; a soft pool local to the text keeps
        contrast guaranteed without reading as a box."""
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        text = (text or "").strip()
        if not text:
            return layer
        measure = ImageDraw.Draw(layer)
        margin = int(width * 0.09)
        font, lines, line_height = _fit_text(
            measure, text, width - margin * 2, int(height * 0.5),
            size_from=int(height * 0.075), size_to=int(height * 0.030), bold=True,
        )
        block_height = len(lines) * line_height
        top = (height - block_height) // 2  # true vertical centre

        pool = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(pool).rounded_rectangle(
            [margin - int(width * 0.06), top - int(height * 0.045),
             width - margin + int(width * 0.06), top + block_height + int(height * 0.045)],
            radius=int(width * 0.12), fill=(0, 0, 0, 130),
        )
        layer = pool.filter(ImageFilter.GaussianBlur(width * 0.06))

        draw = ImageDraw.Draw(layer)
        y = top
        for line in lines:
            x = (width - draw.textlength(line, font=font)) / 2
            draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0, 150))
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
            y += line_height
        return layer

    def _draw_progress(self, frame: Image.Image, fraction: float) -> None:
        """A thin progress bar so an 8-second clip reads as finished, not cut."""
        draw = ImageDraw.Draw(frame)
        bar_y = int(frame.height * 0.035)
        margin = int(frame.width * 0.09)
        track = frame.width - margin * 2
        draw.rounded_rectangle([margin, bar_y, margin + track, bar_y + 6], radius=3, fill=(120, 120, 128))
        filled = int(track * max(0.0, min(fraction, 1.0)))
        if filled > 0:
            draw.rounded_rectangle([margin, bar_y, margin + filled, bar_y + 6], radius=3, fill=(255, 255, 255))


# ------------------------------------------------------- legacy video plans
def _draw_caption(img: Image.Image, text: str, font: ImageFont.FreeTypeFont) -> None:
    """Bottom caption bar - used by the older campaign video_plan path."""
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
    """Assembles a video from a campaign `video_plan` (the multi-channel pack
    workflow's storyboard shape: scenes of {caption, image_prompt}). The studio
    uses StudioRenderer.render_slideshow instead; this stays for content
    generated by the campaign flow."""

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

        try:
            import numpy as np
            import imageio.v2 as imageio
        except Exception:  # noqa: BLE001
            return None

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
        except Exception:  # noqa: BLE001
            return None
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        return (data, "video/mp4") if data else None
