import pytest

from app.services.product_matching import extract_features, group_listings, relevance

QUERY = "Samsung Galaxy S24 Ultra 256GB"


@pytest.mark.parametrize(
    "title",
    [
        "Samsung Galaxy S24 Ultra 256GB",
        "Samsung S24 Ultra 256 GB",
        "Galaxy S24 Ultra 256GB Smartphone",
        "SAMSUNG Galaxy S24 Ultra 256GB Unlocked Android Smartphone, Titanium Black",
        "Samsung Galaxy S24 Ultra, 6.8\", 256GB + 12GB RAM, Titanium Black",
        "Samsung Galaxy S 24 Ultra 256GB",
    ],
)
def test_same_product_variants_match(title):
    assert relevance(QUERY, title) >= 0.6


@pytest.mark.parametrize(
    "title",
    [
        "Samsung Galaxy S24 Ultra 512GB",  # different storage
        "Samsung Galaxy S24 256GB",  # missing variant
        "Samsung Galaxy S23 Ultra 256GB",  # different model
        "Silicone Case Cover For Samsung Galaxy S24 Ultra",  # accessory
        "Apple iPhone 16 Pro 256GB",  # different brand
        "Tempered glass screen protector Galaxy S24 Ultra",
    ],
)
def test_different_products_do_not_match(title):
    assert relevance(QUERY, title) < 0.6


def test_variant_words_distinguish_products():
    assert relevance("iPhone 16", "Apple iPhone 16 128GB Black") >= 0.6
    assert relevance("iPhone 16", "Apple iPhone 16 Pro Max 256GB") < 0.6


def test_other_product_queries():
    assert relevance("Logitech MX Master 3S", "Logitech MX Master 3S Wireless Mouse") >= 0.6
    assert relevance("Logitech MX Master 3S", "Logitech MX Anywhere 3") < 0.6
    assert relevance("Samsung 55-inch TV", "Samsung 55\" Crystal UHD 4K Smart TV") >= 0.6
    assert relevance("Samsung 55-inch TV", "Samsung 65 inch Crystal UHD Smart TV") < 0.6
    assert relevance("Dell XPS 15", "Dell XPS 15 9530 Laptop, 32GB RAM, 1TB SSD") >= 0.6
    assert relevance("Dell XPS 15", "Dell XPS 13 Plus") < 0.6


def test_feature_extraction():
    f = extract_features("Samsung Galaxy S24 Ultra 256GB 1TB 6.8\" Phone")
    assert f.brand == "samsung"
    assert "s24" in f.model_tokens
    assert "ultra" in f.variants
    assert {str(c) for c in f.capacities_gb} == {"256", "1024"}
    assert {str(s) for s in f.sizes_inch} == {"6.8"}


def test_empty_query_scores_zero():
    assert relevance("", "Anything") == 0.0


def test_grouping_is_conservative():
    items = [
        (1, "Samsung Galaxy S24 Ultra 256GB Titanium Black"),
        (2, "SAMSUNG Galaxy S24 Ultra 256 GB Unlocked"),
        (3, "Samsung Galaxy S24 Ultra 512GB"),
        (4, "Samsung Galaxy S24 256GB"),
        (5, "Galaxy S24 Ultra 256GB Smartphone"),
    ]
    groups = group_listings(items)
    assert groups[1] == groups[2] == groups[5]
    assert groups[3] != groups[1]
    assert groups[4] != groups[1]
    assert groups[3] != groups[4]


def test_grouping_unknown_storage_stays_separate():
    groups = group_listings([(1, "Samsung Galaxy S24 Ultra 256GB"), (2, "Samsung Galaxy S24 Ultra")])
    assert groups[1] != groups[2]


def test_accessory_detection_is_positional():
    query = "Samsung Galaxy A15"
    assert relevance(query, "Samsung Fast Charger 25W USB C for Galaxy A15, A14") < 0.6
    assert relevance(query, "Luxury PU Leather Case For Samsung Galaxy A15 5G Back Cover") < 0.6
    # A feature mention after the product is not an accessory listing.
    assert relevance(
        query, "Samsung Galaxy A15 5G Cell Phone, 128GB Unlocked, AMOLED Display, Super Fast Charging"
    ) >= 0.6
    # Searching for the accessory itself still matches.
    assert relevance("Galaxy S24 Ultra case", "Silicone Case Cover For Samsung Galaxy S24 Ultra") >= 0.6


@pytest.mark.parametrize(
    "title",
    [
        "Samsung Galaxy A07, A17, A06, A08, A18, A16, A26, A36, A56, A57, A37, A15, A25, A27, A35, A55 "
        "Cover Luxury High Protection Premium and ORIGINAL Silicone Purple Phone Case.",
        "Samsung Galaxy A15 5G Silicone Phone Case",
        "Samsung Galaxy A15 Tempered Glass Screen Protector",
    ],
)
def test_accessories_named_after_the_product_are_not_matches(title):
    assert relevance("Samsung Galaxy A15", title) < 0.6


@pytest.mark.parametrize(
    "title",
    [
        "Refurbished Phone Samsung Galaxy A15 5G 6.5 inch 4GB/6GB/8GB+128GB/256GB clean screen 50mp+13mp 5000mah",
        "Tracfone Samsung Galaxy A15 5G, 128GB, 50MP Camera (Locked to Carrier)",
        "Samsung Galaxy A15 5G (64GB, 2024) 6.5\" 90Hz AMOLED, 50MP Camera, Fully Unlocked",
    ],
)
def test_real_phones_still_match(title):
    assert relevance("Samsung Galaxy A15", title) >= 0.6
