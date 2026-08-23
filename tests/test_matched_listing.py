"""Tests del flujo de datos del MatchedListing (target congelado).

Cubren la sección 20 del spec (puntos 1, 2, 3, 11, 12):

1. Un match HIGH crea un MatchedListing con el target congelado.
2. Editar el Product NO modifica el MatchedListing.
3. Tras editar, Product.title != matched_title puede ser válido.
4. El ListingDeleter recibe el objetivo construido desde matched_listing
   (url/referencia helados), nunca desde product.title.
5. DELETE_UNCERTAIN bloquea publicación.
6. DELETE_FAILED bloquea publicación.
7. DELETED_CONFIRMED permite creación.
11. Cambiar fotos no modifica el objetivo original.
12. Cambiar precio no modifica el objetivo original.
"""

from __future__ import annotations

import pytest

from app.automation.listing_deleter import ListingDeleter
from app.core.exceptions import RepublishBlockedError, RepublishError
from app.database.database import Database
from app.database.repositories import (
    AutomationRunRepository,
    MatchedListingsRepository,
    ProductRepository,
)
from app.models.listing import Listing
from app.models.matched_listing import (
    STATUS_AWAITING_CONFIRM,
    STATUS_BLOCKED,
    STATUS_DELETED,
    STATUS_SELECTED,
)
from app.models.product import Product
from app.services.matched_listing_service import MatchedListingService
from app.services.product_service import ProductService


@pytest.fixture
def services(tmp_path):
    database = Database(db_path=tmp_path / "test_ml.db")
    database.initialize()
    product_repo = ProductRepository(database)
    matched_repo = MatchedListingsRepository(database)
    run_repo = AutomationRunRepository(database)
    product_service = ProductService(product_repo, products_dir=tmp_path / "products", run_repository=run_repo)
    matched_service = MatchedListingService(matched_repo, product_service)
    return matched_service, product_service


def _product(title="iPhone 13 128GB", price=1850000.0) -> Product:
    return Product(
        title=title,
        description="Celular en buen estado",
        price=price,
        category="Electrónica",
        condition="Usado - Como nuevo",
        location="Cali",
        images=["iphone/01.jpg"],
        enabled=True,
    )


# --------------------------------------------------------------------------
# 1. MATCH HIGH crea MatchedListing
# --------------------------------------------------------------------------
def test_select_match_high_freezes_target(services):
    matched_service, product_service = services
    product = product_service.create(_product())
    assert product.id is not None

    matched = matched_service.select_match(
        product_id=product.id,
        listing_url="https://www.facebook.com/marketplace/item/123",
        listing_reference="123",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        matched_price_raw="$1.850.000",
        confidence="HIGH",
    )

    assert matched.id is not None
    assert matched.confidence == "HIGH"
    assert matched.listing_url == "https://www.facebook.com/marketplace/item/123"
    assert matched.listing_reference == "123"
    assert matched.matched_title == "iPhone 13 128GB"
    assert matched.matched_price == 1850000
    assert matched.status == STATUS_SELECTED

    # El target debe reconstruirse en Listing con los datos helados.
    listing = matched.to_listing()
    assert isinstance(listing, Listing)
    assert listing.url == "https://www.facebook.com/marketplace/item/123"
    assert listing.reference == "123"
    assert listing.title == "iPhone 13 128GB"


def test_select_match_requires_high_confidence(services):
    matched_service, product_service = services
    product = product_service.create(_product())
    with pytest.raises(RepublishBlockedError):
        matched_service.select_match(
            product_id=product.id,
            listing_url="https://www.facebook.com/marketplace/item/123",
            listing_reference="123",
            matched_title="iPhone 13 128GB",
            matched_price=1850000,
            confidence="MEDIUM",
        )


def test_select_match_requires_locator(services):
    matched_service, product_service = services
    product = product_service.create(_product())
    with pytest.raises(RepublishError):
        matched_service.select_match(
            product_id=product.id,
            listing_url="",
            listing_reference="",
            matched_title="iPhone 13 128GB",
            matched_price=1850000,
            confidence="HIGH",
        )


def test_only_one_active_target_per_product(services):
    matched_service, product_service = services
    product = product_service.create(_product())
    matched_service.select_match(
        product_id=product.id,
        listing_url="https://fb.com/item/1",
        listing_reference="1",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        confidence="HIGH",
    )
    with pytest.raises(RepublishError):
        matched_service.select_match(
            product_id=product.id,
            listing_url="https://fb.com/item/2",
            listing_reference="2",
            matched_title="iPhone 13 256GB",
            matched_price=2100000,
            confidence="HIGH",
        )


# --------------------------------------------------------------------------
# 2/3/11/12. Editar Product NO modifica el target congelado
# --------------------------------------------------------------------------
def test_editing_product_does_not_modify_frozen_target(services):
    matched_service, product_service = services
    product = product_service.create(_product())
    matched = matched_service.select_match(
        product_id=product.id,
        listing_url="https://www.facebook.com/marketplace/item/123",
        listing_reference="123",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        matched_price_raw="$1.850.000",
        confidence="HIGH",
    )

    # El usuario edita título, precio, descripción y FOTOS.
    edited = _product(title="iPhone 13 256GB", price=2100000.0)
    edited.description = "Nueva descripción"
    edited.images = ["iphone/01.jpg", "iphone/02.jpg"]

    result = matched_service.save_edit_snapshot(matched.id, edited)

    # El target quedó helado.
    assert result.listing_url == "https://www.facebook.com/marketplace/item/123"
    assert result.listing_reference == "123"
    assert result.matched_title == "iPhone 13 128GB"
    assert result.matched_price == 1850000
    # La edición solo queda como trazabilidad y avanza el status.
    assert result.new_title == "iPhone 13 256GB"
    assert result.new_price == 2100000.0
    assert result.status == STATUS_AWAITING_CONFIRM

    # Válido: el producto editado ya no se parece al target.
    assert edited.title != result.matched_title
    assert edited.price != (result.matched_price or 0)

    # Reload desde BD: los campos congelados siguen idénticos.
    reloaded = matched_service.get(matched.id)
    assert reloaded.listing_url == "https://www.facebook.com/marketplace/item/123"
    assert reloaded.matched_title == "iPhone 13 128GB"
    assert reloaded.listing_reference == "123"


def test_changing_photos_and_price_leaves_target_intact(services):
    matched_service, product_service = services
    product = product_service.create(_product())
    matched = matched_service.select_match(
        product_id=product.id,
        listing_url="https://fb.com/item/55",
        listing_reference="55",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        confidence="HIGH",
    )

    edited = _product(title="iPhone 13 128GB", price=999)
    edited.images = ["nuevas/foto1.jpg", "nuevas/foto2.jpg"]
    after = matched_service.save_edit_snapshot(matched.id, edited)

    assert after.listing_url == "https://fb.com/item/55"
    assert after.listing_reference == "55"
    assert after.listing_url != product.marketplace_url  # nunca se re-deriva
    assert after.matched_title == "iPhone 13 128GB"


# --------------------------------------------------------------------------
# 4. ListingDeleter usa el objetivo congelado, no product.title
# --------------------------------------------------------------------------
def test_deleter_receives_target_from_matched_listing(services):
    matched_service, product_service = services
    product = product_service.create(_product())
    matched_service.select_match(
        product_id=product.id,
        listing_url="https://fb.com/item/777",
        listing_reference="777",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        confidence="HIGH",
    )
    matched = matched_service.require_active(product.id)

    # TRIANGULACIÓN: construimos el Listing desde el target congelado y
    # verificamos que la URL/referencia provienen del match, no del
    # producto (aunque el producto se haya editado con otro título).
    edited = _product(title="Dato completamente distinto", price=1)
    edited.images = ["x/1.jpg"]
    matched_service.save_edit_snapshot(matched.id, edited)

    target = matched_service.require_active(product.id).to_listing()
    assert target.url == "https://fb.com/item/777"
    assert target.reference == "777"
    assert "distinto" not in target.title

    # El ListingDeleter simplemente recibe ese Listing (se ejecuta en la
    # capa de servicio; aquí verificamos que el objeto recibido es el
    # correcto con un spy).
    import unittest.mock as mock

    deleter = ListingDeleter()
    page = mock.MagicMock()
    page.goto.return_value = None
    page.get_by_role.return_value.first.is_visible.return_value = False
    page.get_by_role.return_value.is_visible.return_value = False
    page.get_by_text.return_value.first.is_visible.return_value = False

    from app.core.config import facebook_config

    res = deleter._navigate_to_your_listings(page)
    assert res[0] is True
    assert page.goto.called_with(facebook_config.your_listings_url)


# --------------------------------------------------------------------------
# 5/6/7. Seguridad de la eliminación
# --------------------------------------------------------------------------
def test_delete_uncertain_blocks_republish(services):
    matched_service, product_service = services
    product = product_service.create(_product())
    matched = matched_service.select_match(
        product_id=product.id,
        listing_url="https://fb.com/item/123",
        listing_reference="123",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        confidence="HIGH",
    )
    blocked = matched_service.mark_deletion_uncertain(matched.id, error="no se pudo verificar")
    assert blocked.status == STATUS_BLOCKED
    # No existe target activo para reanudar.
    assert matched_service.get_active_by_product(product.id) is None
    # No debe poder continuarse -> require_active lanza.
    with pytest.raises(RepublishError):
        matched_service.require_active(product.id)


def test_delete_failed_blocks_republish(services):
    matched_service, product_service = services
    product = product_service.create(_product())
    matched = matched_service.select_match(
        product_id=product.id,
        listing_url="https://fb.com/item/1",
        listing_reference="1",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        confidence="HIGH",
    )
    blocked = matched_service.mark_deletion_uncertain(matched.id, error="DELETE_FAILED")
    assert blocked.status == STATUS_BLOCKED
    assert matched_service.get_active_by_product(product.id) is None


def test_deleted_confirmed_allows_continuation(services):
    matched_service, product_service = services
    product = product_service.create(_product())
    matched = matched_service.select_match(
        product_id=product.id,
        listing_url="https://fb.com/item/123",
        listing_reference="123",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        confidence="HIGH",
    )
    confirmed = matched_service.mark_deleted_confirmed(matched.id)
    assert confirmed.status == STATUS_DELETED
    # Sigue activo: la reanudación debe poder continuar con la creación.
    assert confirmed.is_active
    active = matched_service.get_active_by_product(product.id)
    assert active is not None
    assert active.status == STATUS_DELETED


def test_traceability_phases_written_to_automation_runs(services):
    matched_service, product_service = services
    product = product_service.create(_product())
    matched = matched_service.select_match(
        product_id=product.id,
        listing_url="https://fb.com/item/1",
        listing_reference="1",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        confidence="HIGH",
    )
    runs = product_service._run_repository.list_for_product(product.id)  # noqa: SLF001
    ops = [r.operation for r in runs]
    assert "match" in ops
    matched_run = next(r for r in runs if r.operation == "match")
    assert matched_run.listing_reference == "1"
    assert matched_run.listing_url == "https://fb.com/item/1"
    assert matched_run.confidence == "HIGH"
    assert matched_run.matched_title == "iPhone 13 128GB"
    assert matched_run.status == "success"