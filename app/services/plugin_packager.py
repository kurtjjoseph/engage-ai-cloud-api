import io
import json
import zipfile
from pathlib import Path

# Copied from ~/Downloads/engage-ai-wordpress/engage-ai - not a live checkout.
# Re-sync (rsync -a, excluding .DS_Store) whenever the plugin changes, or
# personalized downloads will silently ship a stale version.
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "plugin_template" / "engage-ai"


def _zip_template() -> tuple[io.BytesIO, zipfile.ZipFile]:
    buffer = io.BytesIO()
    zf = zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED)
    for path in sorted(TEMPLATE_DIR.rglob("*")):
        if path.is_file():
            arcname = "engage-ai/" + str(path.relative_to(TEMPLATE_DIR))
            zf.write(path, arcname)
    return buffer, zf


def build_personalized_zip(api_base_url: str, token: str, organization_id: int) -> bytes:
    """Builds a ready-to-install engage-ai.zip with includes/preconfigured.php
    baked in, so activating it in WordPress skips the Settings connect flow
    entirely - the plugin's activation hook reads this file if present.
    Used by POST /onboarding for first-time installs."""
    preconfigured_php = _render_preconfigured_php(api_base_url, token, organization_id)

    buffer, zf = _zip_template()
    zf.writestr("engage-ai/includes/preconfigured.php", preconfigured_php)
    zf.close()

    return buffer.getvalue()


def build_plain_zip() -> bytes:
    """The same plugin source with no includes/preconfigured.php baked in -
    used for plugin *updates* (GET /plugin/download.zip), since an already-
    connected site's token/org id live in wp_options, not in this file, and
    an update zip shouldn't imply a fresh onboarding identity."""
    buffer, zf = _zip_template()
    zf.close()
    return buffer.getvalue()


def _render_preconfigured_php(api_base_url: str, token: str, organization_id: int) -> str:
    # Values originate from our own DB/JWT issuance (URL + JWT are guaranteed
    # plain ASCII), but json.dumps is still used here rather than raw string
    # interpolation so nothing in these values can break out of the PHP
    # string literal.
    return f"""<?php
if (!defined('ABSPATH')) {{
    exit;
}}

// Auto-generated per download by POST /onboarding - do not hand-edit.
// If this file is present, the plugin's activation hook uses it to connect
// automatically instead of showing the Settings page's connect form.
return [
    'api_base_url' => {json.dumps(api_base_url)},
    'token' => {json.dumps(token)},
    'organization_id' => {int(organization_id)},
];
"""
