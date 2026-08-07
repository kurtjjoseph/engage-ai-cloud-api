# Content types by channel

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

## Website

| Content type | id | Produces | Canvas | Publish | Fields drafted beyond the copy |
|---|---|---|---|---|---|
| **Blog post** — A dated article on the site's blog. | `website.post` | 1 image | 1200×630 | draft | — |
| **Standalone page** — An undated page - a service, an FAQ, a cornerstone resource. | `website.page` | 1 image | 1200×630 | draft | — |

## Google Business

| Content type | id | Produces | Canvas | Publish | Fields drafted beyond the copy |
|---|---|---|---|---|---|
| **What's new** — The standard Google Business update post. | `google_business.whats_new` | 1 image | 1200×900 | **live** | `cta_label`, `cta_url` |
| **Offer** — A time-bound promotion with a coupon code and terms. | `google_business.offer` | 1 image | 1200×900 | manual | `offer_title`, `starts_on`, `ends_on`, `coupon_code`, `terms`, `cta_url` |
| **Event** — A dated event on the business profile. | `google_business.event` | 1 image | 1200×900 | manual | `event_title`, `starts_on`, `ends_on`, `cta_label`, `cta_url` |
| **Update / alert** — A short notice - changed hours, a closure, an urgent update. | `google_business.alert` | text only | — | manual | `cta_url` |

## YouTube

| Content type | id | Produces | Canvas | Publish | Fields drafted beyond the copy |
|---|---|---|---|---|---|
| **Short** — A vertical Short - the format the algorithm pushes hardest. | `youtube.short` | video | 720×1280 · 30s | **live** | `video_title`, `slides` |
| **Standard video** — A landscape video for the main feed. | `youtube.video` | video | 1280×720 · 45s | **live** | `video_title`, `slides` |
| **Community post** — A text-and-image post to subscribers, between videos. | `youtube.community_post` | 1 image | 1280×720 | manual | — |

## Facebook

| Content type | id | Produces | Canvas | Publish | Fields drafted beyond the copy |
|---|---|---|---|---|---|
| **Text post** — A plain conversational post. | `facebook.text_post` | text only | — | **live** | — |
| **Photo post** — A photo with a caption - the everyday Page post. | `facebook.photo_post` | 1 image | 1200×630 | **live** | — |
| **Link post** — A post whose payload is a link with its preview card. | `facebook.link_post` | text only | — | **live** | `link_url` |
| **Reel** — A vertical short video on the Page. | `facebook.reel` | video | 720×1280 · 30s | manual | `slides` |
| **Story** — A full-screen frame that expires after 24 hours. | `facebook.story` | 1 image | 1080×1920 | manual | `overlay_headline` |
| **Event** — A Facebook event with a date, a place and a cover image. | `facebook.event` | 1 image | 1200×628 | manual | `event_title`, `starts_on`, `ends_on`, `location`, `ticket_url` |

## Instagram

| Content type | id | Produces | Canvas | Publish | Fields drafted beyond the copy |
|---|---|---|---|---|---|
| **Feed post** — A single image in the grid. | `instagram.feed_image` | 1 image | 1080×1080 | **live** | — |
| **Carousel** — Swipeable pages - the format people save and send on. | `instagram.carousel` | N images | 1080×1350 | manual | `slides` |
| **Reel** — A vertical short video. | `instagram.reel` | video | 720×1280 · 30s | **live** | `slides` |
| **Story** — A full-screen frame that expires after 24 hours. | `instagram.story` | 1 image | 1080×1920 | manual | `overlay_headline` |

## LinkedIn

| Content type | id | Produces | Canvas | Publish | Fields drafted beyond the copy |
|---|---|---|---|---|---|
| **Text post** — The plain professional post. | `linkedin.text_post` | text only | — | **live** | — |
| **Image post** — A post with one supporting image. | `linkedin.image_post` | 1 image | 1200×627 | **live** | — |
| **Multi-image post** — Two to nine images in one post. | `linkedin.multi_image` | N images | 1200×1200 | manual | `slides` |
| **Video post** — A native video - uploaded, not linked. | `linkedin.video_post` | video | 1080×1080 · 45s | manual | `slides` |
| **Document (PDF carousel)** — A swipeable PDF - LinkedIn's highest-dwell format. | `linkedin.document` | PDF | 1080×1350 | manual | `document_title`, `slides` |
| **Article** — Long-form published on LinkedIn itself. | `linkedin.article` | 1 image | 1200×627 | manual | `article_title` |
| **Poll** — A question with two to four options. | `linkedin.poll` | text only | — | manual | `question`, `options`, `duration` |

## X / Twitter

| Content type | id | Produces | Canvas | Publish | Fields drafted beyond the copy |
|---|---|---|---|---|---|
| **Post** — A single post. | `twitter_x.tweet` | text only | — | **live** | — |
| **Thread** — A sequence of posts that reads as one argument. | `twitter_x.thread` | text only | — | manual | `parts` |
| **Poll** — A question with two to four options. | `twitter_x.poll` | text only | — | manual | `options`, `duration` |

## TikTok

| Content type | id | Produces | Canvas | Publish | Fields drafted beyond the copy |
|---|---|---|---|---|---|
| **Video** — A vertical video. Generated here; not yet a scored channel. | `tiktok.video` | video | 720×1280 · 30s | manual | `slides` |

## Totals

**30 content types across 8 channels.**
Engage AI publishes 11 of them directly today and 2 as
site drafts; the remaining 17 are generated for the owner to post.

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
POST /studio/{id}/check?revise=      re-check, optionally rewrite
POST /studio/{id}/edit               operator edits, re-checked on save
POST /studio/{id}/render             -> image | N images | PDF | MP4
```
