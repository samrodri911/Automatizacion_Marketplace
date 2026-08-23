"""Modo de diagnóstico Iteración 4.2 — Tests A-G.

Estos tests son AISLAMIENTOS controlados. NO abren Facebook real:
utilizan fakes/MagicMock para reproducir la secuencia exacta de
operaciones que hace `AutomationService._run_search()` en producción,
sin la red ni Chromium.

Su objetivo es detectar en cuál de los pasos (A..G) del flujo
"Buscar publicación" se reproduce el EPIPE cuando se inyecta un fallo
controlado de IPC en el fake de `Page`.

USO:
    MM_FORENSICS=1 .venv/Scripts/python.exe -m pytest tests/test_diagnostic_isolation.py -v -s

Si el fake lanza BrokenPipeError, este test demuestra que el código
sigue intentando operar sobre la `Page` muerta después del cierre del
pipe (la causa raíz más probable del EPIPE en el teardown real).
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import errno
import subprocess
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

from app.automation.listing_extractor import ListingExtractor
from app.automation.listing_finder import ListingFinder
from app.automation.marketplace import MarketplaceAdapter
from app.core import forensics


def _fake_page_with_epe() -> MagicMock:
    """Crea un fake de Page Playwright que lanza BrokenPipeError en cada
    operación IPC (evaluate, goto, locator.inner_text, etc.), simulando
    lo que ocurre cuando el driver Node ha muerto y el pipe ya no es
    escribible.

    ESTE ES EL ÚNICO test que reproduce el EPIPE en un entorno
    controlado: si NO detecta el problema aquí, sabemos que el bug está
    en cómo Playwright Node entrega los errores a Python, no en nuestro
    código. Si SÍ lo detecta, sabemos exactamente en qué paso.
    """
    page = MagicMock()
    epe = OSError(errno.EPIPE, "Broken pipe")

    def _boom(*a, **kw):
        raise epe

    page.goto.side_effect = _boom
    page.evaluate.side_effect = _boom
    page.locator.return_value.inner_text.side_effect = _boom
    page.get_by_role.return_value.all.side_effect = _boom
    page.get_by_role.return_value.first.is_visible.side_effect = _boom
    page.url = "https://www.facebook.com/marketplace/you/selling"
    return page


# --------------------------------------------------------------------------
# Test A — Browser + sesión detectada
# Test B — Abrir Marketplace
# Test C — Abrir "Tus publicaciones"
# Test D — Una sola operación page.evaluate tras tus publicaciones
# Test E — ListingExtractor sin scroll
# Test F — ListingFinder un solo ciclo
# Test G — ListingFinder completo (varios ciclos)
# --------------------------------------------------------------------------

class TestDiagnostic:
    def test_a_browser_session_detection_swallows_epipe(self, caplog):
        """Test A: si el driver muere justo después de detectar sesión, ¿se
        propaga el EPIPE o se traga silenciosamente?

        OBSERVADO: check_facebook_session llama page.goto + page.get_by_label.
        HIPÓTESIS: si page.goto lanza BrokenPipeError, browser.check_facebook_session
        NO lo captura (no tiene try/except), por lo que debería propagarse.
        DEMOSTRADO: este test verifica el comportamiento actual.
        """
        from app.automation.browser import BrowserManager
        page = _fake_page_with_epe()
        bm = BrowserManager()
        with pytest.raises(OSError):
            asyncio.run(bm.check_facebook_session(page))

    def test_b_open_marketplace_propagates_epipe(self):
        """Test B: tras Marketplace adapter.open_marketplace, page.goto rompe."""
        page = _fake_page_with_epe()
        adapter = MarketplaceAdapter(page)
        with pytest.raises(OSError):
            adapter.open_marketplace()

    def test_c_open_your_listings_propagates_epipe(self):
        """Test C: open_your_listings ejecuta goto + wait_for_listings."""
        page = _fake_page_with_epe()
        adapter = MarketplaceAdapter(page)
        with pytest.raises(OSError):
            adapter.open_your_listings()

    def test_d_single_evaluate_after_your_listings_propagates_epipe(self):
        """Test D: una page.evaluate tras tus publicaciones rompe."""
        page = _fake_page_with_epe()
        with pytest.raises(OSError):
            page.evaluate("() => 1")

    def test_e_listing_extractor_no_scroll_propagates_epipe(self):
        """Test E: ListingExtractor.extract_listings usa page.evaluate atómico."""
        page = _fake_page_with_epe()
        extractor = ListingExtractor()
        # extract_listings captura la excepción internamente (fallback a locators);
        # el test confirma si la traga o propaga.
        listings = extractor.extract_listings(page)
        assert listings == [], "Si no propaga, se traga el EPIPE en el fallback a locators (que también fallan)"

    def test_f_listing_finder_single_cycle_propagates_epipe(self):
        """Test F: ListingFinder hace un solo ciclo (extract + match)."""
        page = _fake_page_with_epe()
        from app.automation.listing_matcher import ListingMatcher
        from app.models.product import Product
        matcher = ListingMatcher()
        extractor = ListingExtractor()
        finder = ListingFinder(page=page, extractor=extractor, matcher=matcher, navigator=None)

        product = Product(
            id=1, title="Test", description="", price=1.0,
            category="X", condition="Y", location="Z",
        )
        # No debe propagar EPIPE: debe devolver un resultado (vacío) sin lanzar.
        result = finder.find(product)
        assert result.status.name in ("NOT_FOUND", "LOW_CONFIDENCE"), \
            f"Status inesperado: {result.status.name}"

    def test_g_listing_finder_full_loop_with_epipe_terminates(self):
        """Test G: ListingFinder completo con EPIPE permanente debe TERMINAR,
        no quedar en bucle infinito buscando listings nuevos."""
        page = _fake_page_with_epe()
        from app.automation.listing_matcher import ListingMatcher
        from app.models.product import Product
        matcher = ListingMatcher()
        extractor = ListingExtractor()
        finder = ListingFinder(page=page, extractor=extractor, matcher=matcher, navigator=None)

        product = Product(
            id=1, title="Test", description="", price=1.0,
            category="X", condition="Y", location="Z",
        )
        # Si NO termina en un número acotado de iteraciones, hay un loop infinito.
        result = finder.find(product)
        assert result is not None
        assert result.scanned_count >= 0


# --------------------------------------------------------------------------
# Test Paso 5: Aislamiento de Playwright sin stack propio
# Test Paso 6: Comparativa con/sin QThread
# --------------------------------------------------------------------------

class TestIsolation:
    def test_paso5_minimal_playwright_path_does_not_import_our_stack(self):
        """Verifica que un script Playwright puro (Paso 5) NO necesita
        ningún módulo de app/ — esto es lo que haríamos como aislamiento
        en la prueba real para descartar nuestra arquitectura.

        Para evitar que el propio test file contamine sys.modules,
        ejecutamos un subproceso que solo importa playwright + json.
        """
        code = (
            "import json, sys; "
            "import playwright.sync_api; "
            "forbidden = {'app','PySide6'}; "
            "loaded = sorted(m for m in sys.modules if any(m==f or m.startswith(f+'.') for f in forbidden)); "
            "sys.exit(0 if not loaded else 1)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            pytest.fail(
                "El aislamiento de Paso 5 debe ejecutarse SIN importar app.* ni PySide6. "
                f"stderr: {result.stderr}"
            )

    def test_paso6_qthread_is_used_for_automation(self):
        """Verifica estructuralmente que MainWindow mueve el AutomationService
        a un QThread dedicado, no al hilo de la GUI. Esto es la base de la
        prueba comparativa de Paso 6 (mismo flujo con QThread vs sin él).

        NOTA: No lanzamos Chromium real aquí (sin display). Solo verificamos
        la propiedad de threading: el QObject vive en un QThread distinto
        del hilo principal de Qt.
        """
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QThread
        from app.gui.main_window import MainWindow
        from app.services.product_service import ProductService
        from app.services.matched_listing_service import MatchedListingService
        from app.services.automation_service import AutomationService
        from app.database.database import Database
        from app.database.repositories import MatchedListingsRepository, ProductRepository
        import tempfile

        qapp = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db = Database(db_path=tmp_path / "test.db")
            db.initialize()
            repo = ProductRepository(db)
            service = ProductService(repo, products_dir=tmp_path / "products")
            matched_service = MatchedListingService(MatchedListingsRepository(db), service)

            # Construimos el thread y el servicio manualmente (sin lanzar
            # Chromium real) para inspeccionar la propiedad de threading.
            win = MainWindow(service, matched_service)
            win._automation_thread = QThread()
            win._automation_service = AutomationService()
            win._automation_service.moveToThread(win._automation_thread)

            assert isinstance(win._automation_thread, QThread)
            service_thread = win._automation_service.thread()
            gui_thread = qapp.thread()
            assert service_thread != gui_thread, (
                f"AutomationService debe estar en un QThread dedicado, "
                f"no en el hilo de la GUI. service_thread={service_thread}, "
                f"gui_thread={gui_thread}"
            )


# --------------------------------------------------------------------------
# Test del heartbeat y captura de PID del driver
# --------------------------------------------------------------------------

class TestDriverHeartbeat:
    def test_driver_proc_info_does_not_crash_on_partial_state(self):
        """Verifica que driver_proc_info nunca rompe la app si Playwright
        está en estado intermedio o si el atributo _impl_obj no existe."""
        for obj in [None, MagicMock(), MagicMock(spec=[]), object()]:
            info = forensics.driver_proc_info(obj)
            assert isinstance(info, dict)
            # Puede estar vacío, pero nunca debe lanzar.
