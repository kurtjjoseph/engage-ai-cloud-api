"""The channel setup wizard: how a non-technical owner gets each channel from
"we don't have one" to "Engage AI can post on it."

This is the content behind the plugin's **Set up a channel** page. It is served
from the API rather than hardcoded in PHP for the same reason the Studio catalog
is: when a provider moves the page that issues access tokens - and they do - one
deploy fixes it everywhere, instead of every customer's WordPress install
carrying a stale link until they update the plugin.

Each channel has three parts, and a wizard shows whichever the owner needs:

    create_steps   the channel doesn't exist yet - make it
    prepare_steps  it exists, but something has to be true before it can be
                   authorized (an Instagram account has to be a business
                   account linked to a Page, for example)
    connect_steps  authorize Engage AI: one click if this deployment has an
                   OAuth app for the provider, otherwise generate an access
                   token at the provider and paste it in

Every step that asks someone to go somewhere carries the actual URL. The token
steps in particular link straight to the page that issues the token, because
"generate an access token" is otherwise an instruction to go and get lost in a
developer console.

Nothing in here is a credential, and no step ever asks the owner to send one to
anyone: a pasted token goes into their own Engage AI, encrypted.
"""

from __future__ import annotations

from app.models.entities import Organization

from .providers import AUTHENTICATABLE_CHANNELS, get_provider

# Where each provider issues an access token by hand, for the paste-a-token
# path. These are the pages that actually mint one - not the docs about them.
TOKEN_TOOLS = {
    "facebook": {
        "label": "Graph API Explorer",
        "url": "https://developers.facebook.com/tools/explorer/",
        "extend_label": "Access Token Debugger (to extend it)",
        "extend_url": "https://developers.facebook.com/tools/debug/accesstoken/",
    },
    "instagram": {
        "label": "Graph API Explorer",
        "url": "https://developers.facebook.com/tools/explorer/",
        "extend_label": "Access Token Debugger (to extend it)",
        "extend_url": "https://developers.facebook.com/tools/debug/accesstoken/",
    },
    "linkedin": {
        "label": "LinkedIn OAuth token generator",
        "url": "https://www.linkedin.com/developers/tools/oauth/token-generator",
    },
    "youtube": {
        "label": "Google OAuth 2.0 Playground",
        "url": "https://developers.google.com/oauthplayground/",
    },
    "google_business": {
        "label": "Google OAuth 2.0 Playground",
        "url": "https://developers.google.com/oauthplayground/",
    },
    "twitter_x": {
        "label": "X developer portal",
        "url": "https://developer.x.com/en/portal/dashboard",
    },
}


def _step(title: str, body: str, *, link=None, note=None, chips=None, wait=None) -> dict:
    step: dict = {"title": title, "body": body}
    if link:
        step["link"] = {"label": link[0], "url": link[1]}
    if note:
        step["note"] = note
    if chips:
        step["chips"] = [{"label": label, "value": value} for label, value in chips if value]
    if wait:
        step["wait"] = wait
    return step


def build_guide(org: Organization, statuses: list[dict]) -> list[dict]:
    """The wizard for one organization: the generic steps with this org's own
    name, website and handle already filled into the copy-me chips, and each
    channel's live connection state folded in so a connected channel shows as
    finished instead of asking the owner to do it again."""
    name = org.name or "Your organisation"
    site = org.website_url or ""
    handle = _handle(name)
    by_channel = {entry["channel"]: entry for entry in statuses}

    guide = []
    for channel in AUTHENTICATABLE_CHANNELS:
        status = by_channel.get(channel, {})
        builder = _BUILDERS[channel]
        entry = builder(name, site, handle)
        provider = get_provider(channel)
        entry.update({
            "channel": channel,
            "label": provider.label,
            "oauth_available": provider.configured,
            "supports_media": provider.supports_media,
            "connected": bool(status.get("connected")),
            "account_name": status.get("account_name"),
            "token_tool": TOKEN_TOOLS.get(channel),
            "connect_steps": _connect_steps(channel, provider.configured),
        })
        guide.append(entry)
    return guide


def _handle(name: str) -> str:
    slug = "".join(ch for ch in name.lower() if ch.isalnum() or ch == " ").strip()
    return "".join(slug.split())[:30] or "yourorg"


# --------------------------------------------------------------- connecting


def _connect_steps(channel: str, oauth_available: bool) -> list[dict]:
    """The last leg, and the only one that differs by how this deployment is
    configured: a Connect button when there's an OAuth app, otherwise the
    generate-a-token route, which needs the live link most."""
    if oauth_available:
        return [
            _step(
                "Authorize Engage AI",
                "Open the Channels page and press Connect on this channel. You sign in at "
                "the provider itself and approve there - Engage AI never sees your password.",
                note="Connecting does not start anything posting. Content still goes out only "
                     "when you publish it, unless you switch automatic posting on yourself.",
            ),
        ]
    return _TOKEN_STEPS[channel]


_TOKEN_STEPS: dict[str, list[dict]] = {
    "facebook": [
        _step(
            "Open the Graph API Explorer",
            "This is Meta's own tool for issuing an access token. Sign in with the account "
            "that administers the Page.",
            link=("Open the Graph API Explorer", TOKEN_TOOLS["facebook"]["url"]),
        ),
        _step(
            "Ask it for a Page token",
            "Top right, set the dropdown from “User Token” to your Page. Under permissions "
            "add pages_show_list, pages_manage_posts and pages_read_engagement. Then press "
            "Generate Access Token and approve.",
        ),
        _step(
            "Make it long-lived",
            "Paste the token into the Access Token Debugger and press “Extend Access Token” at "
            "the bottom. A short-lived token stops working within the hour; an extended one "
            "lasts about 60 days.",
            link=("Open the Access Token Debugger", TOKEN_TOOLS["facebook"]["extend_url"]),
        ),
        _step(
            "Paste it into Engage AI",
            "On the Channels page, open “Connect with an access token” under Facebook Page and "
            "paste it there. Engage AI checks it against Facebook before saving, so you'll know "
            "straight away if it works.",
            note="The token is encrypted before it is stored, and is never shown again.",
        ),
    ],
    "instagram": [
        _step(
            "Open the Graph API Explorer",
            "Sign in with the account that administers the Facebook Page your Instagram is "
            "linked to. Instagram publishing runs through that Page.",
            link=("Open the Graph API Explorer", TOKEN_TOOLS["instagram"]["url"]),
        ),
        _step(
            "Ask it for a Page token with Instagram rights",
            "Switch the dropdown to your Page, and under permissions add instagram_basic, "
            "instagram_content_publish and pages_show_list. Press Generate Access Token and "
            "approve.",
        ),
        _step(
            "Make it long-lived",
            "Paste it into the Access Token Debugger and press “Extend Access Token”.",
            link=("Open the Access Token Debugger", TOKEN_TOOLS["instagram"]["extend_url"]),
        ),
        _step(
            "Paste it into Engage AI",
            "Channels page → Instagram → “Connect with an access token”. Engage AI works out "
            "which Instagram business account the Page is linked to and posts as that.",
        ),
    ],
    "linkedin": [
        _step(
            "Open LinkedIn's token generator",
            "LinkedIn issues a token straight from its developer tools. You'll need a LinkedIn "
            "app; if you don't have one, create it there first - it takes a minute and needs no "
            "review for your own account.",
            link=("Open the token generator", TOKEN_TOOLS["linkedin"]["url"]),
        ),
        _step(
            "Tick the posting permission",
            "Select your app, tick w_member_social (plus openid and profile), and generate the "
            "token.",
            note="Posts go out from the person who authorizes, not from a company Page - that's "
                 "what this permission grants.",
        ),
        _step(
            "Paste it into Engage AI",
            "Channels page → LinkedIn → “Connect with an access token”.",
        ),
    ],
    "youtube": [
        _step(
            "Open Google's OAuth Playground",
            "Google's own tool for issuing a token against your account.",
            link=("Open the OAuth 2.0 Playground", TOKEN_TOOLS["youtube"]["url"]),
        ),
        _step(
            "Pick the YouTube upload scope",
            "In step 1 on the left, paste this scope into the “Input your own scopes” box, then "
            "press Authorize APIs and sign in with the Google account that owns the channel.",
            chips=[("Scope", "https://www.googleapis.com/auth/youtube.upload")],
        ),
        _step(
            "Exchange it for a token",
            "In step 2, press “Exchange authorization code for tokens” and copy the access "
            "token it shows.",
            note="A Playground token lasts about an hour. It's fine for trying this out; for "
                 "day-to-day use, ask us to register the Google app so this channel gets a "
                 "one-click Connect that refreshes itself.",
        ),
        _step(
            "Paste it into Engage AI",
            "Channels page → YouTube → “Connect with an access token”. Videos upload as "
            "unlisted, so you always get to look before anything is public.",
        ),
    ],
    "google_business": [
        _step(
            "Open Google's OAuth Playground",
            "Google's own tool for issuing a token against your account.",
            link=("Open the OAuth 2.0 Playground", TOKEN_TOOLS["google_business"]["url"]),
        ),
        _step(
            "Pick the Business Profile scope",
            "In step 1, paste this scope into the “Input your own scopes” box, press Authorize "
            "APIs, and sign in with the Google account that manages the business.",
            chips=[("Scope", "https://www.googleapis.com/auth/business.manage")],
        ),
        _step(
            "Exchange it for a token",
            "In step 2, press “Exchange authorization code for tokens” and copy the access token.",
            note="A Playground token lasts about an hour - fine for a first try, not for "
                 "day-to-day use.",
        ),
        _step(
            "Paste it into Engage AI",
            "Channels page → Google Business Profile → “Connect with an access token”.",
        ),
    ],
    "twitter_x": [
        _step(
            "Open the X developer portal",
            "X needs a developer app before anything can post on your behalf. Sign in with the "
            "account you want to post from and create a project and app if you don't have one.",
            link=("Open the developer portal", TOKEN_TOOLS["twitter_x"]["url"]),
        ),
        _step(
            "Set it up for posting",
            "In the app's User authentication settings, turn on OAuth 2.0, set the app type to "
            "a confidential client, and give it Read and write permissions with the "
            "tweet.write scope.",
            note="Be aware: X only issues an OAuth 2.0 posting token through the sign-in flow "
                 "itself - the “Keys and tokens” tab gives a different kind of credential that "
                 "won't work here. In practice this channel wants the one-click Connect, which "
                 "means giving us the app's client id and secret.",
        ),
        _step(
            "Send us the app details",
            "Message us the app's OAuth 2.0 client id and secret and we'll set them on your "
            "Engage AI. Connect then becomes one click, and X refreshes itself from then on.",
            note="Send them through the password vault we set up together - never loose over "
                 "WhatsApp or email.",
        ),
    ],
}


# ---------------------------------------------------- per-channel preparation


def _facebook(name: str, site: str, handle: str) -> dict:
    return {
        "exists_question": f"Does {name} already have a Facebook Page?",
        "create_steps": [
            _step(
                "Create the Page",
                "Open Facebook with your own account and go to “Create Page”. This does not "
                "make a new account - the Page belongs to your normal Facebook.",
                link=("Open “Create Page”", "https://www.facebook.com/pages/create"),
            ),
            _step(
                "Fill in the name and category",
                "Copy and paste the name below, and pick the category that fits.",
                chips=[("Name", name), ("Website", site)],
            ),
            _step(
                "Add the photos",
                "Set a square logo as the profile photo and a wide photo as the cover. Both "
                "look better than the placeholder Facebook gives you, and both can be changed "
                "later.",
            ),
        ],
        "prepare_steps": [
            _step(
                "Check you're an admin of the Page",
                "You need to administer the Page yourself to authorize Engage AI on it. In "
                "Meta Business Suite, your name should appear under the Page's People with "
                "full control.",
                link=("Open Meta Business Suite", "https://business.facebook.com/"),
            ),
        ],
    }


def _instagram(name: str, site: str, handle: str) -> dict:
    return {
        "exists_question": f"Does {name} already have an Instagram account?",
        "create_steps": [
            _step(
                "Create the account",
                "Open the Instagram app and choose “Create new account”. Use an address the "
                "organisation controls, not a personal one.",
                chips=[("Suggested username", f"@{handle}"), ("Name", name)],
            ),
            _step(
                "Choose your own password",
                "Make up a password and keep it in your password app.",
                note="Engage AI never sees or asks for this password.",
            ),
            _step(
                "Add the bio and photo",
                "Paste the website into the bio link and set the logo as the profile photo.",
                chips=[("Website", site)],
            ),
        ],
        "prepare_steps": [
            _step(
                "Switch it to a business account",
                "In Instagram: Settings → Account type and tools → Switch to professional "
                "account. Instagram only allows anything to post on your behalf if the account "
                "is a business or creator account.",
            ),
            _step(
                "Link it to your Facebook Page",
                "Settings → Accounts Centre → Accounts → add the Facebook Page. Instagram "
                "publishing runs through that Page, so this link is what makes it possible.",
                link=("Open Meta Business Suite", "https://business.facebook.com/"),
                note="This is the fiddliest screen of the lot. If it won't cooperate, message "
                     "us and we'll walk through it with you.",
            ),
        ],
    }


def _linkedin(name: str, site: str, handle: str) -> dict:
    return {
        "exists_question": f"Does {name} already have a LinkedIn Page?",
        "create_steps": [
            _step(
                "Create the Page",
                "Open LinkedIn with your own account and create a company page. It belongs to "
                "your normal LinkedIn - it is not a new account.",
                link=("Open “Create Page”", "https://www.linkedin.com/company/setup/new/"),
            ),
            _step(
                "Fill in the details",
                "Copy and paste the name and website, add a short description, and upload the "
                "logo.",
                chips=[("Name", name), ("Website", site)],
            ),
        ],
        "prepare_steps": [
            _step(
                "Know who the posts come from",
                "Engage AI posts to LinkedIn as the person who authorizes it - not as the "
                "company Page. So authorize with the account you're happy to see the posts "
                "come from.",
            ),
        ],
    }


def _youtube(name: str, site: str, handle: str) -> dict:
    return {
        "exists_question": f"Does {name} already have a YouTube channel?",
        "create_steps": [
            _step(
                "Create the channel",
                "Open the page below with your own Google account and choose “Create channel”. "
                "This is not a new account - the channel belongs to your normal Google.",
                link=("Open YouTube channels", "https://www.youtube.com/channel_switcher"),
            ),
            _step(
                "Name the channel",
                "Copy and paste the name and handle.",
                chips=[("Name", name), ("Handle", f"@{handle}")],
            ),
        ],
        "prepare_steps": [
            _step(
                "Check the channel can accept uploads",
                "Open YouTube Studio and confirm the channel is verified for uploads. An "
                "unverified channel can't be uploaded to at all.",
                link=("Open YouTube Studio", "https://studio.youtube.com/"),
            ),
        ],
    }


def _google_business(name: str, site: str, handle: str) -> dict:
    return {
        "exists_question": f"Is {name} already on Google Maps?",
        "create_steps": [
            _step(
                "Start at Google",
                "Open the page below and sign in with your own Google account.",
                link=("Open Google Business Profile", "https://business.google.com/create"),
                note="You type your own password yourself. Engage AI never sees it.",
            ),
            _step(
                "Fill in the name, category and address",
                "This is how people find you on the map.",
                chips=[("Name", name), ("Website", site)],
            ),
            _step(
                "Wait for the postcard",
                "Google mails a postcard with a 6-digit code. It takes 1 to 2 weeks - "
                "completely normal, and nothing else can happen until it arrives.",
                wait="Enter the 6 digits at Google when the postcard arrives. Nobody else "
                     "needs that code.",
            ),
        ],
        "prepare_steps": [
            _step(
                "Claim it as yours",
                "Find your place on Google Maps and tap “Own this business?”. Follow the steps "
                "with your own Google account. A profile you don't own can't be posted to.",
                link=("Open Google Maps", "https://www.google.com/maps"),
            ),
        ],
    }


def _twitter_x(name: str, site: str, handle: str) -> dict:
    return {
        "exists_question": f"Does {name} already have an X account?",
        "create_steps": [
            _step(
                "Create the account",
                "Open X and choose “Create account”. Use an address the organisation controls.",
                link=("Open X", "https://x.com/i/flow/signup"),
                chips=[("Suggested username", f"@{handle}"), ("Name", name)],
            ),
            _step(
                "Choose your own password",
                "Make up a password and put it straight into your password app.",
                note="If you get a text-message code, only you enter it. Engage AI never asks "
                     "for it.",
            ),
            _step(
                "Add the profile text and photo",
                "Paste the website into the profile and set the logo as the profile photo.",
                chips=[("Website", site)],
            ),
        ],
        "prepare_steps": [
            _step(
                "Know what X will and won't carry",
                "Engage AI posts text to X - it does not attach the image or video it made. "
                "Everything else about the piece works normally.",
            ),
        ],
    }


_BUILDERS = {
    "facebook": _facebook,
    "instagram": _instagram,
    "linkedin": _linkedin,
    "youtube": _youtube,
    "google_business": _google_business,
    "twitter_x": _twitter_x,
}
