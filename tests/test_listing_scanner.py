"""Tests unitarios para ListingScanner y el matching por lote (Batch Matching)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.automation.listing_scanner import (
    ListingScanner,
    ScanBatchResult,
    ScannedListingItem,
)
from app.models.listing import Listing
from app.models.product import Product


@pytest.fixture
def sample_products() -> list[Product]:
    return [
        Product(
            id=1,
            title="iPhone 13 128GB",
            description="Celular en buen estado",
            price=1850000.0,
            category="Electrónica",
            condition="Usado",
            location="Cali",
        ),
        Product(
            id=2,
            title="PS5",
            description="Consola con dos mandos",
            price=1900000.0,
            category="Videojuegos",
            condition="Usado",
            location="Cali",
        ),
        Product(
            id=3,
            title="AirPods Pro",
            description="Auriculares inalámbricos",
            price=850000.0,
            category="Audio",
            condition="Nuevo",
            location="Cali",
        ),
    ]


def test_scanner_extracts_and_deduplicates_across_scrolls():
    page = MagicMock()
    extractor = MagicMock()

    batch_1 = [
        Listing(title="iPhone 13 128GB", price=1850000, reference="101", url="https://fb.com/item/101/"),
        Listing(title="Samsung S23", price=2100000, reference="102", url="https://fb.com/item/102/"),
    ]
    batch_2 = [
        Listing(title="iPhone 13 128GB", price=1850000, reference="101", url="https://fb.com/item/101/"),  # repetido
        Listing(title="PS5", price=1900000, reference="103", url="https://fb.com/item/103/"),
    ]
    batch_3 = []  # vacío

    extractor.extract_listings.side_effect = [batch_1, batch_2, batch_3, batch_3]

    navigator = MagicMock()
    navigator.requires_intervention.return_value = False
    navigator.scroll_feed.return_value = True

    scanner = ListingScanner(page=page, extractor=extractor, navigator=navigator)
    listings = scanner.scan_all_listings()

    assert len(listings) == 3
    refs = [l.reference for l in listings]
    assert refs == ["101", "102", "103"]


def test_scan_and_match_auto_selects_only_high_confidence(sample_products):
    page = MagicMock()
    extractor = MagicMock()

    scanned_listings = [
        Listing(title="iPhone 13 128GB", price=1850000, reference="101", url="https://fb.com/item/101/"),  # HIGH
        Listing(title="Samsung S23", price=2100000, reference="102", url="https://fb.com/item/102/"),  # NO_MATCH
        Listing(title="PS5", price=1900000, reference="103", url="https://fb.com/item/103/"),  # HIGH
        Listing(title="MacBook Air M1", price=3500000, reference="104", url="https://fb.com/item/104/"),  # NO_MATCH
        Listing(title="AirPods Pro", price=850000, reference="105", url="https://fb.com/item/105/"),  # HIGH
    ]
    extractor.extract_listings.side_effect = [scanned_listings, []]

    navigator = MagicMock()
    navigator.requires_intervention.return_value = False
    navigator.scroll_feed.return_value = False

    scanner = ListingScanner(page=page, extractor=extractor, navigator=navigator)
    batch_result = scanner.scan_and_match(sample_products)

    assert batch_result.total_listings == 5
    assert batch_result.matched_high_count == 3
    assert batch_result.unmatched_count == 2

    # Verificar selección automática por ítem
    items_by_title = {item.listing.title: item for item in batch_result.items}

    assert items_by_title["iPhone 13 128GB"].confidence == "HIGH"
    assert items_by_title["iPhone 13 128GB"].auto_selected is True
    assert items_by_title["iPhone 13 128GB"].matched_product_id == 1

    assert items_by_title["PS5"].confidence == "HIGH"
    assert items_by_title["PS5"].auto_selected is True
    assert items_by_title["PS5"].matched_product_id == 2

    assert items_by_title["AirPods Pro"].confidence == "HIGH"
    assert items_by_title["AirPods Pro"].auto_selected is True
    assert items_by_title["AirPods Pro"].matched_product_id == 3

    assert items_by_title["Samsung S23"].confidence == "NO_MATCH"
    assert items_by_title["Samsung S23"].auto_selected is False

    assert items_by_title["MacBook Air M1"].confidence == "NO_MATCH"
    assert items_by_title["MacBook Air M1"].auto_selected is False


def test_scan_and_match_does_not_auto_select_medium_or_ambiguous(sample_products):
    page = MagicMock()
    extractor = MagicMock()

    scanned_listings = [
        # Título idéntico pero precio diferente -> LOW / MEDIUM
        Listing(title="iPhone 13 128GB", price=1200000, reference="201", url="https://fb.com/item/201/"),
    ]
    extractor.extract_listings.side_effect = [scanned_listings, []]

    navigator = MagicMock()
    navigator.requires_intervention.return_value = False
    navigator.scroll_feed.return_value = False

    scanner = ListingScanner(page=page, extractor=extractor, navigator=navigator)
    batch_result = scanner.scan_and_match(sample_products)

    assert len(batch_result.items) == 1
    item = batch_result.items[0]
    assert item.confidence in ("LOW", "MEDIUM")
    assert item.auto_selected is False


def test_batch_result_to_dict_serializability(sample_products):
    page = MagicMock()
    extractor = MagicMock()
    extractor.extract_listings.side_effect = [
        [Listing(title="PS5", price=1900000, reference="103", url="https://fb.com/item/103/")],
        [],
    ]
    scanner = ListingScanner(page=page, extractor=extractor)
    batch_result = scanner.scan_and_match(sample_products)

    data = batch_result.to_dict()
    assert isinstance(data, dict)
    assert data["total_listings"] == 1
    assert data["matched_high_count"] == 1
    assert isinstance(data["items"], list)
    assert data["items"][0]["auto_selected"] is True
    assert data["items"][0]["confidence"] == "HIGH"
