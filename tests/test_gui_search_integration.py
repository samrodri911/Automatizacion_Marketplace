"""Tests de integración GUI ↔ señales de búsqueda (Iteración 3).

Cubren la persistencia segura del localizador, el diálogo de resultado y el
mapeo de estados FSM SIN abrir un navegador real.

Desde la fase "UI de un solo botón" el buscador per-producto no tiene botón
propio en la interfaz, pero el flujo de búsqueda/matching sigue existiendo a
nivel de servicio (ver `tests/test_invoke_dispatch.py`) y su persistencia
sigue protegida por las mismas reglas.

Los widgets Qt se ejecutan en modo `offscreen`, por lo que estos tests
corren en cualquier máquina, incluida una sin pantalla.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QPushButton

from app.automation.listing_matcher import MatchStatus
from app.database.database import Database
from app.database.repositories import MatchedListingsRepository, ProductRepository
from app.gui.listing_result_dialog import ListingResultDialog
from app.gui.main_window import MainWindow
from app.models.product import Product
from app.services.automation_service import AutomationService
from app.services.matched_listing_service import MatchedListingService
from app.services.product_service import ProductService


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeAutomation(QObject):
    """Sustituto del `AutomationService` en la GUI: solo expone la señal
    de entrada que la GUI emite, sin hilo ni navegador."""

    search_listing_requested = Signal(object)


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=tmp_path / "test.db")
    database.initialize()
    return database


@pytest.fixture
def window(qapp, db, tmp_path):
    repository = ProductRepository(db)
    service = ProductService(repository, products_dir=tmp_path / "products")
    matched_service = MatchedListingService(MatchedListingsRepository(db), service)
    fake = FakeAutomation()
    win = MainWindow(service, matched_service)
    win._automation_service = fake  # noqa: SLF001 (acceso intencional en test)
    return win


def _db_of(window) -> Database:
    return window._product_service._repository._db  # noqa: SLF001


def _sample_product(repository: ProductRepository) -> Product:
    product = Product(
        title="iPhone 13 128GB",
        description="iPhone 13, 128 GB, impecable",
        price=2_300_000,
        category="Celulares",
        condition="Usado - Como nuevo",
        location="Cali",
        tags=["iphone"],
        images=["iphone-13-128gb/01.jpg"],
        enabled=True,
    )
    return repository.create(product)


# --------------------------------------------------------------------------
# Persistencia segura del localizador
# --------------------------------------------------------------------------
def test_persist_found_locator_only(window, monkeypatch):
    repo = ProductRepository(_db_of(window))
    product = _sample_product(repo)
    win = window
    win._reload_products()

    # Para no bloquear el test con el diálogo modal, el exec se sustituye.
    monkeypatch.setattr(ListingResultDialog, "exec", lambda self: 0)

    payload = {
        "product_id": product.id,
        "title": product.title,
        "status": MatchStatus.FOUND.name,
        "best": {
            "confidence": "HIGH",
            "listing": {"url": "https://www.facebook.com/marketplace/item/ABC123", "reference": "ABC123"},
            "reasons": ["título coincide exactamente"],
            "warnings": [],
        },
        "scanned": 2,
        "had_intervention": False,
    }
    win._on_search_listing_result(payload)

    refreshed = win._product_service.get(product.id)
    assert refreshed.marketplace_url == "https://www.facebook.com/marketplace/item/ABC123"
    assert refreshed.marketplace_reference == "ABC123"


@pytest.mark.parametrize(
    "status",
    [
        MatchStatus.AMBIGUOUS.name,
        MatchStatus.NOT_FOUND.name,
        MatchStatus.SEARCH_LIMIT_REACHED.name,
        MatchStatus.MEDIUM_CONFIDENCE.name,
        MatchStatus.LOW_CONFIDENCE.name,
    ],
)
def test_no_persist_ambiguous_or_doubtful(window, monkeypatch, status):
    """Los resultados que NO son FOUND nunca guardan el localizador."""
    repo = ProductRepository(_db_of(window))
    product = _sample_product(repo)
    win = window
    win._reload_products()

    monkeypatch.setattr(ListingResultDialog, "exec", lambda self: 0)

    payload = {
        "product_id": product.id,
        "title": product.title,
        "status": status,
        "best": None,
        "scanned": 2,
        "had_intervention": False,
    }
    win._on_search_listing_result(payload)

    refreshed = win._product_service.get(product.id)
    assert refreshed.marketplace_url is None
    assert refreshed.marketplace_reference is None


def test_no_persist_without_real_locator(window, monkeypatch):
    """FOUND pero sin URL/referencia real extraída => no se guarda nada."""
    repo = ProductRepository(_db_of(window))
    product = _sample_product(repo)
    win = window
    win._reload_products()

    monkeypatch.setattr(ListingResultDialog, "exec", lambda self: 0)

    payload = {
        "product_id": product.id,
        "title": product.title,
        "status": MatchStatus.FOUND.name,
        "best": {
            "confidence": "HIGH",
            "listing": {"url": "", "reference": ""},
            "reasons": [],
            "warnings": [],
        },
        "scanned": 1,
        "had_intervention": False,
    }
    win._on_search_listing_result(payload)

    refreshed = win._product_service.get(product.id)
    assert refreshed.marketplace_url is None
    assert refreshed.marketplace_reference is None


# --------------------------------------------------------------------------
# Diálogo de resultado (solo lectura)
# --------------------------------------------------------------------------
def test_result_dialog_shows_fields_and_no_action_buttons(qapp):
    payload = {
        "product_id": 1,
        "title": "iPhone 13 128GB",
        "status": MatchStatus.FOUND.name,
        "scanned": 3,
        "had_intervention": True,
        "best": {
            "confidence": "HIGH",
            "listing": {
                "title": "iPhone 13 128GB",
                "price": 8_500_000,
                "price_raw": "8.500.000",
                "url": "https://www.facebook.com/marketplace/item/XYZ",
                "reference": "XYZ",
                "image_count": 1,
            },
            "reasons": ["título coincide exactamente", "precio idéntico"],
            "warnings": [],
        },
    }
    diag = ListingResultDialog(result_payload=payload, product_title="iPhone 13 128GB")

    buttons = diag.findChildren(QPushButton)
    assert len(buttons) == 1
    assert buttons[0].text() == "Cerrar"


def test_result_dialog_renders_ambiguous_without_best(qapp):
    payload = {
        "product_id": 2,
        "title": "iPad Pro",
        "status": MatchStatus.AMBIGUOUS.name,
        "scanned": 4,
        "had_intervention": False,
        "best": None,
    }
    diag = ListingResultDialog(result_payload=payload, product_title="iPad Pro")
    buttons = diag.findChildren(QPushButton)
    assert len(buttons) == 1


# --------------------------------------------------------------------------
# Mapeo de estados FSM en el servicio
# --------------------------------------------------------------------------
def test_map_find_status_maps_all_matches():
    mapping = {
        MatchStatus.FOUND: "LISTING_FOUND",
        MatchStatus.MEDIUM_CONFIDENCE: "LISTING_FOUND",
        MatchStatus.LOW_CONFIDENCE: "LISTING_NOT_FOUND",
        MatchStatus.NOT_FOUND: "LISTING_NOT_FOUND",
        MatchStatus.AMBIGUOUS: "AMBIGUOUS_LISTING",
        MatchStatus.SEARCH_LIMIT_REACHED: "SEARCH_LIMIT_REACHED",
    }
    for source, expected in mapping.items():
        assert AutomationService._map_find_status(source).name == expected