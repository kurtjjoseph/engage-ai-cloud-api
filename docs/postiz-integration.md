# Posting through Postiz

*Design note for `app/services/channels/postiz.py`, `postiz_store.py` and
`app/routers/postiz.py`. The companion to `channel-authentication.md`, which
covers the direct per-platform path.*

## Why this exists

`channel-authentication.md` describes the right way to post: Engage AI holds the
organization's own credentials and calls Meta, Google, LinkedIn and X directly.
It has one problem nothing in this codebase can fix — every one of those
providers gates posting *on behalf of accounts outside the developer's own*
behind an app review:

| Provider | What stands between a customer and a real post |
| --- | --- |
| Meta (Facebook, Instagram, Threads) | App Review, plus a Business account |
| LinkedIn | Community Management API, partner approval |
| TikTok | Content Posting API audit, 2–4 weeks; until it passes, posts are self-only |
| Google Business Profile | API access request |
| X | Metered access, immediate but billed per call |

Postiz ([postiz.com](https://postiz.com), AGPL, self-hostable) has already been
through those reviews. An organization connects its accounts *inside Postiz*,
and Engage AI posts through one API key.

That is a real trade — a third party is now in the path of every post — so it is
offered as an **alternative transport, not a replacement**. It also reaches nine
channels this codebase has no provider for at all: TikTok, Threads, Bluesky,
Mastodon, Pinterest, Telegram, Discord, Reddit, Slack.

## Where it sits

Postiz is a transport, not a channel. A post relayed to a Facebook Page is still
a `facebook` Publication — analytics, the content library and the engagement
cycle learn nothing new.

`registry.get_adapter()` resolves per organization, most specific first:

```
1. an explicit runtime override    register_adapter()
2. the org's DIRECT connection     live.py       real, its own credentials
3. the org's POSTIZ workspace      postiz.py     real, relayed
4. the simulated adapter           social.py     records where it would have gone
```

**Direct beats relayed on purpose.** Connecting Postiz can never silently
re-route a channel the organization already authorized itself; Postiz fills the
gaps. An org with a direct Facebook connection and a Postiz workspace posts to
Facebook through its own token and to TikTok through Postiz, without configuring
anything to say so.

## What a post through Postiz actually is

**It is real.** `Publication.simulated` stays `False` — there is a real post, for
a real account, on a real platform.

**It is not confirmed live.** Postiz accepts a post into its own queue and
releases it afterwards, so what comes back from `POST /posts` is an id, not a
permalink. Collapsing "we sent it" and "it is live" into one fact would be the
same mistake `simulated` exists to prevent, so `Publication` gained three
columns:

- `delivery` — `"direct"` | `"postiz"` | `NULL` (recorded before the distinction existed)
- `external_id` — Postiz's own post id
- `status` — `"published"` | `"queued"` | `"scheduled"` | `"failed"`

A queued publication's `url` points at the **account**, taken from
`Organization.channel_details`, not at a post URL that would 404.
`POST /organizations/{id}/postiz/reconcile` walks the unresolved publications,
asks Postiz which have been released, and promotes those to their real permalink.
A post Postiz has not released yet is left exactly as it was — nothing is
invented.

## Consent

Identical to the direct path, and enforced in the same two places:

- **`auto_post` is off per account** until an admin turns it on. The engagement
  cycle asks the registry with `require_auto_post=True`, so an account that is
  connected but not opted in keeps getting the simulated adapter.
- **`confirm=true` is required** on `POST .../postiz/publish`. This is the one
  endpoint that can put the same piece on every channel an organization owns in
  a single call, so it refuses without an explicit acknowledgement and answers
  with the exact list it would have posted to.

Disconnecting, or an account disappearing from Postiz, revokes `auto_post` with
it — a re-appearing account never silently resumes unattended posting.

## Sync: Postiz owns the account list

`GET /integrations` is the source of truth for what can be posted to. `sync()`
reconciles it into `PostizChannel` rows on every connect and on demand, and
preserves exactly the two things a person chose rather than Postiz reported:
`auto_post` and `settings`. Names, pictures and disabled flags are refreshed
from Postiz every time.

A Postiz platform Engage AI has no channel for (Lemmy, Nostr, VK) is skipped, not
stored — so nothing downstream can ever route a post to it.

## Per-provider settings

Postiz requires a `settings` block per platform. Most need only `__type`, which
is the integration's own identifier read back from Postiz — never guessed. Three
platforms demand a choice, and it is made once, in `postiz.default_settings()`:

- **YouTube → unlisted**, matching the direct `YouTubeAdapter`. An automated
  system putting a video in front of a congregation's subscribers is not
  reversible; unlisted keeps a human able to flip it.
- **TikTok →** interaction settings off, non-commercial declared — the most
  conservative combination it accepts.
- **Reddit** is deliberately *not* defaulted: it needs a subreddit nobody can
  guess. A Reddit post is refused with `"reddit needs subreddit set..."` until an
  admin supplies one via `PATCH .../postiz/channels/{integration_id}`.

Anything an admin sets in `PostizChannel.settings` is merged over these.

## Endpoints

```
GET    /organizations/{id}/postiz                          workspace + accounts
POST   /organizations/{id}/postiz/connect                  {api_key, base_url?, app_url?}
POST   /organizations/{id}/postiz/sync                     re-read the accounts
PATCH  /organizations/{id}/postiz/channels/{integration}   {auto_post?, settings?, is_default?}
DELETE /organizations/{id}/postiz                          forget the key
POST   /organizations/{id}/postiz/publish                  {content_id, channels?, scheduled_for?, confirm}
POST   /organizations/{id}/postiz/reconcile                fill in permalinks
```

The API key is verified before it is stored (`GET /integrations` either answers
or it doesn't), encrypted at rest with the same Fernet key as channel tokens
(`services/crypto.py`), and returned by no endpoint.

`publish` with no `channels` means every usable account in the workspace. One
channel failing does not cancel the others — each result carries its own
outcome, because a piece that reached five accounts of six is a partial success
and reporting it as a failure would be wrong.

## Setup

**Hosted:** create a key at Postiz → Settings → Public API. `base_url` can be
omitted.

**Self-hosted:** `base_url` is the instance's backend URL — whatever
`NEXT_PUBLIC_BACKEND_URL` is set to, commonly `https://postiz.example.org/api`.
`normalize_base_url()` appends `/public/v1`, so a URL pasted with or without it
works either way. Set `POSTIZ_BASE_URL` in the environment to make a self-hosted
instance the default offered to every organization.

```bash
curl -X POST "$API/organizations/1/postiz/connect" \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d '{"api_key":"...","base_url":"https://postiz.example.org/api"}'
```

`scripts/postiz_smoke.py` runs connect → sync → publish → reconcile against a
real instance, which is the only way to verify the parts a mocked test cannot.

## Known limits

- **90 posts/hour per Postiz instance** on `POST /posts` — a global instance
  limit, not per key. Self-hosters raise it with `API_LIMIT`. A fan-out to ten
  channels is ten posts against that budget. Surfaced verbatim as a 429 message.
- **The engagement cycle still only distributes to `DISTRIBUTABLE_CHANNELS`**
  (the original six). Postiz-only channels are reachable by explicit publish but
  are not yet planned for by the cycle — extending `cycle_engine` to plan them is
  a separate change, deliberately not smuggled in here.
- **Postiz's `GET /posts` query params have changed across versions.** The client
  tries the known shapes in turn rather than pinning one, so reconciliation
  survives an instance upgrade. `POST /posts` and `/integrations` have been
  stable and are called directly.
- **No webhook.** Reconciliation is pull-based, on demand. Postiz can call a
  webhook on publish; wiring one would remove the polling but adds a public
  endpoint to authenticate, which is its own decision.
- **Verified against mocks, not yet against a live instance.** Every request
  shape here is asserted in `tests/test_postiz.py` against a fake transport;
  what no mock can prove is that a real Postiz accepts them. That is what the
  smoke script is for, and it has not been run yet.
