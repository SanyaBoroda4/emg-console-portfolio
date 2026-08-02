"""POST /api/auth/google (+ /google/redirect), GET /api/auth/me, POST /api/auth/logout."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.auth import (
    clear_session_cookie,
    create_session_token,
    get_current_user,
    set_session_cookie,
)
from app.config import get_settings
from app.db import get_db
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class GoogleLoginIn(BaseModel):
    credential: str


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: str
    display_name: str | None
    role: str


def _verify_google_credential(credential: str, db: Session) -> User:
    """Verify a Google ID token and map it to a roster user, or raise
    HTTPException. Shared by the JSON (popup) and form (redirect) endpoints."""
    # verify_oauth2_token checks the signature AND the audience (our client
    # id). Never trust the token's claims without this call.
    try:
        claims = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            get_settings().google_client_id,
        )
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_token",
                "message": "Google sign-in could not be verified. Try again.",
            },
        )
    if not claims.get("email_verified"):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_token",
                "message": "This Google account's email address is not verified.",
            },
        )

    email = str(claims.get("email", "")).lower()
    user = db.get(User, email)
    if user is None:
        # Authenticated by Google, rejected by us. No cookie.
        raise HTTPException(
            status_code=403,
            detail={
                "error": "not_authorized",
                "message": "This Google account isn't set up for the EMG console. "
                "Contact Alex.",
            },
        )
    return user


@router.post("/google", response_model=MeOut)
def login_with_google(
    payload: GoogleLoginIn,
    response: Response,
    db: Session = Depends(get_db),
) -> User:
    user = _verify_google_credential(payload.credential, db)
    set_session_cookie(response, create_session_token(user.email, user.role))
    return user


@router.post("/google/redirect", include_in_schema=False)
def login_with_google_redirect(
    request: Request,
    credential: str = Form(...),
    g_csrf_token: str = Form(default=""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """GIS ux_mode='redirect' target: Google form-POSTs the credential here.

    Used on iPhones, where the popup flow white-screens in Safari and inside
    Home Screen web apps. Google's redirect flow double-submits a CSRF token
    (cookie + form field) — they must match. On success we set the session
    cookie and redirect into the app; failures land back on /login with the
    message in the query string (this endpoint answers a top-level browser
    navigation, so JSON error bodies would be a dead end)."""

    def fail(message: str) -> RedirectResponse:
        return RedirectResponse(f"/login?error={quote(message)}", status_code=303)

    if not g_csrf_token or g_csrf_token != request.cookies.get("g_csrf_token", ""):
        return fail("Google sign-in could not be verified (CSRF). Try again.")

    try:
        user = _verify_google_credential(credential, db)
    except HTTPException as exc:
        message = (
            exc.detail.get("message", "Sign-in failed.")
            if isinstance(exc.detail, dict)
            else str(exc.detail)
        )
        return fail(message)

    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, create_session_token(user.email, user.role))
    return response


@router.get("/me", response_model=MeOut)
def me(response: Response, user: User = Depends(get_current_user)) -> User:
    # Sliding renewal: the app calls /me on every open, so the cookie's
    # clock restarts constantly — "once in, forever in".
    set_session_cookie(response, create_session_token(user.email, user.role))
    return user


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    # Always 204, even if nothing was set.
    clear_session_cookie(response)
