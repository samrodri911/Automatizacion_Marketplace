"""Tests de estabilización del ciclo de vida y manejo de IPC de Playwright (Iteración 4.1).

Verifican:
1. Ownership de recursos: ListingFinder y MarketplaceAdapter no cierran ni detienen el navegador.
2. Extracción atómica en ListingExtractor (1 sola llamada JS evaluate en lugar de bucles IPC desincronizados).
3. Recolección atómica en MarketplaceAdapter._collect_snippets.
4. Captura limpia y resiliente de errores de desconexión IPC (EPIPE / TargetClosed).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.automation.browser import BrowserManager
from app.automation.listing_extractor import ListingExtractor
from app.automation.marketplace import MarketplaceAdapter
from app.core.exceptions import AutomationError
from app.services.automation_service import AutomationService


def test_listing_extractor_uses_atomic_js_evaluation():
    """Verifica que ListingExtractor usa page.evaluate en 1 sola llamada IPC."""
    page = MagicMock()
    page.evaluate.return_value = [
        {"text": "iPhone 13 128GB\n$1.850.000", "url": "https://www.facebook.com/marketplace/item/100", "image_srcs": ["https://img.jpg"]}
    ]

    extractor = ListingExtractor()
    listings = extractor.extract_listings(page)

    assert len(listings) == 1
    assert listings[0].title == "iPhone 13 128GB"
    assert listings[0].reference == "100"
    # Verificar que solo se llamó evaluate (1 sola llamada IPC, no bucles get_by_role)
    page.evaluate.assert_called()
    page.get_by_role.assert_not_called()


def test_marketplace_adapter_snippets_uses_atomic_js_evaluation():
    """Verifica que MarketplaceAdapter._collect_snippets usa page.evaluate en 1 sola llamada IPC."""
    page = MagicMock()
    page.evaluate.return_value = ["Tus publicaciones", "Activos", "Vendidos"]

    adapter = MarketplaceAdapter(page)
    snippets = adapter._collect_snippets()

    assert "Tus publicaciones" in snippets
    assert "Activos" in snippets
    page.evaluate.assert_called_once()
    page.get_by_role.assert_not_called()


def test_browser_manager_stop_detaches_websockets_before_closing_page():
    """Verifica que stop() desconecta los WebSockets de cada página (anti-EPIPE)
    antes de cerrarla, para que el driver no escriba eventos en un pipe cerrado."""
    bm = BrowserManager()
    page = AsyncMock()
    bm._context = AsyncMock()
    bm._context.pages = [page]
    bm._playwright = AsyncMock()

    asyncio.run(bm.stop())

    # Navegación a about:blank para cerrar conexiones, ANTES de page.close()
    page.goto.assert_awaited_once_with("about:blank", wait_until="domcontentloaded", timeout=3000)
    page.wait_for_timeout.assert_awaited_once_with(200)
    call_names = [mc[0] for mc in page.method_calls]
    assert call_names.index("goto") < call_names.index("close")


def test_browser_manager_stop_tolerates_goto_failure():
    """Si la desconexión de WebSockets falla o la página ya está cerrada,
    stop() sigue cerrando la página sin dejar morir el teardown."""
    bm = BrowserManager()
    page = AsyncMock()
    page.goto = AsyncMock(side_effect=Exception("Target closed"))
    context = AsyncMock()
    context.pages = [page]
    bm._context = context
    bm._playwright = AsyncMock()

    asyncio.run(bm.stop())

    page.close.assert_awaited_once()
    context.close.assert_awaited_once()


def test_browser_manager_ownership_and_is_running_check():
    """Verifica que is_running es un flag seguro de ciclo de vida (sin tocar
    objetos asyncio): evita fallos de cross-thread al consultar el contexto."""
    bm = BrowserManager()
    assert bm.is_running is False
    bm._running = True  # noqa: SLF001
    assert bm.is_running is True
    bm._running = False  # noqa: SLF001
    assert bm.is_running is False


def test_automation_service_catches_disconnection_errors_cleanly(monkeypatch):
    """Verifica que si la búsqueda sufre una desconexión (EPIPE/TargetClosed),
    AutomationService captura la excepción y no rompe el proceso."""
    service = AutomationService()
    service._page = MagicMock()  # noqa: SLF001
    service._page.url = "https://www.facebook.com/marketplace/you/selling"  # noqa: SLF001
    service._browser_manager = MagicMock()  # noqa: SLF001
    service._browser_manager.is_running = True  # noqa: SLF001

    # Simular TargetClosed / EPIPE en finder.find
    mock_product = MagicMock()
    mock_product.id = 1
    mock_product.title = "Producto Test"
    mock_product.marketplace_url = None

    monkeypatch.setattr(
        "app.automation.listing_finder.ListingFinder.find",
        MagicMock(side_effect=Exception("Error: EPIPE: broken pipe, write")),
    )

    with pytest.raises(AutomationError) as exc_info:
        service._run_search(mock_product)  # noqa: SLF001

    assert "desconectó" in str(exc_info.value).lower()
