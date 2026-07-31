# Per-channel posting authentication

Until now Engage AI could *write* for every channel but only *post* to one: the
website, through the WordPress plugin. Everything else ended at "copy this and
paste it into Instagram." This is the layer that closes that gap — the site
owner authorizes Engage AI once per channel, and from then on a piece can be
published to that channel from inside WordPress.

## What a site owner sees

**Engage AI → Channels** in wp-admin lists every connectable channel:

| Channel | Posts as | Media |
|---|---|---|
| Facebook Page | the Page the authorizing account administers | image |
| Instagram | the business account linked to that Page | image or video (required) |
| LinkedIn | the authorizing member | image |
| YouTube | the account's channel | video (uploaded **unlisted**) |
| X (Twitter) | the authorizing account | text only |
| Google Business Profile | the first location on the profile | image |

Each row is **Connect** (sign in at the provider, approve, come back), or —
where this deployment has no OAuth app registered for that provider yet —
**Connect with an access token**, where the owner generates a long-lived token
at the provider and pastes it in. Both paths end in the same place.

Once connected, the Content Studio's publish step gains **"Post it to *(your
account)* now"**. The copy-and-paste route stays exactly where it was.

## The two rules this is built around

**1. Engage AI never holds a credential in the clear.**
Tokens are encrypted at rest with Fernet (`app/services/crypto.py`) and no
endpoint returns token material — the status API reports *that* a channel is
connected, as which account, and until when. The WordPress plugin never stores
a token either; a pasted one goes straight through to the API.

**2. Connecting is not consent to post.**
`ChannelConnection.auto_post` starts `False` and is a separate, per-channel
switch. With it off, the only thing that publishes is a human pressing publish
for one named piece. The unattended engagement cycle asks the registry with
`require_auto_post=True`, so an authorized-but-not-opted-in channel keeps
getting the *simulated* adapter — the same behaviour as before this feature
existed. An org that has connected nothing is bit-for-bit unchanged.

## How it fits together

```
routers/channel_connections.py   the flow: authorize → callback → connected
services/channels/providers.py   per-provider auth: URLs, scopes, code exchange,
                                 refresh, "which account is this?"
services/channels/connections.py storage + lifecycle: encrypt, refresh-before-use,
                                 verify, disconnect, status
services/channels/live.py        the real adapters — one per channel, each posting
                                 through its provider's API
services/channels/registry.py    picks live vs simulated, per organization
services/crypto.py               token encryption at rest
services/media_links.py          short-lived signed URLs (Instagram and Google
                                 Business fetch media themselves)
```

Endpoints:

```
GET    /organizations/{id}/channels                  status of every channel
POST   /organizations/{id}/channels/{ch}/authorize   -> provider consent URL
GET    /channels/callback/{ch}                       provider returns here (public)
POST   /organizations/{id}/channels/{ch}/token       paste a long-lived token
POST   /organizations/{id}/channels/{ch}/verify      re-check without posting
PATCH  /organizations/{id}/channels/{ch}             auto_post on/off
DELETE /organizations/{id}/channels/{ch}             disconnect
POST   /organizations/{id}/channels/{ch}/publish     publish one piece now
GET    /channels/media/{asset_id}?expires=&signature= signed media (public)
```

### Why the callback is public, and why that's safe

The provider redirects a *browser* back, with no bearer token — so the callback
can't require auth. What protects it is the `ChannelAuthRequest` row: the
`state` is minted server-side by an authenticated request, tied to one
organization and one user, valid 15 minutes, and burned before the code is
exchanged. An unknown or replayed state connects nothing. The PKCE verifier
(X, Google) is kept in that row too, so it never travels through the browser.

### Why some media is served by a signed public URL

Instagram and Google Business Profile don't accept uploaded bytes on the
publishing call — they fetch the media from a URL themselves. The owner-scoped
`GET /content/asset/{id}` is useless to them. So a publish to those channels
mints `GET /channels/media/{id}?expires=&signature=`: an HMAC over that one
asset id and an expiry, good for 15 minutes, granting nothing else. Facebook,
LinkedIn and YouTube upload bytes directly and never use it.

## Setting up the provider apps

Nothing below is required to ship — every channel works via the pasted-token
path without it. Registering an app is what turns a channel's row into a
one-click **Connect**.

Register this redirect URI with each provider:

```
{API_BASE_URL}/channels/callback/{channel}
```

e.g. `https://engage-ai-api.onrender.com/channels/callback/facebook`.

| Env pair | Covers | Register at | Scopes requested |
|---|---|---|---|
| `FACEBOOK_CLIENT_ID` / `_SECRET` | facebook, instagram | developers.facebook.com | `pages_show_list`, `pages_manage_posts`, `pages_read_engagement`, `business_management`, plus `instagram_basic`, `instagram_content_publish` |
| `GOOGLE_CLIENT_ID` / `_SECRET` | youtube, google_business | console.cloud.google.com | `youtube.upload`, `youtube.readonly`, `business.manage` |
| `LINKEDIN_CLIENT_ID` / `_SECRET` | linkedin | linkedin.com/developers | `openid`, `profile`, `w_member_social` |
| `TWITTER_CLIENT_ID` / `_SECRET` | twitter_x | developer.x.com | `tweet.read`, `tweet.write`, `users.read`, `offline.access` |

Each of these platforms reviews an app before it may act for accounts outside
your own developer account — that review, not this code, is the long pole on
making one-click connect available to customers. The pasted-token path exists
precisely so a customer isn't blocked waiting for it.

Also set:

* **`TOKEN_ENCRYPTION_KEY`** — a Fernet key
  (`python -c "from app.services.crypto import generate_key; print(generate_key())"`).
  Unset, it is derived from `JWT_SECRET`, which works but couples the two:
  rotating `JWT_SECRET` would then make every stored channel token
  undecryptable. That degrades safely — `decrypt()` returns `None`, the channel
  is marked needing attention, and the owner reconnects — but it is avoidable
  noise, so set this explicitly in production.
* **`MEDIA_URL_SIGNING_KEY`** *(optional)* — signs the media links above;
  falls back to `JWT_SECRET`.

## Known limits

* **X posts text only.** Its media upload needs the v1.1 endpoint with OAuth
  1.0a signing, which this integration doesn't carry. The channel reports
  `supports_media: false` and the UI says so before anyone renders an image.
* **YouTube uploads are unlisted.** An automated system making a video public
  to a congregation's subscribers isn't reversible; unlisted leaves the human
  able to flip it.
* **Facebook and Google Business pick the first Page/location** the token
  administers. Fine for a single-Page church; an org with several would need a
  picker at connect time.
* **Instagram video publishing polls** for the container to finish transcoding
  (up to ~60s inside the request). Long videos can outlast that; the error says
  so rather than recording a Publication that points at nothing.
* **Facebook long-lived tokens last ~60 days** and have no refresh token. The
  connection reports its expiry, and `verify` says plainly when it has lapsed —
  the owner reconnects. Google and X refresh silently.

## Tests

`tests/test_channel_connections.py` — 26 tests, no network. They cover the
things that must hold whatever the platform on the other end does: tokens
encrypted and never returned, state single-use and non-replayable, a pasted
token verified before storage, connecting not enabling autonomous posting,
disconnect keeping history but dropping credentials, refresh-before-use,
signed media links scoped to one asset, and an unconnected org still getting
the simulated adapter.
