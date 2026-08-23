"""Paso 5 — Aislamiento Playwright PURO (sin nuestra arquitectura).

Reproduce el flujo:
    sync_playwright → launch_persistent_context → page → goto Facebook →
    goto Marketplace → goto /you/selling → page.evaluate(...)

SIN usar:
    ListingFinder, ListingExtractor, MarketplaceAdapter,
    AutomationService, PySide6.

Si esta prueba reproduce el EPIPE: el problema está DEBAJO de nuestra
arquitectura (probablemente en Playwright/Chromium/Node).
Si NO lo reproduce: el problema está en NUESTRA arquitectura/QThread.

USO (en Windows real, con sesión de Facebook):
    MM_FORENSICS=1 .venv/Scripts/python.exe tests/diagnostic_paso5_playwright_puro.py
"""

from __future__ import annotations

import os
import sys
import time

# Forzar la instrumentación forense.
os.environ["MM_FORENSICS"] = "1"

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core import forensics  # noqa: E402

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "browser_profile")


def _step(name: str, fn):
    """Ejecuta un paso aislado con instrumentación forense."""
    forensics.evt("paso5.step", name)
    t0 = time.perf_counter()
    try:
        result = fn()
        dt = (time.perf_counter() - t0) * 1000
        forensics.evt("paso5.step.done", f"{name} dt_ms={dt:.0f}")
        return result
    except Exception as exc:
        dt = (time.perf_counter() - t0) * 1000
        forensics.evt("paso5.step.fail", f"{name} dt_ms={dt:.0f} exc={type(exc).__name__}: {exc}")
        raise


def main() -> int:
    print(f"[PASO 5] Profile dir: {PROFILE_DIR}")
    print(f"[PASO 5] MM_FORENSICS = {os.environ.get('MM_FORENSICS')}")
    print("[PASO 5] Iniciando Playwright PURO (sin nuestra arquitectura)...")

    with sync_playwright() as p:
        forensics.evt("paso5.playwright.start", f"driver={forensics.driver_proc_info(p)}")

        # Paso 1: lanzar contexto persistente.
        def _launch():
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=False,
                viewport={"width": 1280, "height": 900},
            )
            return ctx
        context = _step("launch_persistent_context", _launch)
        forensics.evt("paso5.browser.launched", f"pid={os.getpid()} ctx_id={id(context)}")

        # Paso 2: navegar a Facebook.
        page = context.pages[0] if context.pages else context.new_page()
        _step("goto facebook.com", lambda: page.goto("https://www.facebook.com/", wait_until="domcontentloaded"))
        _step("evaluate 1 (sin parámetros)", lambda: page.evaluate("() => document.title"))

        # Paso 3: ir a Marketplace.
        _step("goto /marketplace/", lambda: page.goto("https://www.facebook.com/marketplace/", wait_until="domcontentloaded"))
        _step("evaluate 2 (scrollY)", lambda: page.evaluate("() => window.scrollY"))

        # Paso 4: ir a "Tus publicaciones".
        _step("goto /you/selling", lambda: page.goto("https://www.facebook.com/marketplace/you/selling", wait_until="domcontentloaded"))
        _step("evaluate 3 (count links)", lambda: page.evaluate("() => document.querySelectorAll('a[href*=item]').length"))

        # Paso 5: varias operaciones IPC consecutivas para estresar el pipe.
        for i in range(5):
            _step(f"evaluate loop {i}", lambda: page.evaluate("() => document.querySelectorAll('a').length"))

        forensics.evt("paso5.completed")
        print("[PASO 5] Flujo completado sin excepción. EPIPE NO reproducido en este flujo básico.")
        context.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
