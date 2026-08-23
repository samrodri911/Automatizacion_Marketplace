"""Pruebas de aislamiento A-G (Iteración 4.2, PASO 4).

Uso (en Windows REAL):
    $env:MM_FORENSICS="1"
    python diagnostics/test_isolated.py A        # Test A: sesión
    python diagnostics/test_isolated.py B        # Test B: Marketplace
    python diagnostics/test_isolated.py C        # Test C: Tus publicaciones
    python diagnostics/test_isolated.py D        # Test D: una page.evaluate tras C
    python diagnostics/test_isolated.py E        # Test E: ListingExtractor sin scroll
    python diagnostics/test_isolated.py F        # Test F: ListingFinder 1 ciclo
    python diagnostics/test_isolated.py G        # Test G: ListingFinder completo

Cada test se detiene ahí (no sigue al siguiente paso), para aislar en qué
operación aparece por primera vez el EPIPE. Usa el perfil persistente real
(data/browser_profile) y Chromium visible.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.automation.browser import BrowserManager
from app.automation.listing_extractor import ListingExtractor
from app.automation.listing_finder import ListingFinder
from app.automation.marketplace import MarketplaceAdapter
from app.automation.listing_matcher import ListingMatcher, MatchStatus
from app.core import forensics
from app.core.config import facebook_config
from app.models.product import Product


def _wait(seconds: float = 3.0) -> None:
    """Espera a que Facebook asiente y registra quién está vivo."""
    forensics.evt("wait", f"{seconds}s")
    time.sleep(seconds)


def test_a(manager: BrowserManager, page) -> None:
    print("TEST A: abrir browser + Facebook + detectar sesión + esperar")
    status = manager.check_facebook_session(page)
    print(f"Sesión detectada: {status.logged_in} ({status.detail})")
    _wait(5.0)


def test_b(manager: BrowserManager, page) -> None:
    print("TEST B: abrir Marketplace + esperar")
    adapter = MarketplaceAdapter(page)
    result = adapter.open_marketplace()
    print(f"Marketplace: {result.ok} ({result.detail})")
    _wait(5.0)


def test_c(manager: BrowserManager, page) -> None:
    print("TEST C: abrir Tus publicaciones + esperar")
    adapter = MarketplaceAdapter(page)
    state = adapter.open_your_listings()
    print(f"'Tus publicaciones': {state.found} ({state.reason})")
    _wait(5.0)


def test_d(manager: BrowserManager, page) -> None:
    print("TEST D: Tus publicaciones + UNA page.evaluate + esperar")
    adapter = MarketplaceAdapter(page)
    state = adapter.open_your_listings()
    print(f"'Tus publicaciones': {state.found}")
    value = page.evaluate("document.title")
    print(f"page.evaluate -> {value!r}")
    _wait(5.0)


def test_e(manager: BrowserManager, page) -> None:
    print("TEST E: Tus publicaciones + ListingExtractor sin scroll")
    adapter = MarketplaceAdapter(page)
    adapter.ensure_listings_section()
    extractor = ListingExtractor()
    listings = extractor.extract_listings(page)
    print(f"Listings extraídos: {len(listings)}")
    _wait(5.0)


def test_f(manager: BrowserManager, page) -> None:
    print("TEST F: Tus publicaciones + ListingFinder (un solo ciclo)")
    product = Product(
        title="PRUEBA_AISLAMIENTO_DIAG",
        description="Prueba",
        price=10.0,
        category="Electrónica",
        condition="Nuevo",
        location="Madrid",
        images=["img.png"],
    )
    adapter = MarketplaceAdapter(page)
    finder = ListingFinder(page=page, navigator=adapter, matcher=ListingMatcher())
    result = finder.find(product, on_phase=lambda phase: print(f"  fase: {phase}"))
    print(f"Resultado: {result.status.name} (scanned={result.scanned_count})")
    _wait(3.0)


def test_g(manager: BrowserManager, page) -> None:
    print("TEST G: Tus publicaciones + ListingFinder completo")
    adapter = MarketplaceAdapter(page)
    finder = ListingFinder(page=page, navigator=adapter, matcher=ListingMatcher())
    product = Product(
        title="PRUEBA DE TESTF",
        description="Prueba G",
        price=10.0,
        category="Electrónica",
        condition="Nuevo",
        location="Madrid",
        images=["img.png"],
    )
    result = finder.find(product)
    print(f"Resultado: {result.status.name} (scanned={result.scanned_count})")
    _wait(3.0)


def main() -> int:
    step = (sys.argv[1] if len(sys.argv) > 1 else "A").upper().strip()
    forensics.evt("test.step", step)

    manager = BrowserManager()
    page = manager.start()

    try:
        {
            "A": test_a,
            "B": test_b,
            "C": test_c,
            "D": test_d,
            "E": test_e,
            "F": test_f,
            "G": test_g,
        }[step](manager, page)
        print(f"== Test {step} TERMINADO sin EPIPE (el driver sigue vivo: {forensics.driver_alive(manager._playwright)})")
    finally:
        manager.stop()

    return 0


if __name__ == "__main__":
    os.environ.setdefault("MM_FORENSICS", "1")
    sys.exit(main())