=== Engage AI ===
Contributors: visionoutreachmedia
Tags: church, ai, content generation, engagement, automation
Requires at least: 6.0
Tested up to: 6.7
Requires PHP: 8.0
Stable tag: 0.35.0
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
6. Go to Engage AI > Campaigns to plan a whole run of content at once, or Engage AI > Content Studio to create a single piece step by step. Everything either one produces collects in Engage AI > Content Library, and each piece opens back in the Studio from there.
7. Engage AI > Channels is where you connect the accounts it may post to — the "Set one up" tab walks you through a channel from scratch, including where to find its access token. Engage AI > Agents holds the ticket dashboard for any active side-hustle module, and Engage AI > Analytics runs a scan.

Only the pages for your active modules appear in the menu, so this list is longer than what most organizations see.

== Changelog ==

= 0.35.0 =
* Every finished piece now has a Share button. It hands the piece to the sharing your phone and the platforms already have — no accounts to connect, no permissions to grant, nothing to set up. It works today on channels Engage AI cannot post to automatically, which is most of them until you connect them.
* On a phone or tablet it opens your normal share sheet, so the piece can go to any app you have installed — including Instagram, which offers no web sharing of any kind.
* On a computer it offers the platforms directly. X, WhatsApp, Telegram, Reddit and Pinterest receive your caption already written. Facebook and LinkedIn accept a link only — they discarded caption support years ago — so those say so plainly and put your caption on the clipboard ready to paste.
* The caption is copied every time, whatever you pick. Nothing here quietly drops your words and posts an empty frame.
* Where a piece has a generated image, the share carries it on mobile, and there is a Download image link everywhere else.
* Replaces the old advice on the Content Library, which was the words "Copy & post" and nothing else.

= 0.34.0 =
* Engage AI can now actually post. Publishing was a locked step in 0.33.0, which meant everything else could run itself right up to the last move and then stop — finished posts piling up with nobody sending them. It is now a step like the others, on the Automation page.
* A piece goes out on the day it was planned for, to its own channel, using the same copy and the same connection the Publish button uses. Nothing is re-written or re-formatted on the way out.
* Four things can each stop a post on their own, and the Automation page names every one against a count: no date was ever set, the day has not come yet, the piece's own quality check flagged it, or the channel is not connected and switched on for posting. "Ready but not moving" always has a stated reason now.
* A piece whose quality check failed is never posted — if Engage AI has already told you the copy has placeholder text or a figure nothing supports, it will not then publish it anyway.
* Nothing posts to a channel you have not connected AND switched on for posting on the Channels page. Both switches, every time. If a channel is not genuinely ready, the piece stays in the queue rather than being quietly marked as sent.
* Fixed, and the reason this release was held: the older engagement-cycle feature reads the same per-channel setting, and its content is still placeholder text. Switching a channel on so your real content could go out would also have started posting "[DRAFT ...] placeholder content pending review" to that same account. It can no longer reach a live channel under any configuration.
* "Approve in a daily digest" and "approve each piece" are named on the Automation page but not built yet. Choosing one is refused outright rather than accepted and ignored — being told no is much better than thinking you are approving posts that are already going out.

= 0.33.0 =
* The steps you were doing by hand can now do themselves. Checking a new draft, writing a kept idea, building the pieces a campaign already planned, and taking the first performance measurement of a published post are all things Engage AI did the same way every time and still waited for you to click. Each is now a switch — on the new Automation page, and directly under the queue it drains on the page you are already looking at.
* Two switches, not one. A step's own switch says "you may do this for me". The master switch says "and you may do it while I am not here". An operator who wants the checks run when they press Run now, but nothing happening at three in the morning, can have exactly that.
* Publishing is not on the list and cannot be added to it. It is shown, locked, with the reason next to it, because a missing step reads as an oversight rather than a decision. Nothing goes in front of your audience without you choosing to send it — the Automation page, the API and the queue toggles each refuse it independently.
* Every automatic run is written down item by item: what it wrote, what it checked, what it could not do and why. You were not there when it happened, so "3 things done" is not an answer — the Automation page names them.
* Each step has a ceiling on how much it takes in one run, so switching everything on cannot turn one sweep into a surprise. One item failing is recorded against that item and never stops the rest.
* A run interrupted partway through — almost always an update landing while it was working — now says so and lists what it had already done, instead of showing "Running" forever and refusing to start another.

= 0.32.0 =
* Campaigns now tell you when a piece is about to state something you never gave them. A price, a date, a headcount, a percentage or the word "free" that appears nowhere in your organization's details or in the subject you typed is flagged on the plan, before six pieces get written on top of it. The first real run of the campaign planner invented a "free presence scan" nobody had offered, and every later piece repeated it as settled fact.
* The copywriter is now told, in as many words, never to invent a price, a date, a statistic, a named person or a result. It was the one pass that had never been told.
* Titles stopped talking to us instead of to your readers. "Proof post: our own scorecard", "8 Places People Check — LinkedIn Carousel", "Behind the Scenes: Scan to Plan (IG Carousel)" — the format, the channel and the piece's job in the campaign no longer leak into the title, which on a website piece is the published post title.
* When a piece cannot be written, Engage AI now says why. A rate limit, an exhausted account, a reply it could not read and copy cut off for being too long are four different problems; all four used to arrive as "is ANTHROPIC_API_KEY configured?", which was never the problem.
* A campaign build gives a piece one second chance before giving up on it. A single rate-limited response used to lose that piece for the whole run — and the piece it lost in testing was the offer, the only one in the arc that asks.

= 0.31.1 =
* Fixed: "Write it" on the Ideas page opened the Content Studio but left the idea behind, so you had to type it in again — and the idea stayed in your kept list after it had been written. The idea now arrives in the Studio ready to build, and once written it moves to the Written tab and links to the piece it became. Your kept list actually goes down as you work through it.

= 0.31.0 =
* Every workflow page now has an in-queue and an out-queue at the top: what has arrived and needs you, and what has already moved on to the next step. No more opening a page to find out whether there is anything to do.
* A third box appears only when something needs attention — a piece scheduled for a day that has passed and never written, a piece with no date at all, or anything in a state the queues do not recognise. Work that quietly did not happen is the thing this is for. Nothing is filtered out for being unexpected; if Engage AI cannot place something it says so instead of hiding it.
* The queues are worked out from your actual content every time, not stored in a separate list that could drift out of step with it. If the numbers ever failed to add up against what really exists, the page would tell you rather than showing a plausible-looking subset.

= 0.30.0 =
* New Chatbot module — a chat bubble on every public page that answers visitors from your Site Brain: your actual pages, the business facts you entered, your FAQs. It replies in the visitor's own language, links the page each answer came from, and says plainly when your site does not cover something instead of making something up. When someone is ready to talk to a person it takes their name, email and message, emails you, and keeps the lead with a CSV export.
* Replies run through your Engage AI account. There is no AI key to create, no second bill, and nothing to configure beyond the greeting and colours.
* Off by default. Turning it on puts a visible widget on your website and answers strangers in your name, so it waits for you to switch it on under Settings > Chatbot. It needs Site Brain on to have anything to answer from, and tells you if it is not.

= 0.29.0 =
* New Ideas page — the step that was missing. Ideas used to be generated inside the Content Studio and thrown away: they were held for half an hour, tied to the goal you happened to pick, and gone if you changed your mind. Now they are kept for your organisation. Ask for a batch, keep the ones worth doing, add your own, and send one to the Studio when it is time to write it. Turning an idea down keeps it too, so the same suggestion is not offered back to you next week.
* New Calendar page — everything your campaigns have planned, on one four-week grid, across every channel. Set how often you mean to post per channel and it tells you where you are short. Pieces that were planned but never given a date are listed underneath rather than quietly left out, because a piece with no date is not going out.
* New "What each channel takes" tab on the Content Library — what every channel can carry and what each kind of piece is for, so you can decide what to make without guessing.
* Post performance is its own tab on Analytics. It was always there, at the bottom of the page under the channel scans; it answers a different question from "how is our Instagram doing?" and now has its own place to do it.
* The menu now reads in the order the work happens: see where you stand, get an idea, plan the run, write it, keep it, schedule it, connect where it goes, then measure. It used to list the Content Studio before Campaigns, which is backwards — a campaign's pieces open into the Studio.

= 0.28.0 =
* Site Brain is now on by default. Updating to this version turns it on, including on sites that have never opened its settings: your published pages become readable by AI agents at /llms.txt, /llms-full.txt and /.well-known/mcp.json on your own domain, and the first crawl starts in the background. If you had already switched it off, it stays off — the new default never overrides a choice you made.
* Only published content in the post types you select is ever served. Never users, comments, orders or form submissions. You can require a token instead of allowing open access, see every agent that called, and switch the whole thing off, all under Settings > Site Brain.

= 0.27.2 =
* Fixed: "Set up a channel" could not be opened at all in 0.27.0 and 0.27.1. Making it a tab of Channels took it out of the sidebar correctly, but also took away WordPress's permission to load it, so every link to it answered "Sorry, you are not allowed to access this page." The wizard is reachable again, and still lives on the Channels page rather than in the menu.

= 0.27.1 =
* Fixed: the Content Library offered "Open in Studio" on every piece, including older ones drafted before the Content Studio existed. The Studio cannot reopen those - it has no record of how they were built - so the button led to an editor with blank fields and an error on save. It now appears only on pieces the Studio actually made; older pieces keep the actions they have always had.

= 0.27.0 =
* New Site Brain module — your website, readable by AI. Everything the rest of the plugin does pushes content out to your channels. This does the opposite: it reads your own site, breaks every page into passages, and publishes the result as a live knowledge base an AI agent can search. Point a chatbot at it and it answers from your actual pages, with a link to the one it used, instead of making something up.
* It knows the things it must not guess. Alongside the pages, you fill in your opening hours, phone number, address, booking link and the answers to the questions you get asked constantly. Those are handed over as verified facts, so an assistant quotes them rather than inferring them from a paragraph somewhere.
* It keeps itself current. Publish or edit a page and it is re-read straight away; delete one and the brain says so. A full pass runs daily. Nothing to press.
* Off until you turn it on, under Settings. Only published content in the post types you pick is ever served — never users, comments, orders or form submissions. You can require a token instead of allowing open access, and see every agent that called, what it asked and when.

= 0.26.0 =
* One place to make content, instead of three. The Content page used to have its own "Create a campaign" and "draft a single piece" forms, on an older pipeline than the rest of the plugin — what it produced could not be quality-checked, revised, given an image or published to a channel, because it could not get into the Content Studio. Those forms are gone. It is the Content Library now: everything ever written for your site, in one list, and every row opens in the Studio.
* Setting up a channel is part of Channels, not a separate page. The wizard walked you to an access token and then sent you to a different menu item to hand it over. They are two tabs of one screen now — Connected, and Set one up.
* The menu only shows what you actually have. Pages now appear based on the modules switched on for your organisation, so a site using Analytics alone is no longer given eight content pages it cannot use. If the module list can't be read for any reason, everything shows, exactly as before.

= 0.25.0 =
* New Campaigns page - plan a whole run of content instead of one post at a time. Say what you want it to achieve, what it's about and the dates it runs between, and Engage AI comes back with a named campaign: one idea, argued piece by piece, each piece already on the channel that suits it and the day it should go out.
* The run is a sequence, not a pile. Each piece has a job in it - earn attention, teach, prove, make the ask, close - and is written knowing what the campaign argues and where it sits, so the pieces build on each other instead of repeating each other.
* Nothing is fixed until you say so. The plan comes back before anything is written: untick a piece you don't want, move a date, swap the channel, rename the campaign. Then press the button once and every piece is written and quality-checked for you, one at a time, while you get on with something else.
* Campaign pieces are ordinary drafts. Each one opens in the Content Studio for editing, the image or video, and publishing - exactly like a piece you made there yourself. Nothing is ever posted automatically.
* Deleting a campaign never deletes the content it wrote - those drafts stay in your Content Library.

= 0.24.0 =
* New "Set up a channel" page - a step-by-step walkthrough that takes one channel from "we don't have one yet" to "Engage AI can post on it". It asks whether you already have the channel, then gives you only the steps you actually need: creating the Facebook Page, switching Instagram to a business account, claiming your place on Google Maps, and so on.
* Every step that sends you somewhere has the real link on it, opening in its own tab so the page keeps your place. Where a channel needs an access token, the steps link straight to the page that issues one - Meta's Graph API Explorer, LinkedIn's token generator, Google's OAuth Playground - instead of leaving you to find it.
* Your organisation's own name, website and suggested handle are already filled into the steps with a Copy button, so there is nothing to retype into a form on someone else's website.
* Stop halfway and come back - the wizard remembers which channel you were on and which step, per user. A channel that's already connected is shown as done rather than asking you to set it up again.

= 0.23.0 =
* New Channels page - connect the accounts you want Engage AI to post to: your Facebook Page, Instagram, LinkedIn, YouTube, X and Google Business Profile. You sign in at the channel itself, so Engage AI never sees your password, and you can withdraw its access from that page at any time.
* Publish straight from the Content Studio. Once a channel is connected, the last step offers "Post it to <your account> now" and the piece goes out for real - copy, hashtags and the image or video it rendered. Nothing changes for channels you haven't connected: the copy-and-paste route is still there.
* Connecting a channel does not start anything posting. Content still goes out only when you publish it, unless you switch automatic posting on for that channel yourself - it starts off, per channel, and says which channels have it on.
* Each connected channel shows who Engage AI posts as, when its access expires (renewed automatically where the channel allows), and a "check it still works" button that tells you straight away if a channel has stopped accepting posts.

= 0.22.0 =
* The Content Studio now remembers where you were. Open it from the menu and it drops you straight back into the piece you were working on - so you can go and preview the image or video in your Media Library, come back, and pick up exactly where you left off, instead of starting from the goal screen again.
* Removed the old Generate Content page. Everything it did is now in the Content Studio (create) and the Content Library (see what you've made), so there's one clear place to create content instead of two overlapping ones.

= 0.21.0 =
* New Content Studio - content creation rebuilt as a workflow you can steer instead of one button and a wall of output. It runs in passes: pick the business goal, choose from competing ideas, shape the copy, read the quality check, make the media, publish. Every pass is its own screen, so you can change the format, rewrite a line, or send a draft back for another pass at any point, and a half-built piece can be left and picked up later.
* Three content types that render reliably, every time, with no API key: a post with an image; an image with your headline set on it (auto-sized so it always fits and always stays legible); and an 8-second vertical video - four slides, two seconds each, your narration centred on screen over a slow zoom with cross-fades. Sized automatically to each channel (Instagram 4:5, X 16:9, website landscape, video 9:16).
* The quality check is real: it measures every draft against the channel's actual limits and fixes what it can mechanically (over-long copy, too many hashtags, hashtags on channels that don't take them, an on-image headline too long to read, missing alt text, over-long narration). What's left - placeholder text, a missing call to action when the goal needs one, too few slides - is listed with a one-click "have the AI fix these".
* Media now renders in the background and lands in your Media Library on its own, so a slow image or a full video no longer times out the page. Website pieces publish as a WordPress draft with the generated image already set as the featured image.
* Fixed: "Generate image" and "Generate video" on the Content page fetched the wrong asset and could fail to save the file into your Media Library.
* The old Content page is now Content Library - the log of everything created, unchanged.

= 0.20.0 =
* Image and video generation now work out of the box - no API key required. "Generate image" creates a real image and saves it straight to your Media Library using a built-in generator (add an OpenAI API key anytime to upgrade the quality). "Generate video" assembles a short captioned video from the storyboard - a still per scene with the caption burned in, stitched into an MP4 - and saves it to your Media Library, ready to post to YouTube, Reels, or anywhere.

= 0.19.0 =
* New content campaign workflow: enter one topic, pick your channels, and the content-design agent drafts a coordinated post for each - with the copy and the media each channel needs. Image posts (Instagram, Facebook, LinkedIn, website featured image, Google Business) get an AI-generated image saved straight to your Media Library; video channels (YouTube, Reels) get a full storyboard (script, scene-by-scene captions and image prompts, and a thumbnail prompt). Image generation uses OpenAI - add an OpenAI API key to switch it on; until then every post still comes with a ready-to-use image prompt and alt text.

= 0.18.0 =
* The Content generator can now draft for any channel, not just your website. Pick a channel (Google Business, YouTube, Facebook, Instagram, LinkedIn, X, news) and one of five content types designed to raise that channel's engagement score - e.g. an Instagram educational carousel, a YouTube Short script, a Google review-request message, a LinkedIn insight post, or a press release. Each draft is saved with a copy-ready body (and hashtags where relevant); website drafts still become WordPress drafts in one click.

= 0.17.0 =
* Content drafts now open as proper WordPress blocks (paragraphs, headings, lists) instead of one raw HTML block that some themes displayed with a broken, collapsed layout in the editor.
* You can now set your Website type under Settings > Organization details (church / business / online shop, or leave on Auto-detect) - it tailors content suggestions to your kind of site.

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
