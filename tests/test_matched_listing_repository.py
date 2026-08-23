"""Tests del repositorio de MatchedListing (persistencia SQLite).

Cubren el CRUD, la regla de "un único target ACTIVO por producto"
(índice único parcial) y las transiciones de estado, sin navegador.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import RepublishError
from app.database.database import Database
from app.database.repositories import MatchedListingsRepository, ProductRepository
from app.models.matched_listing import (
    STATUS_BLOCKED,
    STATUS_SELECTED,
    MatchedListing,
)
from app.models.product import Product


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=tmp_path / "test_mlr.db")
    database.initialize()
    return database


@pytest.fixture
def repos(db):
    return ProductRepository(db), MatchedListingsRepository(db)


def _product() -> Product:
    return Product(
        title="Bicicleta GW 26",
        description="Bici urbana en buen estado",
        price=950000.0,
        category="Deportes",
        condition="Usado - Buen estado",
        location="Cali",
        images=["bici/01.jpg"],
        enabled=True,
    )


def _matched(product_id: int, **overrides) -> MatchedListing:
    data = dict(
        product_id=product_id,
        listing_url="https://www.facebook.com/marketplace/item/777",
        listing_reference="777",
        matched_title="Bicicleta GW 26",
        matched_price=950000,
        matched_price_raw="$950.000",
        confidence="HIGH",
        status=STATUS_SELECTED,
    )
    data.update(overrides)
    return MatchedListing(**data)


def test_create_assigns_id_and_timestamps(repos):
    product_repo, matched_repo = repos
    product = product_repo.create(_product())
    matched = matched_repo.create(_matched(product.id))
    assert matched.id is not None
    assert matched.created_at is not None
    assert matched.updated_at is not None
    assert matched.matched_at is not None


def test_get_roundtrip_preserves_frozen_fields(repos):
    product_repo, matched_repo = repos
    product = product_repo.create(_product())
    created = matched_repo.create(_matched(product.id))
    fetched = matched_repo.get(created.id)
    assert fetched.listing_url == "https://www.facebook.com/marketplace/item/777"
    assert fetched.listing_reference == "777"
    assert fetched.matched_title == "Bicicleta GW 26"
    assert fetched.matched_price == 950000
    assert fetched.matched_price_raw == "$950.000"
    assert fetched.confidence == "HIGH"
    assert fetched.status == STATUS_SELECTED


def test_create_rejects_non_high_confidence(repos):
    product_repo, matched_repo = repos
    product = product_repo.create(_product())
    with pytest.raises(RepublishError):
        matched_repo.create(_matched(product.id, confidence="MEDIUM"))


def test_only_one_active_target_per_product(repos):
    product_repo, matched_repo = repos
    product = product_repo.create(_product())
    first = matched_repo.create(_matched(product.id))
    assert first.id is not None
    with pytest.raises(RepublishError):
        matched_repo.create(_matched(product.id))


def test_transition_updates_status(repos):
    product_repo, matched_repo = repos
    product = product_repo.create(_product())
    matched = matched_repo.create(_matched(product.id))
    updated = matched_repo.transition(matched.id, STATUS_BLOCKED)
    assert updated.status == STATUS_BLOCKED
    assert matched_repo.get(matched.id).status == STATUS_BLOCKED


def test_get_active_by_product_returns_none_after_terminal(repos):
    product_repo, matched_repo = repos
    product = product_repo.create(_product())
    matched = matched_repo.create(_matched(product.id))
    assert matched_repo.get_active_by_product(product.id) is not None
    matched_repo.transition(matched.id, STATUS_BLOCKED)
    assert matched_repo.get_active_by_product(product.id) is None


def test_list_active_filters_terminal(repos):
    product_repo, matched_repo = repos
    p1 = product_repo.create(_product())
    p2 = product_repo.create(Product(title="Monitor LG 24", description="Monitor", price=600000.0, category="Electrónica", condition="Nuevo", location="Cali", images=["mon/01.jpg"], enabled=True))
    m1 = matched_repo.create(_matched(p1.id))
    matched_repo.create(_matched(p2.id))
    assert len(matched_repo.list_active()) == 2
    matched_repo.transition(m1.id, STATUS_BLOCKED)
    actives = matched_repo.list_active()
    assert len(actives) == 1
    assert actives[0].product_id == p2.id


def test_list_historical_keeps_terminal(repos):
    product_repo, matched_repo = repos
    product = product_repo.create(_product())
    matched = matched_repo.create(_matched(product.id))
    matched_repo.transition(matched.id, STATUS_BLOCKED)
    history = matched_repo.list_historical(product.id)
    assert len(history) == 1
    assert history[0].status == STATUS_BLOCKED


def test_update_only_changes_passed_fields(repos):
    product_repo, matched_repo = repos
    product = product_repo.create(_product())
    matched = matched_repo.create(_matched(product.id))
    matched.new_title = "Bicicleta GW 26 Pro"
    matched.new_price = 1100000.0
    matched_repo.update(matched)
    fetched = matched_repo.get(matched.id)
    # Campos congelados intactos.
    assert fetched.listing_url == matched.listing_url
    assert fetched.listing_reference == matched.listing_reference
    assert fetched.matched_title == "Bicicleta GW 26"
    assert fetched.matched_price == 950000
    assert fetched.matched_price_raw == "$950.000"
    # Snapshot de trazabilidad guardado.
    assert fetched.new_title == "Bicicleta GW 26 Pro"
    assert fetched.new_price == 1100000.0


def test_update_missing_raises(repos):
    product_repo, matched_repo = repos
    product = product_repo.create(_product())
    ghost = _matched(product.id)
    ghost.id = 999999
    with pytest.raises(RepublishError):
        matched_repo.update(ghost)