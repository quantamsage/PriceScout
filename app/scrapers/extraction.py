"""HTML → Product extraction helpers shared by all HTML adapters.

Extraction priority (see spec): JSON-LD → microdata → Open Graph →
site-specific selectors → generic heuristics. Every function here is pure
(HTML in, products out) and never executes scripts from the page.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString
from pydantic import ValidationError

from app.schemas.product import Availability, Product, clean_text
from app.services.price_parsing import ParsedPrice, parse_price, parse_structured_price

logger = logging.getLogger(__name__)


def parse_html(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover - lxml missing or failing
        return BeautifulSoup(html, "html.parser")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def absolute_url(base_url: str, value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    if not value or value.startswith(("javascript:", "data:", "mailto:", "#")):
        return None
    if value.startswith("//"):
        value = urlsplit(base_url).scheme + ":" + value
    return urljoin(base_url, value)


def normalize_availability(value: Optional[str]) -> Availability:
    if not value:
        return "unknown"
    text = str(value).lower()
    if any(k in text for k in ("outofstock", "out of stock", "out-of-stock", "sold out", "soldout",
                               "unavailable", "discontinued", "currently unavailable")):
        return "out_of_stock"
    if any(k in text for k in ("instock", "in stock", "in-stock", "available", "limitedavailability",
                               "onlineonly", "presale", "preorder", "add to cart")):
        return "in_stock"
    return "unknown"


def image_from_tag(img: Optional[Tag], base_url: str) -> Optional[str]:
    if img is None:
        return None
    for attr in ("data-src", "data-lazy-src", "data-original", "src", "data-srcset", "srcset"):
        value = img.get(attr)
        if not value:
            continue
        if "srcset" in attr:
            value = value.split(",")[0].strip().split(" ")[0]
        url = absolute_url(base_url, value)
        if url:
            return url
    return None


def make_product(**fields: Any) -> Optional[Product]:
    try:
        return Product(**fields)
    except ValidationError as exc:
        logger.debug("Discarding invalid product: %s", exc.errors()[:1])
        return None


def dedupe(products: Iterable[Product], limit: int) -> list[Product]:
    seen: set[str] = set()
    result: list[Product] = []
    for product in products:
        key = product.url.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        result.append(product)
        if len(result) >= limit:
            break
    return result


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------

def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _types(node: dict) -> set[str]:
    return {str(t).split("/")[-1].lower() for t in _as_list(node.get("@type"))}


def _iter_json_ld_nodes(soup: BeautifulSoup) -> Iterable[dict]:
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw, strict=False)
        except (ValueError, RecursionError):
            continue
        stack = _as_list(data)
        depth_guard = 0
        while stack and depth_guard < 5000:
            depth_guard += 1
            node = stack.pop(0)
            if not isinstance(node, dict):
                continue
            yield node
            stack.extend(_as_list(node.get("@graph")))
            types = _types(node)
            if "itemlist" in types or "offercatalog" in types:
                for element in _as_list(node.get("itemListElement")):
                    if isinstance(element, dict):
                        item = element.get("item")
                        stack.append(item if isinstance(item, dict) else element)
            if "searchresultspage" in types or "collectionpage" in types or "webpage" in types:
                stack.extend(_as_list(node.get("mainEntity")))


def _json_ld_image(value: Any, base_url: str) -> Optional[str]:
    for item in _as_list(value):
        if isinstance(item, str):
            return absolute_url(base_url, item)
        if isinstance(item, dict):
            url = item.get("url") or item.get("contentUrl")
            if url:
                return absolute_url(base_url, str(url))
    return None


def _offer_price(offers: Any) -> tuple[ParsedPrice, Optional[str], Optional[str], Optional[str]]:
    """Return (price, availability, seller, url) from an offers node."""
    best: Optional[ParsedPrice] = None
    availability = seller = url = None
    for offer in _as_list(offers):
        if not isinstance(offer, dict):
            continue
        currency = offer.get("priceCurrency")
        spec = offer.get("priceSpecification")
        if isinstance(spec, list):
            spec = spec[0] if spec else None
        if isinstance(spec, dict):
            currency = currency or spec.get("priceCurrency")
        raw = offer.get("price")
        if raw is None:
            raw = offer.get("lowPrice")
        if raw is None and isinstance(spec, dict):
            raw = spec.get("price")
        parsed = parse_structured_price(raw, currency)
        if parsed.amount is not None and (best is None or best.amount is None or parsed.amount < best.amount):
            best = parsed
        availability = availability or offer.get("availability")
        offer_seller = offer.get("seller")
        if isinstance(offer_seller, dict):
            seller = seller or offer_seller.get("name")
        elif isinstance(offer_seller, str):
            seller = seller or offer_seller
        url = url or offer.get("url")
    return best or ParsedPrice(None, None), availability, seller, url


def extract_json_ld(soup: BeautifulSoup, base_url: str, source: str) -> list[Product]:
    products: list[Product] = []
    for node in _iter_json_ld_nodes(soup):
        if "product" not in _types(node) and "productgroup" not in _types(node):
            continue
        price, availability, seller, offer_url = _offer_price(node.get("offers"))
        url = absolute_url(base_url, node.get("url") or offer_url) or base_url
        product = make_product(
            name=node.get("name"),
            url=url,
            price=price.amount,
            currency=price.currency,
            raw_price_text=None,
            image_url=_json_ld_image(node.get("image"), base_url),
            seller=seller,
            availability=normalize_availability(availability),
            source=source,
            extraction_method="json-ld",
        )
        if product:
            products.append(product)
    return products


# ---------------------------------------------------------------------------
# Microdata (itemscope itemtype="https://schema.org/Product")
# ---------------------------------------------------------------------------

def _itemprop(scope: Tag, name: str) -> Optional[Tag]:
    return scope.find(attrs={"itemprop": re.compile(rf"(^|\s){name}(\s|$)")})


def _itemprop_value(tag: Optional[Tag]) -> Optional[str]:
    if tag is None:
        return None
    for attr in ("content", "href", "src", "value"):
        if tag.get(attr):
            return str(tag.get(attr))
    return tag.get_text(" ", strip=True)


def extract_microdata(soup: BeautifulSoup, base_url: str, source: str) -> list[Product]:
    products: list[Product] = []
    for scope in soup.find_all(attrs={"itemtype": re.compile(r"schema\.org/Product$", re.I)}):
        name = _itemprop_value(_itemprop(scope, "name"))
        url = absolute_url(base_url, _itemprop_value(_itemprop(scope, "url")))
        if not url:
            link = scope.find("a", href=True)
            url = absolute_url(base_url, link["href"]) if link else base_url
        currency = _itemprop_value(_itemprop(scope, "priceCurrency"))
        price_tag = _itemprop(scope, "price") or _itemprop(scope, "lowPrice")
        price = parse_structured_price(_itemprop_value(price_tag), currency)
        image_tag = _itemprop(scope, "image")
        image = (
            image_from_tag(image_tag, base_url)
            if image_tag is not None and image_tag.name == "img"
            else absolute_url(base_url, _itemprop_value(image_tag))
        )
        product = make_product(
            name=name,
            url=url,
            price=price.amount,
            currency=price.currency,
            raw_price_text=price_tag.get_text(" ", strip=True) if price_tag else None,
            image_url=image,
            availability=normalize_availability(_itemprop_value(_itemprop(scope, "availability"))),
            source=source,
            extraction_method="microdata",
        )
        if product:
            products.append(product)
    return products


# ---------------------------------------------------------------------------
# Open Graph / meta (product detail pages)
# ---------------------------------------------------------------------------

def _meta(soup: BeautifulSoup, *names: str) -> Optional[str]:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag["content"])
    return None


def extract_open_graph(soup: BeautifulSoup, base_url: str, source: str) -> list[Product]:
    og_type = (_meta(soup, "og:type") or "").lower()
    amount = _meta(soup, "product:price:amount", "og:price:amount")
    if "product" not in og_type and amount is None:
        return []
    currency = _meta(soup, "product:price:currency", "og:price:currency")
    price = parse_structured_price(amount, currency)
    product = make_product(
        name=_meta(soup, "og:title") or (soup.title.get_text(strip=True) if soup.title else None),
        url=absolute_url(base_url, _meta(soup, "og:url")) or base_url,
        price=price.amount,
        currency=price.currency,
        image_url=absolute_url(base_url, _meta(soup, "og:image")),
        availability=normalize_availability(_meta(soup, "product:availability", "og:availability")),
        source=source,
        extraction_method="open-graph",
    )
    return [product] if product else []


# ---------------------------------------------------------------------------
# Site-specific CSS selectors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SelectorConfig:
    """CSS selectors for one site's search-results markup. Each field may
    contain comma-separated alternatives; the first match wins."""

    item: str
    name: str
    link: str
    price: str
    image: str = "img"
    seller: Optional[str] = None
    availability: Optional[str] = None
    brand: Optional[str] = None  # prepended to the name when the title omits it
    skip_if_name_matches: Optional[str] = None


def _first(scope: Tag, selector: Optional[str]) -> Optional[Tag]:
    if not selector:
        return None
    for alternative in selector.split(","):
        found = scope.select_one(alternative.strip())
        if found is not None:
            return found
    return None


def extract_with_selectors(
    soup: BeautifulSoup,
    base_url: str,
    source: str,
    config: SelectorConfig,
    default_currency: Optional[str] = None,
) -> list[Product]:
    products: list[Product] = []
    skip = re.compile(config.skip_if_name_matches, re.I) if config.skip_if_name_matches else None
    for item in soup.select(config.item):
        name_tag = _first(item, config.name)
        link_tag = _first(item, config.link)
        if name_tag is None or link_tag is None:
            continue
        name = (
            name_tag.get("aria-label") or name_tag.get("title") or name_tag.get_text(" ", strip=True)
        )
        if skip and name and skip.search(name):
            continue
        brand_tag = _first(item, config.brand)
        brand = brand_tag.get_text(" ", strip=True) if brand_tag is not None else ""
        if brand and name and brand_tag is not name_tag and brand.lower() not in name.lower():
            name = f"{brand} {name}"
        price_tag = _first(item, config.price)
        price_text = clean_text(price_tag.get_text(" ", strip=True), 100) if price_tag else None
        parsed = parse_price(price_text, default_currency=default_currency)
        seller_tag = _first(item, config.seller)
        availability_tag = _first(item, config.availability)
        availability_text = availability_tag.get_text(" ", strip=True) if availability_tag else None
        product = make_product(
            name=name,
            url=absolute_url(base_url, link_tag.get("href")),
            price=parsed.amount,
            currency=parsed.currency,
            raw_price_text=price_text,
            image_url=image_from_tag(_first(item, config.image), base_url),
            seller=seller_tag.get_text(" ", strip=True) if seller_tag else None,
            availability=normalize_availability(availability_text),
            source=source,
            extraction_method="selectors",
        )
        if product:
            products.append(product)
    return products


# ---------------------------------------------------------------------------
# Generic heuristic extraction (unknown sites)
# ---------------------------------------------------------------------------

# Requires a currency marker next to the number, so stray numbers are ignored.
_PRICE_TEXT = re.compile(
    r"(?:US\$|C\$|A\$|KSh?s?\.?|KES|USD|EUR|GBP|NGN|UGX|TZS|ZAR|[$€£₦₹¥])\s?\d[\d.,\s ]*"
    r"|\d[\d.,\s ]*\s?(?:€|£|KES|KSh|USD|EUR|GBP)",
    re.IGNORECASE,
)
_SKIP_ANCESTORS = {"nav", "header", "footer", "script", "style", "noscript", "template", "form", "select", "option"}
_UTILITY_LINK = re.compile(r"cart|basket|wishlist|wish-list|compare|login|signin|account|share", re.I)
_OLD_PRICE_HINT = re.compile(r"old|was|strike|before|regular|list-?price|crossed", re.I)


def _is_old_price(node: Tag) -> bool:
    for ancestor in [node, *list(node.parents)[:3]]:
        if not isinstance(ancestor, Tag):
            continue
        if ancestor.name in ("del", "s", "strike"):
            return True
        classes = " ".join(ancestor.get("class") or [])
        if _OLD_PRICE_HINT.search(classes):
            return True
    return False


def _in_skipped_region(node: Tag) -> bool:
    return any(isinstance(p, Tag) and p.name in _SKIP_ANCESTORS for p in node.parents)


def _card_name(card: Tag, link: Tag) -> Optional[str]:
    candidates: list[str] = []
    for heading in card.find_all(["h1", "h2", "h3", "h4", "h5"]):
        candidates.append(heading.get_text(" ", strip=True))
    if link.get("title"):
        candidates.append(str(link["title"]))
    if link.get("aria-label"):
        candidates.append(str(link["aria-label"]))
    for anchor in card.find_all("a", href=True):
        candidates.append(anchor.get_text(" ", strip=True))
    img = card.find("img", alt=True)
    if img is not None:
        candidates.append(str(img["alt"]))
    for candidate in candidates:
        cleaned = clean_text(candidate, 500)
        if cleaned and len(cleaned) >= 4 and not _PRICE_TEXT.fullmatch(cleaned) and re.search(r"[A-Za-z]{2}", cleaned):
            return cleaned
    return None


def extract_generic_cards(
    soup: BeautifulSoup,
    base_url: str,
    source: str,
    default_currency: Optional[str] = None,
) -> list[Product]:
    body = soup.body or soup
    price_nodes: list[Tag] = []
    for text in body.find_all(string=_PRICE_TEXT):
        if not isinstance(text, NavigableString):
            continue
        parent = text.parent
        if parent is None or _in_skipped_region(parent) or _is_old_price(parent):
            continue
        if len(text.strip()) > 60:
            continue
        price_nodes.append(parent)

    price_node_ids = {id(node) for node in price_nodes}
    products: list[Product] = []
    used_cards: set[int] = set()
    for node in price_nodes:
        card: Optional[Tag] = None
        ancestor: Optional[Tag] = node
        for _depth in range(7):
            ancestor = ancestor.parent if ancestor is not None else None
            if ancestor is None or not isinstance(ancestor, Tag) or ancestor.name in ("body", "html"):
                break
            links = {
                str(a.get("href")).split("#")[0]
                for a in ancestor.find_all("a", href=True)
                if not str(a.get("href")).startswith(("#", "javascript:"))
                and not _UTILITY_LINK.search(str(a.get("href")))
            }
            prices_inside = sum(1 for d in ancestor.find_all(True) if id(d) in price_node_ids)
            # A product card links to exactly one product and has one current price.
            if len(links) > 1 or prices_inside > 2 or len(ancestor.get_text(" ", strip=True)) > 1200:
                break
            if links:
                card = ancestor
        if card is None or id(card) in used_cards:
            continue
        used_cards.add(id(card))
        link = card.find("a", href=True)
        if link is None:
            continue
        price_text = clean_text(node.get_text(" ", strip=True), 100)
        parsed = parse_price(price_text, default_currency=default_currency)
        product = make_product(
            name=_card_name(card, link),
            url=absolute_url(base_url, link.get("href")),
            price=parsed.amount,
            currency=parsed.currency,
            raw_price_text=price_text,
            image_url=image_from_tag(card.find("img"), base_url),
            availability=normalize_availability(card.get_text(" ", strip=True)[:400]),
            source=source,
            extraction_method="generic-html",
        )
        if product:
            products.append(product)
    return products


# ---------------------------------------------------------------------------
# Page classification
# ---------------------------------------------------------------------------

_BLOCK_MARKERS = (
    "/errors/validatecaptcha",
    "captcha-delivery.com",
    "px-captcha",
    "cf-chl-",
    "challenge-platform",
    "are you a robot",
    "are you a human",
    "verify you are human",
    "unusual traffic",
    "pardon our interruption",
    "request unsuccessful. incapsula",
    "enter the characters you see below",
)
_BLOCK_TITLES = ("robot check", "attention required", "just a moment", "access denied", "security check", "captcha")


def looks_blocked(soup: BeautifulSoup, html: str) -> bool:
    title = (soup.title.get_text(" ", strip=True).lower() if soup.title else "")
    if any(marker in title for marker in _BLOCK_TITLES):
        return True
    lowered = html[:200_000].lower()
    return any(marker in lowered for marker in _BLOCK_MARKERS)


def looks_js_rendered(soup: BeautifulSoup) -> bool:
    """Heuristic: the page is an app shell whose content needs JavaScript."""
    body = soup.body
    if body is None:
        return False
    text_length = len(body.get_text(" ", strip=True))
    scripts = len(soup.find_all("script"))
    shell = body.find(id=re.compile(r"^(__next|__nuxt|root|app|svelte)$"))
    noscript = " ".join(n.get_text(" ", strip=True).lower() for n in soup.find_all("noscript"))
    if "enable javascript" in noscript or "javascript is required" in noscript:
        return text_length < 3000
    if shell is not None and len(shell.get_text(" ", strip=True)) < 200:
        return True
    return text_length < 400 and scripts >= 3
