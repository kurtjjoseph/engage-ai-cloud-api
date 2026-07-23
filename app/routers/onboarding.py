from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse, Response
from pydantic import EmailStr
from sqlalchemy.orm import Session
from app.config import settings
from app.db.session import get_db
from app.models.entities import Organization, User
from app.services.plugin_packager import build_personalized_zip
from app.services.security import create_long_lived_token, hash_password, verify_password

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

PAGE_STYLE = """
<style>
  body { font-family: -apple-system, Helvetica, Arial, sans-serif; background: #f6f7f7; margin: 0; padding: 0; }
  .card { max-width: 440px; margin: 60px auto; background: #fff; border: 1px solid #dcdcde; border-radius: 8px; padding: 32px; }
  h1 { font-size: 22px; margin-top: 0; }
  p.lead { color: #50575e; margin-bottom: 24px; }
  label { display: block; font-weight: 600; margin: 16px 0 6px; font-size: 14px; }
  input, select { width: 100%; padding: 8px 10px; border: 1px solid #8c8f94; border-radius: 4px; font-size: 14px; box-sizing: border-box; }
  button { margin-top: 24px; width: 100%; padding: 10px; background: #1e5f2e; color: #fff; border: none; border-radius: 4px; font-size: 15px; cursor: pointer; }
  .error { background: #fbeaea; color: #8a1f1f; padding: 10px 14px; border-radius: 4px; margin-bottom: 16px; font-size: 14px; }
  .hint { color: #646970; font-size: 12px; margin-top: 6px; }
</style>
"""


def render_form(error: str | None = None) -> str:
    error_html = f'<div class="error">{error}</div>' if error else ""
    return f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>Get Engage AI</title>{PAGE_STYLE}</head>
<body>
  <div class="card">
    <h1>Get Engage AI</h1>
    <p class="lead">Create your account and download a WordPress plugin that's already connected - no setup screen, just install and activate. Already have an account? Enter the same email and password below to get a fresh download.</p>
    {error_html}
    <form method="post" action="/onboarding">
      <label for="business_name">Church / business / channel name</label>
      <input type="text" id="business_name" name="business_name" required>
      <p class="hint">One account can run several sites - tick "additional site" below to add another under the same email.</p>

      <label for="org_type">Type</label>
      <select id="org_type" name="org_type">
        <option value="church">Church / ministry</option>
        <option value="business">Business / creator</option>
      </select>

      <label for="email">Email</label>
      <input type="email" id="email" name="email" required>

      <label for="password">Password</label>
      <input type="password" id="password" name="password" minlength="8" required>
      <p class="hint">At least 8 characters. Used to connect the plugin - never shown or emailed.</p>

      <label style="font-weight:400;margin-top:16px;"><input type="checkbox" name="additional_site" value="1" style="width:auto;margin-right:8px;">Register this as an <strong>additional site</strong> on an existing account (same email &amp; password)</label>

      <button type="submit">Create / add site &amp; download plugin</button>
    </form>
  </div>
</body>
</html>"""


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def signup_form():
    return render_form()


@router.post("")
@router.post("/")
def signup_submit(
    business_name: str = Form(...),
    org_type: str = Form("business"),
    email: EmailStr = Form(...),
    password: str = Form(...),
    additional_site: str = Form(""),
    db: Session = Depends(get_db),
):
    if len(password) < 8:
        return HTMLResponse(render_form(error="Password must be at least 8 characters."), status_code=400)

    existing_user = db.query(User).filter(User.email == email).first()
    want_new_site = bool(additional_site)

    if existing_user:
        # Already have an account (e.g. re-downloading after losing the zip,
        # or after a plugin update) - treat this as a login and hand back a
        # fresh personalized zip instead of blocking with an error.
        if not verify_password(password, existing_user.hashed_password):
            return HTMLResponse(
                render_form(error="An account with that email already exists, and that password doesn't match. Use the same password to redownload your plugin."),
                status_code=400,
            )
        user = existing_user
        # Multi-tenant: "additional site" creates a NEW organization under the
        # same account (one email -> many sites). Otherwise reuse the latest
        # org (the re-download case), creating one only if the account has none.
        if want_new_site:
            org = Organization(
                owner_id=user.id,
                name=business_name,
                org_type="church" if org_type == "church" else "business",
            )
            db.add(org)
            db.commit()
            db.refresh(org)
        else:
            org = (
                db.query(Organization)
                .filter(Organization.owner_id == user.id)
                .order_by(Organization.id.desc())
                .first()
            )
            if not org:
                org = Organization(
                    owner_id=user.id,
                    name=business_name,
                    org_type="church" if org_type == "church" else "business",
                )
                db.add(org)
                db.commit()
                db.refresh(org)
    else:
        user = User(email=email, hashed_password=hash_password(password))
        db.add(user)
        db.commit()
        db.refresh(user)

        org = Organization(
            owner_id=user.id,
            name=business_name,
            org_type="church" if org_type == "church" else "business",
        )
        db.add(org)
        db.commit()
        db.refresh(org)

    # Long-lived, not the 7-day login session token - there's no login form
    # on the WordPress side to refresh it (see services/security.py).
    token = create_long_lived_token(str(user.id))
    zip_bytes = build_personalized_zip(settings.api_base_url, token, org.id)

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="engage-ai.zip"'},
    )
