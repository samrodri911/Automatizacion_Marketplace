import pytest

from app.core.exceptions import ProductNotFoundError
from app.database.database import Database
from app.database.repositories import AutomationRunRepository, ProductRepository
from app.models.product import Product


@pytest.fixture
def database(tmp_path):
    db = Database(db_path=tmp_path / "test.db")
    db.initialize()
    return db


@pytest.fixture
def repository(database):
    return ProductRepository(database)


def _sample_product(**overrides) -> Product:
    base = dict(
        title="PS5 Slim",
        description="PS5 slim con 2 controles",
        price=2_500_000,
        category="Consolas",
        condition="Usado - Buen estado",
        location="Cali",
        tags=["ps5", "sony"],
        images=["ps5-slim/01.jpg"],
    )
    base.update(overrides)
    return Product(**base)


def test_create_assigns_id_and_timestamps(repository):
    product = repository.create(_sample_product())
    assert product.id is not None
    assert product.created_at is not None
    assert product.updated_at is not None


def test_get_returns_same_data(repository):
    created = repository.create(_sample_product())
    fetched = repository.get(created.id)
    assert fetched.title == "PS5 Slim"
    assert fetched.tags == ["ps5", "sony"]
    assert fetched.images == ["ps5-slim/01.jpg"]


def test_get_missing_raises(repository):
    with pytest.raises(ProductNotFoundError):
        repository.get(9999)


def test_update_persists_changes(repository):
    created = repository.create(_sample_product())
    created.title = "PS5 Slim Digital"
    created.price = 2_300_000
    updated = repository.update(created)

    fetched = repository.get(updated.id)
    assert fetched.title == "PS5 Slim Digital"
    assert fetched.price == 2_300_000


def test_update_missing_raises(repository):
    ghost = _sample_product()
    ghost.id = 9999
    with pytest.raises(ProductNotFoundError):
        repository.update(ghost)


def test_delete_removes_product(repository):
    created = repository.create(_sample_product())
    repository.delete(created.id)
    with pytest.raises(ProductNotFoundError):
        repository.get(created.id)


def test_list_all_returns_all_products(repository):
    repository.create(_sample_product(title="A"))
    repository.create(_sample_product(title="B"))
    products = repository.list_all()
    assert {p.title for p in products} == {"A", "B"}


def test_list_enabled_filters_disabled(repository):
    repository.create(_sample_product(title="Activo", enabled=True))
    repository.create(_sample_product(title="Inactivo", enabled=False))
    enabled = repository.list_enabled()
    assert [p.title for p in enabled] == ["Activo"]


def test_automation_run_lifecycle(database):
    product_repo = ProductRepository(database)
    run_repo = AutomationRunRepository(database)

    product = product_repo.create(_sample_product())
    run = run_repo.start_run(product.id, operation="republish")
    assert run.id is not None
    assert run.status == "running"

    run_repo.finish_run(run.id, status="success")
    runs = run_repo.list_for_product(product.id)
    assert len(runs) == 1
    assert runs[0].status == "success"
    assert runs[0].finished_at is not None
