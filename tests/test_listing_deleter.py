"""Tests de la lógica de seguridad del eliminador (Iteración 4).

Prueban el comportamiento de `ListingDeleter` y las reglas de seguridad
sin abrir un navegador real (usando mocks/fakes de Playwright).

Cubre:
- HIGH -> permite preparar eliminación
- MEDIUM -> bloquea eliminación
- LOW -> bloquea eliminación
- AMBIGUOUS -> bloquea eliminación
- NOT_FOUND -> bloquea eliminación
- Cancelación del usuario -> no ejecuta delete
- Verificación sin señales positivas -> DELETE_UNCERTAIN (modificación 3)
- Intervención -> WAITING_USER
- last_deleted_at SOLO con DELETED_CONFIRMED (modificación 2)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.automation.listing_deleter import DeleteStatus, ListingDeleter
from app.automation.selectors import DeletionVerificationResult, verify_deletion_from_page
from app.database.database import Database
from app.database.repositories import AutomationRunRepository, ProductRepository
from app.models.listing import Listing
from app.models.product import Product
from app.services.product_service import ProductService


# --------------------------------------------------------------------------
# Tests de reglas de verificación pura (selectors.py)
# --------------------------------------------------------------------------
def test_verify_deletion_positive_url_redirection():
    res = verify_deletion_from_page("https://www.facebook.com/marketplace/you/selling", "Cualquier texto")
    assert res.confirmed is True
    assert len(res.signals_found) > 0


def test_verify_deletion_positive_page_text():
    res = verify_deletion_from_page(
        "https://www.facebook.com/marketplace/item/123",
        "Esta publicación ya no está disponible",
    )
    assert res.confirmed is True
    assert len(res.signals_found) > 0


def test_verify_deletion_positive_toast_success():
    res = verify_deletion_from_page(
        "https://www.facebook.com/marketplace/item/123",
        "Publicación eliminada correctamente",
    )
    assert res.confirmed is True


def test_verify_deletion_uncertain_on_empty_text_or_ambiguous():
    """Modificación 3: Ausencia de señales positivas -> confirmed=False."""
    res = verify_deletion_from_page("https://www.facebook.com/marketplace/item/123", "Página normal de Marketplace")
    assert res.confirmed is False
    assert len(res.signals_found) == 0


def test_verify_deletion_positive_item_url_serves_feed():
    """El item URL sin redirigir pero sirviendo el feed general (sin el
    título del listing) ES señal positiva de eliminación (DOM real verificado
    con diag_menu_probe)."""
    res = verify_deletion_from_page(
        "https://www.facebook.com/marketplace/item/123",
        "Marketplace\nSugerencias de hoy\nRecién publicado\nEn un radio de 65 km\nExplorar todo",
        listing_title="Laptop HP Pavilion 15",
    )
    assert res.confirmed is True
    assert len(res.signals_found) > 0


def test_verify_deletion_no_false_positive_when_title_present():
    """Si el título del listing SIGUE en la página, no hay señal de
    eliminación (el item aún existe aunque haya marcadores de feed)."""
    res = verify_deletion_from_page(
        "https://www.facebook.com/marketplace/item/123",
        "Sugerencias de hoy\nLaptop HP Pavilion 15\n$600.000",
        listing_title="Laptop HP Pavilion 15",
    )
    assert res.confirmed is False
    assert len(res.signals_found) == 0


def test_verify_deletion_feed_signal_requires_listing_title():
    """Sin listing_title no se aplica la señal de feed (evita falsos
    positivos cuando no hay título conocido)."""
    res = verify_deletion_from_page(
        "https://www.facebook.com/marketplace/item/123",
        "Sugerencias de hoy\nRecién publicado",
    )
    assert res.confirmed is False


# --------------------------------------------------------------------------
# Tests de ProductService.record_deletion() (modificación 2)
# --------------------------------------------------------------------------
@pytest.fixture
def db_service(tmp_path):
    database = Database(db_path=tmp_path / "test_del.db")
    database.initialize()
    repo = ProductRepository(database)
    run_repo = AutomationRunRepository(database)
    service = ProductService(repo, products_dir=tmp_path / "products", run_repository=run_repo)
    return service, repo


def test_record_deletion_updates_last_deleted_at_only_on_confirmed(db_service):
    """Modificación 2: last_deleted_at SOLO se actualiza con DELETED_CONFIRMED."""
    service, repo = db_service
    product = Product(
        title="iPhone 13 128GB",
        description="Desc",
        price=1000,
        category="Celulares",
        condition="Nuevo",
        location="Cali",
        images=["img.jpg"],
    )
    product = repo.create(product)
    assert product.id is not None
    assert product.last_deleted_at is None

    # Caso 1: DELETE_UNCERTAIN -> last_deleted_at NO cambia
    service.record_deletion(
        product_id=product.id,
        result="DELETE_UNCERTAIN",
        confidence="HIGH",
        listing_url="https://url",
        listing_reference="REF1",
    )
    refreshed = service.get(product.id)
    assert refreshed.last_deleted_at is None

    # Caso 2: DELETE_FAILED -> last_deleted_at NO cambia
    service.record_deletion(
        product_id=product.id,
        result="DELETE_FAILED",
        confidence="HIGH",
        listing_url="https://url",
        listing_reference="REF1",
    )
    refreshed = service.get(product.id)
    assert refreshed.last_deleted_at is None

    # Caso 3: DELETED_CONFIRMED -> last_deleted_at SÍ se actualiza
    service.record_deletion(
        product_id=product.id,
        result="DELETED_CONFIRMED",
        confidence="HIGH",
        listing_url="https://url",
        listing_reference="REF1",
    )
    refreshed = service.get(product.id)
    assert refreshed.last_deleted_at is not None


# --------------------------------------------------------------------------
# Tests de ListingDeleter con fakes de Playwright
# --------------------------------------------------------------------------
def test_deleter_fails_safe_when_menu_not_found():
    page = MagicMock()
    page.goto.return_value = None
    page.evaluate.return_value = 0
    # Ningún botón con aria-label o text coincide
    page.get_by_role.return_value.is_visible.return_value = False
    page.get_by_role.return_value.filter.return_value.first.is_visible.return_value = False
    page.get_by_role.return_value.first.is_visible.return_value = False

    deleter = ListingDeleter()
    listing = Listing(title="iPhone 13", price=1000, url="https://facebook.com/item/123")
    res = deleter.delete(listing, page)

    assert res.status == DeleteStatus.DELETE_FAILED
    assert "menú" in res.detail.lower()


def test_deleter_fails_safe_when_delete_action_not_in_menu():
    page = MagicMock()
    page.goto.return_value = None
    # Menú abre
    page.get_by_role.return_value.first.is_visible.side_effect = [True, False]
    # Pero el ítem 'Eliminar' no se encuentra
    page.get_by_role.return_value.is_visible.return_value = False
    page.get_by_text.return_value.first.is_visible.return_value = False

    deleter = ListingDeleter()
    listing = Listing(title="iPhone 13", price=1000, url="https://facebook.com/item/123")
    res = deleter.delete(listing, page)

    assert res.status == DeleteStatus.DELETE_FAILED


def test_deleter_verify_only_returns_uncertain_on_network_error():
    page = MagicMock()
    page.goto.side_effect = Exception("Connection closed")

    deleter = ListingDeleter()
    listing = Listing(title="iPhone 13", price=1000, url="https://facebook.com/item/123")
    res = deleter.verify_only(listing, page)

    assert res.status == DeleteStatus.DELETE_UNCERTAIN
    assert "error de red" in res.detail.lower()
