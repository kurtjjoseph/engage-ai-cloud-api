import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.config import settings
from app.db.session import get_db
from app.models.entities import PasswordResetToken, User
from app.schemas import LoginRequest, PasswordResetConfirm, PasswordResetRequest, RegisterRequest, TokenResponse
from app.services.email import send_email
from app.services.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

RESET_TTL_MINUTES = 60


@router.post("/register", response_model=TokenResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.post("/password-reset/request")
def password_reset_request(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    """Start a password reset: if the email has an account, email a one-time
    reset link (valid 1h). ALWAYS returns the same message, so it never reveals
    whether an address is registered. If email isn't configured (or the send
    fails), the link is logged server-side for an operator to relay - never
    returned in the response (that would let anyone reset anyone's password)."""
    user = db.query(User).filter(User.email == payload.email).first()
    if user is not None:
        token = secrets.token_urlsafe(32)
        db.add(PasswordResetToken(
            user_id=user.id, token=token,
            expires_at=datetime.utcnow() + timedelta(minutes=RESET_TTL_MINUTES),
        ))
        db.commit()
        link = f"{settings.api_base_url.rstrip('/')}/reset?token={token}"
        html = (
            "<p>A password reset was requested for your Engage AI account.</p>"
            f'<p><a href="{link}">Reset your password</a> — this link is valid for {RESET_TTL_MINUTES} minutes.</p>'
            "<p>If you didn't request this, you can ignore this email; your password stays unchanged.</p>"
        )
        if not send_email(user.email, "Reset your Engage AI password", html):
            print(f"[password-reset] email not sent - link for {user.email}: {link}", flush=True)
    return {"status": "ok", "message": "If that email has an account, a password reset link has been sent."}


@router.post("/password-reset/confirm", response_model=TokenResponse)
def password_reset_confirm(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    """Consume a reset token and set a new password. Returns a fresh login token
    so the user is signed in immediately after resetting."""
    row = db.query(PasswordResetToken).filter(PasswordResetToken.token == payload.token).first()
    if row is None or row.used or row.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired. Request a new one.")
    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None:
        raise HTTPException(status_code=400, detail="Account not found for this reset link.")
    user.hashed_password = hash_password(payload.new_password)
    row.used = True
    db.commit()
    return TokenResponse(access_token=create_access_token(str(user.id)))
