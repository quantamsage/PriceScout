"""Homepage with the search form."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.templating import templates

router = APIRouter()

SUGGESTED_WEBSITES = [
    ("Amazon", "https://www.amazon.com"),
    ("eBay", "https://www.ebay.com"),
    ("Jumia", "https://www.jumia.co.ke"),
    ("Kilimall", "https://www.kilimall.co.ke"),
]


def render_home(
    request: Request,
    *,
    query: str = "",
    websites: list[str] | None = None,
    errors: list[str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    settings = request.app.state.settings
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "query": query,
            "websites": websites or [""],
            "errors": errors or [],
            "suggested": SUGGESTED_WEBSITES,
            "max_websites": settings.max_websites_per_search,
        },
        status_code=status_code,
    )


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return render_home(request)
