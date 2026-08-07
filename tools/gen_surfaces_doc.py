#!/usr/bin/env python
"""Regenerates docs/channel-surfaces.md from the surface catalog.

The table is generated rather than written so it can't drift from the code:
add a surface to app/services/surfaces.py, run this, and the documentation is
correct by construction.

    .venv/bin/python tools/gen_surfaces_doc.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.services import surfaces as S  # noqa: E402

MEDIA = {"none": "text only", "image": "1 image", "images": "N images",
         "video": "video", "document": "PDF"}
PUBLISH = {"live": "**live**", "draft": "draft", "manual": "manual"}

HEADER = """# Content types by channel

Every post object Engage AI can generate, per channel.

Generated from `app/services/surfaces.py` - don't hand-edit, regenerate:

```bash
.venv/bin/python tools/gen_surfaces_doc.py
```

**Publish** is the honest state of distribution, checked against
`app/services/channels/live.py`:

| state | meaning |
|---|---|
| **live** | an adapter exists; Engage AI posts it once the channel is connected |
| draft | it lands as a draft for the owner to approve (the WordPress path) |
| manual | Engage AI produces the copy and the file; the owner posts it |

Generation does not depend on `publish` - every surface below drafts, checks
and renders today regardless of whether we can push it. What each "manual"
would take to become "live" is in
[content-generation-all-channels.md](content-generation-all-channels.md).
"""


def build() -> str:
    out = [HEADER]
    for channel in S.CHANNELS:
        out.append(f"## {S.channel_label(channel)}\n")
        out.append("| Content type | id | Produces | Canvas | Publish | Fields drafted beyond the copy |")
        out.append("|---|---|---|---|---|---|")
        for surface in S.surfaces_for(channel):
            canvas = f"{surface.width}×{surface.height}" if surface.width else "—"
            if surface.seconds:
                canvas += f" · {surface.seconds:.0f}s"
            extra = ", ".join(f"`{f.key}`" for f in surface.fields) or "—"
            out.append(
                f"| **{surface.label}** — {surface.summary} | `{surface.id}` | "
                f"{MEDIA[surface.media]} | {canvas} | {PUBLISH[surface.publish]} | {extra} |"
            )
        out.append("")

    counts = {state: sum(1 for s in S.SURFACES if s.publish == state)
              for state in ("live", "draft", "manual")}
    out.append(f"""## Totals

**{len(S.SURFACES)} content types across {len(S.CHANNELS)} channels.**
Engage AI publishes {counts['live']} of them directly today and {counts['draft']} as
site drafts; the remaining {counts['manual']} are generated for the owner to post.

Every one is drafted against the fields listed above, checked deterministically
against those same declarations, and - where it has a canvas - rendered to a
real file with no API key required.

## How a content type is added

One entry in `app/services/surfaces.py`. Its `fields` become the drafting
prompt, the validation rules and the render inputs at once, so there is no
prompt to write, no validator to add and no renderer branch to extend:

```
GET  /studio/surfaces                 the catalog below, as JSON
POST /studio/surfaces/ideas           goal -> ideas, each naming a surface
POST /studio/surfaces/draft           idea + surface -> copy + fields, checked
POST /studio/{{id}}/check?revise=      re-check, optionally rewrite
POST /studio/{{id}}/edit               operator edits, re-checked on save
POST /studio/{{id}}/render             -> image | N images | PDF | MP4
```
""")
    return "\n".join(out)


if __name__ == "__main__":
    target = pathlib.Path(__file__).resolve().parent.parent / "docs" / "channel-surfaces.md"
    target.write_text(build())
    print(f"wrote {target} ({len(S.SURFACES)} surfaces)")
