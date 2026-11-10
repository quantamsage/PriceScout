// Results page: consumes Server-Sent Events, renders progress and the
// comparison table, and handles client-side sorting/filtering.
// All scraped content is inserted with textContent (never innerHTML).
(function () {
  "use strict";

  const config = document.getElementById("search-config");
  const eventsUrl = config.dataset.eventsUrl;

  const els = {
    status: document.getElementById("search-status"),
    statSuccess: document.getElementById("stat-success"),
    statFailed: document.getElementById("stat-failed"),
    statMatches: document.getElementById("stat-matches"),
    progress: document.getElementById("progress-list"),
    body: document.getElementById("results-body"),
    empty: document.getElementById("empty-state"),
    best: document.getElementById("best-deal"),
    filter: document.getElementById("filter-website"),
    sort: document.getElementById("sort-by"),
    showAll: document.getElementById("show-all"),
    priceHeader: document.getElementById("price-header"),
    notice: document.getElementById("currency-notice"),
  };

  const state = {
    websites: new Map(), // id -> {status, message, name}
    listings: new Map(), // id -> listing
    comparison: { currency: null, rates_available: false, as_of: null, source: null },
    summary: null,
    done: false,
  };

  els.progress.querySelectorAll("[data-website-id]").forEach((item) => {
    state.websites.set(Number(item.dataset.websiteId), {
      name: item.querySelector(".site").textContent,
      status: "pending",
    });
  });

  // ---------------------------------------------------------------- helpers
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function formatMoney(amount, currency) {
    const value = Number(amount);
    if (!currency) return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency: currency }).format(value);
    } catch (_err) {
      return currency + " " + value.toFixed(2);
    }
  }

  function formatDate(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    return isNaN(date) ? "—" : date.toLocaleString();
  }

  function isSafeUrl(url) {
    try {
      const parsed = new URL(url);
      return parsed.protocol === "https:" || parsed.protocol === "http:";
    } catch (_err) {
      return false;
    }
  }

  function websiteName(id) {
    const site = state.websites.get(id);
    return site ? site.name : "";
  }

  // ------------------------------------------------------ comparison rules
  // Mirrors app/services/comparison_rules.py.
  function comparisonCurrency() {
    if (state.summary) return state.summary.comparison_currency;
    if (state.comparison.rates_available) return state.comparison.currency;
    const priced = Array.from(state.listings.values()).filter((l) => l.price !== null && l.currency);
    const matching = priced.filter((l) => l.is_match);
    const currencies = new Set((matching.length ? matching : priced).map((l) => l.currency));
    return currencies.size === 1 ? currencies.values().next().value : null;
  }

  function comparableValue(listing, currency) {
    if (!currency || listing.price === null) return null;
    if (listing.currency === currency) return Number(listing.price);
    if (listing.converted_currency === currency && listing.converted_price !== null) {
      return Number(listing.converted_price);
    }
    return null;
  }

  function cheapestId(currency) {
    if (state.summary) return state.summary.cheapest_listing_id;
    let best = null;
    state.listings.forEach((listing) => {
      if (!listing.is_match) return;
      const value = comparableValue(listing, currency);
      if (value === null) return;
      if (best === null || value < best.value || (value === best.value && listing.id < best.id)) {
        best = { id: listing.id, value: value };
      }
    });
    return best ? best.id : null;
  }

  function sortListings(listings, currency) {
    const mode = els.sort.value;
    const priceKey = (l) => {
      const comparable = comparableValue(l, currency);
      if (comparable !== null) return [0, comparable];
      if (l.price !== null) return [1, (l.currency || "~") + ":" + String(Number(l.price)).padStart(15, "0")];
      return [2, 0];
    };
    const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
    return listings.sort((a, b) => {
      if (mode === "relevance") return b.match_score - a.match_score || a.id - b.id;
      if (mode === "website") return cmp(websiteName(a.website_id), websiteName(b.website_id)) || a.id - b.id;
      const ka = priceKey(a);
      const kb = priceKey(b);
      if (ka[0] !== kb[0]) return ka[0] - kb[0]; // comparable first, unavailable last
      const order = mode === "price-desc" ? -1 : 1;
      return order * cmp(ka[1], kb[1]) || a.id - b.id;
    });
  }

  // -------------------------------------------------------------- rendering
  function priceCell(listing, currency) {
    const cell = el("td", null);
    cell.dataset.label = "Price";
    if (listing.price === null) {
      const span = el("span", "unavailable", "Price unavailable");
      if (listing.raw_price_text) span.title = "Website showed: " + listing.raw_price_text;
      cell.appendChild(span);
      return cell;
    }
    cell.appendChild(el("div", "price", formatMoney(listing.price, listing.currency)));
    if (listing.converted_price !== null && listing.converted_currency && listing.converted_currency !== listing.currency) {
      const converted = el("div", "converted", "≈ " + formatMoney(listing.converted_price, listing.converted_currency));
      converted.title =
        "1 " + listing.currency + " = " + Number(listing.exchange_rate) + " " + listing.converted_currency +
        " (" + (listing.exchange_rate_source || "exchange rate") + ", " + formatDate(listing.exchange_rate_as_of) + ")";
      cell.appendChild(converted);
    } else if (listing.currency && currency && listing.currency !== currency) {
      cell.appendChild(el("div", "converted", "No exchange rate"));
    }
    return cell;
  }

  function imageNode(listing, className) {
    if (listing.image_url && isSafeUrl(listing.image_url)) {
      const img = el("img", className);
      img.src = listing.image_url;
      img.alt = "";
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.addEventListener("error", () => img.replaceWith(el("div", "thumb-empty", "No image")), { once: true });
      return img;
    }
    return el("div", "thumb-empty", "No image");
  }

  function row(listing, currency, cheapest) {
    const tr = el("tr");
    if (listing.id === cheapest) tr.classList.add("is-cheapest");
    if (!listing.is_match) tr.classList.add("not-match");

    const imageCell = el("td", "cell-image");
    imageCell.appendChild(imageNode(listing, "thumb"));

    const nameCell = el("td", "cell-name");
    nameCell.appendChild(el("div", "product-name", listing.name));
    if (listing.id === cheapest) nameCell.appendChild(el("span", "badge badge-best", "Cheapest"));
    if (!listing.is_match) nameCell.appendChild(el("span", "badge badge-unknown", "Not a match"));

    const siteCell = el("td", null, websiteName(listing.website_id));
    siteCell.dataset.label = "Website";

    const currencyCell = el("td", null, listing.currency || "Unknown");
    currencyCell.dataset.label = "Currency";

    const availabilityCell = el("td");
    availabilityCell.dataset.label = "Availability";
    const labels = { in_stock: "In stock", out_of_stock: "Out of stock", unknown: "Unknown" };
    availabilityCell.appendChild(el("span", "badge badge-" + listing.availability, labels[listing.availability] || "Unknown"));

    const sellerCell = el("td", null, listing.seller || "—");
    sellerCell.dataset.label = "Seller";

    const checkedCell = el("td", null, formatDate(listing.retrieved_at));
    checkedCell.dataset.label = "Checked";

    const linkCell = el("td");
    linkCell.dataset.label = "Link";
    if (isSafeUrl(listing.url)) {
      const a = el("a", null, "View listing ↗");
      a.href = listing.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer nofollow";
      linkCell.appendChild(a);
    }

    [imageCell, nameCell, siteCell, priceCell(listing, currency), currencyCell,
     availabilityCell, sellerCell, checkedCell, linkCell].forEach((c) => tr.appendChild(c));
    return tr;
  }

  function renderBestDeal(listing) {
    els.best.replaceChildren();
    if (!listing) {
      els.best.hidden = true;
      return;
    }
    els.best.hidden = false;
    els.best.className = "best-deal";
    els.best.appendChild(imageNode(listing, ""));
    const info = el("div");
    info.appendChild(el("span", "badge badge-best", "Cheapest match"));
    info.appendChild(el("div", "product-name", listing.name));
    const line = el("div");
    line.appendChild(el("span", "price", formatMoney(listing.price, listing.currency)));
    line.appendChild(document.createTextNode(" at " + websiteName(listing.website_id) + " · "));
    if (isSafeUrl(listing.url)) {
      const a = el("a", null, "View listing ↗");
      a.href = listing.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer nofollow";
      line.appendChild(a);
    }
    info.appendChild(line);
    els.best.appendChild(info);
  }

  function renderNotice(currency) {
    const priced = Array.from(state.listings.values()).filter((l) => l.price !== null && l.currency);
    const currencies = Array.from(new Set(priced.map((l) => l.currency))).sort();
    let text = "";
    if (state.comparison.rates_available && currency) {
      text = "Prices are compared in " + currency + " using exchange rates from " +
        (state.comparison.source || "the configured provider") + " (as of " + formatDate(state.comparison.as_of) + ").";
    } else if (!currency && currencies.length > 1) {
      text = "Listings use different currencies (" + currencies.join(", ") +
        ") and no exchange rates are configured, so they are not ranked against each other.";
    }
    els.notice.textContent = text;
    els.notice.hidden = !text;
  }

  function render() {
    const currency = comparisonCurrency();
    const cheapest = cheapestId(currency);
    const filter = els.filter.value;
    const showAll = els.showAll.checked;
    const visible = sortListings(
      Array.from(state.listings.values()).filter(
        (l) => (showAll || l.is_match) && (!filter || String(l.website_id) === filter)
      ),
      currency
    );
    const fragment = document.createDocumentFragment();
    visible.forEach((listing) => fragment.appendChild(row(listing, currency, cheapest)));
    els.body.replaceChildren(fragment);

    const hidden = Array.from(state.listings.values()).filter((l) => !l.is_match).length;
    if (visible.length) {
      els.empty.hidden = true;
    } else {
      els.empty.hidden = false;
      if (!state.done) {
        els.empty.textContent = "Waiting for results…";
      } else if (state.listings.size === 0) {
        els.empty.textContent = "No products were found on the searched websites.";
      } else if (hidden && !showAll) {
        els.empty.textContent = "No listings closely matched your search. Tick “Show non-matching listings” to see all " + hidden + ".";
      } else {
        els.empty.textContent = "No listings for this filter.";
      }
    }
    renderBestDeal(cheapest !== null ? state.listings.get(cheapest) : null);
    renderNotice(currency);
    renderStats();
  }

  function renderStats() {
    let success = 0;
    let failed = 0;
    state.websites.forEach((site) => {
      if (site.status === "completed") success += 1;
      if (site.status === "failed") failed += 1;
    });
    els.statSuccess.textContent = success;
    els.statFailed.textContent = failed;
    const matches = state.summary
      ? state.summary.matching_products
      : Array.from(state.listings.values()).filter((l) => l.is_match).length;
    els.statMatches.textContent = matches;
  }

  function updateProgress(data) {
    const item = els.progress.querySelector('[data-website-id="' + Number(data.id) + '"]');
    const site = state.websites.get(data.id) || { name: "" };
    site.status = data.status;
    state.websites.set(data.id, site);
    if (!item) return;
    item.className = "progress-item status-" + data.status;
    let detail = "Waiting…";
    if (data.status === "searching") detail = "Searching…";
    else if (data.status === "completed") {
      detail = data.product_count
        ? "Completed (" + data.product_count + " product" + (data.product_count === 1 ? "" : "s") +
          " found, " + (data.matching_count || 0) + " matching)"
        : "Completed (no products found)";
    } else if (data.status === "failed") detail = "Failed (" + (data.message || "unknown error") + ")";
    item.querySelector(".detail").textContent = detail;
  }

  // ----------------------------------------------------------------- events
  const source = new EventSource(eventsUrl);

  function parse(event) {
    try {
      return JSON.parse(event.data);
    } catch (_err) {
      return null;
    }
  }

  source.addEventListener("start", (event) => {
    const data = parse(event);
    if (!data) return;
    state.comparison = data.comparison || state.comparison;
    els.status.textContent = "running";
    els.status.className = "badge badge-running";
    render();
  });

  source.addEventListener("website", (event) => {
    const data = parse(event);
    if (!data) return;
    updateProgress(data);
    (data.listings || []).forEach((listing) => state.listings.set(listing.id, listing));
    render();
  });

  source.addEventListener("summary", (event) => {
    const data = parse(event);
    if (!data) return;
    state.summary = data;
    const groups = data.groups || {};
    state.listings.forEach((listing) => {
      listing.group_key = groups[String(listing.id)] || listing.group_key;
    });
    els.status.textContent = data.status;
    els.status.className = "badge badge-" + data.status;
    render();
  });

  source.addEventListener("done", () => {
    state.done = true;
    source.close();
    if (!state.summary) {
      els.status.textContent = "failed";
      els.status.className = "badge badge-failed";
    }
    render();
  });

  source.addEventListener("error", () => {
    if (state.done) source.close();
    // Otherwise the browser reconnects automatically and the server replays
    // the event history; listings are keyed by id so nothing duplicates.
  });

  // ----------------------------------------------------------------- controls
  els.filter.addEventListener("change", render);
  els.sort.addEventListener("change", render);
  els.showAll.addEventListener("change", render);
  els.priceHeader.addEventListener("click", () => {
    els.sort.value = els.sort.value === "price-asc" ? "price-desc" : "price-asc";
    render();
  });
})();
