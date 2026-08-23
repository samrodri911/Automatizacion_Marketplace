"""Tests de integración GUI ↔ señales de eliminación (Iteración 4).

Prueban el diálogo de confirmación de eliminación y la interacción con
`MainWindow` sin abrir un navegador real.

Desde la fase "UI de un solo botón", la eliminación ya no tiene un botón
propio: vive integrada en "🔄 Republicar". Estas pruebas cubren el diálogo
de confirmación legacy (ruta `from_republish=False`) y el despacho de la
señal de ejecución de eliminación.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QPushButton

from app.database.database import Database
from app.database.repositories import AutomationRunRepository, MatchedListingsRepository, ProductRepository
from app.gui.delete_confirm_dialog import DeleteConfirmDialog
from app.gui.main_window import MainWindow
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
    execute_delete_requested = Signal(object)


@pytest.fixture
def db(tmp_path):
    database = Database(db_path=tmp_path / "test_gui_del.db")
    database.initialize()
    return database


@pytest.fixture
def window(qapp, db, tmp_path):
    repository = ProductRepository(db)
    run_repo = AutomationRunRepository(db)
    service = ProductService(repository, products_dir=tmp_path / "products", run_repository=run_repo)
    matched_service = MatchedListingService(MatchedListingsRepository(db), service)
    fake = FakeAutomation()
    win = MainWindow(service, matched_service)
    win._automation_service = fake  # noqa: SLF001
    return win


# --------------------------------------------------------------------------
# Diálogo DeleteConfirmDialog
# --------------------------------------------------------------------------
def test_delete_confirm_dialog_has_action_buttons(qapp):
    ready_payload = {
        "product_title": "iPhone 13 128GB",
        "listing_title": "iPhone 13 128GB",
        "price": "2.300.000",
        "url": "https://facebook.com/item/123",
        "confidence": "HIGH",
    }
    diag = DeleteConfirmDialog(ready_payload=ready_payload)
    buttons = diag.findChildren(QPushButton)
    assert len(buttons) == 2
    button_texts = [b.text() for b in buttons]
    assert "Cancelar" in button_texts
    assert any("Eliminar" in t for t in button_texts)
