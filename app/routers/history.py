"""Search history."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.templating import templates

router = APIRouter()

PAGE_SIZE = 20


@router.get("/history", response_class=HTMLResponse)
async def history(request: Request, page: int = Query(default=1, ge=1, le=10_000)) -> HTMLResponse:
    repository = request.app.state.search_manager.repository
    searches, total = await repository.list_searches(limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "searches": searches,
            "page": page,
            "has_prev": page > 1,
            "has_next": page * PAGE_SIZE < total,
            "total": total,
        },
    )
