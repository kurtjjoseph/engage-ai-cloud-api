from fastapi import APIRouter
from fastapi.responses import Response
from app.config import settings
from app.services.plugin_metadata import get_plugin_metadata
from app.services.plugin_packager import build_plain_zip

router = APIRouter(prefix="/plugin", tags=["plugin"])


@router.get("/metadata.json")
def plugin_metadata():
    """PUC-compatible update metadata (Plugin Update Checker's generic 'custom
    JSON server' format - no GitHub involved). Version/changelog/compatibility
    are parsed straight from the bundled plugin source, never hand-maintained
    here, so this can't drift out of sync with what actually ships."""
    meta = get_plugin_metadata()
    base_url = settings.api_base_url.rstrip("/")
    return {
        "name": "Engage AI",
        "slug": "engage-ai",
        "version": meta["version"],
        "download_url": base_url + "/plugin/download.zip",
        "requires": meta["requires"],
        "tested": meta["tested"],
        "requires_php": meta["requires_php"],
        "sections": {
            "description": "Church engagement content generation, autonomous Claude AI side-hustle agents, and web-search analytics for WordPress.",
            "changelog": meta["changelog_html"],
        },
    }


@router.get("/download.zip")
def plugin_download():
    """Public - no auth, no secrets in this zip (contrast with POST /onboarding's
    personalized download, which bakes in a token). An already-connected
    site's credentials live in its own wp_options, untouched by an update."""
    zip_bytes = build_plain_zip()
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="engage-ai.zip"'},
    )
