"""
Prueba real de búsqueda y matching de producto en Facebook Marketplace.
Verifica que ListingFinder encuentra el producto real y calcula el MatchResult.
100% solo lectura: NO elimina nada.
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from playwright.sync_api import sync_playwright

from app.automation.browser import BrowserManager
from app.automation.listing_extractor import ListingExtractor
from app.automation.listing_finder import ListingFinder
from app.automation.listing_matcher import ListingMatcher
from app.automation.marketplace import MarketplaceAdapter
from app.core.config import BROWSER_PROFILE_DIR, DB_PATH
from app.database.database import Database
from app.database.repositories import ProductRepository


def run_real_find_test() -> None:
    print("\n" + "=" * 70)
    print("  PRUEBA REAL DE BÚSQUEDA Y MATCHING (SOLO LECTURA)")
    print("=" * 70)

    db = Database(DB_PATH)
    repo = ProductRepository(db)
    products = repo.list_all()
    if not products:
        print("[-] No hay productos en la base de datos local para probar.")
        return

    target_product = products[0]
    # Crear copia con el precio exacto del listing para verificar match HIGH
    from app.models.product import Product
    exact_product = Product(
        id=target_product.id,
        title=target_product.title,
        description=target_product.description,
        price=600000.0,
        category=target_product.category,
        condition=target_product.condition,
        location=target_product.location,
    )
    print(f"\n[*] Producto objetivo de la BD (con precio exacto de Facebook):")
    print(f"    ID:          {exact_product.id}")
    print(f"    Título:      {exact_product.title}")
    print(f"    Precio:      {exact_product.price}")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--start-maximized"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        adapter = MarketplaceAdapter(page)
        extractor = ListingExtractor()
        matcher = ListingMatcher()
        finder = ListingFinder(page=page, extractor=extractor, matcher=matcher, navigator=adapter)

        print("\n[*] Ejecutando finder.find() en vivo...")
        result = finder.find(exact_product)

        print("\n" + "=" * 70)
        print("  RESULTADO DEL FINDER")
        print("=" * 70)
        print(f"  Status:             {result.status.name}")
        print(f"  Stopped for:        {result.stopped_for}")
        print(f"  Listings escaneados:{result.scanned_count}")
        print(f"  Candidatos evaluados:{len(result.outcome.candidates)}")

        if result.best_match:
            best = result.best_match
            print(f"\n[+] MEJOR COINCIDENCIA:")
            print(f"    Título listing:  {best.listing.title}")
            print(f"    Precio listing:  {best.listing.price} ({best.listing.price_raw})")
            print(f"    URL:             {best.listing.url}")
            print(f"    Referencia:      {best.listing.reference}")
            print(f"    Confianza:       {best.confidence.name}")
            print(f"    Puntaje:         {best.score}")
            print(f"    Razones:         {best.reasons}")
            print(f"    Advertencias:    {best.warnings}")
        else:
            print("\n[-] No hubo mejor coincidencia.")

        context.close()
        print("\nPrueba completada.")


if __name__ == "__main__":
    run_real_find_test()
