"""Prueba dirigida reproducible: patio de botones.

Secuencia EXACTA de la GUI al pulsar "Probar Marketplace" / "Comprobar sesión"
después de que la navegación inicial YA estaba en "Tus publicaciones":

    1) abrir sesión (como hace el arranque)
    2) marketplace
    3) tus publicaciones   <- la app queda aquí (funciona)
    4) volver a navegar tus publicaciones (lo que hace el botón)
    5) `listings_section_state` (lo que hace el finder al iniciar)
    6) scrolls + extract (lo que hace el botón "Buscar")

Con el perfil real (WebSockets de Facebook activos) para intentar reproducir
el EPIPE del driver con los WebSockets.

Uso:
    $env:MM_FORENSICS="1"
    python diagnostics/diag_button_flow.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.automation.browser import BrowserManager
from app.automation.listing_extractor import ListingExtractor
from app.automation.marketplace import MarketplaceAdapter
from app.core import forensics


def main() -> int:
    manager = BrowserManager()
    page = manager.start()

    adapter = MarketplaceAdapter(page)
    extractor = ListingExtractor()

    steps = {
        "session": lambda: print(f"  sesión: {manager.check_facebook_session(page).detail}"),
        "marketplace": lambda: print(f"  marketplace: {adapter.open_marketplace().detail}"),
        "tus_listings": lambda: print(f"  tus list: {adapter.open_your_listings().found}"),
    }

    try:
        # 1) Arranque (igual que el arranque automático)
        print("[1] Arranque: sesión -> marketplace -> tus publicaciones")
        for name, fn in steps.items():
            print(f"  -> {name}", flush=True)
            fn()
            print(f"     driver alive={forensics.driver_alive(manager._playwright)}", flush=True)

        time.sleep(2)

        # 2) RESUMÉ: esto es lo que se desencadena al pulsar un botón,
        #    estando ALREADY en tus publicaciones.
        for rep in range(3):
            print(f"[2.{rep}] REPETIR 'tus publicaciones' (simula pulsar botón)", flush=True)
            forensics.evt(
                "diag.button",
                f"rep={rep} driver={forensics.driver_proc_info(manager._playwright)}",
            )
            st = adapter.open_your_listings()
            print(f"     repetido: found={st.found} driver={forensics.driver_alive(manager._playwright)}", flush=True)

            st = adapter.listings_section_state()
            print(f"     state.found={st.found} driver={forensics.driver_alive(manager._playwright)}", flush=True)

            listings = extractor.extract_listings(page)
            print(f"     extraídos={len(listings)} driver={forensics.driver_alive(manager._playwright)}", flush=True)

            moved = adapter.scroll_feed()
            print(f"     scroll={moved} driver={forensics.driver_alive(manager._playwright)}", flush=True)
            time.sleep(1.5)
    except Exception as exc:
        forensics.evt("diag.error", repr(exc))
        print(f"EXCEPCIÓN: {exc!r}", flush=True)
        print(f"  driver alive tras excepción={forensics.driver_alive(manager._playwright)}", flush=True)
        try:
            page.goto("about:blank", wait_until="domcontentloaded")
            print("  tras excepción, goto about:blank OK (pipe NO cerrado)", flush=True)
            return 1
        except Exception as exc2:
            print(f"  tras excepción, goto about:blank FALLO -> pipe cerrado: {exc2!r}", flush=True)
            return 2
    else:
        print("SECUENCIA COMPLETA SIN EPIPE", flush=True)
        return 0
    finally:
        manager.stop()


if __name__ == "__main__":
    os.environ.setdefault("MM_FORENSICS", "1")
    sys.exit(main())