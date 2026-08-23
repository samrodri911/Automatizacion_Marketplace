"""Tests del creador/publicador de publicaciones (ListingCreator).

Se usa una `FakePage` que imita la superficie mínima de Playwright que toca
`ListingCreator` (goto, get_by_label/get_by_role/get_by_text, locator del
input de archivos, url, inner_text). Nunca se abre un navegador real.

Cubren: flujo de relleno y publicación, decisión de verificación
(verify_publication_from_page positivo/incierto), intervención (CAPTCHA) y
EPIPE → AutomationError.
"""

from __future__ import annotations

import pytest

from app.automation.listing_creator import ListingCreator, PublishStatus
from app.automation.listing_extractor import ListingExtractor
from app.core.exceptions import AutomationError, InterventionRequiredError
from app.models.listing import Listing
from app.models.product import Product

PRODUCT = Product(
    title="iPhone 13 128GB",
    description="Celular en perfecto estado, batería al 89%.",
    price=1850000.0,
    category="Electrónica",
    condition="Usado - Como nuevo",
    location="Cali",
    images=["iphone/01.jpg", "iphone/02.jpg"],
    enabled=True,
)


class FakeElement:
    def __init__(self, page: "FakePage", name: str) -> None:
        self._page = page
        self._name = name

    @property
    def first(self):
        return self

    def is_visible(self, timeout=None) -> bool:
        return self._name in self._page.visible_names

    def fill(self, value: str) -> None:
        self._page.filled[self._name] = value

    def click(self) -> None:
        self._page.clicked.append(self._name)
        if self._name.startswith("role:combobox:"):
            self._page.last_opened_combo = self._name
        if self._page.last_opened_combo and (
            self._name.startswith("text:")
            or self._name.startswith("role:option:")
            or self._name.startswith("role:menuitem:")
        ):
            self._page.combo_values[self._page.last_opened_combo] = self._name.split(":", 2)[-1]
        if self._name in self._page.checked:
            self._page.checked[self._name] = not self._page.checked[self._name]
        for g in self._page.audience_groups:
            if self._name == "role:checkbox:" + g["name"]:
                g["checked"] = not g["checked"]
        if "Publicar" in self._name and self._page.redirect_url:
            self._page.url = self._page.redirect_url

    def inner_text(self, timeout=None) -> str:
        return self._page.combo_values.get(self._name, "")

    def is_checked(self) -> bool:
        return self._page.checked.get(self._name, False)

    def get_attribute(self, name: str) -> str | None:
        return self._page.attributes.get(self._name, {}).get(name)

    def set_input_files(self, paths) -> None:
        self._page.uploaded = list(paths)


class FakeLocator:
    def __init__(self, page: "FakePage", name: str) -> None:
        self._page = page
        self._name = name

    @property
    def first(self):
        return FakeElement(self._page, self._name)

    def set_input_files(self, paths) -> None:
        self._page.uploaded = list(paths)


class FakePage:
    def __init__(self) -> None:
        self.visible_names: set[str] = set()
        self.filled: dict[str, str] = {}
        self.clicked: list[str] = []
        self.uploaded: list[str] = []
        self.url: str = ""
        self.body_text: str = ""
        self.goto_error: Exception | None = None
        self.redirect_url: str = ""
        self.evaluate_js_result: object = []
        self.attributes: dict[str, dict[str, str]] = {}
        self.label_selectors: dict[str, str] = {}
        self.checked: dict[str, bool] = {}
        self.audience_groups: list[dict] = []
        self.combo_values: dict[str, str] = {}
        self.last_opened_combo: str = ""

    def get_by_label(self, name: str) -> FakeLocator:
        return FakeLocator(self, f"label:{name}")

    def get_by_role(self, role: str, name=None, exact=False) -> FakeLocator:
        return FakeLocator(self, f"role:{role}:{name}")

    def get_by_text(self, name: str, exact=False) -> FakeLocator:
        return FakeLocator(self, f"text:{name}")

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, f"css:{selector}")

    def goto(self, url: str, wait_until=None, timeout=None) -> None:
        if self.goto_error is not None:
            raise self.goto_error
        self.url = url

    def evaluate(self, js: str, arg=None) -> object:
        if arg is not None:
            return self.label_selectors.get(arg)
        if 'role="checkbox"' in js and "checked" in js:
            return self.audience_groups
        return self.evaluate_js_result

    def wait_for_load_state(self, *args, **kwargs) -> None:
        pass


class FakeNavigator:
    def __init__(self, intervention: bool = False) -> None:
        self._intervention = intervention

    def requires_intervention(self) -> bool:
        return self._intervention

    def ensure_listings_section(self) -> None:
        pass

    def scroll_feed(self) -> bool:
        # Sin contenido nuevo tras la primera lectura: el loop de verificación
        # termina (evita que espere el deadline de búsqueda real).
        return False


def _page_for_happy_path() -> FakePage:
    page = FakePage()
    page.visible_names = {
        "label:Título",
        "label:Precio",
        "label:Descripción",
        "label:Ubicación",
        "role:combobox:Categoría",
        "role:combobox:Condición",
        "text:Electrónica",
        "role:button:Electrónica",
        "text:Usado - Como nuevo",
        "role:checkbox:Encuentro en un lugar público",
        "role:checkbox:Retiro en la puerta",
        "role:button:Siguiente",
        "role:button:Publicar",
    }
    # Grupos reales del paso final (step=audience), todos desmarcados.
    page.audience_groups = [
        {"checked": False, "name": "Implementos deportivos compra - venta"},
        {"checked": False, "name": "Emprendedores Vendiendo De Todo"},
        {"checked": False, "name": "Ventas de Articulos Deportivos"},
        {"checked": False, "name": "Tu mejor opción, en compra de Inmuebles en México"},
        {"checked": False, "name": "COMPRA Y VENTAS (VALLE DEL CAUCA) ENTRETENIMIENTO"},
        {"checked": False, "name": "venta de todo tipo, estado de México"},
    ]
    for g in page.audience_groups:
        page.visible_names.add("role:checkbox:" + g["name"])
    # Opciones reales del picker de categoría (el matcher elige la mejor).
    page.evaluate_js_result = [
        "Casa y jardín",
        "Electrónica",
        "Teléfonos celulares",
        "Electrodomésticos",
        "Instrumentos musicales",
    ]
    page.attributes["role:checkbox:Encuentro en un lugar público"] = {"aria-checked": "false"}
    page.attributes["role:checkbox:Retiro en la puerta"] = {"aria-checked": "false"}
    page.label_selectors = {
        "Marca": "#marca",
        "Etiquetas de productos": "#etiquetas",
    }
    page.url = "https://www.facebook.com/marketplace/item/987654"
    page.redirect_url = "https://www.facebook.com/marketplace/item/987654"
    return page


# ---------------------------------------------------------------------------
# Flujo de relleno y publicación
# ---------------------------------------------------------------------------
def test_create_fills_form_uploads_and_confirms():
    page = _page_for_happy_path()
    creator = ListingCreator()
    res = creator.create(PRODUCT, page, image_paths=["/tmp/a.jpg", "/tmp/b.jpg"])

    assert res.status == PublishStatus.PUBLISHED_CONFIRMED
    assert res.is_confirmed
    assert res.new_reference == "987654"
    assert res.new_url == "https://www.facebook.com/marketplace/item/987654"
    # Fotos subidas por el input[type=file].
    assert page.uploaded == ["/tmp/a.jpg", "/tmp/b.jpg"]
    # Campos rellenados con los datos ACTUALES del producto.
    assert page.filled["label:Título"] == PRODUCT.title
    assert page.filled["label:Precio"] == str(int(PRODUCT.price))
    assert page.filled["label:Descripción"] == PRODUCT.description
    assert page.filled["label:Ubicación"] == PRODUCT.location
    # Categoría (pick de Facebook), preferencias de entrega y condición por
# combo; "Siguiente" -> "Publicar".
    assert "role:combobox:Categoría" in page.clicked
    assert "role:button:Electrónica" in page.clicked
    assert "role:checkbox:Encuentro en un lugar público" in page.clicked
    assert "role:checkbox:Retiro en la puerta" in page.clicked
    assert "text:Usado - Como nuevo" in page.clicked
    assert "role:button:Siguiente" in page.clicked
    # Grupos del paso final: los generales SIEMPRE, los específicos sin
    # relación NO.
    for name in ("Emprendedores Vendiendo De Todo",
                 "COMPRA Y VENTAS (VALLE DEL CAUCA) ENTRETENIMIENTO",
                 "venta de todo tipo, estado de México"):
        assert f"role:checkbox:{name}" in page.clicked, f"debió marcar {name!r}"
    for name in ("Implementos deportivos compra - venta",
                 "Ventas de Articulos Deportivos",
                 "Tu mejor opción, en compra de Inmuebles en México"):
        assert f"role:checkbox:{name}" not in page.clicked, f"no debió marcar {name!r}"
    assert "role:button:Publicar" in page.clicked
    # Marca derivada del título ("iPhone 13..." -> Apple); toggles sin tocar.
    assert page.filled["css:#marca"] == "Apple"
    assert "role:switch:Promocionar tras publicar" not in page.clicked
    assert "role:switch:Ocultar a amigos" not in page.clicked


def test_create_returns_uncertain_without_positive_signal():
    page = _page_for_happy_path()
    page.redirect_url = ""  # sin redirección a un item real
    page.url = "https://www.facebook.com/marketplace"
    page.body_text = "Página sin señales de éxito"
    creator = ListingCreator()
    res = creator.create(PRODUCT, page, image_paths=["/tmp/a.jpg"])
    assert res.status == PublishStatus.PUBLISH_UNCERTAIN
    assert not res.is_confirmed


def test_create_returns_failed_when_field_cannot_be_filled():
    page = FakePage()
    page.visible_names = {"role:button:Publicar"}  # sin campos de título
    page.url = "https://www.facebook.com/marketplace/item/123"
    creator = ListingCreator()
    res = creator.create(PRODUCT, page, image_paths=["/tmp/a.jpg"])
    assert res.status == PublishStatus.PUBLISH_FAILED


def test_create_expands_more_details_before_filling():
    """Facebook actual oculta la descripción en 'Más detalles': el creator
    expande la sección antes de rellenar el formulario."""
    page = FakePage()
    page.visible_names = {
        "text:Más detalles",  # sección colapsada -> debe expandirse
        "label:Título",
        "label:Precio",
        "label:Descripción",
        "label:Ubicación",
        "role:combobox:Categoría",
        "role:combobox:Estado",
        "text:Electrónica",
        "text:Usado - Como nuevo",
        "role:button:Publicar",
    }
    page.evaluate_js_result = [
        "Casa y jardín",
        "Electrónica",
        "Teléfonos celulares",
        "Instrumentos musicales",
    ]
    page.url = "https://www.facebook.com/marketplace/item/987654"
    page.redirect_url = "https://www.facebook.com/marketplace/item/987654"
    creator = ListingCreator()
    res = creator.create(PRODUCT, page, image_paths=["/tmp/a.jpg"])
    assert res.status == PublishStatus.PUBLISHED_CONFIRMED
    assert "text:Más detalles" in page.clicked


def test_create_fills_description_via_bare_textarea_fallback():
    """La descripción de Facebook actual es un <textarea> SIN nombre
    accesible (sin label/aria-label): se rellena con el primer textarea
    visible tras expandir 'Más detalles'."""
    page = FakePage()
    page.visible_names = {
        "label:Título",
        "label:Precio",
        "css:textarea",  # textarea sin label -> fallback de descripción
        "label:Ubicación",
        "role:combobox:Categoría",
        "role:combobox:Estado",
        "text:Electrónica",
        "text:Usado - Como nuevo",
        "role:button:Publicar",
    }
    page.evaluate_js_result = [
        "Casa y jardín",
        "Electrónica",
        "Teléfonos celulares",
        "Instrumentos musicales",
    ]
    page.url = "https://www.facebook.com/marketplace/item/987654"
    page.redirect_url = "https://www.facebook.com/marketplace/item/987654"
    creator = ListingCreator()
    res = creator.create(PRODUCT, page, image_paths=["/tmp/a.jpg"])
    assert res.status == PublishStatus.PUBLISHED_CONFIRMED
    assert page.filled["css:textarea"] == PRODUCT.description


def test_create_reports_intervention_before_filling():
    page = FakePage()
    navigator = FakeNavigator(intervention=True)
    creator = ListingCreator()
    res = creator.create(PRODUCT, page, navigator=navigator, image_paths=["/tmp/a.jpg"])
    assert res.status == PublishStatus.INTERVENTION_REQUIRED


def test_create_category_high_fuzzy_matches_fb_option():
    """La categoría de BD 'Electronica e informatica' (sin tilde) se normaliza
    y coincide HIGH con 'Electrónica e informática' del picker de Facebook."""
    page = _page_for_happy_path()
    page.visible_names.add("role:button:Electrónica e informática")
    page.evaluate_js_result = [
        "Casa y jardín",
        "Electrónica e informática",
        "Teléfonos celulares",
        "Instrumentos musicales",
    ]
    product = Product(
        title=PRODUCT.title,
        description=PRODUCT.description,
        price=PRODUCT.price,
        category="Electronica e informatica",
        condition=PRODUCT.condition,
        location=PRODUCT.location,
        images=PRODUCT.images,
        enabled=True,
    )
    creator = ListingCreator()
    res = creator.create(product, page, image_paths=["/tmp/a.jpg"])
    assert res.status == PublishStatus.PUBLISHED_CONFIRMED
    assert "role:button:Electrónica e informática" in page.clicked


def test_create_category_medium_requests_intervention():
    """Coincidencia razonable pero no clara -> intervención manual, sin
    seleccionar arbitrariamente."""
    page = _page_for_happy_path()
    page.visible_names = {
        "label:Título",
        "label:Precio",
        "label:Descripción",
        "label:Ubicación",
        "role:combobox:Categoría",
        "role:button:Publicar",
    }
    page.evaluate_js_result = ["Portátiles", "Muebles", "Electrodomésticos"]
    product = Product(
        title=PRODUCT.title,
        description=PRODUCT.description,
        price=PRODUCT.price,
        category="Computadores Portátiles",
        condition=PRODUCT.condition,
        location=PRODUCT.location,
        images=PRODUCT.images,
        enabled=True,
    )
    creator = ListingCreator()
    with pytest.raises(InterventionRequiredError):
        creator.create(product, page, image_paths=["/tmp/a.jpg"])


def test_create_category_no_options_requests_intervention():
    """Si no se pueden leer las opciones del picker, no se selecciona nada a
    ciegas: se pide intervención manual."""
    page = _page_for_happy_path()
    page.visible_names = {
        "label:Título",
        "label:Precio",
        "label:Descripción",
        "label:Ubicación",
        "role:combobox:Categoría",
        "role:button:Publicar",
    }
    page.evaluate_js_result = []
    creator = ListingCreator()
    with pytest.raises(InterventionRequiredError):
        creator.create(PRODUCT, page, image_paths=["/tmp/a.jpg"])


def test_create_delivery_preferences_are_idempotent():
    """Si las preferencias de entrega ya están activas no se vuelven a marcar,
    y 'Entrega en la puerta' nunca se toca."""
    page = _page_for_happy_path()
    page.visible_names.add("role:checkbox:Entrega en la puerta")
    # Ya activas: no debe haber nuevos clics sobre ellas.
    page.attributes["role:checkbox:Encuentro en un lugar público"] = {"aria-checked": "true"}
    page.attributes["role:checkbox:Retiro en la puerta"] = {"aria-checked": "true"}
    page.attributes["role:checkbox:Entrega en la puerta"] = {"aria-checked": "false"}
    creator = ListingCreator()
    res = creator.create(PRODUCT, page, image_paths=["/tmp/a.jpg"])
    assert res.status == PublishStatus.PUBLISHED_CONFIRMED
    assert "role:checkbox:Encuentro en un lugar público" not in page.clicked
    assert "role:checkbox:Retiro en la puerta" not in page.clicked
    assert "role:checkbox:Entrega en la puerta" not in page.clicked


def test_create_fills_brand_and_tags_best_effort():
    """Marca derivada del título y etiquetas del producto se rellenan en los
    campos sin nombre accesible (localizados por su etiqueta visual)."""
    page = _page_for_happy_path()
    product = Product(
        title="Laptop HP Pavilion 15 - i5, 12GB RAM",
        description=PRODUCT.description,
        price=PRODUCT.price,
        category=PRODUCT.category,
        condition=PRODUCT.condition,
        location=PRODUCT.location,
        tags=["notebook", "hp", "usado"],
        images=PRODUCT.images,
        enabled=True,
    )
    creator = ListingCreator()
    res = creator.create(product, page, image_paths=["/tmp/a.jpg"])
    assert res.status == PublishStatus.PUBLISHED_CONFIRMED
    assert page.filled["css:#marca"] == "HP"
    assert page.filled["css:#etiquetas"] == "notebook, hp, usado"


def test_create_turns_off_promote_and_hide_switches():
    """Si 'Promocionar tras publicar' u 'Ocultar a amigos' estuvieran activos,
    se desactivan antes de publicar."""
    page = _page_for_happy_path()
    page.visible_names.add("role:switch:Promocionar tras publicar")
    page.visible_names.add("role:switch:Ocultar a amigos")
    page.checked["role:switch:Promocionar tras publicar"] = True
    page.checked["role:switch:Ocultar a amigos"] = True
    creator = ListingCreator()
    res = creator.create(PRODUCT, page, image_paths=["/tmp/a.jpg"])
    assert res.status == PublishStatus.PUBLISHED_CONFIRMED
    assert "role:switch:Promocionar tras publicar" in page.clicked
    assert "role:switch:Ocultar a amigos" in page.clicked
    assert page.checked["role:switch:Promocionar tras publicar"] is False
    assert page.checked["role:switch:Ocultar a amigos"] is False


def test_create_with_disabled_next_reports_form_incomplete():
    """Si 'Siguiente' está deshabilitado (formulario incompleto) NO se pulsa
    ningún otro botón con 'Publicar' en el nombre (p. ej. el toggle
    'Promocionar tras publicar')."""
    page = _page_for_happy_path()
    page.attributes["role:button:Siguiente"] = {"aria-disabled": "true"}
    # El toggle de promoción es también un role=button cuyo nombre contiene
    # "Publicar"; NO debe golpearse.
    page.visible_names.add("role:button:Promocionar tras publicar")
    creator = ListingCreator()
    res = creator.create(PRODUCT, page, image_paths=["/tmp/a.jpg"])
    assert res.status == PublishStatus.PUBLISH_FAILED
    assert "formulario quedó incompleto" in (res.detail or "")
    assert "role:button:Promocionar tras publicar" not in page.clicked
    assert "role:button:Publicar" not in page.clicked


def test_create_publish_never_clicks_promote_button():
    """El botón de publicar nunca debe golpear el toggle 'Promocionar tras
    publicar' (que aparece como role=button en la pantalla de detalles)."""
    page = _page_for_happy_path()
    page.visible_names.add("role:button:Promocionar tras publicar")
    page.visible_names.add("role:button:Ocultar a amigos")
    creator = ListingCreator()
    res = creator.create(PRODUCT, page, image_paths=["/tmp/a.jpg"])
    assert res.status == PublishStatus.PUBLISHED_CONFIRMED
    assert "role:button:Promocionar tras publicar" not in page.clicked
    assert "role:button:Ocultar a amigos" not in page.clicked


def test_create_raises_automation_error_on_epipe():
    page = FakePage()
    page.goto_error = RuntimeError("broken pipe")
    creator = ListingCreator()
    with pytest.raises(AutomationError):
        creator.create(PRODUCT, page, image_paths=["/tmp/a.jpg"])


# ---------------------------------------------------------------------------
# Verificación de reanudación (verify_only)
# ---------------------------------------------------------------------------
def test_verify_only_confirmed_when_listing_matches(monkeypatch):
    found = [
        Listing(
            title="iPhone 13 128GB",
            price=1850000,
            price_raw="$1.850.000",
            url="https://www.facebook.com/marketplace/item/555",
            reference="555",
        )
    ]
    monkeypatch.setattr(ListingExtractor, "extract_listings", lambda self, page: found)
    creator = ListingCreator()
    res = creator.verify_only(PRODUCT, FakePage(), navigator=FakeNavigator())
    assert res.status == PublishStatus.PUBLISHED_CONFIRMED
    assert res.new_reference == "555"


def test_verify_only_uncertain_when_not_found(monkeypatch):
    other = [
        Listing(
            title="Zapatos para correr",
            price=120000,
            price_raw="$120.000",
            url="https://www.facebook.com/marketplace/item/999",
            reference="999",
        )
    ]
    monkeypatch.setattr(ListingExtractor, "extract_listings", lambda self, page: other)
    creator = ListingCreator()
    res = creator.verify_only(PRODUCT, FakePage(), navigator=FakeNavigator())
    assert res.status == PublishStatus.PUBLISH_UNCERTAIN


def test_verify_only_intervention_required():
    creator = ListingCreator()
    res = creator.verify_only(PRODUCT, FakePage(), navigator=FakeNavigator(intervention=True))
    assert res.status == PublishStatus.INTERVENTION_REQUIRED