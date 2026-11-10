# PriceScout

Compare the price of a product across several shopping websites. Enter what you want to buy and the websites to search. PriceScout searches them all at once, streams results in as each site finishes, and lines up the prices with the cheapest matching listing highlighted.

- **No AI required:** extraction, matching and comparison are all deterministic Python.
- **Honest data:** prices that can't be read are shown as *unavailable*, never as zero. Different currencies are only ranked against each other after a real exchange-rate conversion. Websites that block automated access are reported as failed, never bypassed.
- **History:** every search, listing and price is stored in SQLite, so past comparisons can be revisited and price history builds up over time.

Stack: Python, FastAPI, Jinja2, vanilla JS/CSS, SQLite via SQLAlchemy 2.0 (async), Pydantic, HTTPX, BeautifulSoup4, Playwright (fallback only), pytest.

## Setup

PriceScout must be run from a virtual environment named `.venv` in the project root. Don't install the dependencies globally.

### 1. Create the virtual environment

```powershell
# Windows PowerShell
python -m venv .venv
```

```bash
# Linux / macOS
python3 -m venv .venv
```

### 2. Activate it

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

```bash
# Linux / macOS
source .venv/bin/activate
```

If PowerShell refuses to run the activation script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### 3. Install the dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Playwright's Chromium

This is only needed for websites that render their results with JavaScript:

```bash
playwright install chromium
```

PriceScout still works without it. Sites that need JavaScript are then reported as *"needs JavaScript … browser rendering is unavailable"*.

### 5. Initialise the database

```bash
python -m app.cli init-db
```

This creates `data/pricescout.db`. The app also creates the tables automatically on startup.

### 6. Start the application

```bash
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000.

### 7. Run the tests

```bash
pytest                                   # everything
pytest tests/test_matching.py            # one file
pytest tests/test_scrapers.py::test_amazon_adapter   # one test
```

The tests never contact live websites. They use local HTML fixtures (`tests/fixtures/`) and mocked HTTP responses.

## Configuration

Settings are read from environment variables prefixed with `PRICESCOUT_`, or from a `.env` file. Copy `.env.example` to `.env` to start. The most useful settings:

| Setting | Default | Purpose |
|---|---|---|
| `PRICESCOUT_MAX_WEBSITES_PER_SEARCH` | 8 | Maximum number of websites per search |
| `PRICESCOUT_MAX_CONCURRENT_SITES` | 4 | Websites searched at the same time |
| `PRICESCOUT_SITE_TIMEOUT_SECONDS` | 45 | Overall time limit for each website |
| `PRICESCOUT_REQUEST_TIMEOUT_SECONDS` | 15 | Time limit for each HTTP request |
| `PRICESCOUT_MAX_RESPONSE_BYTES` | 5 MB | Largest page that will be downloaded |
| `PRICESCOUT_PER_HOST_MIN_INTERVAL_SECONDS` | 1.0 | Minimum gap between requests to the same host |
| `PRICESCOUT_PLAYWRIGHT_ENABLED` | true | Turns the JavaScript-rendering fallback on or off |
| `PRICESCOUT_MATCH_THRESHOLD` | 0.6 | Relevance score a listing needs to count as a match |
| `PRICESCOUT_COMPARISON_CURRENCY` | USD | Currency that converted prices are compared in |
| `PRICESCOUT_EXCHANGE_RATES` | *(empty)* | Static rates, e.g. `{"USD":1,"KES":129.2}` |
| `PRICESCOUT_EXCHANGE_RATE_PROVIDER_URL` | *(empty)* | Rate provider, e.g. `https://open.er-api.com/v6/latest/{base}` |
| `PRICESCOUT_EBAY_CLIENT_ID` / `_SECRET` | *(empty)* | Uses the official eBay Browse API instead of scraping |

**Currency conversion is off by default.** Without exchange rates, listings in a single currency are still ranked. When currencies are mixed, they are shown side by side and not ranked. Once rates are configured, every converted price shows its rate, source and timestamp.

## How it works

```
Browser ──POST /search──▶ routers/search.py ──▶ SearchManager.start()  (background task)
   ▲                                               │
   │  SSE /search/{id}/events                      ▼
   └──────── SearchEventBroker ◀── ComparisonService.run()
                                     │  asyncio.gather + Semaphore + per-site timeout
                                     ▼
                     registry.resolve_adapter(url) → WebsiteAdapter.search()
                                     │  SafeFetcher (HTTPX) → BeautifulSoup
                                     │  Playwright only if the page needs JS
                                     ▼
                     relevance() + currency.convert() → SearchRepository (SQLite)
```

| Layer | Location | Responsibility |
|---|---|---|
| Routes | `app/routers/` | Pages, form/API validation, SSE stream |
| Services | `app/services/` | Orchestration, matching, currency, ranking rules, URL safety |
| Scrapers | `app/scrapers/` | Adapter interface, registry, fetcher, extraction, browser pool |
| Data | `app/repositories/`, `app/models/` | All SQL; searches, websites, listings, price snapshots |

### Website adapters

Every adapter implements `WebsiteAdapter.search(query, ctx) -> list[Product]` (`app/scrapers/base.py`) and returns the same normalized `Product` (`app/schemas/product.py`). The registry tries them in this order:

1. **API adapters.** The official eBay Browse API, when credentials are configured.
2. **Site-specific adapters** (`app/scrapers/sites/`). Amazon, eBay, Jumia and Kilimall, each with its known search URL, CSS selectors and default currency.
3. **Generic adapter** (`app/scrapers/generic.py`). Used for any other site. It finds the search URL from a `{query}` placeholder in the submitted URL (e.g. `https://shop.example/find?q={query}`), then the homepage search form, then OpenSearch, then common Shopify/WooCommerce/Magento patterns.

Within a page, extraction tries JSON-LD, then microdata, then site selectors. Heuristic product-card detection and Open Graph run only if those find nothing.

**To add a website:** subclass `HtmlAdapter` with a `host_pattern`, `selectors` and `search_urls()`, then add it to `SITE_ADAPTERS` in `app/scrapers/registry.py`. Nothing in the comparison logic needs to change.

### Product matching

`app/services/product_matching.py` gives each listing a relevance score from 0 to 1. The score combines token coverage, fuzzy name similarity (RapidFuzz), brand, model numbers, variant words (Pro/Max/Ultra…) and specs (storage, screen size). It is penalised for conflicting specs, accessories ("Case for Galaxy S24") and long lists of compatible models. Listings below the threshold are kept but labelled *not a match*.

The same-product grouping is conservative: if PriceScout is unsure whether two listings are the same product, it keeps them separate.

### Security

- **Allowed destinations:** only http/https on standard ports, with no embedded credentials, no internal hostnames, and no private, loopback, link-local, CGNAT or metadata addresses. Alternative IP notations are rejected too.
- **Redirects:** followed manually and re-validated at every hop.
- **DNS rebinding:** the connection layer resolves each host, validates the IPs and connects to the validated IP, so the address can't change between the check and the connection.
- **Limits:** responses are size-limited and timed out, and only text content types are accepted. Downloaded scripts or files are never executed.
- **Playwright:** each request a rendered page makes is checked against the same rules. Images, fonts and downloads are blocked.
- **Being a good citizen:** robots.txt is respected, requests are rate-limited per host, and the User-Agent identifies the tool honestly. CAPTCHAs and bot walls are reported as *blocked*.
- **Output safety:** scraped text is escaped (Jinja autoescape, `textContent` in JS), and a strict Content-Security-Policy is applied. Internal errors are logged, never shown to users.

## Limitations of searching user-provided websites

- **Bot protection:** large retailers often answer automated requests with CAPTCHAs or "access denied" (403/429). PriceScout reports these as failed rather than bypassing them, and results vary from run to run.
- **Markup changes:** retailers change their pages often, so site-specific selectors break over time. Extraction then falls back to structured data and heuristics, and the adapters may need updating.
- **Unknown sites:** the generic adapter only works if a site has a discoverable search URL and product data it can read (JSON-LD, microdata or recognisable product cards). Otherwise the site is reported as unsearchable or as having no results.
- **JavaScript-rendered sites** need Playwright, which is slower and uses more resources.
- **robots.txt:** some sites disallow automated search, and PriceScout honours that.
- **Location:** some sites (e.g. Amazon) show prices in the visitor's local currency, so the currency shown is whatever the site returned.
- **Prices:** they change constantly, and shipping, taxes and seller fees are not included. Always confirm the final price on the retailer's site.

## Project layout

```
app/
  main.py            app factory, lifespan, error handlers, security headers
  config.py          settings (PRICESCOUT_* env vars)
  database.py        async engine, SQLite foreign keys, init_db
  cli.py             python -m app.cli init-db
  models/            SQLAlchemy models
  schemas/           Pydantic models (normalized Product, API payloads)
  repositories/      database access
  routers/           home, search (+SSE, JSON API), history
  services/          comparison, matching, currency, ranking rules, URL safety, events
  scrapers/          base interface, registry, fetcher, extraction, browser, generic, sites/, api/
  templates/         Jinja2 templates
  static/            CSS and vanilla JS
tests/               pytest suite + HTML fixtures
```

There is no migrations folder. The schema is created with `create_all` (`python -m app.cli init-db`). If the schema changes later, add Alembic.
