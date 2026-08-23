from app.models.product import Product, ProductCondition


def _valid_product(**overrides) -> Product:
    base = dict(
        title="iPhone 13 128GB",
        description="iPhone 13 en excelente estado",
        price=1_850_000,
        category="Electrónica",
        condition=ProductCondition.USED_LIKE_NEW.value,
        location="Cali",
        tags=["iphone", "apple"],
        images=["iphone-13/01.jpg"],
    )
    base.update(overrides)
    return Product(**base)


def test_valid_product_has_no_errors():
    product = _valid_product()
    assert product.validate() == []
    assert product.is_valid is True


def test_missing_title_is_invalid():
    product = _valid_product(title="")
    errors = product.validate()
    assert any("título" in e.lower() for e in errors)


def test_negative_price_is_invalid():
    product = _valid_product(price=-1)
    errors = product.validate()
    assert any("precio" in e.lower() for e in errors)


def test_no_images_is_invalid():
    product = _valid_product(images=[])
    errors = product.validate()
    assert any("fotografía" in e.lower() for e in errors)


def test_tags_json_roundtrip():
    product = _valid_product(tags=["a", "b", "c"])
    raw = product.tags_as_json()
    assert Product.parse_tags(raw) == ["a", "b", "c"]


def test_images_json_roundtrip():
    product = _valid_product(images=["x/01.jpg", "x/02.jpg"])
    raw = product.images_as_json()
    assert Product.parse_images(raw) == ["x/01.jpg", "x/02.jpg"]


def test_parse_tags_handles_invalid_json():
    assert Product.parse_tags("no es json") == []
    assert Product.parse_tags(None) == []


def test_parse_images_handles_invalid_json():
    assert Product.parse_images("no es json") == []
    assert Product.parse_images(None) == []
