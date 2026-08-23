"""
Prueba real del nuevo flujo: Escaneo automático de todas las publicaciones
y matching por lote contra los productos de SQLite.
100% solo lectura: NO elimina nada.
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from playwright.sync_api import sync_playwright

from app.automation.listing_extractor import ListingExtractor
from app.automation.listing_matcher import ListingMatcher
from app.automation.listing_scanner import ListingScanner
from app.automation.marketplace import MarketplaceAdapter
from app.core.config import BROWSER_PROFILE_DIR, DB_PATH
from app.database.database import Database
from app.database.repositories import ProductRepository
from app.models.product import Product


def test_real_auto_scan() -> None:
    print("\n" + "=" * 70)
    print("  PRUEBA REAL: ESCANEO AUTOMÁTICO Y MATCHING POR LOTE")
    print("=" * 70)

    db = Database(DB_PATH)
    repo = ProductRepository(db)
    products = repo.list_all()

    # Si la BD solo tiene 1 producto, añadimos para la prueba en memoria otros productos
    # representativos para ver el matching contra los 5 items reales de Facebook
    test_products = list(products)
    if len(test_products) == 1:
        # Añadir productos de prueba en memoria (no persistidos) para validar el batch matching
        test_products.append(
            Product(
                id=2,
                title="Microondas General Electric con Plato Giratorio – 20L Aprox",
                description="Microondas",
                price=130000.0,
                category="Electrodomésticos",
                condition="Usado",
                location="Cali",
            )
        )
        test_products.append(
            Product(
                id=3,
                title="Teclado Gamer Unitech RGB – Membrana",
                description="Teclado gamer",
                price=40000.0,
                category="Computación",
                condition="Usado",
                location="Cali",
            )
        )
        test_products.append(
            Product(
                id=4,
                title="Teclado HP Original – USB – Color Verde",
                description="Teclado de oficina",
                price=35000.0,
                category="Computación",
                condition="Usado",
                location="Cali",
            )
        )
        test_products.append(
            Product(
                id=5,
                title="PlayStation 5 Digital",
                description="Consola inexistente en FB",
                price=2000000.0,
                category="Videojuegos",
                condition="Nuevo",
                location="Cali",
            )
        )

    print(f"\n[*] Productos a evaluar ({len(test_products)} productos):")
    for p in test_products:
        print(f"    - ID {p.id}: {p.title} (${p.price:,.0f})")

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
        scanner = ListingScanner(page=page, extractor=extractor, matcher=matcher, navigator=adapter)

        print("\n[*] Ejecutando escaneo automático en vivo...")
        batch_res = scanner.scan_and_match(test_products)

        print("\n" + "=" * 70)
        print("  RESULTADO DEL ESCANEO AUTOMÁTICO")
        print("=" * 70)
        print(f"  Total publicaciones detectadas en Facebook: {batch_res.total_listings}")
        print(f"  Coincidencias seguras (HIGH - Auto-seleccionadas): {batch_res.matched_high_count}")
        print(f"  Coincidencias dudosas (MEDIUM): {batch_res.matched_medium_count}")
        print(f"  Sin coincidencia (NO_MATCH / LOW): {batch_res.unmatched_count}")
        print("-" * 70)

        print("\n  LISTA DE PUBLICACIONES EN LA GUI (Simulacion):")
        for idx, item in enumerate(batch_res.items, 1):
            check_box = "[X]" if item.auto_selected else "[ ]"
            listing = item.listing
            conf = item.confidence
            prod_info = f" (Coincide con: '{item.matched_product_title}')" if item.matched_product_title else ""
            warn_info = f" [{', '.join(item.warnings)}]" if item.warnings else ""
            print(f"  {check_box} {idx}. {listing.title} | {listing.price_raw or listing.price} | Confianza: {conf}{prod_info}{warn_info}")

        context.close()
        print("\nPrueba completada.")


if __name__ == "__main__":
    test_real_auto_scan()
