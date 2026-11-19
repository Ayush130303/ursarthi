from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.rate_limit import client_ip, enforce_rate_limit
from app.core.security import (
    InvalidTokenError,
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.db.session import get_db
from app.models import PasswordResetToken, Preference, RefreshToken, User
from app.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserResponse,
)
from app.services.email import send_password_reset_email

router = APIRouter(prefix="/auth", tags=["Authentication"])
REFRESH_COOKIE_NAME = "sarthi_refresh"
COOKIE_PATH = f"{get_settings().api_prefix}/auth"  # cookie only goes to the auth endpoints


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def user_out(db: Session, user: User) -> UserResponse:
    out = UserResponse.model_validate(user)
    out.has_preferences = db.get(Preference, user.id) is not None
    return out


def _issue_refresh_cookie(response: Response, db: Session, user: User) -> None:
    s = get_settings()
    raw = create_refresh_token()
    db.add(RefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(days=s.refresh_token_days),
    ))
    db.commit()
    response.set_cookie(
        REFRESH_COOKIE_NAME, raw, httponly=True, secure=s.cookie_secure,
        samesite=s.cookie_samesite, path=COOKIE_PATH, max_age=s.refresh_token_days * 86400,
    )


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, path=COOKIE_PATH)


def _session_response(response: Response, db: Session, user: User) -> TokenResponse:
    _issue_refresh_cookie(response, db, user)
    return TokenResponse(access_token=create_access_token(user.id, user.role), user=user_out(db, user))


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(payload: RegisterRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    enforce_rate_limit("register", client_ip(request), get_settings().rate_limit_register_per_minute)
    email = payload.email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(name=payload.name.strip(), email=email, password_hash=hash_password(payload.password), role="user")
    db.add(user)
    db.commit()
    db.refresh(user)
    return _session_response(response, db, user)  # sign the user in straight away


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    enforce_rate_limit("login", f"{client_ip(request)}:{email}", get_settings().rate_limit_login_per_minute)
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is disabled")
    return _session_response(response, db, user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=401, detail="Refresh session missing")
    record = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_token(raw)).first()
    now = datetime.now(timezone.utc)
    if not record or record.revoked_at is not None:
        if record and record.revoked_at is not None and (now - _aware(record.revoked_at)).total_seconds() > 30:
            # A long-revoked token was replayed: likely theft. Revoke every session of this user.
            db.query(RefreshToken).filter(RefreshToken.user_id == record.user_id, RefreshToken.revoked_at.is_(None)) \
              .update({"revoked_at": now}, synchronize_session=False)
            db.commit()
        _clear_cookie(response)
        raise HTTPException(status_code=401, detail="Refresh session is invalid")
    if _aware(record.expires_at) <= now:
        record.revoked_at = now
        db.commit()
        _clear_cookie(response)
        raise HTTPException(status_code=401, detail="Refresh session has expired")
    user = db.query(User).filter(User.id == record.user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=401, detail="Account is unavailable")
    record.revoked_at = now  # rotation: each refresh token is single-use
    db.commit()
    return _session_response(response, db, user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if raw:
        record = db.query(RefreshToken).filter(RefreshToken.token_hash == hash_token(raw)).first()
        if record and record.revoked_at is None:
            record.revoked_at = datetime.now(timezone.utc)
            db.commit()
    _clear_cookie(response)
    return {"message": "Logged out"}


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return user_out(db, current_user)


@router.patch("/me", response_model=UserResponse)
def update_me(payload: UpdateProfileRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    current_user.name = payload.name.strip()
    db.commit()
    return user_out(db, current_user)


@router.post("/change-password")
def change_password(payload: ChangePasswordRequest, response: Response,
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    current_user.password_hash = hash_password(payload.new_password)
    db.query(RefreshToken).filter(RefreshToken.user_id == current_user.id, RefreshToken.revoked_at.is_(None)) \
      .update({"revoked_at": datetime.now(timezone.utc)}, synchronize_session=False)
    db.commit()
    _issue_refresh_cookie(response, db, current_user)  # keep this device signed in
    return {"message": "Password changed. Other devices have been signed out."}


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, request: Request, db: Session = Depends(get_db)):
    enforce_rate_limit("forgot", client_ip(request), 5)
    user = db.query(User).filter(User.email == payload.email.lower().strip()).first()
    if user and user.is_active:
        token = create_password_reset_token(user.email)
        db.add(PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=get_settings().reset_token_minutes),
        ))
        db.commit()
        try:
            send_password_reset_email(user.email, token)
        except Exception as exc:
            print(f"Password reset email failed: {type(exc).__name__}")
    # Same response either way, to prevent account enumeration.
    return {"message": "If an active account exists, password reset instructions will be sent."}


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    invalid = HTTPException(status_code=400, detail="Invalid or expired reset link")
    try:
        claims = decode_token(payload.token)
        if claims.get("type") != "password_reset":
            raise invalid
        email = claims.get("sub")
    except (InvalidTokenError, TypeError, ValueError):
        raise invalid
    record = db.query(PasswordResetToken).filter(
        PasswordResetToken.token_hash == hash_token(payload.token), PasswordResetToken.used_at.is_(None)
    ).first()
    now = datetime.now(timezone.utc)
    if not record or _aware(record.expires_at) <= now:
        raise invalid
    user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
    if not user or user.id != record.user_id:
        raise invalid
    record.used_at = now
    user.password_hash = hash_password(payload.new_password)
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)) \
      .update({"revoked_at": now}, synchronize_session=False)
    db.commit()
    return {"message": "Password updated. Please sign in again."}