=== Engage AI ===
Contributors: visionoutreachmedia
Tags: church, ai, content generation, engagement, automation
Requires at least: 6.0
Tested up to: 6.7
Requires PHP: 8.0
Stable tag: 0.2.0
License: GPLv2 or later

Generates and auto-publishes church engagement content, plus modular autonomous check-in agents for the 8 Claude AI side hustles, via the Engage AI Cloud API.

== Description ==

Engage AI connects your WordPress site to the Engage AI Cloud API. It does two things, each independently switched on per organization under Settings > Modules:

* **Engagement** (the original feature): turns a message, sermon, or event into practical engagement — a website post, social media caption, email, WhatsApp message, presentation slides, and follow-up actions — matched to your organization's stored voice (mission, tone, audience).
* **Agent modules** (one per Claude AI side hustle — physical product business, reselling, YouTube channel growth, paid Q&A, local service business, app building, UGC creation, coaching): each runs its own autonomous check-in cycle, proposing concrete work as tickets you approve, reject, or redirect from the Agents page. Anything reversible it drafts immediately; anything that would spend money or act publicly is held for your explicit approval.

= Setup =

1. Deploy the Engage AI Cloud API (see the `engage-ai-cloud-api` project) and note its base URL.
2. In WordPress, go to Engage AI > Settings and enter the API URL.
3. Connect with the email/password you registered with on the API (your password is used once to connect and is not stored — only the resulting session token is kept).
4. Select or create your organization.
5. Under Settings > Modules, turn on Engagement and/or whichever side-hustle agents this organization needs.
6. Go to Engage AI > Generate Content for the church-engagement generators, or Engage AI > Agents for the ticket dashboard of any active side-hustle module.

== Changelog ==

= 0.2.0 =
* Modular activation: organizations turn on exactly the capabilities they need (Settings > Modules).
* Added autonomous agent modules for all 8 Claude AI side hustles, each with its own scheduled check-in cycle and ticket queue (approve/reject/redirect) on the new Agents page.

= 0.1.0 =
* Initial release: settings/connection flow, organization management, event/announcement/sermon generation with auto-publish to a WordPress post.
