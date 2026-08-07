# Generating posts and video for every channel — research and build plan

Scope: how Engage AI should generate **social posts** and **video posts** for all
channels it scores, and what it takes to actually get them published. Written
against the code as it stands at commit `af5eeaa` (plugin 0.24.0), live at
<https://engage-ai-api.onrender.com/dashboard>.

Everything below is split into two layers that fail independently and are worth
budgeting separately:

* **Generation** — turning a goal into copy + pixels + an MP4. Fully under our
  control. Cost is compute and model spend.
* **Distribution** — getting the asset into a platform's feed. Not under our
  control. Cost is app review, audits, quota, and per-channel API weirdness.

Most of the perceived "we can't post video" problem is distribution, not
generation. But the biggest *quality* problem is generation. Both are addressed.

---

## 1. Where we actually are

### Generation (Content Studio, `services/studio_formats.py` + `services/media_gen.py`)

Three formats, deliberately three, each with a deterministic local renderer so
output is guaranteed even with no API key:

| Format | Media | Canvas | Notes |
|---|---|---|---|
| `post_image` | image | 1080×1080 (per-channel overrides to 1200×630 / 1200×675 / 1280×720) | copy + illustrative image |
| `image_text` | image | 1080×1350 (overrides as above) | headline composited on the image |
| `video_slideshow` | video | 720×1280, 9:16 | 4 slides × 2s = **8.00s**, slow zoom, crossfade, narration centred |

Copy rules are per channel (`_CHANNEL_COPY`): body ceiling, length target,
hashtag ceiling, and a note. Eight channels are modelled: website, instagram,
facebook, linkedin, twitter_x, google_business, youtube (news_mentions is scored
but not generated for).

Image generation (`ImageGenService`) tries OpenAI `gpt-image-1` when a key is
set, else falls back to **Pollinations** (keyless, flux). Video
(`render_slideshow`) is stitched locally with imageio + system ffmpeg —
libx264, yuv420p, 24fps.

**The MP4 has no audio track at all.** There is no TTS, no music bed, no
`audio` parameter anywhere in the writer call
(`app/services/media_gen.py:386-389`). "Narration" is on-screen text only.

### Distribution (`services/channels/live.py`, shipped 2026-07-31)

| Channel | Text | Image | Video | How |
|---|---|---|---|---|
| Website | ✅ | ✅ | — | WP plugin, Gutenberg blocks |
| Facebook Page | ✅ | ✅ `/photos` | ❌ | no `/video_reels` adapter |
| Instagram | — | ✅ | ✅ `media_type=REELS` | container + poll + publish |
| LinkedIn | ✅ | ✅ (`/rest/images`) | ❌ | no `/rest/videos` adapter |
| YouTube | — | — | ✅ (forced **unlisted**) | resumable upload |
| X / Twitter | ✅ | ❌ | ❌ | `supports_media=False` by design |
| Google Business | ✅ | ✅ (signed URL) | ❌ | v4 `localPosts` |
| TikTok | ❌ | ❌ | ❌ | **channel does not exist in the product** |

So: **we generate video for 7 channels and can publish it to 2.**

---

## 2. The two real gaps

### Gap A — the video itself is not competitive

An 8-second silent slideshow is technically a valid Reel, and it renders every
time, which was the right first call. But measured against how these formats are
actually consumed in 2026 it is off-spec in three ways:

1. **Length.** TikTok's engagement band is ~21–34s; Reels average their best
   engagement at 15–30s; Shorts land best at ~20–45s. Eight seconds sits under
   all three. ([sureshot.video](https://sureshot.video/blog/how-long-is-a-short-form-video),
   [recapo.ai](https://recapo.ai/blog/best-clip-length-for-shorts-reels-tiktok/))
2. **No audio.** Every short-form surface assumes an audio track; a silent
   upload reads as low-effort to both viewers and ranking. This is also the
   cheapest thing on this whole list to fix.
3. **Static text, not captions.** Captioned video gets ~12% more watch time and
   is ~80% more likely to be watched to completion; the working pattern is
   3–7-word on-screen chunks timed to speech, not one held sentence per slide.
   ([opus.pro](https://www.opus.pro/blog/youtube-vs-instagram-vs-tiktok-caption-best-practices))

The hook rule is worth encoding in the drafting prompt directly: the first
1–3 seconds must do a pattern interrupt, set a promise, and open a loop. Shorts
with a hook inside 2 seconds retain measurably more viewers.
([virvid.ai](https://virvid.ai/blog/first-3-seconds-hook-faceless-shorts-2026))

### Gap B — five channels can take video and we don't send it

Facebook, LinkedIn, TikTok are all buildable. X is a genuine dead end on our
current auth. GBP is probably photo-only. Details in §4.

---

## 3. Generation — the options

### 3.1 Copy

Already solved and cheap (one Claude call per piece, per-channel layout
contract, deterministic quality check first). The improvements are prompt-level,
not architectural:

- Add a **hook contract** to the video prompt: slide 1 is the hook and is
  scored separately by the deterministic check (must be ≤ 8 words, must not
  open with the brand name).
- Add a **caption-length target per channel** distinct from the body ceiling —
  TikTok 80–100 chars, Instagram 138–150 chars are the measured sweet spots;
  LinkedIn is the inversion (the text *is* the content, so longer wins).

### 3.2 Images

Pollinations was the right unblock — keyless, ~6s, works today. Its honest
limits for production: no SLA, anonymous tier throttled to roughly one request
per 15s, and it 429s on concurrency (we already discovered this and serialised
the slideshow renders). ([pollinations FAQ](https://pollinations-ai.com/faq.html),
[tooljunction](https://www.tooljunction.io/ai-tools/pollinations))

The upgrade path that matters is **text-in-image**, because `image_text` is the
format most likely to look amateur. Current approach composites the headline
with PIL, which is actually the *reliable* choice — models still fumble type.
Keep PIL compositing as the default; if we ever want type generated into the
art, Ideogram V3 is the short-typography specialist and GPT Image 2 the
dense-text one; Imagen 4 Ultra is ~$0.06/image for reference pricing.
([masonry](https://masonry.so/blog/best-ai-image-model-for-text-rendering),
[teamday](https://www.teamday.ai/blog/best-ai-image-models-2026))

Recommendation: keep Pollinations as the free tier, add a `FAL_KEY` /
Replicate-backed Flux path as the paid tier behind the same
`ImageGenService.generate_image` interface, and give the org a quality toggle.
No architectural change — it already degrades gracefully.

### 3.3 Video — three tiers, and they are not alternatives

This is the core finding. Treat these as a **ladder**, not a choice, because
they have completely different cost, latency and failure profiles.

**Tier 1 — deterministic assembly (what we have, upgraded).** ffmpeg + PIL +
generated stills. Cost ≈ €0. Latency dominated by image generation. Never fails
in a way that produces nothing. Upgrades available without leaving this tier:

- **Add a voice track.** Kokoro-82M (Apache 2.0, ~82M params, 54 voices, 8
  languages, faster than realtime on CPU, 2–3GB) is the obvious fit — it runs
  inside the existing Render container next to ffmpeg, no key, no per-call cost,
  and matches the "keyless by default" design rule the studio already follows.
  Chatterbox (MIT, zero-shot cloning, beat ElevenLabs in blind preference tests)
  is the upgrade if per-client voice ever matters.
  ([bentoml](https://www.bentoml.com/blog/exploring-the-world-of-open-source-text-to-speech-models),
  [tryspeakeasy](https://www.tryspeakeasy.io/blog/open-source-text-to-speech-2026))
- **Let the voice drive the timing.** Once there is narration audio, slide
  duration should come from the TTS output length, not a hardcoded 2.0s — which
  naturally moves the video from 8s into the 20–35s band.
- **Word-level captions.** Whisper (or WhisperX for forced alignment) over our
  own TTS output → word timings → burn with ffmpeg `subtitles`/ASS. Several
  MIT/Apache reference pipelines exist to lift from.
  ([ai-video-captions](https://github.com/nicolaigaina/ai-video-captions))
  Note we can skip transcription entirely for TTS audio if the engine returns
  timings — cheaper and exact.
- **A music bed** at −18dB under the voice, from a royalty-free pack shipped in
  the image. Licensing must be checked once, then it is free forever.

**Tier 2 — template render APIs.** JSON scene description → finished MP4.
Buys motion design we would otherwise hand-code. JSON2Video ~$49/mo for 200 min
1080p with TTS included in the same credit bucket; Shotstack ~$49/mo for 200 min
720p (+30% overage premium); Creatomate ~$54/mo ≈143 min 720p with TTS billed
separately; Remotion is React-based and self-hosted (most control, most build).
([samautomation](https://samautomation.work/blog/best-video-apis-developers-2026/),
[json2video comparisons](https://json2video.com/how-to/creatomate-alternative/))

Verdict: **skip Tier 2 for now.** It buys polish we can approximate in Tier 1,
adds a vendor to the critical path of a per-tenant product, and its pricing is
per-minute — the wrong shape for a per-client SaaS with many small tenants.
Revisit only if hand-built motion design becomes the bottleneck.

**Tier 3 — generative video models.** Real footage, no stock, no slideshow feel.
Pricing has fallen far enough to matter: Veo 3.1 Lite ~$0.05/s and Veo 3.1 Fast
~$0.10/s at 720p, Sora 2 ~$0.10/s, Kling 3.0 / Seedance 2.0 ~$0.09–0.14/s,
LTX 2.3 Fast ~$0.06/s at 1080p; Veo 3.1 Standard ~$0.40/s is the premium end.
Veo 3.1 is currently the only one shipping **native audio**, which removes a
whole pipeline stage. ([modelslab](https://modelslab.com/blog/api/veo-3-1-vs-kling-3-sora-2-ai-video-api-cost-2026),
[buildmvpfast](https://www.buildmvpfast.com/api-costs/ai-video))

Practical cost: a 30s clip is ~$1.50 at Veo Fast, ~$3.00 at Sora 2, ~$12 at Veo
Standard. Most models cap generation at 5–10s per call, so a 30s piece is a
multi-shot stitch — Tier 1's assembly code becomes the *stitcher* for Tier 3
clips rather than being replaced by it.

Verdict: **Tier 3 is a premium per-org add-on, not the default.** Gate it behind
an org flag with a hard monthly budget, generate the hook shot only (first 3–5s
is where the money buys the most), and fall back to Tier 1 stills for the rest.

---

## 4. Distribution — channel by channel, with what will bite

### Instagram — ✅ working, worth tightening
Three-step publish (container → poll `status_code` → `media_publish`) is already
what `InstagramAdapter` does. Constraints to encode in the renderer:

- Reels-tab eligibility: 9:16, **5–90 seconds**, H.264 or HEVC, Business
  account. Outside that range it silently publishes as a plain video post
  instead — which is the failure mode nobody notices.
- Container MOV/MP4, `moov` atom at the front, no edit lists; audio AAC ≤48kHz;
  23–60 fps. Our libx264/yuv420p/24fps output complies; adding AAC audio keeps
  it compliant.
- **100 API-published posts per 24h** per account (per Meta's own docs).
- Permissions: `instagram_business_basic` + `instagram_business_content_publish`
  (Instagram Login) or `instagram_basic` + `instagram_content_publish` +
  `pages_read_engagement` (Facebook Login).
  ([Meta content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/),
  [postproxy](https://postproxy.dev/blog/instagram-reels-api-publishing-guide/))

One caution: third-party guides quote an 8MB video cap, which does not match
Meta's own documentation and is almost certainly a stale figure for a different
endpoint. **Verify empirically before sizing the encoder.**

### Facebook — ❌ missing, and it is the cheapest win
The Reels Publishing API is a three-phase flow on `/{page-id}/video_reels`:
`upload_phase=start` returns a `video_id`, then a binary upload, then
`upload_phase=finish` with the description. Page-only (no personal profiles or
groups). Requirements: ≥540×960, 9:16, ≥23fps, **4–60 seconds** — our current
8s 720×1280 output already qualifies as-is. Permissions overlap what we
already request: `pages_show_list`, `pages_read_engagement`,
`pages_manage_posts`. ([Meta reels publishing](https://developers.facebook.com/docs/video-api/guides/reels-publishing/),
[ayrshare](https://www.ayrshare.com/blog/facebook-reels-api-how-to-post-fb-reels-using-a-social-media-api/))

Build estimate: one adapter branch in `FacebookPageAdapter._publish` on
`asset.kind == "video"`. Same token we already hold.

### LinkedIn — ❌ missing, straightforward
`POST /rest/videos?action=initializeUpload` with `owner`, `fileSizeBytes`,
`uploadCaptions`, `uploadThumbnail` → one or more upload URLs (chunked, ETags
tracked) → finalize → reference the returned URN in the Posts API. Headers
`LinkedIn-Version: YYYYMM` and `X-Restli-Protocol-Version: 2.0.0` are mandatory.
This mirrors the `/rest/images` flow `LinkedInAdapter._upload_image` already
implements. ([Microsoft Learn — Videos API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api?view=li-lms-2026-04))

### YouTube — ✅ working, one policy call and one good surprise
- **The quota picture improved materially.** `videos.insert` reportedly dropped
  from ~1600 units to ~100 units per call on 2025-12-04, taking the default free
  project from ~6 uploads/day to ~100. Anything beyond 10,000 units/day still
  needs a quota-extension request plus a compliance audit, with reported waits
  of weeks to months. ([getphyllo](https://www.getphyllo.com/post/youtube-api-limits-how-to-calculate-api-usage-cost-and-fix-exceeded-api-quota),
  [Google quota audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits))
  **Confidence: medium — third-party reporting. Measure our own consumption
  before relying on it.** At ~6/day it is a per-tenant bottleneck; at ~100/day
  it stops being an issue for a long time.
- We force `unlisted` on purpose. That is defensible for an autonomous system,
  but it means every YouTube "publication" we report is invisible. Either make
  privacy an explicit per-org setting with the default staying unlisted, or stop
  counting unlisted uploads as distribution in the running inventory.

### TikTok — ❌ absent, highest strategic value, highest friction
Not a channel in the product at all today, and it is the single most obvious
omission for a short-video product.

- Two scopes: `video.upload` drops the video into the user's TikTok inbox for
  manual confirmation; `video.publish` posts directly.
- **Unaudited clients cannot post publicly** — content is forced to private, and
  at most 5 users may post per 24h. Public direct posting requires app audit,
  reported at 2–4 weeks with multiple feedback rounds.
- Hard UX requirements the reviewer checks: the creator's **username and avatar
  must be shown before every post**, and the creator must pick a privacy level
  (public / friends / private).
  ([TikTok content-sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines),
  [netrows](https://www.netrows.com/blog/tiktok-content-posting-api-guide-2026))

Those UX rules are a **product design constraint on the WP plugin**, not just an
API detail — the Studio publish step has to render the connected account's
avatar and a privacy selector, or the audit fails. Note that `video.upload`
(inbox) needs no audit and is a perfectly good v1: Engage AI renders, the owner
taps confirm in the TikTok app. That fits our "authorization ≠ consent to
publish" rail better than direct posting does anyway.

### X / Twitter — ❌ and honestly blocked
`POST /2/media/upload` with an OAuth 2.0 user token is returning 403 for many
developers even with `media.write` granted, while `/2/tweets` works with the
same token; OAuth 1.0a remains the reliable media path. Our current
`supports_media=False` is the correct call, not a shortcoming.
([devcommunity thread](https://devcommunity.x.com/t/post-2-media-upload-returns-403-while-2-tweets-works-with-the-same-oauth2-user-token/265176))

Options: add OAuth 1.0a signing as a second credential type for X only, or
accept text-only on X, or route X through an aggregator. **Recommendation:
accept text-only and say so in the UI** (we already do) until X's v2 media path
stabilises. It is the lowest-value video surface of the seven.

### Google Business — likely photo-only, verify
`localPosts` v4 is alive (the 2021–22 sunset hit v4.9 legacy endpoints, not the
current Local Posts API), and the `media[].mediaFormat` enum names VIDEO. But
video creation on local posts is not something Google supports in practice.
**Verify with one live call before building anything.** Treat GBP as an
image + text channel. ([Google — localPosts](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts),
[sunset dates](https://developers.google.com/my-business/content/sunset-dates))

### The channels we don't have at all
Pinterest (Trial access is sandbox-only until Standard is granted), Threads,
and Bluesky (no gatekeeping, trivial to add) are all real surfaces we score
nothing for. Bluesky in particular is near-zero-cost to support.
([postpeer](https://www.postpeer.dev/blog/best-bluesky-posting-api),
[blotato](https://www.blotato.com/blog/pinterest-api-pricing))

### The aggregator question
Ayrshare (30+ platforms, SDKs in 8 languages), Postiz (open-source,
self-hostable, MCP server included), and Blotato (flat rate) all collapse
"seven app reviews" into one integration.

The economics decide it. Ayrshare bills **per connected profile** — ~$770/mo at
50 profiles; Postiz is $99/mo at 50 accounts hosted, or server cost self-hosted;
Blotato ~$499/mo at 50. ([blotato comparison](https://www.blotato.com/blog/blotato-vs-ayrshare),
[buffer](https://buffer.com/resources/social-media-api-multi-platform-posting/))

For a per-tenant product where every client brings 4–6 profiles, per-profile
pricing scales exactly wrong. And we have already built the hard part —
`services/channels/providers.py` + `connections.py` is a working per-provider
auth layer with encryption at rest and refresh-before-use. **Recommendation: do
not adopt an aggregator as the primary path.** Keep self-hosted Postiz on the
shelf as the specific escape hatch for channels stuck in app review (TikTok
public posting, Pinterest Standard), since it self-hosts and does not meter
per profile.

---

## 5. Per-channel spec table to encode in `studio_formats.py`

The current `_FORMAT_CANVAS` hardcodes one video canvas for all channels. That
is nearly right (9:16 everywhere) but the *duration* must become per-channel,
because the eligibility windows differ and silently downgrade when missed.

| Channel | Aspect | Duration to target | Hard window | Caption target |
|---|---|---|---|---|
| Instagram Reels | 9:16 | 20–30s | 5–90s (else not in Reels tab) | 138–150 chars |
| Facebook Reels | 9:16 | 20–30s | **4–60s**, ≥540×960, ≥23fps | ~1200 chars |
| YouTube Shorts | 9:16 | 25–45s | ≤180s to count as a Short | title + 2–3 para description |
| TikTok | 9:16 | 21–34s | wide | 80–100 chars |
| LinkedIn | 9:16 or 1:1 | 30–60s | wide | longer — text is the content |
| X | 16:9 | n/a | text-only for us | ≤270 chars |
| Google Business | — | n/a | photo + text | 100–250 words, no hashtags |
| Website | 16:9 embed | any | none | 180–350 words HTML |

Sources for the duration bands: [sureshot](https://sureshot.video/blog/how-long-is-a-short-form-video),
[recapo](https://recapo.ai/blog/best-clip-length-for-shorts-reels-tiktok/),
[joinbrands](https://joinbrands.com/blog/youtube-shorts-best-practices/).

---

## 6. Recommended build order

Ordered by (value ÷ friction), not by ambition. Each step ships independently.

**1. Give the video a voice, real length, and captions.** *(Tier 1, no external
dependency, no app review, no per-call cost.)*
Kokoro TTS into the Render image beside ffmpeg → narration audio per slide →
slide duration derived from audio length → word-timed captions burned with
ffmpeg → AAC muxed into the existing libx264 output. Drop the hardcoded
`VIDEO_SECONDS = 8.0` for a per-channel target with the hard windows above as
guardrails. This is the largest quality jump available and it touches one file.

**2. Facebook Reels + LinkedIn video adapters.** Both reuse the exact MP4 we
already render and the tokens we already hold. Takes video from 2 publishable
channels to 4. No new permissions beyond what the existing app requests.

**3. TikTok as an inbox-upload channel (`video.upload`).** No audit required,
so it ships in days rather than weeks, and the owner-confirms step is a better
fit for the consent rails than direct posting. Build the avatar + privacy-picker
UI at the same time — that is the audit prerequisite if we later want
`video.publish`.

**4. Hook contract + per-channel caption targets in the drafting prompt,**
enforced by the existing deterministic quality check. Prompt-level, near-free.

**5. Bluesky and Threads adapters.** Cheap, and they widen the benchmark
surface the product scores against.

**6. Generative video (Tier 3) as a per-org premium flag.** Veo 3.1 Fast for
the hook shot only, hard monthly cap per org, Tier 1 stills for the remainder,
falls back silently when the budget is spent.

Explicitly **not** recommended: adopting a per-profile aggregator as the primary
publishing path; adopting a per-minute template render API; building OAuth 1.0a
signing just for X media.

---

## 7. Things that will bite, listed so they can't ambush us

1. **Meta app review is the long pole.** 2–6 weeks for Advanced Access on
   `instagram_content_publish`, business verification is a separate and common
   independent rejection, and most rejections are incomplete screencasts. The
   paste-a-long-lived-token escape hatch already built into 0.23.0 is what keeps
   customers unblocked meanwhile — keep it, and make sure the docs say the
   Facebook long-lived token expires around 60 days with no refresh path.
   ([singhamandeep](https://singhamandeep.com/instagram-api-advanced-access-approval/))
2. **`TOKEN_ENCRYPTION_KEY` must be set in Render before the first customer
   connects a channel.** Changing it later invalidates every stored connection.
   Still open (open item 8 in the project notes).
3. **Off-spec video downgrades silently.** A 95-second Reel or a 61-second FB
   Reel does not error — it publishes as something else. The renderer must
   enforce the window, not the platform.
4. **Pollinations has no SLA and throttles anonymous traffic.** Fine as the free
   tier; needs a keyed fallback before any tenant depends on volume.
5. **TikTok's UX rules are product requirements.** Username + avatar shown
   before posting, and a privacy selector, or the audit fails.
6. **YouTube's unlisted default makes our own reporting dishonest** if unlisted
   uploads are counted as published reach.
7. **The quota figure for `videos.insert` is third-party sourced.** Measure it.

---

## Sources

- [TikTok Content Posting API guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines) · [Netrows guide 2026](https://www.netrows.com/blog/tiktok-content-posting-api-guide-2026) · [PostPeer](https://www.postpeer.dev/blog/best-tiktok-posting-api)
- [Meta — Publish a Reel](https://developers.facebook.com/docs/video-api/guides/reels-publishing/) · [Meta — Instagram content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/) · [Ayrshare on FB Reels](https://www.ayrshare.com/blog/facebook-reels-api-how-to-post-fb-reels-using-a-social-media-api/) · [Postproxy Reels guide](https://postproxy.dev/blog/instagram-reels-api-publishing-guide/)
- [LinkedIn Videos API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api?view=li-lms-2026-04) · [LinkedIn Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-05)
- [YouTube quota and compliance audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits) · [Phyllo on YouTube quota](https://www.getphyllo.com/post/youtube-api-limits-how-to-calculate-api-usage-cost-and-fix-exceeded-api-quota)
- [X devcommunity — /2/media/upload 403 with OAuth2](https://devcommunity.x.com/t/post-2-media-upload-returns-403-while-2-tweets-works-with-the-same-oauth2-user-token/265176)
- [Google Business Profile localPosts](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts) · [GBP deprecation schedule](https://developers.google.com/my-business/content/sunset-dates)
- [Meta Advanced Access requirements](https://singhamandeep.com/what-is-meta-advanced-access/) · [Instagram Advanced Access approval](https://singhamandeep.com/instagram-api-advanced-access-approval/)
- [AI video API pricing 2026 — ModelsLab](https://modelslab.com/blog/api/veo-3-1-vs-kling-3-sora-2-ai-video-api-cost-2026) · [BuildMVPFast API costs](https://www.buildmvpfast.com/api-costs/ai-video) · [Awesome Agents pricing](https://awesomeagents.ai/pricing/video-generation-pricing/)
- [Open-source TTS 2026 — BentoML](https://www.bentoml.com/blog/exploring-the-world-of-open-source-text-to-speech-models) · [Speakeasy comparison](https://www.tryspeakeasy.io/blog/open-source-text-to-speech-2026) · [Inworld self-hosted TTS](https://inworld.ai/resources/best-self-hosted-tts)
- [Video API comparison — Samautomation](https://samautomation.work/blog/best-video-apis-developers-2026/) · [JSON2Video vs Creatomate](https://json2video.com/how-to/creatomate-alternative/) · [JSON2Video vs Shotstack](https://json2video.com/how-to/shotstack-alternative/)
- [Blotato vs Ayrshare](https://www.blotato.com/blog/blotato-vs-ayrshare) · [Buffer multi-platform APIs](https://buffer.com/resources/social-media-api-multi-platform-posting/) · [Upload-Post comparison](https://www.upload-post.com/best-social-media-apis/)
- [Text rendering in images — Masonry](https://masonry.so/blog/best-ai-image-model-for-text-rendering) · [Teamday model ranking](https://www.teamday.ai/blog/best-ai-image-models-2026) · [Pollinations FAQ](https://pollinations-ai.com/faq.html)
- [Short-form length by platform — Sureshot](https://sureshot.video/blog/how-long-is-a-short-form-video) · [Recapo clip length](https://recapo.ai/blog/best-clip-length-for-shorts-reels-tiktok/) · [YouTube Shorts best practices](https://joinbrands.com/blog/youtube-shorts-best-practices/) · [Caption best practices — OpusClip](https://www.opus.pro/blog/youtube-vs-instagram-vs-tiktok-caption-best-practices) · [First 3 seconds](https://virvid.ai/blog/first-3-seconds-hook-faceless-shorts-2026)
- [Whisper + ffmpeg caption pipeline](https://github.com/nicolaigaina/ai-video-captions) · [Transloadit subtitle workflow](https://transloadit.com/devtips/cli-subtitle-workflow-generate-convert-and-burn/)
- [Bluesky posting API](https://www.postpeer.dev/blog/best-bluesky-posting-api) · [Pinterest API access tiers](https://www.blotato.com/blog/pinterest-api-pricing)
