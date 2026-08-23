"""Tests unitarios para ListingExtractor y parse_card."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.automation.listing_extractor import (
    CardRawData,
    ListingExtractor,
    _extract_title_from_text,
    clean_image_refs,
    parse_card,
)


def test_extract_title_ignores_facebook_suggestions():
    text = (
        "Sugerencia: ¿Renovar la publicación?\n"
        "Microondas General Electric con Plato Giratorio – 20L Aprox\n"
        "$\xa0130.000\n"
        "Activo\n"
        "43 clics"
    )
    title = _extract_title_from_text(text)
    assert title == "Microondas General Electric con Plato Giratorio – 20L Aprox"


def test_parse_card_with_card_raw_data():
    raw = CardRawData(
        title="Laptop HP Pavilion 15 – i5, 12GB RAM",
        text="Laptop HP Pavilion 15 – i5, 12GB RAM\n$\xa0600.000\nActivo",
        url="https://www.facebook.com/marketplace/item/26897881756537527/",
        reference="26897881756537527",
        image_srcs=["https://scontent.fbcdn.net/v/t45.jpg"],
        strategy="seller_menu_aria",
    )
    listing = parse_card(raw)

    assert listing.title == "Laptop HP Pavilion 15 – i5, 12GB RAM"
    assert listing.price == 600000
    assert listing.price_raw == "$\xa0600.000"
    assert listing.reference == "26897881756537527"
    assert listing.url == "https://www.facebook.com/marketplace/item/26897881756537527/"
    assert len(listing.image_refs) == 1
    assert listing.key == "ref:26897881756537527"


def test_clean_image_refs_filters_data_uris_and_rsrc():
    srcs = [
        "https://scontent.fbcdn.net/v/photo.jpg",
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==",
        "https://static.xx.fbcdn.net/rsrc.php/v4/yB/r/blank.gif",
        "",
        "https://scontent.fbcdn.net/v/photo.jpg",  # duplicado
    ]
    cleaned = clean_image_refs(srcs)
    assert len(cleaned) == 1
    assert cleaned[0] == "https://scontent.fbcdn.net/v/photo.jpg"


def test_listing_extractor_with_seller_menu_aria_mock():
    page = MagicMock()
    page.evaluate.return_value = {
        "strategyUsed": "seller_menu_aria",
        "candidatesFound": 2,
        "discarded": 0,
        "cards": [
            {
                "strategy": "seller_menu_aria",
                "title": "Teclado Gamer Unitech RGB",
                "raw_text": "Teclado Gamer Unitech RGB\n$\xa040.000\nVendido",
                "url": "",
                "reference": "",
                "image_srcs": ["https://img.fb.com/pic1.jpg"],
            },
            {
                "strategy": "seller_menu_aria",
                "title": "Microondas General Electric",
                "raw_text": "Sugerencia: ¿Renovar?\nMicroondas General Electric\n$\xa0130.000\nActivo",
                "url": "https://www.facebook.com/marketplace/item/12345/",
                "reference": "12345",
                "image_srcs": ["https://img.fb.com/pic2.jpg"],
            },
        ],
    }

    extractor = ListingExtractor()
    listings = extractor.extract_listings(page)

    assert len(listings) == 2
    assert listings[0].title == "Teclado Gamer Unitech RGB"
    assert listings[0].price == 40000
    assert listings[1].title == "Microondas General Electric"
    assert listings[1].price == 130000
    assert listings[1].reference == "12345"


def test_listing_extractor_deduplicates_by_key():
    page = MagicMock()
    page.evaluate.return_value = {
        "strategyUsed": "seller_menu_aria",
        "candidatesFound": 3,
        "discarded": 0,
        "cards": [
            {
                "title": "Laptop HP Pavilion",
                "raw_text": "Laptop HP Pavilion\n$600.000",
                "url": "https://www.facebook.com/marketplace/item/999/",
                "reference": "999",
                "image_srcs": [],
            },
            {
                "title": "Laptop HP Pavilion",
                "raw_text": "Laptop HP Pavilion\n$600.000",
                "url": "https://www.facebook.com/marketplace/item/999/",
                "reference": "999",
                "image_srcs": ["https://img.jpg"],
            },
        ],
    }

    extractor = ListingExtractor()
    listings = extractor.extract_listings(page)

    assert len(listings) == 1
    assert listings[0].reference == "999"
