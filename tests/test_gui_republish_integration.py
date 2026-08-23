"""Tests de integración GUI ↔ flujo "Republicar" (Iteración 5).

Offscreen, sin navegador real. Cubren:
- habilitación del botón "🔄 Republicar" SOLO con un ítem HIGH del scan
  (MEDIUM/LOW/AMBIGUOUS/NO_MATCH lo dejan deshabilitado);
- el diálogo de confirmación muestra original (congelada) vs nueva;
- editar el producto conserva el target congelado (assert en BD);
- cancelar la republicación NO deja un target viejo asociado al volver a
  seleccionar el producto (ciclo de vida del target);
- reinicio de targets pre-confirmación huérfanos y bloqueo de targets
  post-confirmación;
- recuperación automática al arrancar (encadena create_and_publish /
  resume_republish según el status del target, siempre verificando).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

from app.core.exceptions import RepublishError
from app.database.database import Database
from app.database.repositories import (
    AutomationRunRepository,
    MatchedListingsRepository,
    ProductRepository,
)
from app.gui.main_window import MainWindow
from app.gui.republish_confirm_dialog import RepublishConfirmDialog
from app.models.matched_listing import MatchedListing
from app.models.product import Product
from app.services.matched_listing_service import MatchedListingService
from app.services.product_service import ProductService


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeAutomation(QObject):
    delete_listing_requested = Signal(object)
    delete_ready = Signal(object)
    delete_result = Signal(object)
    republish_progress = Signal(object)
    publication_result = Signal(object)
    intervention_paused = Signal(str)
    republish_freeze_requested = Signal(object)
    republish_mark_editing_requested = Signal(int)
    republish_mark_edit_saved_requested = Signal(int)
    execute_delete_requested = Signal(object)
    create_and_publish_requested = Signal(object)
    resume_republish_requested = Signal(object)


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=tmp_path / "test_republish_gui.db")
    database.initialize()
    return database


@pytest.fixture
def window(qapp, db, tmp_path):
    repository = ProductRepository(db)
    run_repo = AutomationRunRepository(db)
    service = ProductService(repository, products_dir=tmp_path / "products", run_repository=run_repo)
    matched_service = MatchedListingService(MatchedListingsRepository(db), service)
    win = MainWindow(service, matched_service)
    win._automation_service = FakeAutomation()  # noqa: SLF001
    win._search_ready = True  # noqa: SLF001
    return win


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


def _scan_item(matched_product_id, confidence, auto_selected=False):
    return {
        "listing": {
            "title": "iPhone 13 128GB",
            "price": 1850000,
            "price_raw": "$1.850.000",
            "url": "https://www.facebook.com/marketplace/item/777",
            "reference": "777",
            "image_refs": [],
            "key": "777",
        },
        "matched_product_id": matched_product_id,
        "matched_product_title": "iPhone 13 128GB" if matched_product_id else "",
        "confidence": confidence,
        "score": 95.0,
        "reasons": [],
        "warnings": [],
        "auto_selected": auto_selected,
    }


def _fill_scan(window, items: list[dict]) -> None:
    window._on_scan_completed(
        {
            "items": items,
            "total_listings": len(items),
            "matched_high_count": sum(1 for i in items if i["confidence"] == "HIGH"),
            "matched_medium_count": sum(1 for i in items if i["confidence"] == "MEDIUM"),
            "unmatched_count": sum(1 for i in items if i["confidence"] == "NO_MATCH"),
            "scrolls_executed": 0,
            "stopped_for": "complete",
        }
    )


# ---------------------------------------------------------------------------
# 1. Habilitación del botón Republicar SOLO con HIGH del scan
# ---------------------------------------------------------------------------
def test_republish_button_enabled_only_with_high_scan_item(window):
    product = window._product_service.create(_product())
    window._update_search_button_state()

    _fill_scan(window, [_scan_item(product.id, "HIGH", auto_selected=True)])
    window.scanned_listings_list.setCurrentRow(0)

    assert window.republish_btn.isEnabled()
    assert window.republish_btn.toolTip()


@pytest.mark.parametrize(
    "confidence",
    ["MEDIUM", "LOW", "AMBIGUOUS", "NO_MATCH"],
)
def test_republish_button_disabled_with_non_high_confidence(window, confidence):
    product = window._product_service.create(_product())
    _fill_scan(window, [_scan_item(product.id, confidence)])
    window.scanned_listings_list.setCurrentRow(0)
    assert not window.republish_btn.isEnabled()


def test_republish_button_disabled_without_locator(window):
    product = window._product_service.create(_product())
    item = _scan_item(product.id, "HIGH", auto_selected=True)
    item["listing"]["url"] = ""
    item["listing"]["reference"] = ""
    _fill_scan(window, [item])
    window.scanned_listings_list.setCurrentRow(0)
    assert not window.republish_btn.isEnabled()


def test_republish_button_disabled_without_search_ready(window):
    product = window._product_service.create(_product())
    window._search_ready = False  # noqa: SLF001
    _fill_scan(window, [_scan_item(product.id, "HIGH", auto_selected=True)])
    window.scanned_listings_list.setCurrentRow(0)
    assert not window.republish_btn.isEnabled()


def test_republish_button_disabled_when_no_product_selected(window):
    product = window._product_service.create(_product())
    _fill_scan(window, [_scan_item(product.id, "HIGH", auto_selected=True)])
    window._update_search_button_state()
    assert not window.republish_btn.isEnabled()


def test_edit_data_button_enabled_with_product_selection(window):
    product = window._product_service.create(_product())
    window._reload_products()
    assert not window.edit_data_btn.isEnabled()
    window.products_list.setCurrentRow(0)
    assert window.edit_data_btn.isEnabled()


# ---------------------------------------------------------------------------
# 2. Diálogo de confirmación: original congelada vs nueva
# ---------------------------------------------------------------------------
def test_republish_confirm_dialog_shows_original_and_new(qapp):
    matched = MatchedListing(
        product_id=1,
        listing_url="https://www.facebook.com/marketplace/item/777",
        listing_reference="777",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        matched_price_raw="$1.850.000",
        confidence="HIGH",
        status="awaiting_confirm",
    )
    product = _product(title="iPhone 13 Editado 2.0", price=2000000.0)

    dialog = RepublishConfirmDialog(matched=matched, product=product)
    texts = " | ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "iPhone 13 128GB" in texts          # original (congelada)
    assert "$1.850.000" in texts               # precio FB
    assert "HIGH" in texts                     # confianza
    assert "iPhone 13 Editado 2.0" in texts    # nueva
    assert "2,000,000" in texts.replace(".", "") or "2000000" in texts

    buttons = [b.text() for b in dialog.findChildren(QPushButton)]
    assert any("Eliminar y republicar" in b for b in buttons)
    assert any("Cancelar" in b for b in buttons)


# ---------------------------------------------------------------------------
# 3. Editar el producto conserva el target congelado (assert en BD)
# ---------------------------------------------------------------------------
def test_edit_preserves_frozen_target(window):
    product = window._product_service.create(_product())
    matched = window._matched_service.select_match(
        product_id=product.id,
        listing_url="https://www.facebook.com/marketplace/item/777",
        listing_reference="777",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        matched_price_raw="$1.850.000",
        confidence="HIGH",
    )

    product.title = "iPhone 13 Editado 2.0"
    product.price = 2000000.0
    window._product_service.update(product)
    window._matched_service.save_edit_snapshot(matched.id, product)

    fetched = window._matched_service.get(matched.id)
    assert fetched.listing_url == "https://www.facebook.com/marketplace/item/777"
    assert fetched.listing_reference == "777"
    assert fetched.matched_title == "iPhone 13 128GB"
    assert fetched.matched_price == 1850000
    assert fetched.matched_price_raw == "$1.850.000"
    # Snapshot de trazabilidad con los datos editados.
    assert fetched.new_title == "iPhone 13 Editado 2.0"
    assert fetched.new_price == 2000000.0


# ---------------------------------------------------------------------------
# 3b. Ciclo de vida del target: cancelar no deja target viejo al re-seleccionar
# ---------------------------------------------------------------------------
def test_cancel_republish_leaves_no_stale_target(window, monkeypatch):
    """Editar → Republicar → cancelar → volver a seleccionar el producto:
    no debe quedar ningún target activo asociado."""
    product = window._product_service.create(_product())

    # Edición previa (equivale a guardar con "✏️ Editar datos").
    product.title = "iPhone 13 Editado 2.0"
    product.price = 2000000.0
    product.description = "Descripción editada"
    window._product_service.update(product)

    _fill_scan(window, [_scan_item(product.id, "HIGH", auto_selected=True)])
    window.scanned_listings_list.setCurrentRow(0)
    assert window.republish_btn.isEnabled()

    # El usuario cancela la confirmación de republicación.
    monkeypatch.setattr(RepublishConfirmDialog, "exec", lambda self: 0)
    frozen_emissions = []
    window._automation_service.republish_freeze_requested.connect(
        lambda payload: frozen_emissions.append(payload)
    )

    window._on_republish()

    # La operación se despachó por señal (sin TypeError de invokeMethod) y el
    # target quedó CANCELADO de forma explícita (terminal).
    assert frozen_emissions
    assert frozen_emissions[0]["confidence"] == "HIGH"
    assert window._matched_service.get_active_by_product(product.id) is None

    # Al volver a seleccionar el producto: el panel vuelve a proponer la
    # coincidencia HIGH fresca y no muestra un target viejo asociado.
    window.products_list.setCurrentRow(0)
    window._refresh_selected_product_panel()
    assert window._matched_service.get_active_by_product(product.id) is None
    assert "Publicación encontrada" in window.pub_listing_label.text()
    assert "🟢" in window.match_label.text() or "ALTA" in window.match_label.text()


def test_republish_restarts_preconfirm_orphan(window, monkeypatch):
    """Un target pre-confirmación huérfano (sin confirmar) se reinicia
    explícitamente: se cancela y se congela uno nuevo desde el escaneo."""
    product = window._product_service.create(_product())
    old = window._matched_service.select_match(
        product_id=product.id,
        listing_url="https://www.facebook.com/marketplace/item/OLD",
        listing_reference="OLD",
        matched_title="Publicación vieja",
        matched_price=999,
        matched_price_raw="$999",
        confidence="HIGH",
    )

    _fill_scan(window, [_scan_item(product.id, "HIGH", auto_selected=True)])
    window.scanned_listings_list.setCurrentRow(0)

    monkeypatch.setattr(
        RepublishConfirmDialog, "exec", lambda self: RepublishConfirmDialog.DialogCode.Accepted
    )
    window._on_republish()

    # El target viejo se canceló y existe un target NUEVO congelado desde el
    # scan (la eliminación se despachó; aquí no hay worker real).
    assert window._matched_service.get(old.id).status == "cancelled"
    fresh = window._matched_service.get_active_by_product(product.id)
    assert fresh is not None
    assert fresh.id != old.id
    assert fresh.listing_reference == "777"


def test_republish_disabled_with_postconfirm_active_target(window):
    """Con un target post-confirmación activo (deleting), Republicar queda
    deshabilitado: el flujo debe reanudarse, no reiniciarse."""
    product = window._product_service.create(_product())
    matched = window._matched_service.select_match(
        product_id=product.id,
        listing_url="https://www.facebook.com/marketplace/item/777",
        listing_reference="777",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        matched_price_raw="$1.850.000",
        confidence="HIGH",
    )
    window._matched_service.mark_deleting(matched.id)

    _fill_scan(window, [_scan_item(product.id, "HIGH", auto_selected=True)])
    window.scanned_listings_list.setCurrentRow(0)
    assert not window.republish_btn.isEnabled()


def test_deleting_product_cancels_active_target(window, monkeypatch):
    """Eliminar el producto limpia explícitamente su target (deja de ser válido)."""
    product = window._product_service.create(_product())
    matched = window._matched_service.select_match(
        product_id=product.id,
        listing_url="https://www.facebook.com/marketplace/item/777",
        listing_reference="777",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        matched_price_raw="$1.850.000",
        confidence="HIGH",
    )
    window._reload_products()
    window.products_list.setCurrentRow(0)

    monkeypatch.setattr(
        "app.gui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    window._on_delete_product()

    # El producto se eliminó y su target quedó limpio: cancel_active lo marcó
    # y la FK con ON DELETE CASCADE elimina la fila. No queda target activo.
    assert window._matched_service.get_active_by_product(product.id) is None
    with pytest.raises(RepublishError):
        window._matched_service.get(matched.id)


# ---------------------------------------------------------------------------
# 4. Recuperación automática al arrancar (encadena según status)
# ---------------------------------------------------------------------------
def test_recovery_chains_create_after_deleted(window, monkeypatch):
    product = window._product_service.create(_product())
    matched = window._matched_service.select_match(
        product_id=product.id,
        listing_url="https://www.facebook.com/marketplace/item/777",
        listing_reference="777",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        matched_price_raw="$1.850.000",
        confidence="HIGH",
    )
    window._matched_service.mark_deleted_confirmed(matched.id)

    calls: list[tuple] = []
    monkeypatch.setattr(window, "_invoke_service", lambda method, payload: calls.append((method, payload)))
    window._maybe_resume_republish()

    assert calls and calls[0][0] == "create_and_publish"
    assert calls[0][1]["matched_id"] == matched.id


def test_recovery_verifies_before_continuing_creating(window, monkeypatch):
    product = window._product_service.create(_product())
    matched = window._matched_service.select_match(
        product_id=product.id,
        listing_url="https://www.facebook.com/marketplace/item/777",
        listing_reference="777",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        matched_price_raw="$1.850.000",
        confidence="HIGH",
    )
    window._matched_service.mark_creating(matched.id)

    calls: list[tuple] = []
    monkeypatch.setattr(window, "_invoke_service", lambda method, payload: calls.append((method, payload)))
    window._maybe_resume_republish()

    assert calls and calls[0][0] == "resume_republish"
    assert calls[0][1]["phase"] == "publish"
    # Nunca se invoca create_and_publish sin verificar primero.
    assert [c[0] for c in calls] == ["resume_republish"]


def test_recovery_verifies_delete_without_redeleting(window, monkeypatch):
    product = window._product_service.create(_product())
    matched = window._matched_service.select_match(
        product_id=product.id,
        listing_url="https://www.facebook.com/marketplace/item/777",
        listing_reference="777",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        matched_price_raw="$1.850.000",
        confidence="HIGH",
    )
    window._matched_service.mark_deleting(matched.id)

    calls: list[tuple] = []
    monkeypatch.setattr(window, "_invoke_service", lambda method, payload: calls.append((method, payload)))
    window._maybe_resume_republish()

    assert calls and calls[0][0] == "resume_republish"
    assert calls[0][1]["phase"] == "delete"
    assert calls[0][1]["matched_target"]["reference"] == "777"


def test_recovery_noop_on_editing_pending(window, monkeypatch):
    product = window._product_service.create(_product())
    window._matched_service.select_match(
        product_id=product.id,
        listing_url="https://www.facebook.com/marketplace/item/777",
        listing_reference="777",
        matched_title="iPhone 13 128GB",
        matched_price=1850000,
        matched_price_raw="$1.850.000",
        confidence="HIGH",
    )

    calls: list[tuple] = []
    monkeypatch.setattr(window, "_invoke_service", lambda method, payload: calls.append((method, payload)))
    window._maybe_resume_republish()

    # Un target en 'selected' NO dispara ninguna acción automática.
    assert calls == []
    assert "pendiente" in window.republish_status_label.text().lower()


def test_recovery_skips_when_no_active_target(window, monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(window, "_invoke_service", lambda method, payload: calls.append((method, payload)))
    window._maybe_resume_republish()
    assert calls == []