"""Transactional email via Brevo (https://www.brevo.com).

Used for password-reset links. Stdlib/httpx only - httpx is already a
dependency. send_email() never raises: callers (e.g. the password-reset
request endpoint) must return the same response whether or not the email
actually went out, so they don't leak whether an address exists.
"""
import httpx

from app.config import settings

BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def email_configured() -> bool:
    return bool(settings.brevo_api_key and settings.brevo_sender_email)


def send_email(to: str, subject: str, html: str) -> bool:
    """Send one transactional email. Returns True on success, False if email
    isn't configured or the send failed (logged, never raised)."""
    if not email_configured():
        return False
    try:
        resp = httpx.post(
            BREVO_URL,
            headers={
                "api-key": settings.brevo_api_key,
                "content-type": "application/json",
                "accept": "application/json",
            },
            json={
                "sender": {"email": settings.brevo_sender_email, "name": settings.brevo_sender_name or "Engage AI"},
                "to": [{"email": to}],
                "subject": subject,
                "htmlContent": html,
            },
            timeout=15,
        )
        if resp.status_code >= 300:
            print(f"[email] Brevo returned {resp.status_code}: {resp.text[:300]}", flush=True)
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - email must never break the caller
        print(f"[email] Brevo send failed: {exc}", flush=True)
        return False
