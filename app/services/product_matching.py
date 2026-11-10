"""Deterministic product matching.

Two jobs:
  * `relevance(query, title)` — how well a listing matches what the user
    searched for (0..1). Listings below the threshold are kept but shown as
    "not a match".
  * `group_listings(...)` — cluster listings that describe the *same*
    product across websites. Grouping is deliberately conservative: when in
    doubt, listings stay in separate groups.

Signals: token coverage, fuzzy name similarity (rapidfuzz), brand, model
numbers, variant words (pro/max/ultra...) and specifications (storage,
screen size). Conflicting specs (256GB vs 512GB) strongly reduce scores.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional

from rapidfuzz import fuzz

STOPWORDS = {
    "a", "an", "and", "the", "for", "with", "of", "in", "on", "to", "by", "new", "brand",
    "original", "genuine", "official", "sale", "free", "shipping", "latest", "best",
    "hot", "deal", "offer", "version", "edition", "model", "inch", "inches", "gb", "tb",
    "smartphone", "phone", "mobile", "cellphone", "cell", "dual", "sim", "4g", "5g", "lte",
    "black", "white", "silver", "gray", "grey", "blue", "red", "green", "gold", "titanium",
    "pink", "purple", "yellow", "graphite", "midnight", "starlight", "color", "colour",
    "w", "x", "-", "|", "/",
}

ACCESSORY_WORDS = frozenset({
    "case", "cases", "cover", "covers", "protector", "protectors", "tempered", "glass", "skin",
    "skins", "charger", "charging", "cable", "cables", "adapter", "holder", "strap", "straps",
    "pouch", "sleeve", "stand", "mount", "replacement", "compatible", "film", "decal", "sticker",
    "battery", "lens", "dock", "bumper", "wallet", "keyboard", "stylus", "earbuds",
})

VARIANT_WORDS = {"pro", "max", "ultra", "plus", "mini", "lite", "fe", "air", "neo", "note", "fold", "flip", "edge", "se"}

BRAND_ALIASES = {
    "apple": "apple", "iphone": "apple", "ipad": "apple", "macbook": "apple", "airpods": "apple",
    "samsung": "samsung", "galaxy": "samsung",
    "google": "google", "pixel": "google",
    "xiaomi": "xiaomi", "redmi": "xiaomi", "poco": "xiaomi",
    "dell": "dell", "xps": "dell", "alienware": "dell", "inspiron": "dell", "latitude": "dell",
    "lenovo": "lenovo", "thinkpad": "lenovo", "ideapad": "lenovo", "legion": "lenovo",
    "hp": "hp", "pavilion": "hp", "elitebook": "hp", "omen": "hp",
    "logitech": "logitech", "asus": "asus", "rog": "asus", "zenbook": "asus", "vivobook": "asus",
    "acer": "acer", "sony": "sony", "playstation": "sony", "lg": "lg", "oppo": "oppo",
    "vivo": "vivo", "tecno": "tecno", "infinix": "infinix", "itel": "itel", "huawei": "huawei",
    "honor": "honor", "nokia": "nokia", "oneplus": "oneplus", "microsoft": "microsoft",
    "surface": "microsoft", "xbox": "microsoft", "hisense": "hisense", "tcl": "tcl",
    "philips": "philips", "jbl": "jbl", "bose": "bose", "anker": "anker", "canon": "canon",
    "nikon": "nikon", "nintendo": "nintendo", "motorola": "motorola", "realme": "realme",
    "msi": "msi", "razer": "razer", "garmin": "garmin", "fitbit": "google", "beats": "apple",
}

_CAPACITY = re.compile(r"(\d+(?:\.\d+)?)\s?(gb|tb)\b")
_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s?(?:\"|''|”|″|-?\s?inch(?:es)?\b|in\b)")
_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")


@dataclass(frozen=True)
class ProductFeatures:
    normalized: str
    tokens: frozenset[str]
    brand: Optional[str]
    model_tokens: frozenset[str]
    variants: frozenset[str]
    capacities_gb: frozenset[Decimal]
    sizes_inch: frozenset[Decimal]
    significant: tuple[str, ...] = field(default_factory=tuple)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("“", '"').replace("”", '"').replace("″", '"')
    # "Galaxy S 24" -> "galaxy s24"; "256 GB" stays matched by _CAPACITY.
    text = re.sub(r"\b([a-z])\s+(\d{1,4})\b", r"\1\2", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_features(text: str) -> ProductFeatures:
    norm = normalize(text)
    capacities = frozenset(
        (Decimal(m.group(1)) * (1024 if m.group(2) == "tb" else 1)).normalize()
        for m in _CAPACITY.finditer(norm)
    )
    sizes = frozenset(Decimal(m.group(1)).normalize() for m in _SIZE.finditer(norm))
    without_specs = _SIZE.sub(" ", _CAPACITY.sub(" ", norm))
    raw_tokens = [t.strip(".-") for t in _TOKEN.findall(without_specs)]
    tokens = [t for t in raw_tokens if t]

    brand = next((BRAND_ALIASES[t] for t in tokens if t in BRAND_ALIASES), None)
    variants = frozenset(t for t in tokens if t in VARIANT_WORDS)
    model_tokens = frozenset(
        t for t in tokens
        if any(c.isdigit() for c in t) and (any(c.isalpha() for c in t) or len(t) <= 4)
    )
    significant = tuple(dict.fromkeys(t for t in tokens if t not in STOPWORDS))
    return ProductFeatures(
        normalized=norm,
        tokens=frozenset(tokens),
        brand=brand,
        model_tokens=model_tokens,
        variants=variants,
        capacities_gb=capacities,
        sizes_inch=sizes,
        significant=significant,
    )


def _token_present(token: str, features: ProductFeatures) -> bool:
    if token in features.tokens:
        return True
    # Allow small spelling differences for longer words ("logitec" ~ "logitech").
    if len(token) >= 5 and not any(c.isdigit() for c in token):
        return any(fuzz.ratio(token, other) >= 88 for other in features.tokens if len(other) >= 4)
    return False


def relevance(query: str, title: str) -> float:
    """Score 0..1 of how well `title` matches the user's `query`."""
    q = extract_features(query)
    t = extract_features(title)
    if not q.significant and not q.capacities_gb and not q.sizes_inch:
        return 0.0

    present = sum(1 for token in q.significant if _token_present(token, t))
    coverage = present / len(q.significant) if q.significant else 1.0
    similarity = fuzz.token_set_ratio(q.normalized, t.normalized) / 100
    score = 0.65 * coverage + 0.35 * similarity

    # Model numbers in the query (s24, 16, mx3s, xps15...) must appear.
    missing_models = [m for m in q.model_tokens if m not in t.tokens]
    if missing_models:
        score *= 0.45 ** len(missing_models)
    # Specs that are present on both sides must agree.
    if q.capacities_gb and t.capacities_gb and not q.capacities_gb <= t.capacities_gb:
        score *= 0.3
    if q.sizes_inch and t.sizes_inch and not q.sizes_inch & t.sizes_inch:
        score *= 0.3
    if q.brand and t.brand and q.brand != t.brand:
        score *= 0.2
    # Variant words distinguish different products (iPhone 16 vs 16 Pro).
    score *= 0.5 ** len(q.variants - t.variants)
    score *= 0.55 ** len(t.variants - q.variants)
    # Accessories for the product ("case for Galaxy S24") are not the product.
    if (t.tokens & ACCESSORY_WORDS) - q.tokens:
        score *= 0.4
    return round(max(0.0, min(1.0, score)), 4)


def same_product(a: ProductFeatures, b: ProductFeatures) -> bool:
    """Conservative check that two listings describe the same product."""
    if a.brand and b.brand and a.brand != b.brand:
        return False
    if a.model_tokens != b.model_tokens:
        return False
    if a.variants != b.variants:
        return False
    if a.capacities_gb and b.capacities_gb and a.capacities_gb != b.capacities_gb:
        return False
    if bool(a.capacities_gb) != bool(b.capacities_gb):
        return False
    if a.sizes_inch and b.sizes_inch and a.sizes_inch != b.sizes_inch:
        return False
    return fuzz.token_set_ratio(" ".join(a.significant), " ".join(b.significant)) >= 80


def group_listings(items: Iterable[tuple[int, str]]) -> dict[int, str]:
    """Assign a group key to each (id, title). Complete linkage: a listing
    joins a group only if it matches *every* member."""
    groups: list[list[tuple[int, ProductFeatures]]] = []
    for item_id, title in items:
        features = extract_features(title)
        for group in groups:
            if all(same_product(features, member) for _, member in group):
                group.append((item_id, features))
                break
        else:
            groups.append([(item_id, features)])

    assignment: dict[int, str] = {}
    for index, group in enumerate(groups):
        first = group[0][1]
        seed = "|".join([
            str(index),
            *sorted(first.significant),
            *sorted(str(c) for c in first.capacities_gb),
            *sorted(str(s) for s in first.sizes_inch),
        ])
        key = "g" + hashlib.sha1(seed.encode()).hexdigest()[:10]
        for item_id, _ in group:
            assignment[item_id] = key
    return assignment
