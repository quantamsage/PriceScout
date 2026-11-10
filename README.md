# PriceScout

PriceScout is a website that compares the price of a product across several online shops at once.

You type what you want to buy (for example *Samsung Galaxy S24 Ultra 256GB*) and pick the shops to check (for example Amazon, eBay, Jumia and Kilimall). PriceScout searches every shop at the same time, shows results as each shop answers, and puts the prices side by side with the cheapest matching listing highlighted.

- **No AI and no paid services.** It reads shop pages using ordinary, predictable rules.
- **Honest results.** If a price can't be read, it says *Price unavailable* instead of guessing. Prices in different currencies are never compared unless real exchange rates are configured. Shops that block automated searches are shown as *blocked*, not bypassed.
- **Remembers your searches.** Every search and the prices it found are saved, so you can look back at them later on the History page.

---

## Contents

1. [What you need](#1-what-you-need)
2. [Install and start PriceScout (step by step)](#2-install-and-start-pricescout-step-by-step)
3. [Using the website](#3-using-the-website)
4. [Stopping and restarting](#4-stopping-and-restarting)
5. [Troubleshooting](#5-troubleshooting)
6. [Configuration](#6-configuration)
7. [Running the tests](#7-running-the-tests)
8. [How it works (technical)](#8-how-it-works-technical)
9. [Security and good behaviour](#9-security-and-good-behaviour)
10. [Limitations](#10-limitations)
11. [Project layout](#11-project-layout)

---

## 1. What you need

| Requirement | Details |
|---|---|
| **Computer** | Windows 10/11, macOS or Linux |
| **Python** | Version **3.10 or newer**. Check with `python --version` (Windows) or `python3 --version` (macOS/Linux). Download it from [python.org](https://www.python.org/downloads/). On Windows, tick **"Add python.exe to PATH"** during installation. |
| **Internet connection** | Needed to install PriceScout and to search shops |
| **Disk space** | About 1 GB: roughly 200 MB for the Python packages and about 700 MB for the optional Chromium browser |
| **A terminal** | **Windows:** PowerShell (Start menu → type *PowerShell*). **macOS:** Terminal. **Linux:** any terminal. |
| **A web browser** | Any modern browser (Chrome, Edge, Firefox, Safari) |

You don't need an account, an API key or any paid service.

---

## 2. Install and start PriceScout (step by step)

Type each command into your terminal and press **Enter**. Wait for each one to finish before typing the next. Steps 1 to 6 are needed only the first time.

### Step 1: Get the code and open its folder

Download or clone the project, then move into its folder. Use the folder's actual location on your computer:

```powershell
cd "C:\path\to\PriceScout"
```

Every command below must be run from inside this folder.

### Step 2: Create the virtual environment

PriceScout keeps its packages in a private folder called `.venv`, so it doesn't interfere with anything else on your computer. This is required. Never install the packages globally.

```powershell
# Windows
python -m venv .venv
```

```bash
# macOS / Linux
python3 -m venv .venv
```

### Step 3: Activate the virtual environment

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

When it works, your prompt starts with **`(.venv)`**. You need to activate it again every time you open a new terminal.

> **Windows: "running scripts is disabled on this system"?** Run this once, then try Step 3 again:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### Step 4: Install the packages

```bash
pip install -r requirements.txt
```

This downloads everything PriceScout needs and can take a few minutes. On a slow connection, use `pip install --timeout 120 -r requirements.txt` to give each download more time.

### Step 5: Install the browser for JavaScript-heavy shops (optional)

Some shops only show their products after running JavaScript. For those, PriceScout uses a hidden copy of the Chromium browser:

```bash
playwright install chromium
```

This download is about 200 MB (roughly 700 MB once unpacked). You can skip it: PriceScout still works, and those shops will show *"needs JavaScript … browser rendering is unavailable"*.

### Step 6: Create the database

```bash
python -m app.cli init-db
```

You should see `Database ready: sqlite+aiosqlite:///./data/pricescout.db`. This creates the file where searches are saved. If you skip it, the database is created automatically the first time the app starts.

### Step 7: Start the website

```bash
uvicorn app.main:app --reload
```

Wait until you see `Application startup complete.`, then open this address in your browser:

**http://127.0.0.1:8000**

Leave the terminal window open while you use PriceScout. Closing it stops the website.

### Every time after the first

```powershell
cd "C:\path\to\PriceScout"
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
uvicorn app.main:app --reload
```

---

## 3. Using the website

### Start a comparison (Compare page)

1. **Product:** type what you want, as specifically as you can, e.g. `Samsung Galaxy S24 Ultra 256GB`, `Logitech MX Master 3S` or `Samsung 55-inch TV`. Including the model and storage size gives better matches.
2. **Websites to search:**
   - Click a quick-add button (**Amazon**, **eBay**, **Jumia**, **Kilimall**), or
   - type a shop's address in the box (`jumia.co.ke` works, and `https://` is added for you), and
   - click **+ Add website** for another box, or **✕** to remove one.
   - You can search up to **8** websites at once.
3. Click **Compare prices**.

Shops without built-in support can also be searched. PriceScout tries to find the shop's search page on its own. If it can't, you can show it where the search term goes by putting `{query}` in the address, for example `https://shop.example.com/search?q={query}`.

### Watch the progress

You're taken to the results page straight away:

- **Summary boxes:** websites submitted, successful searches, failed searches and matching products.
- **Progress:** one card per shop.
  - Blue: *Searching…*
  - Green: *Completed (8 products found, 3 matching)*
  - Red: *Failed (reason)*, for example *Request blocked by the website* or *Search timed out*
- Results appear as soon as each shop finishes. One slow or failing shop never holds up the others.

### Read the results

| What you see | Meaning |
|---|---|
| **Green "Cheapest match" box** | The lowest-priced listing that matches your search |
| **CHEAPEST** badge / green row | The same listing, inside the table |
| **NOT A MATCH** badge | The listing looks like a different product (other storage size, other model, or an accessory such as a case or charger). These are hidden unless you tick **Show non-matching listings**. |
| ***Price unavailable*** | The shop didn't show a readable price. It is *not* zero. |
| **≈ USD 1,201.54** under a price | Converted price. Hover over it to see the exchange rate, its source and its date. Only shown when exchange rates are configured (see [Configuration](#6-configuration)). |
| **No exchange rate** | This price is in a currency that can't be converted, so it isn't ranked |
| **In stock / Out of stock / Unknown** | Availability, when the shop shows it |
| **View listing ↗** | Opens the product on the shop's own website in a new tab |
| **Last checked** | When that price was read |

If the shops use different currencies (for example KES and USD) and no exchange rates are set up, a note explains that the prices are shown side by side but not ranked against each other.

### Sort and filter

- **Website:** show only one shop's listings.
- **Sort:** price low to high, price high to low, best match, or website. You can also click the **Price ↕** column header.
- **Show non-matching listings:** include listings marked *not a match*.

### History

Click **History** at the top to see every past search: date, shops searched, number of matches and status (*completed*, *partial* when some shops failed, or *failed*). Click a search to reopen it exactly as it was, with the prices from that time. Searching again never overwrites old results, so you build up a price history.

### On a phone

The site works on phones. To use it from a phone on the same Wi-Fi, start the server with `uvicorn app.main:app --host 0.0.0.0` and open `http://<your-computer's-IP>:8000` on the phone. Only do this on a network you trust.

---

## 4. Stopping and restarting

- **Stop:** click the terminal where PriceScout is running and press **Ctrl + C**.
- **Restart:** run `uvicorn app.main:app --reload` again, after activating the virtual environment.
- **Different port** (if 8000 is busy): `uvicorn app.main:app --port 8001`, then open http://127.0.0.1:8001.
- **Clear all saved searches:** stop the server, delete `data/pricescout.db` (and any `data/pricescout.db-wal` / `-shm` files next to it), then start it again.

`--reload` restarts the server automatically when the code changes. It's useful while developing, and you can leave it out for normal use.

---

## 5. Troubleshooting

| Problem | Solution |
|---|---|
| `python` is not recognised (Windows) | Reinstall Python with **"Add python.exe to PATH"** ticked, or use `py -m venv .venv` instead of `python -m venv .venv`. |
| "running scripts is disabled on this system" | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again. |
| `uvicorn` / `pip` / `playwright` is not recognised | The virtual environment isn't active. Run Step 3 again and look for `(.venv)` in the prompt. |
| `pip install` fails with timeouts or "network connectivity" | Retry with `pip install --timeout 120 --retries 10 -r requirements.txt`. |
| "address already in use" / port 8000 busy | Another program (or another PriceScout) is using the port. Stop it, or use `--port 8001`. |
| A shop says **"Request blocked by the website"** | The shop refused automated access (a CAPTCHA or bot check). This is the shop's choice and PriceScout won't get around it. Try again later or use other shops. |
| A shop says **"robots.txt does not allow automated searches"** | The shop has asked automated tools not to search it, and PriceScout respects that. |
| A shop says **"needs JavaScript … browser rendering is unavailable"** | Install the browser with Step 5 (`playwright install chromium`). |
| A shop says **"cannot be searched automatically"** or finds no products | PriceScout couldn't find that shop's search page. Add `{query}` to the address (see [Using the website](#3-using-the-website)). |
| **"Search timed out"** | The shop was too slow. Try again, or raise `PRICESCOUT_SITE_TIMEOUT_SECONDS`. |
| Only "not a match" results | The shop returned other products or accessories. Tick **Show non-matching listings** to see them, or make the product name more specific. |
| Prices aren't ranked across shops | They're in different currencies. Configure exchange rates (see below). |
| Prices on Amazon are in your local currency | Amazon shows prices for your location. PriceScout displays whatever currency the shop returned. |

---

## 6. Configuration

Everything works with the defaults. To change a setting, copy `.env.example` to a new file called `.env`, edit the values, and restart the server. You can also set the same names as environment variables.

| Setting | Default | What it does |
|---|---|---|
| `PRICESCOUT_MAX_WEBSITES_PER_SEARCH` | 8 | Maximum number of websites in one search |
| `PRICESCOUT_MAX_CONCURRENT_SITES` | 4 | How many websites are searched at the same time |
| `PRICESCOUT_SITE_TIMEOUT_SECONDS` | 45 | Total time allowed for each website |
| `PRICESCOUT_REQUEST_TIMEOUT_SECONDS` | 15 | Time allowed for each single page request |
| `PRICESCOUT_MAX_RESPONSE_BYTES` | 5242880 (5 MB) | Largest page that will be downloaded |
| `PRICESCOUT_MAX_RESULTS_PER_SITE` | 25 | Maximum listings kept from each website |
| `PRICESCOUT_PER_HOST_MIN_INTERVAL_SECONDS` | 1.0 | Minimum pause between requests to the same website |
| `PRICESCOUT_RESPECT_ROBOTS_TXT` | true | Honour websites' robots.txt rules |
| `PRICESCOUT_PLAYWRIGHT_ENABLED` | true | Use the hidden browser for JavaScript-heavy sites |
| `PRICESCOUT_PLAYWRIGHT_MAX_CONCURRENCY` | 2 | How many browser pages may run at once |
| `PRICESCOUT_MATCH_THRESHOLD` | 0.6 | How closely a listing must match your search (0 to 1) to count as a match |
| `PRICESCOUT_COMPARISON_CURRENCY` | USD | Currency that converted prices are compared in |
| `PRICESCOUT_EXCHANGE_RATES` | *(empty)* | Fixed exchange rates (see below) |
| `PRICESCOUT_EXCHANGE_RATES_AS_OF` | *(empty)* | Date shown for the fixed rates, e.g. `2026-11-01T00:00:00Z` |
| `PRICESCOUT_EXCHANGE_RATE_PROVIDER_URL` | *(empty)* | Online exchange-rate source (see below) |
| `PRICESCOUT_EBAY_CLIENT_ID` / `PRICESCOUT_EBAY_CLIENT_SECRET` | *(empty)* | Use eBay's official API instead of reading its pages |
| `PRICESCOUT_EBAY_MARKETPLACE_ID` | EBAY_US | eBay marketplace for the official API |
| `PRICESCOUT_DATABASE_URL` | `sqlite+aiosqlite:///./data/pricescout.db` | Where searches are saved |
| `PRICESCOUT_LOG_LEVEL` | INFO | Log detail: DEBUG, INFO, WARNING… |

### Comparing different currencies

Conversion is **off by default**, because PriceScout won't invent exchange rates. Without rates, listings in one currency are still ranked, and mixed currencies are shown side by side but not ranked. To turn conversion on, pick one of these options:

```ini
# Option A: fixed rates = how many units of each currency equal 1 comparison currency
PRICESCOUT_COMPARISON_CURRENCY=USD
PRICESCOUT_EXCHANGE_RATES={"USD": 1, "KES": 129.2, "EUR": 0.92, "GBP": 0.79}
PRICESCOUT_EXCHANGE_RATES_AS_OF=2026-11-01T00:00:00Z

# Option B: live rates from a provider ({base} is replaced by the comparison currency)
PRICESCOUT_EXCHANGE_RATE_PROVIDER_URL=https://open.er-api.com/v6/latest/{base}
```

Every converted price shows the rate, its source and its date.

### Official eBay API (optional)

With eBay developer credentials (from developer.ebay.com) set in `PRICESCOUT_EBAY_CLIENT_ID` and `PRICESCOUT_EBAY_CLIENT_SECRET`, eBay searches use the official Browse API instead of reading web pages. This is more reliable and is never blocked.

---

## 7. Running the tests

Activate the virtual environment, then run:

```bash
pytest                                                # whole suite
pytest tests/test_matching.py                         # one file
pytest tests/test_scrapers.py::test_amazon_adapter    # one test
pytest -q                                             # compact output
```

The tests never contact real websites. They use saved HTML pages in `tests/fixtures/` and simulated HTTP responses, so they're fast and give the same result every time.

| Test file | What it covers |
|---|---|
| `test_scrapers.py` | Each shop adapter, HTML parsing, the browser fallback, robots.txt, blocking, timeouts, size limits |
| `test_price_parsing.py` | Price and currency extraction (`$1,299.99`, `KSh 12,999`, `1.299,00 €`, …) |
| `test_matching.py` | Product matching, accessories, grouping the same product across shops |
| `test_comparison.py` | Currency conversion and price-ranking rules |
| `test_security.py` | URL validation and protection against requests to internal networks |
| `test_database.py` | Models, foreign keys, history preservation, repository operations |
| `test_routes.py` | Pages, validation, live progress (SSE), concurrent and failing searches, history |

---

## 8. How it works (technical)

**Stack:** Python 3.10+, FastAPI, Jinja2, vanilla JavaScript/CSS, SQLite via SQLAlchemy 2.0 (async, aiosqlite), Pydantic, HTTPX, BeautifulSoup4 + lxml, Playwright (fallback only), RapidFuzz, pytest.

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
| Services | `app/services/` | Orchestration, matching, currency, ranking rules, URL safety, events |
| Scrapers | `app/scrapers/` | Adapter interface, registry, fetcher, extraction, browser pool |
| Data | `app/repositories/`, `app/models/` | All SQL: searches, websites, listings, price snapshots |

### Pages and endpoints

| URL | Purpose |
|---|---|
| `GET /` | Search form |
| `POST /search` | Submit a search (form) and redirect to its results page |
| `GET /search/{id}` | Results page |
| `GET /search/{id}/events` | Server-Sent Events: `start`, `website` (per-site status and listings), `summary`, `done`. Completed searches are replayed from the database. |
| `GET /history?page=N` | Search history |
| `POST /api/searches` | JSON API: `{"query": "...", "websites": ["..."]}` returns `201 {id, url, events_url}` |
| `GET /api/searches/{id}` | JSON API: the full search with websites, listings and summary |

### Website adapters

Every adapter implements `WebsiteAdapter.search(query, ctx) -> list[Product]` (`app/scrapers/base.py`) and returns the same normalized `Product` (`app/schemas/product.py`). The registry checks them in this order:

1. **API adapters.** The official eBay Browse API, when credentials are configured.
2. **Site-specific adapters** (`app/scrapers/sites/`). Amazon, eBay, Jumia and Kilimall, each with its known search URL, CSS selectors and default currency.
3. **Generic adapter** (`app/scrapers/generic.py`). Used for any other shop. It finds the search URL from a `{query}` placeholder, then the homepage search form, then OpenSearch, then common Shopify/WooCommerce/Magento URL patterns.

Within a page, extraction tries JSON-LD, then microdata, then site selectors, and keeps whichever finds the most priced products. Heuristic product-card detection and Open Graph run only if those find nothing. Playwright runs only when the plain HTML yields nothing and the page looks JavaScript-rendered.

**Adding a shop:** subclass `HtmlAdapter` with a `host_pattern`, `selectors` and `search_urls()`, then add it to `SITE_ADAPTERS` in `app/scrapers/registry.py`. Nothing in the comparison logic needs to change.

### Product matching

`app/services/product_matching.py` scores each listing from 0 to 1. The score combines token coverage, fuzzy name similarity (RapidFuzz), brand, model numbers, variant words (Pro/Max/Ultra…) and specs (storage, screen size). It is penalised for conflicting specs, accessories ("Case for Galaxy S24", "… Phone Case") and long lists of compatible models.

Grouping the same product across shops is conservative: if PriceScout is unsure whether two listings are the same product, it keeps them separate.

### Data

The tables are `searches`, `search_websites`, `product_listings` and `price_snapshots`. Foreign keys are enforced and the tables are indexed. Rows are only ever added, so past prices are preserved, and `canonical_url` links the same listing across searches. A missing price is stored as `NULL`, never `0`. There's no migrations folder: the schema is created by `create_all` (`python -m app.cli init-db`). Add Alembic if the schema needs to change later.

---

## 9. Security and good behaviour

- **Allowed destinations:** only `http`/`https` on standard ports, with no embedded passwords and no localhost or internal hostnames. Private, loopback, link-local, CGNAT and cloud-metadata IP addresses are rejected, including disguised forms such as `http://2130706433/`.
- **Every redirect is re-checked**, and connections are pinned to the IP address that was validated, which prevents DNS-rebinding tricks.
- **Limits:** response sizes and timeouts are capped, and only text content types are accepted. Downloaded scripts or files are never executed.
- **The hidden browser** applies the same address rules to every request a page makes. Images, fonts and downloads are blocked.
- **Respectful by design:** robots.txt is honoured, requests are rate-limited per website, the User-Agent identifies PriceScout honestly, and CAPTCHAs or bot checks are reported, never bypassed.
- **Safe display:** shop content is always escaped, and a strict Content-Security-Policy is applied. Internal errors are logged on the server, never shown to users.

---

## 10. Limitations

- **Bot protection:** big shops often block automated searches. The same shop may work one time and be *blocked* the next.
- **Website changes:** shops redesign their pages, which can break the built-in rules for reading them. PriceScout then falls back to generic reading, but the adapters may need updating.
- **Unknown shops** only work if they have a findable search page and readable product information.
- **JavaScript-heavy shops** need the optional Chromium browser and are slower to search.
- **robots.txt:** some shops forbid automated searching, and PriceScout respects that.
- **Location:** some shops show prices in your local currency, based on where you are.
- **The price shown is the listed price.** Shipping, taxes and fees aren't included, and prices change constantly. Always confirm the final price on the shop's website before buying.

---

## 11. Project layout

```
app/
  main.py            app factory, startup/shutdown, error handlers, security headers
  config.py          settings (PRICESCOUT_* variables)
  database.py        async engine, SQLite foreign keys, init_db
  cli.py             python -m app.cli init-db
  models/            SQLAlchemy models
  schemas/           Pydantic models (normalized Product, API payloads)
  repositories/      database access
  routers/           home, search (+SSE, JSON API), history
  services/          comparison, matching, currency, ranking rules, URL safety, events
  scrapers/          adapter interface, registry, fetcher, extraction, browser, generic, sites/, api/
  templates/         Jinja2 page templates
  static/            CSS and JavaScript
data/                SQLite database (created on first run)
tests/               pytest suite and HTML fixtures
requirements.txt     pinned dependencies
.env.example         example configuration
```
