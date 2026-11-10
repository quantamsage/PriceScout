"""Search submission, results page, SSE progress stream and JSON API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from app.models import Search, SearchStatus, WebsiteStatus
from app.routers.home import render_home
from app.schemas.search import SearchCreated, SearchOut, SearchRequest
from app.services.search_events import SearchEvent
from app.services.search_input import validate_search_input
from app.services.search_manager import SearchManager
from app.services.serialization import dump, listing_out, search_out
from app.templating import templates

router = APIRouter()


def _manager(request: Request) -> SearchManager:
    return request.app.state.search_manager


async def _start(request: Request, query: str, websites: list[str]) -> tuple[Search | None, list[str], str]:
    manager = _manager(request)
    validated = validate_search_input(query, websites, manager.settings)
    if not validated.ok:
        return None, validated.errors, validated.query
    search = await manager.create_search(validated.query, validated.websites)
    manager.start(search)
    return search, [], validated.query


@router.post("/search")
async def submit_search(
    request: Request,
    query: str = Form(default=""),
    websites: list[str] = Form(default=[]),
):
    search, errors, _ = await _start(request, query, websites)
    if search is None:
        return render_home(
            request,
            query=query[:500],
            websites=[w[:2048] for w in websites][:25] or [""],
            errors=errors,
            status_code=422,
        )
    return RedirectResponse(url=f"/search/{search.id}", status_code=303)


@router.get("/search/{search_id}", response_class=HTMLResponse)
async def results_page(request: Request, search_id: int) -> HTMLResponse:
    search = await _manager(request).repository.get_search(search_id)
    if search is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "results.html",
        {"search": search, "websites": search.websites},
    )


def _sse(event: SearchEvent) -> str:
    return f"event: {event.type}\ndata: {json.dumps(event.data, separators=(',', ':'))}\n\n"


def _events_from_database(search: Search, preferred_currency: str | None) -> list[SearchEvent]:
    """Rebuild the event stream for a finished search from stored data."""
    out = search_out(search, preferred_currency)
    rates_used = any(l.converted_currency for l in out.listings)
    sample = next((l for l in out.listings if l.converted_currency), None)
    events = [
        SearchEvent("start", {
            "search_id": search.id,
            "query": search.query,
            "comparison": {
                "currency": sample.converted_currency if sample else None,
                "rates_available": rates_used,
                "as_of": sample.exchange_rate_as_of.isoformat() if sample and sample.exchange_rate_as_of else None,
                "source": sample.exchange_rate_source if sample else None,
            },
            "replay": True,
        })
    ]
    by_site: dict[int, list] = {}
    for listing in search.listings:
        by_site.setdefault(listing.search_website_id, []).append(listing)
    for website in search.websites:
        listings = sorted(by_site.get(website.id, []), key=lambda l: l.id)
        payload = {"id": website.id, "status": website.status, "message": website.message,
                   "product_count": website.product_count}
        if website.status == WebsiteStatus.COMPLETED:
            payload["matching_count"] = sum(1 for l in listings if l.is_match)
            payload["listings"] = [dump(listing_out(l)) for l in listings]
        events.append(SearchEvent("website", payload))
    if search.status not in (SearchStatus.PENDING, SearchStatus.RUNNING):
        summary = dump(out.summary)
        summary["groups"] = {str(l.id): l.group_key for l in out.listings if l.group_key}
        events.append(SearchEvent("summary", summary))
    events.append(SearchEvent("done", {}))
    return events


@router.get("/search/{search_id}/events")
async def search_events(request: Request, search_id: int) -> StreamingResponse:
    manager = _manager(request)
    search = await manager.repository.get_search(search_id)
    if search is None:
        raise HTTPException(status_code=404)

    async def stream() -> AsyncIterator[str]:
        yield "retry: 3000\n\n"
        if manager.broker.is_active(search_id):
            async for event in manager.broker.subscribe(search_id):
                if await request.is_disconnected():
                    break
                yield ": ping\n\n" if event is None else _sse(event)
            return
        fresh = await manager.repository.get_search(search_id)
        for event in _events_from_database(fresh or search, manager.settings.comparison_currency):
            yield _sse(event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- JSON API ---------------------------------------------------------------

@router.post("/api/searches", status_code=201, response_model=SearchCreated)
async def api_create_search(request: Request, payload: SearchRequest):
    search, errors, _ = await _start(request, payload.query, payload.websites)
    if search is None:
        return JSONResponse({"detail": errors}, status_code=422)
    return SearchCreated(id=search.id, url=f"/search/{search.id}", events_url=f"/search/{search.id}/events")


@router.get("/api/searches/{search_id}", response_model=SearchOut)
async def api_get_search(request: Request, search_id: int) -> SearchOut:
    manager = _manager(request)
    search = await manager.repository.get_search(search_id)
    if search is None:
        raise HTTPException(status_code=404, detail="Search not found")
    return search_out(search, manager.settings.comparison_currency)
