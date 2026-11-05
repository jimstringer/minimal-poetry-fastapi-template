# Routes that render pages (HTML) for the app. 
# These routes are not API routes, but rather routes that render HTML templates.
# These routes require authentication and authorization, and are protected by the auth middleware.

from fastapi import APIRouter, Request

# from app import nav
# from app.auth import require_role
# from app.database import get_db

router = APIRouter()


@router.get("/")
async def index(request: Request):
    """Render the home page."""
    # with get_db() as db:
    #    summary = dashboard_summary(db, recent_limit=8)

    return request.app.state.templates.TemplateResponse(
        request,
        "home.html",
        {
            # **summary,
            "message": "Hello, welcome to the template!",
        },
    )
