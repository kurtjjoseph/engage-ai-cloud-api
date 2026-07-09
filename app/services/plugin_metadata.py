import re
from app.services.plugin_packager import TEMPLATE_DIR


def get_plugin_metadata() -> dict:
    """Reads version/changelog/compatibility straight from the bundled
    plugin source (see plugin_packager.TEMPLATE_DIR) rather than hardcoding
    them anywhere else, so this can never silently drift out of sync with
    what actually ships."""
    main_text = (TEMPLATE_DIR / "engage-ai.php").read_text()
    version_match = re.search(r"Version:\s*([\d.]+)", main_text)
    version = version_match.group(1) if version_match else "0.0.0"

    readme_path = TEMPLATE_DIR / "readme.txt"
    readme_text = readme_path.read_text() if readme_path.exists() else ""

    requires = _readme_field(readme_text, "Requires at least") or "6.0"
    tested = _readme_field(readme_text, "Tested up to") or requires
    requires_php = _readme_field(readme_text, "Requires PHP") or "8.0"

    changelog_match = re.search(r"==\s*Changelog\s*==\s*(.*)", readme_text, re.DOTALL)
    changelog_raw = changelog_match.group(1).strip() if changelog_match else ""
    changelog_html = "".join(
        f"<p>{line.strip()}</p>" for line in changelog_raw.splitlines() if line.strip()
    )

    return {
        "version": version,
        "requires": requires,
        "tested": tested,
        "requires_php": requires_php,
        "changelog_html": changelog_html,
    }


def _readme_field(text: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}:\s*(.+)", text)
    return match.group(1).strip() if match else None
