
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import RedirectResponse, Response

from app.routers import (
    auth_routes,
    pages
)
from app.database import init_db

ROOT_PATH = Path(__file__).parent
static_folder = ROOT_PATH / "static"
templates_folder = ROOT_PATH / "templates"

# Middleware and lifespan events are defined in main.py 
# to avoid circular imports with routers and database modules.

_SKIP_AUTH_PATHS = frozenset({"/login", "/setup", "/logout", "/health", "/sw.js"})
_SKIP_AUTH_PREFIXES = ("/static/")

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint ) -> Response:
        from app.auth import (
            get_current_user,
            get_user_count,
            set_auth_cookie,
            should_refresh_token,
        )

        path = request.url.path

        # Skip auth for static assets
        if path.startswith(_SKIP_AUTH_PREFIXES):
            return await call_next(request)

        # Inject user into request state
        user = get_current_user(request)
        request.state.user = user

        # Setup wizard: if no users exist, redirect everything to /setup
        if path not in _SKIP_AUTH_PATHS:
            if get_user_count() == 0:
                return RedirectResponse(url="/setup", status_code=303)

        # Login redirect: if users exist but no session, redirect to /login
        # (skip for POST /login, POST /setup to avoid blocking form submissions)
        if path not in _SKIP_AUTH_PATHS and not user:
            if get_user_count() > 0:
                return RedirectResponse(url="/login", status_code=303)

        response = await call_next(request)

        # Sliding expiry: refresh token if past half-life, preserving CSRF token
        if user:
            fresh_token = should_refresh_token(request, user)
            if fresh_token:
                set_auth_cookie(
                    request, response, fresh_token, request.cookies.get("csrf_token")
                )

        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # The secret key is in the enviroment,
    # If we want to generate a secret key and store it in a file, we need to create
    # a function to generate a secret key and store it in a file. 
    # from app.auth import get_secret_key
    # Ensure the secret key is generated and cached at startup
    # get_secret_key()
    yield


app = FastAPI(
    title="pyexpense-tracker",
    description="A simple expense tracker application built with FastAPI.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=static_folder), name="static")
app.add_middleware(GZipMiddleware)
app.add_middleware(AuthMiddleware)

templates = Jinja2Templates(directory=templates_folder)
app.state.templates = templates


# Routers
app.include_router(auth_routes.router)
app.include_router(pages.router)
