from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Engage AI Cloud API"
    app_env: str = "development"
    api_base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./engage_ai.db"
    jwt_secret: str = "change-this-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 10080
    # Baked into downloaded plugin zips (see routers/onboarding.py) instead of
    # the 7-day login session token above - a customer's WP install can't log
    # back in to refresh a token, so this one needs to actually last.
    long_lived_token_expire_minutes: int = 60 * 24 * 365
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    enable_scheduler: bool = True
    cycle_interval_hours: int = 24
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    # Full engagement cycle orchestrator (services/engagement_cycle.py).
    # cycle_auto_approve: whether the APPROVE stage self-approves planned
    # engagements with no human in the loop (this system has no queued
    # human-review UI for this flow yet, so True is the working default).
    # cycle_measure_mode: "simulate" (deterministic offline projection, used
    # by tests and any environment without live web-search) or "live" (real
    # best-effort re-measurement via publication_search.py).
    # cycle_module_key: the enabled_modules entry that gates this cycle for
    # an organization, checked by services/cycle_measurement.is_cycle_enabled.
    cycle_auto_approve: bool = True
    cycle_measure_mode: str = "simulate"
    cycle_module_key: str = "engagement_cycle"

    # Analytics reliability + cadence (services/analytics_reconcile.py,
    # services/scheduler.py). analytics_anomaly_delta: how far a channel score
    # may move between scans before the snapshot is flagged needs_review for a
    # human sanity-check. analytics_scan_interval_hours: cadence of the
    # scheduled full-sweep scan (~monthly by default) - this is what makes the
    # trend accrue on its own instead of waiting for a manual scan.
    # analytics_scan_stagger_seconds: pause between orgs in a scheduled batch so
    # N orgs don't fire N simultaneous web-search calls (rate limits + blast
    # radius). 0 fires the whole batch back-to-back.
    analytics_anomaly_delta: int = 25
    analytics_scan_interval_hours: int = 720
    analytics_scan_stagger_seconds: int = 30

    # Transactional email via Brevo (services/email.py) - used for password
    # reset links. Without these set, reset still works but the link is only
    # logged server-side for an operator to relay (never returned to the caller).
    brevo_api_key: str | None = None
    brevo_sender_email: str | None = None
    brevo_sender_name: str = "Engage AI"

    # --- Per-channel posting authentication (services/channels/providers.py) ---
    # Encryption key for third-party channel tokens at rest. A Fernet key
    # (services/crypto.generate_key()) or any passphrase; unset derives one
    # from jwt_secret - see services/crypto.py for the rotation caveat.
    token_encryption_key: str | None = None
    # OAuth app credentials, one pair per identity provider. A provider with
    # no client id/secret configured simply can't be connected by OAuth - the
    # channel then offers the manual long-lived-token path instead, so the
    # feature is usable before an app review completes. Redirect URI to
    # register with each provider:
    #   {api_base_url}/channels/callback/{channel}
    # facebook_* backs both the "facebook" and "instagram" channels;
    # google_* backs both "youtube" and "google_business".
    facebook_client_id: str | None = None
    facebook_client_secret: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
    linkedin_client_id: str | None = None
    linkedin_client_secret: str | None = None
    twitter_client_id: str | None = None
    twitter_client_secret: str | None = None
    # --- Postiz relay (services/channels/postiz.py) ---
    # The public-API root offered as the default when an admin connects a
    # Postiz workspace. Point it at a self-hosted instance's backend URL (e.g.
    # https://postiz.example.org/api) to make that the house default; each
    # organization can still override it when connecting. API keys are never
    # set here - they belong to one workspace and are stored per organization,
    # encrypted, on PostizWorkspace.
    postiz_base_url: str = "https://api.postiz.com"

    # Signs the short-lived public media URLs Instagram's publishing API
    # requires (it fetches the image itself, so it can't send a bearer token).
    # Unset falls back to jwt_secret.
    media_url_signing_key: str | None = None
    media_url_ttl_seconds: int = 900

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
