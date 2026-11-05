import logging
from datetime import datetime, timezone, timedelta
import time
from typing import Any
import bcrypt
import secrets
import jwt
from fastapi import Request, Response

# from fastapi.responses import RedirectResponse, HTMLResponse

from pydantic import BaseModel
from app.config import SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRY_SECONDS
from app.database import get_db

logger = logging.getLogger(__name__)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(
    user_id: int,
    username: str,
    role: str,
    display_name: str | None = None,
    token_version: int = 1,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, object] = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "display_name": display_name or username,
        "tv": token_version,
        "iat": now,
        "exp": now + timedelta(seconds=JWT_EXPIRY_SECONDS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.debug("Expired JWT token presented")
        return None
    except jwt.InvalidTokenError:
        logger.warning("Invalid JWT token presented")
        return None


def set_auth_cookie(
    request: Request, response: Response, token: str, csrf_token: str | None = None
) -> None:
    # The browser-facing scheme: uvicorn rewrites it from X-Forwarded-Proto only
    # for SHELF_TRUST_PROXY peers, so an untrusted peer cannot claim https.
    secure = request.url.scheme == "https"
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=JWT_EXPIRY_SECONDS,
        path="/",
    )
    # Set a paired CSRF token cookie (readable by JS for double-submit)
    if csrf_token is None:
        csrf_token = secrets.token_hex(32)
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=False,
        secure=secure,
        samesite="strict",
        max_age=JWT_EXPIRY_SECONDS,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="csrf_token", path="/")


def get_current_user(request: Request) -> dict[str, Any] | None:
    """Resolve the session to its users row: id, username,
      role, display_name, token_version.

    The JWT proves the session; identity and role come from the row. A token whose
    version or username no longer matches the row (a password change, a role
    change, an id reused after a restore) resolves to no session.
    """
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None

    user_id = int(str(payload["sub"]))  # cast to str first to avoid pydantic int coercion issues
    with get_db() as db:
        row = db.execute(
            "SELECT username, role, display_name, token_version"
            " FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row or row["token_version"] != payload.get("tv", 1):
        return None
    if row["username"].casefold() != str(payload.get("username", "")).casefold():
        return None

    return {
        "id": user_id,
        "username": row["username"],
        "role": row["role"],
        "display_name": row["display_name"] or row["username"],
        "token_version": row["token_version"],
    }


def should_refresh_token(request: Request, user: dict[str, Any]) -> str | None:
    """If the token is past half-life,
    return a fresh one minted from the resolved user."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    exp: float = payload.get("exp", 0)  # assume float
    iat: float = payload.get("iat", 0)
    now = time.time()
    half_life = (exp - iat) / 2
    if now > iat + half_life:
        return create_token(
            user["id"],
            user["username"],
            user["role"],
            user["display_name"],
            user["token_version"],
        )
    return None


def get_user_count() -> int:
    with get_db() as db:
        row = db.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
        return row["cnt"] if row else 0
