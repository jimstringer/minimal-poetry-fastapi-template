# 
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.auth import (
    hash_password,
    verify_password,
    clear_auth_cookie,
    set_auth_cookie,
    create_token,
    get_user_count,
)

# from app import features
from app.config import get_client_ip
from app.database import get_db
logger = logging.getLogger(__name__)

# Unknown usernames must do the same one bcrypt verification as known usernames
# without generating a fresh salt/hash on every request. The hash is created
# once at process start; the fixed plaintext is deliberately not a valid
# account credential and only exists to equalise the password-check work factor.
_DUMMY_PASSWORD_HASH = hash_password("dummy")

router = APIRouter()

# --- Public pages ---


@router.get("/login")
async def login_page(request: Request):
    user = getattr(request.state, "user", None)
    if user:
        return RedirectResponse(url="/", status_code=303)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    templates = request.app.state.templates
    with get_db() as db:
        user = db.execute(
            "SELECT id, username, password, role, display_name, token_version" \
                " FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if not user:
        # One bcrypt verification on both known and unknown username paths.
        await run_in_threadpool(verify_password, "dummy", _DUMMY_PASSWORD_HASH)
        logger.warning(
            "Failed login attempt for username=%s from %s",
            username,
            get_client_ip(request),
        )
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password"},
            status_code=401,
        )
    if not await run_in_threadpool(verify_password, password, user["password"]):
        logger.warning(
            "Failed login attempt for username=%s from %s",
            username,
            get_client_ip(request),
        )
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password"},
            status_code=401,
        )

    token = create_token(
        user["id"],
        user["username"],
        user["role"],
        user["display_name"],
        user["token_version"],
    )
    response = RedirectResponse(url="/", status_code=303)
    set_auth_cookie(request, response, token)
    logger.info("User '%s' logged in from %s", username, get_client_ip(request))
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    clear_auth_cookie(response)
    return response

# --- Setup wizard (only works when no users exist) ---

def _setup_page(request: Request, error: str | None = None):
    """Render the setup form with an optional error message.

    """
    return request.app.state.templates.TemplateResponse(request, "setup.html", {
        "error": error,
    })


@router.get("/setup")
async def setup_page(request: Request):
    user = getattr(request.state, "user", None)
    if user:
        return RedirectResponse(url="/", status_code=303)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@router.post("/setup")
async def setup(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(""),
    password: str = Form(...),
    password_confirm: str = Form(...),
    # Absent means Standard: an old cached form or a script posting the four
    # account fields gets the default, not a refusal.
    # profile: str = Form("standard"),
):
    if get_user_count() > 0:
        return RedirectResponse(url="/login", status_code=303)

    username = username.strip()
    display_name = display_name.strip() or username

    if len(password) < 8:
        return _setup_page(request, "Password must be at least 8 characters")
    if password != password_confirm:
        return _setup_page(request, "Passwords do not match")
    if not username or len(username) < 2:
        return _setup_page(request, "Username must be at least 2 characters")

    # bcrypt never runs while a write transaction is open.
    hashed = await run_in_threadpool(hash_password, password)
    with get_db() as db:
        # The initial get_user_count() is only a fast path. Serialize the
        # authoritative "still no users?" decision with the insert so two
        # concurrent first-run requests cannot both create an administrator.
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return RedirectResponse(url="/login", status_code=303)
        db.execute(
            "INSERT INTO users (username, password, display_name, role) VALUES (?, ?, ?, 'admin')",
            (username, hashed, display_name),
        )
        user = db.execute("SELECT id, username, role, display_name, token_version FROM users WHERE username = ?", (username,)).fetchone()

    token = create_token(user["id"], user["username"], user["role"], user["display_name"], user["token_version"])
    response = RedirectResponse(url="/", status_code=303)
    set_auth_cookie(request, response, token)
    logger.info("Setup completed: admin user '%s' created", username)
    return response
