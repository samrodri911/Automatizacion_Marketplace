import pytest
from PIL import Image

from app.core.exceptions import ProductValidationError
from app.database.database import Database
from app.database.repositories import ProductRepository
from app.models.product import Product
from app.services.product_service import ProductService, slugify


@pytest.fixture
def service(tmp_path):
    db = Database(db_path=tmp_path / "test.db")
    db.initialize()
    repository = ProductRepository(db)
    products_dir = tmp_path / "products"
    return ProductService(repository, products_dir=products_dir)


def _make_image_file(tmp_path, name="photo.jpg") -> str:
    path = tmp_path / name
    Image.new("RGB", (10, 10), color="red").save(path)
    return str(path)


def _sample_product(**overrides) -> Product:
    base = dict(
        title="AirPods Pro",
        description="AirPods Pro originales",
        price=650_000,
        category="Audio",
        condition="Usado - Como nuevo",
        location="Cali",
        tags=["airpods", "apple"],
        images=[],
    )
    base.update(overrides)
    return Product(**base)


def test_slugify_basic():
    assert slugify("iPhone 13 128GB") == "iphone-13-128gb"
    assert slugify("  Monitor LG 27\" ") == "monitor-lg-27"
    assert slugify("") == "producto"


def test_create_without_images_raises_validation_error(service):
    product = _sample_product()
    with pytest.raises(ProductValidationError):
        service.create(product)


def test_create_copies_images_and_saves_relative_paths(service, tmp_path):
    product = _sample_product()
    image_path = _make_image_file(tmp_path)

    created = service.create(product, source_image_paths=[image_path])

    assert len(created.images) == 1
    resolved = service.resolve_image_path(created.images[0])
    assert resolved.exists()
    assert resolved.name == "01.jpg"


def test_create_ignores_non_image_files(service, tmp_path):
    bogus = tmp_path / "notes.txt"
    bogus.write_text("hola")

    product = _sample_product()
    # El archivo no es una imagen válida, así que no se copia ninguna;
    # como no queda ninguna imagen, la validación debe rechazar el producto.
    with pytest.raises(ProductValidationError):
        service.create(product, source_image_paths=[str(bogus)])


def test_update_appends_new_images(service, tmp_path):
    product = _sample_product()
    first_image = _make_image_file(tmp_path, "a.jpg")
    created = service.create(product, source_image_paths=[first_image])

    second_image = _make_image_file(tmp_path, "b.jpg")
    updated = service.update(created, source_image_paths=[second_image])

    assert len(updated.images) == 2
