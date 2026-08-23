"""Tests de integración GUI ↔ cola de republicación múltiple.

Offscreen, sin navegador real. Cubren:
- habilitación del botón "🔄 Republicar seleccionados (N)" con ≥1 elegible;
- preparación: la edición ocurre ANTES de congelar (el target se congela con
  los datos editados y NUNCA se invalida);
- arranque: se congelan TODOS los targets HIGH y se procesa 1 a 1;
- avance estrictamente secuencial: el siguiente ítem solo empieza tras
  PUBLISHED_CONFIRMED del anterior;
- fallo/incertidumbre: la cola se PAUSA y el usuario decide (reintentar
  verificado / omitir / detener);
- guards: durante la cola los targets congelados no se cancelan al cambiar
  de producto.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog

from app.database.database import Database
from app.database.repositories import (
    AutomationRunRepository,
    MatchedListingsRepository,
    ProductRepository,
)
from app.gui.main_window import MainWindow
from app.gui.product_editor import ProductEditorDialog
from app.gui.queue_failure_dialog import QueueFailureChoice, QueueFailureDialog
from app.gui.queue_prep_dialog import QueuePrepDialog
from app.models.matched_listing import MatchedListing
from app.models.product import Product
from app.models.republish_queue import QueueItemStatus, RepublishQueueState
from app.services.matched_listing_service import MatchedListingService
from app.services.product_service import ProductService


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeAutomation(QObject):
    republish_freeze_requested = Signal(object)
    republish_mark_editing_requested = Signal(int)
    republish_mark_edit_saved_requested = Signal(int)
    delete_listing_requested = Signal(object)
    delete_ready = Signal(object)
    delete_result = Signal(object)
    execute_delete_requested = Signal(object)
    create_and_publish_requested = Signal(object)
    resume_republish_requested = Signal(object)
    publication_result = Signal(object)


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=tmp_path / "test_queue_gui.db")
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


def _scan(matched_product_id, confidence="HIGH", reference="777"):
    return {
        "listing": {
            "title": "iPhone 13 128GB",
            "price": 1850000,
            "price_raw": "$1.850.000",
            "url": f"https://www.facebook.com/marketplace/item/{reference}",
            "reference": reference,
            "image_refs": [],
            "key": reference,
        },
        "matched_product_id": matched_product_id,
        "matched_product_title": "iPhone 13 128GB",
        "confidence": confidence,
        "score": 95.0,
        "reasons": [],
        "warnings": [],
        "auto_selected": True,
    }


def _fill_scan(window, items):
    window._on_scan_completed(
        {
            "items": items,
            "total_listings": len(items),
            "matched_high_count": sum(1 for i in items if i["confidence"] == "HIGH"),
            "matched_medium_count": 0,
            "unmatched_count": 0,
            "scrolls_executed": 0,
            "stopped_for": "complete",
        }
    )


def _select_products(window, product_ids):
    for i in range(window.products_list.count()):
        pid = window.products_list.item(i).data(0x0100 + 1)
        window.products_list.item(i).setSelected(pid in product_ids)


def _delete_payload(product_id, matched_id, result):
    return {
        "result": result,
        "product_id": product_id,
        "product_title": "iPhone 13 128GB",
        "confidence": "HIGH",
        "error": None,
        "detail": "detalle",
        "listing_url": "https://www.facebook.com/marketplace/item/777",
        "listing_reference": "777",
        "matched_id": matched_id,
    }


def _publication_payload(product_id, matched_id, result):
    return {
        "result": result,
        "matched_id": matched_id,
        "product_id": product_id,
        "product_title": "iPhone 13 128GB",
        "new_url": "https://www.facebook.com/marketplace/item/999",
        "new_reference": "999",
        "error": None,
        "detail": "detalle",
    }


# ---------------------------------------------------------------------------
# 1. Botón de cola: habilitación y etiqueta con ≥1 elegible
# ---------------------------------------------------------------------------
def test_queue_button_enabled_with_eligible_selected(window):
    p1 = window._product_service.create(_product(title="A"))
    p2 = window._product_service.create(_product(title="B"))
    _fill_scan(window, [_scan(p1.id), _scan(p2.id)])
    window._reload_products()
    _select_products(window, [p1.id, p2.id])
    window._update_search_button_state()

    assert window.queue_btn.isEnabled()
    assert window.queue_btn.text() == "🔄 Republicar seleccionados (2)"


def test_queue_button_disabled_without_high(window):
    p1 = window._product_service.create(_product(title="A"))
    _fill_scan(window, [_scan(p1.id, confidence="MEDIUM")])
    window._reload_products()
    _select_products(window, [p1.id])
    window._update_search_button_state()

    assert not window.queue_btn.isEnabled()
    assert window.queue_btn.text() == "🔄 Republicar seleccionados (0)"


def test_queue_button_counts_only_eligible(window):
    p1 = window._product_service.create(_product(title="A"))
    p2 = window._product_service.create(_product(title="B"))
    _fill_scan(window, [_scan(p1.id), _scan(p2.id, confidence="LOW")])
    window._reload_products()
    _select_products(window, [p1.id, p2.id])
    window._update_search_button_state()

    assert window.queue_btn.isEnabled()
    assert window.queue_btn.text() == "🔄 Republicar seleccionados (1)"


# ---------------------------------------------------------------------------
# 2. Preparación: la edición ocurre ANTES de congelar
# ---------------------------------------------------------------------------
def test_prep_dialog_edits_product_before_start(window, monkeypatch):
    """El diálogo de preparación permite editar cada producto y lo marca como
    editado, ANTES de que se congele ningún target."""
    from app.services.republish_queue import build_queue

    p1 = window._product_service.create(_product(title="iPhone 13"))
    scan = _scan(p1.id)
    build = build_queue([p1], lambda pid: scan, window._current_active_target)
    dialog = QueuePrepDialog(build.eligible, build.excluded, window._product_service)

    def fake_editor_exec(self):
        self.title_edit.setText("iPhone 13 EDITADO EN COLA")
        self.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ProductEditorDialog, "exec", fake_editor_exec)
    dialog._on_edit(0)  # noqa: SLF001

    updated = window._product_service.get(p1.id)
    assert updated.title == "iPhone 13 EDITADO EN COLA"
    assert dialog.edited_product_ids() == {p1.id}
    assert "EDITADO" in dialog._row_labels[0].text()  # noqa: SLF001


def test_freeze_uses_edited_data_recorded_in_prep(window, monkeypatch):
    """El target se congela desde el ESCANEO (original de Facebook) y guarda el
    snapshot con los datos EDITADOS: la edición de preparación se respeta y
    nunca invalida el target."""
    p1 = window._product_service.create(_product(title="iPhone 13"))
    _fill_scan(window, [_scan(p1.id)])
    window._reload_products()
    _select_products(window, [p1.id])

    # La edición ya ocurrió en la preparación (equivale al diálogo aceptado).
    product = window._product_service.get(p1.id)
    product.title = "iPhone 13 EDITADO EN COLA"
    window._product_service.update(product)

    monkeypatch.setattr(QueuePrepDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    window._on_queue_republish()

    frozen = window._matched_service.get_active_by_product(p1.id)
    assert frozen is not None
    # Original congelado desde el escaneo (nunca del producto editado)...
    assert frozen.matched_title == "iPhone 13 128GB"
    assert frozen.listing_reference == "777"
    # ...y snapshot de la NUEVA publicación con los datos editados.
    assert frozen.new_title == "iPhone 13 EDITADO EN COLA"


# ---------------------------------------------------------------------------
# 3. Arranque: congelar TODOS los targets + procesar 1 a 1
# ---------------------------------------------------------------------------
def test_queue_freezes_all_targets_and_starts_first(window, monkeypatch):
    p1 = window._product_service.create(_product(title="A"))
    p2 = window._product_service.create(_product(title="B"))
    _fill_scan(window, [_scan(p1.id), _scan(p2.id)])
    window._reload_products()
    _select_products(window, [p1.id, p2.id])

    calls = []
    monkeypatch.setattr(window, "_invoke_service", lambda method, payload: calls.append((method, payload)))
    monkeypatch.setattr(QueuePrepDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    window._on_queue_republish()

    # TODOS los targets quedaron congelados ANTES de procesar.
    frozen_1 = window._matched_service.get_active_by_product(p1.id)
    frozen_2 = window._matched_service.get_active_by_product(p2.id)
    assert frozen_1 is not None and frozen_2 is not None

    assert window._queue is not None
    assert window._queue.state == RepublishQueueState.RUNNING
    assert window._queue.current_item().product_id == p1.id
    assert window.queue_items_list.count() == 2
    assert not window._queue_box.isHidden()

    # La primera eliminación se preparó con el target congelado de A.
    prepared = [p for m, p in calls if m == "prepare_delete"]
    assert len(prepared) == 1
    assert prepared[0]["matched_target"]["matched_id"] == frozen_1.id


def test_queue_advances_sequentially_after_confirm(window, monkeypatch):
    p1 = window._product_service.create(_product(title="A"))
    p2 = window._product_service.create(_product(title="B"))
    _fill_scan(window, [_scan(p1.id), _scan(p2.id)])
    window._reload_products()
    _select_products(window, [p1.id, p2.id])
    monkeypatch.setattr(QueuePrepDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        "app.gui.main_window.QMessageBox.information",
        lambda *a, **k: None,
    )

    window._on_queue_republish()
    frozen_1 = window._matched_service.get_active_by_product(p1.id)
    frozen_2 = window._matched_service.get_active_by_product(p2.id)

    # Ítem A: DELETED_CONFIRMED → crea; PUBLISHED_CONFIRMED → avanza a B.
    window._on_delete_result(_delete_payload(p1.id, frozen_1.id, "DELETED_CONFIRMED"))
    assert window._queue.current_item().product_id == p1.id  # en creación
    assert window._matched_service.get(frozen_1.id).status == "deleted"

    window._on_publication_result(_publication_payload(p1.id, frozen_1.id, "PUBLISHED_CONFIRMED"))
    assert window._queue.current_item().product_id == p2.id  # solo ahora empieza B
    assert window._matched_service.get(frozen_1.id).status == "republished"

    # Ítem B: mismo ciclo → la cola termina.
    window._on_delete_result(_delete_payload(p2.id, frozen_2.id, "DELETED_CONFIRMED"))
    window._on_publication_result(_publication_payload(p2.id, frozen_2.id, "PUBLISHED_CONFIRMED"))

    assert window._queue is None
    assert window._queue_box.isHidden()
    assert window._matched_service.get(frozen_2.id).status == "republished"


# ---------------------------------------------------------------------------
# 4. Fallo/incertidumbre: pausa + decisión humana
# ---------------------------------------------------------------------------
def test_queue_pauses_on_uncertain_delete_and_skip_advances(window, monkeypatch):
    p1 = window._product_service.create(_product(title="A"))
    p2 = window._product_service.create(_product(title="B"))
    _fill_scan(window, [_scan(p1.id), _scan(p2.id)])
    window._reload_products()
    _select_products(window, [p1.id, p2.id])
    monkeypatch.setattr(QueuePrepDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        "app.gui.main_window.QMessageBox.information",
        lambda *a, **k: None,
    )

    window._on_queue_republish()
    frozen_1 = window._matched_service.get_active_by_product(p1.id)

    # El usuario decide "Omitir y continuar".
    monkeypatch.setattr(
        QueueFailureDialog,
        "exec",
        lambda self: setattr(self, "_choice", QueueFailureChoice.SKIP) or QDialog.DialogCode.Accepted,
    )

    window._on_delete_result(_delete_payload(p1.id, frozen_1.id, "DELETE_UNCERTAIN"))

    assert window._queue.current_item().product_id == p2.id  # avanzó tras omitir
    assert window._matched_service.get(frozen_1.id).status == "blocked"


def test_queue_stop_from_failure_cancels(window, monkeypatch):
    p1 = window._product_service.create(_product(title="A"))
    p2 = window._product_service.create(_product(title="B"))
    _fill_scan(window, [_scan(p1.id), _scan(p2.id)])
    window._reload_products()
    _select_products(window, [p1.id, p2.id])
    monkeypatch.setattr(QueuePrepDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        "app.gui.main_window.QMessageBox.information",
        lambda *a, **k: None,
    )

    window._on_queue_republish()
    frozen_1 = window._matched_service.get_active_by_product(p1.id)

    monkeypatch.setattr(
        QueueFailureDialog,
        "exec",
        lambda self: setattr(self, "_choice", QueueFailureChoice.STOP) or QDialog.DialogCode.Accepted,
    )

    window._on_delete_result(_delete_payload(p1.id, frozen_1.id, "DELETE_UNCERTAIN"))

    assert window._queue is None
    assert window._queue_box.isHidden()
    assert window._matched_service.get(frozen_1.id).status == "blocked"
    # B no se procesó (la cola se detuvo): su target sigue congelado/preconf.
    assert window._matched_service.get_active_by_product(p2.id) is not None


def test_queue_retry_verify_first_on_uncertain_publish(window, monkeypatch):
    p1 = window._product_service.create(_product(title="A"))
    _fill_scan(window, [_scan(p1.id)])
    window._reload_products()
    _select_products(window, [p1.id])

    calls = []
    monkeypatch.setattr(window, "_invoke_service", lambda method, payload: calls.append((method, payload)))
    monkeypatch.setattr(QueuePrepDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        "app.gui.main_window.QMessageBox.information",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        QueueFailureDialog,
        "exec",
        lambda self: setattr(self, "_choice", QueueFailureChoice.RETRY) or QDialog.DialogCode.Accepted,
    )

    window._on_queue_republish()
    frozen_1 = window._matched_service.get_active_by_product(p1.id)

    window._on_delete_result(_delete_payload(p1.id, frozen_1.id, "DELETED_CONFIRMED"))
    window._on_publication_result(_publication_payload(p1.id, frozen_1.id, "PUBLISH_UNCERTAIN"))

    # Reintentar = verificar primero (resume phase=publish), NUNCA re-publicar
    # a ciegas: el create_and_publish se llamó UNA vez (por el delete confirmado)
    # y el retry es resume_republish, no un segundo create_and_publish.
    creates = [p for m, p in calls if m == "create_and_publish"]
    assert len(creates) == 1
    resumes = [p for m, p in calls if m == "resume_republish"]
    assert resumes and resumes[0]["phase"] == "publish"


# ---------------------------------------------------------------------------
# 5. Guards: la edición/cambio de producto NO cancela targets de la cola
# ---------------------------------------------------------------------------
def test_selection_change_during_queue_does_not_cancel_targets(window, monkeypatch):
    p1 = window._product_service.create(_product(title="A"))
    p2 = window._product_service.create(_product(title="B"))
    _fill_scan(window, [_scan(p1.id), _scan(p2.id)])
    window._reload_products()
    _select_products(window, [p1.id, p2.id])
    monkeypatch.setattr(QueuePrepDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        "app.gui.main_window.QMessageBox.information",
        lambda *a, **k: None,
    )

    window._on_queue_republish()
    assert window._queue is not None and window._queue.state == RepublishQueueState.RUNNING

    # Cambiar la selección no cancela los targets congelados de la cola.
    window.products_list.setCurrentRow(0)
    window._on_products_selection_changed()

    assert window._matched_service.get_active_by_product(p1.id) is not None
    assert window._matched_service.get_active_by_product(p2.id) is not None


def test_cleanup_guard_keeps_frozen_targets(window, monkeypatch):
    p1 = window._product_service.create(_product(title="A"))
    _fill_scan(window, [_scan(p1.id)])
    window._reload_products()
    _select_products(window, [p1.id])
    monkeypatch.setattr(QueuePrepDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        "app.gui.main_window.QMessageBox.information",
        lambda *a, **k: None,
    )

    window._on_queue_republish()
    frozen = window._matched_service.get_active_by_product(p1.id)
    assert frozen is not None

    # Durante la cola, `_cleanup_dangling_targets` está desactivado.
    window._last_selected_product_id = p1.id
    window.products_list.clearSelection()
    window._cleanup_dangling_targets()
    assert window._matched_service.get_active_by_product(p1.id) is not None


# ---------------------------------------------------------------------------
# 6. Ítem no congelable: se omite y la cola continúa
# ---------------------------------------------------------------------------
def test_non_freezable_item_is_skipped_and_queue_continues(window, monkeypatch):
    p1 = window._product_service.create(_product(title="A"))
    p2 = window._product_service.create(_product(title="B"))
    _fill_scan(window, [_scan(p1.id), _scan(p2.id)])
    window._reload_products()
    _select_products(window, [p1.id, p2.id])

    real_freeze = window._freeze_target
    calls = []
    monkeypatch.setattr(window, "_invoke_service", lambda method, payload: calls.append((method, payload)))

    def fake_freeze(product, item_data):
        if product.id == p1.id:
            return real_freeze(product, item_data)
        return None  # el producto B no se puede congelar

    monkeypatch.setattr(window, "_freeze_target", fake_freeze)
    monkeypatch.setattr(QueuePrepDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        "app.gui.main_window.QMessageBox.information",
        lambda *a, **k: None,
    )

    window._on_queue_republish()

    # B se omitió y A se procesa; la cola no se detuvo.
    assert window._queue is not None
    assert window._queue.current_item().product_id == p1.id
    assert window._queue.items[1].status == QueueItemStatus.SKIPPED
    prepared = [p for m, p in calls if m == "prepare_delete"]
    assert len(prepared) == 1
    assert window._matched_service.get_active_by_product(p1.id) is not None