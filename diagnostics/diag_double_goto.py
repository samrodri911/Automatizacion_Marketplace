"""Prueba dirigida: navegar DOS veces a la misma URL de Marketplace.

El usuario reporta que el EPIPE ocurre al pulsar un botón (Buscar/Probar)
Despuids de que la navegación inicial ya funcionó. Diferencia con todos
los tests anteriores: la navegación se repite sobre una página ya cargada.

Uso (Windows, perfil real con sesión):
    $env:MM_FORENSICS="1"
    python diagnostics/diag_double_goto.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import forensics
from app.core.config import BROWSER_PROFILE_DIR, facebook_config


def main() -> int:
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(BROWSER_PROFILE_DIR),
        headless=False,
        viewport={"width": 1280, "height": 900},
        args=["--start-maximized"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(facebook_config.action_timeout_ms)
    page.set_default_navigation_timeout(facebook_config.navigation_timeout_ms)

    urls = [
        facebook_config.marketplace_url,
        facebook_config.your_listings_url,
        facebook_config.marketplace_url,
        facebook_config.your_listings_url,
    ]

    try:
        for i, url in enumerate(urls):
            forensics.evt(
                "diag.usuario.goto",
                f"step={i} url={url} driver={forensics.driver_proc_info(pw)}",
            )
            print(f"[{i}] goto {url} (driver alive={forensics.driver_alive(pw)})", flush=True)
            page.goto(url, wait_until="domcontentloaded", timeout=facebook_config.navigation_timeout_ms)
            print(f"[{i}] goto OK  (driver alive={forensics.driver_alive(pw)})", flush=True)
            time.sleep(2)
    except Exception as exc:
        forensics.evt("diag.erro", repr(exc))
        print(f"EXCEPCION: {exc!r}", flush=True)
        print(f"  driver alive DESPUES={forensics.driver_alive(pw)}", flush=True)
        # intentar una llamada posterior para ver si el driver sigue útil
        try:
            page.goto("about:blank", wait_until="domcontentloaded")
            print("  tras excepcion, goto about:blank OK", flush=True)
        except Exception as exc2:
            print(f"  tras excepcion, goto about:blank FALLO: {exc2!r}", flush=True)
            return 2

    try:
        page.close()
        ctx.close()
    finally:
        pw.stop()
    return 0


if __name__ == "__main__":
    os.environ.setdefault("MM_FORENSICS", "1")
    sys.exit(main())