=== Engage AI ===
Contributors: visionoutreachmedia
Tags: church, ai, content generation, engagement, automation
Requires at least: 6.0
Tested up to: 6.7
Requires PHP: 8.0
Stable tag: 0.16.0
License: GPLv2 or later

Generates and auto-publishes church engagement content, modular autonomous check-in agents for the 8 Claude AI side hustles, and web-search-based digital footprint analytics, via the Engage AI Cloud API.

== Description ==

Engage AI connects your WordPress site to the Engage AI Cloud API. It does three things, each independently switched on per organization under Settings > Modules:

* **Engagement** (the original feature): turns a message, sermon, or event into practical engagement — a website post, social media caption, email, WhatsApp message, presentation slides, and follow-up actions — matched to your organization's stored voice (mission, tone, audience).
* **Agent modules** (one per Claude AI side hustle — physical product business, reselling, YouTube channel growth, paid Q&A, local service business, app building, UGC creation, coaching): each runs its own autonomous check-in cycle, proposing concrete work as tickets you approve, reject, or redirect from the Agents page. Anything reversible it drafts immediately; anything that would spend money or act publicly is held for your explicit approval.
* **Analytics**: searches the web for the organization's public digital footprint (website, social profiles, reviews, etc.) and records what it finds per channel. The first scan is flagged as the baseline so later scans have a fixed reference point to compare against, instead of just comparing to whatever the last scan said.

The AI Assistant page (Engage AI > AI Assistant) answers free-form questions grounded in the organization's stored context, for anything that doesn't fit one of the structured generators or a specific agent niche.

= Setup =

1. Deploy the Engage AI Cloud API (see the `engage-ai-cloud-api` project) and note its base URL.
2. In WordPress, go to Engage AI > Settings and enter the API URL.
3. Connect with the email/password you registered with on the API (your password is used once to connect and is not stored — only the resulting session token is kept).
4. Select or create your organization. A website URL sharpens the Analytics module's search a lot for common organization names.
5. Under Settings > Modules, turn on Engagement, Analytics, and/or whichever side-hustle agents this organization needs.
6. Go to Engage AI > Generate Content for the church-engagement generators, Engage AI > Agents for the ticket dashboard of any active side-hustle module, or Engage AI > Analytics to run a scan.

== Changelog ==

= 0.16.0 =
* New Content page: see everything Engage AI has generated for your site in one place, and click "Suggest content" to have the AI draft a few website posts tailored to your site type (a WooCommerce shop gets product-led posts, a church gets sermon/event content, other sites get how-to/expertise posts). Turn any suggestion into a WordPress draft in one click to review and publish. The plugin now reports your site type to the API so suggestions fit the kind of site you run.

= 0.15.0 =
* The plugin now reports your site's real published post and page counts to the API, so your website's analytics score reflects that the site is live and how much content it has actually published - even when a search engine hasn't indexed the site yet. Previously a small or brand-new site could score 0 across the board because the web search couldn't find it; now the website channel is scored from ground truth the plugin knows directly. Counts refresh automatically as you publish.

= 0.14.0 =
* Scores now reward channel availability (how many channels you're actually live on) as an explicit part of the org score, and count the number of posts/pages/videos published per channel. Simply having a channel and having any content on it now both count - you no longer need a large volume before content registers. The Dashboard and Analytics pages now show "X of 8 channels live" and total pieces of content published. A score of 0 means no presence online at all.

= 0.13.0 =
* On first run the plugin now reports its site URL to the API, so the operator console can link straight to the live site and, if the same site had already been added in the console, the two records are merged automatically instead of tracking the site twice.

= 0.12.0 =
* The engagement_growth agent niche can now generate AND publish autonomously for one specific case: a "content_idea" ticket targeting the "website" channel lands straight in WordPress as a draft post, with no approval wait - a draft is fully reversible (nothing is public until you publish it live), unlike every other channel this plugin doesn't have a real publish integration for yet. Runs hourly via WP-Cron.

= 0.11.0 =
* Added an AI Assistant page: ask a free-form question, answered using the organization's stored context (mission, tone, audience, etc) - for anything that doesn't fit the structured generators or a specific agent niche.
* Approving a "high risk" agent ticket (one that spends money, posts publicly, or contacts someone directly) now triggers AI generation of the actual deliverable content, shown right on the ticket once approved. Previously these tickets only ever held a proposal description - the admin had to write the real thing by hand after approving.

= 0.10.0 =
* Analytics scans now run asynchronously: "Run new scan" returns instantly instead of holding the page open for 30s-3min+ waiting on Claude. The scan runs in the background; the Analytics page shows "Scan in progress" until it lands, then refresh to see the result. This replaces the timeout-raising in 0.9.1's scan fix - the scan no longer happens inside the HTTP request at all, so no timeout is long enough to matter.

= 0.9.1 =
* Fixed: scans, campaign generation, and agent check-in cycles could hit "cURL error 28: Operation timed out after 45001 milliseconds" - these all call the API's Claude-backed endpoints, which routinely take 30-90s (longer for scans now that they use web_fetch too), well past the 45-second timeout every API call used by default. These three now get up to 180s (120s for campaign generation).

= 0.9.0 =
* Added a Settings > Channel details section: record each channel's actual profile URL/handle once it exists, so the Analytics module verifies it directly instead of guessing from the organization name.
* Next-best-step tickets for a channel now link straight to that channel's Settings row, and - for "set this up from scratch" tickets - to the platform's own signup/creation page, so a ticket is something to act on immediately.

= 0.8.0 =
* The Dashboard's "Current scores" section now includes a radar chart of the current per-channel scores (fixed axis order, so the shape stays comparable scan to scan) alongside the existing ranking table.

= 0.7.0 =
* The Dashboard now separates agent clarifying questions ("Messages") from actionable next-best-step tickets ("Next best steps"), so a question that's blocking an agent doesn't get lost among proposals awaiting approve/reject/redirect.

= 0.6.0 =
* Added the Dashboard page (now the plugin's landing page): current org/channel engagement scores and next-best-step tickets aggregated across every active agent module, in one place.

= 0.5.0 =
* Added the Publications workflow: mark generated or manually-posted content as published, then scan it independently for its own performance over time.
* Analytics page now shows score/breakdown drill-down, a full channel ranking, and an engagement-type ranking (which kind of content performs best on average).
* Added engagement-growth score targets and wired up its next-best-action ticket payloads.
* Fixed a bug where the Analytics page read the old "metrics" field name instead of "kpis", so channel data never actually rendered.

= 0.4.0 =
* Analytics scans can now be scoped to specific channels instead of always running the full sweep.
* Added an opt-in per-page website visibility ranking (indexed status, keyword rankings, backlink/freshness signals, attributed third-party traffic estimates) - explicitly a discoverability proxy, not real analytics.

= 0.3.0 =
* Added the Analytics module: web-search-based per-channel digital footprint scans, with the first scan flagged as a baseline for later comparison.
* Organization details (website URL, mission, audience) can now be edited after creation, not just set at creation time.

= 0.2.0 =
* Modular activation: organizations turn on exactly the capabilities they need (Settings > Modules).
* Added autonomous agent modules for all 8 Claude AI side hustles, each with its own scheduled check-in cycle and ticket queue (approve/reject/redirect) on the new Agents page.

= 0.1.0 =
* Initial release: settings/connection flow, organization management, event/announcement/sermon generation with auto-publish to a WordPress post.
