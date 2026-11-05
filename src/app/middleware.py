
import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from collections import OrderedDict
import time
from app.config import get_client_ip


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Browsers ignore HSTS over http://, and behind a trusted TLS proxy the
        # scheme is already https.
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # No 'unsafe-inline', no CDN hosts, and no 'unsafe-eval': all JS is
        # served from /static and Alpine is the CSP build (no new Function) —
        # see .devdocs/archive/completed/CSP_BUNDLING.md
        # and .devdocs/archive/completed/ALPINE_CSP.md.
        # scripts/check_alpine_csp.py keeps template expressions parseable.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; "  # blob: for the intake photo preview
            "connect-src 'self'; "  # PWA sync queue + SSE are same-origin only
            "font-src 'self' data:; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none';"
        )
        # Camera stays first-party for barcode scanning (html5-qrcode / ZXing
        # getUserMedia) and the photo-intake webcam viewfinder; everything else
        # is denied outright.
        response.headers["Permissions-Policy"] = (
            "camera=(self), microphone=(), geolocation=(), payment=(), usb=()"
        )
        return response


_SKIP_AUTH_PATHS = frozenset({"/login", "/setup", "/logout", "/health", "/sw.js"})
_SKIP_AUTH_PREFIXES = ("/static/", "/covers/", "/share/")

# Methods that mutate state and must carry a CSRF token
_CSRF_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection for all state-mutating requests.

    HTMX sends the X-CSRF-Token header (configured in base.html).  Standard
    browser form submissions are also blocked unless the hidden _csrf field or
    header matches the csrf_token cookie.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Only validate state-mutating methods on authenticated paths
        if request.method not in _CSRF_METHODS:
            return await call_next(request)
        if path in _SKIP_AUTH_PATHS or path.startswith(_SKIP_AUTH_PREFIXES):
            return await call_next(request)

        cookie_token = request.cookies.get("csrf_token")
        if not cookie_token:
            return Response("CSRF token missing", status_code=403)

        # Accept token from header (HTMX) or form field (plain HTML forms).
        # IMPORTANT: for form bodies we must cache the raw bytes BEFORE calling
        # call_next, because BaseHTTPMiddleware's receive stream is consumed once.
        # We replay the body via a patched Request so the route handler can still
        # read the form data normally.
        submitted = request.headers.get("X-CSRF-Token")
        body_bytes: bytes | None = None
        if not submitted:
            content_type = request.headers.get("content-type", "")
            if (
                "application/x-www-form-urlencoded" in content_type
                or "multipart/form-data" in content_type
            ):
                body_bytes = await request.body()  # caches in request._body
                form = await request.form()
                submitted = form.get("_csrf")

        if not submitted or submitted != cookie_token:
            return Response("CSRF validation failed", status_code=403)

        # If we consumed the body, replay it so the route handler can read it too
        if body_bytes is not None:

            async def _replay_receive():
                return {"type": "http.request", "body": body_bytes, "more_body": False}

            patched = Request(request.scope, _replay_receive)
            return await call_next(patched)

        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory per-IP rate limiter for API endpoints.

    Uses a bounded OrderedDict (max MAX_IPS entries) to prevent unbounded
    memory growth from rotating IPs or IPv6 address churn.
    """

    MAX_IPS = 1000

    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.rpm = requests_per_minute
        self._hits: OrderedDict[str, list[float]] = OrderedDict()

    async def dispatch(self, request: Request, call_next) -> Response:
        # Disable rate limiting when explicitly configured (e.g. test suite)
        if os.environ.get("SHELF_DISABLE_RATE_LIMIT"):
            return await call_next(request)

        # Only rate-limit API and auth endpoints
        path = request.url.path
        if not (path.startswith(("/api/", "/share/")) or path in ("/login", "/setup")):
            return await call_next(request)

        ip = get_client_ip(request)
        now = time.time()
        window = now - 60

        # Evict this IP's stale timestamps and check the limit
        hits = [t for t in self._hits.get(ip, []) if t > window]
        if len(hits) >= self.rpm:
            return Response("Rate limit exceeded", status_code=429)

        hits.append(now)

        # Update entry and move to end (most-recently-seen)
        self._hits[ip] = hits
        self._hits.move_to_end(ip)

        # Evict the oldest IP when the dict exceeds the cap
        while len(self._hits) > self.MAX_IPS:
            self._hits.popitem(last=False)

        return await call_next(request)

