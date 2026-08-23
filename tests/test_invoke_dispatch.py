"""Tests del despacho GUI → AutomationService por señales Qt (fase actual).

Problema corregido: `QMetaObject.invokeMethod(service, "slot", QueuedConnection,
dict)` lanza `TypeError: wrong argument types` en PySide6. La GUI ahora envía
las operaciones por señales `Signal(object)`/`Signal(int)` (patrón
`search_listing_requested`), que sí soportan objetos Python entre hilos.

Estos tests verifican:
- el mapa `_SERVICE_REQUEST_SIGNALS` está completo (método + señal existen);
- `MainWindow._invoke_service` despacha realmente al slot del servicio
  (sin TypeError), con un `AutomationService` REAL pero sin hilo ni navegador;
- el flujo de búsqueda sigue funcionando por señal (slot `search_listing`);
- ningún módulo de `app/gui` importa Playwright (la GUI nunca toca el driver).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.automation.states import AutomationState
from app.database.database import Database
from app.database.repositories import (
    AutomationRunRepository,
    MatchedListingsRepository,
    ProductRepository,
)
from app.gui.main_window import MainWindow, _SERVICE_REQUEST_SIGNALS
from app.services.automation_service import AutomationService
from app.services.matched_listing_service import MatchedListingService
from app.services.product_service import ProductService


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, tmp_path):
    """MainWindow con un AutomationService REAL (sin hilo, sin navegador)."""
    db = Database(db_path=tmp_path / "test_dispatch.db")
    db.initialize()
    repo = ProductRepository(db)
    product_service = ProductService(
        repo, products_dir=tmp_path / "products", run_repository=AutomationRunRepository(db)
    )
    matched_service = MatchedListingService(MatchedListingsRepository(db), product_service)
    svc = AutomationService()
    win = MainWindow(product_service, matched_service)
    win._automation_service = svc  # noqa: SLF001 (acceso intencional en test)
    return win


# ---------------------------------------------------------------------------
# #15: el mapa de señales está completo (método y señal existen)
# ---------------------------------------------------------------------------
def test_request_signal_map_is_complete():
    svc = AutomationService()
    assert _SERVICE_REQUEST_SIGNALS
    for method, signal_name in _SERVICE_REQUEST_SIGNALS.items():
        assert hasattr(svc, method), f"El servicio no tiene el slot {method}"
        assert hasattr(svc, signal_name), f"El servicio no tiene la señal {signal_name}"


# ---------------------------------------------------------------------------
# #15 y #16: _invoke_service despacha al slot real sin TypeError
# ---------------------------------------------------------------------------
def test_invoke_service_dispatches_freeze_match_without_typeerror(window):
    win = window
    # Anteriormente esto lanzaba:
    #   TypeError: QMetaObject.invokeMethod called with wrong argument types
    win._invoke_service(
        "freeze_match",
        {
            "product_id": 1,
            "title": "iPhone 13 128GB",
            "url": "https://www.facebook.com/marketplace/item/777",
            "reference": "777",
            "confidence": "HIGH",
        },
    )
    assert win._automation_service.state == AutomationState.MATCHED


def test_invoke_service_dispatches_mark_edit_saved(window):
    win = window
    win._invoke_service("mark_edit_saved", 42)
    assert win._automation_service.state == AutomationState.AWAITING_REPUBLISH_CONFIRM


def test_invoke_service_dispatches_mark_editing(window):
    win = window
    win._invoke_service("mark_editing", 42)
    assert win._automation_service.state == AutomationState.EDITING_PRODUCT


def test_invoke_service_maps_prepare_delete_to_delete_listing_requested(window):
    """prepare_delete se despacha por la señal existente delete_listing_requested."""
    win = window
    emitted = []
    win._automation_service.delete_listing_requested.connect(lambda payload: emitted.append(payload))
    win._invoke_service(
        "prepare_delete",
        {
            "product": {
                "product_id": 1,
                "title": "iPhone 13 128GB",
                "price": 1850000.0,
                "enabled": True,
            },
            "matched_target": {
                "matched_id": 9,
                "title": "iPhone 13 128GB",
                "url": "https://www.facebook.com/marketplace/item/777",
                "reference": "777",
                "confidence": "HIGH",
            },
        },
    )
    assert emitted
    assert emitted[0]["matched_target"]["reference"] == "777"


# ---------------------------------------------------------------------------
# #18: el flujo de búsqueda/matching sigue funcionando por señal
# ---------------------------------------------------------------------------
def test_search_requested_signal_reaches_search_slot(qapp, monkeypatch):
    svc = AutomationService()
    captured: list = []
    monkeypatch.setattr(svc, "_run_search", lambda product: captured.append(product))

    svc.search_listing_requested.emit(
        {
            "product_id": 7,
            "title": "iPad Pro",
            "description": "",
            "price": 3_500_000,
            "category": "",
            "condition": "",
            "location": "",
            "tags": [],
            "images": [],
            "enabled": True,
            "marketplace_url": None,
            "marketplace_reference": None,
        }
    )
    assert len(captured) == 1
    assert captured[0].id == 7
    assert captured[0].title == "iPad Pro"


# ---------------------------------------------------------------------------
# #17: la GUI nunca importa Playwright (el driver vive en el worker)
# ---------------------------------------------------------------------------
def test_gui_source_does_not_import_playwright():
    gui_dir = Path(__file__).resolve().parents[1] / "app" / "gui"
    offenders = []
    for py in sorted(gui_dir.glob("*.py")):
        for line in py.read_text(encoding="utf-8").splitlines():
            stripped = line.lstrip()
            if stripped.startswith("import playwright") or stripped.startswith("from playwright"):
                offenders.append(f"{py.name}: {stripped}")
    assert not offenders, f"La GUI no debe importar Playwright:\n{chr(10).join(offenders)}"


def test_gui_process_does_not_instantiate_browser_without_automation():
    """Construir la ventana sola NO debe instanciar el worker/browser.

    `playwright` puede cargarse transitivamente (vía automation_service), pero
    la GUI no debe crear el servicio de automatización ni su navegador por sí
    sola: eso ocurre SOLO al pulsar "Abrir navegador" (_on_connect_facebook).
    """
    script = """import os, sys, tempfile
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from app.database.database import Database
from app.database.repositories import (
    AutomationRunRepository, MatchedListingsRepository, ProductRepository,
)
from app.services.product_service import ProductService
from app.services.matched_listing_service import MatchedListingService
from app.gui.main_window import MainWindow

app = QApplication([])
with tempfile.TemporaryDirectory() as tmp:
    db = Database(db_path=Path(tmp) / 't.db')
    db.initialize()
    ps = ProductService(
        ProductRepository(db),
        products_dir=Path(tmp) / 'p',
        run_repository=AutomationRunRepository(db),
    )
    ms = MatchedListingService(MatchedListingsRepository(db), ps)
    w = MainWindow(ps, ms)
    # La GUI sola no crea el servicio ni arranca hilo de automatización.
    if w._automation_service is not None:
        sys.exit(2)
    if w._automation_thread is not None:
        sys.exit(3)
sys.exit(0)
"""
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "probe_gui_browser.py"
        script_path.write_text(script, encoding="utf-8")
        project_root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "PYTHONPATH": str(project_root)}
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    if result.returncode != 0:
        pytest.fail(
            "La GUI sola no debe crear el servicio de automatización ni su navegador. "
            f"stderr: {result.stderr.strip()}"
        )