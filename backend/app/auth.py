"""Session cookie + authorization dependencies (Stage 2).

The session is OUR token (itsdangerous-signed), issued only after Google
verifies identity AND the email is on the users roster. Roles are re-read
from the database on every request, so roster changes take effect
immediately — not when cookies expire.
"""

from fastapi import Cookie, Depends, HTTPException, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User

SESSION_COOKIE = "emg_session"
# "Once in — forever in" (owner): 400 days is the browser-enforced ceiling
# (Chrome caps cookie lifetime), and /api/auth/me re-issues the cookie on
# every app open (sliding renewal), so an even occasionally active user is
# never logged out.
SESSION_MAX_AGE_SECONDS = 400 * 24 * 3600


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret)


def create_session_token(email: str, role: str) -> str:
    return _serializer().dumps({"email": email, "role": role})


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,  # JavaScript can never read it
        secure=True,  # https only
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


def get_current_user(
    emg_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    """401 when there is no valid session; 403 when the session is valid but
    the person has been removed from the roster."""
    if not emg_session:
        raise HTTPException(status_code=401, detail={"error": "not_authenticated"})
    try:
        data = _serializer().loads(emg_session, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=401, detail={"error": "not_authenticated"})

    user = db.get(User, str(data.get("email", "")).lower())
    if user is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "not_authorized",
                "message": "This account is no longer set up for the EMG console.",
            },
        )
    return user


def require_role(*allowed_roles: str):
    """Dependency factory: 403 unless the current user's role is allowed."""

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail={"error": "forbidden"})
        return user

    return dependency
