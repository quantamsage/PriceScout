import httpx
import pytest

from app.main import create_app


@pytest.fixture
async def client(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def test_homepage_renders_search_form(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert 'name="query"' in response.text
    assert 'name="websites"' in response.text
    assert "Compare prices" in response.text
    assert "Content-Security-Policy" in response.headers


async def test_unknown_page_is_404_without_details(client):
    response = await client.get("/does-not-exist")
    assert response.status_code == 404
    assert "Traceback" not in response.text
