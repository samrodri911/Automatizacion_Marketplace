"""PASO 5: aislamiento de Facebook — Playwright puro SIN nuestra arquitectura.

Solo usa `sync_playwright` + contexto persistente real, sin AutomationService,
sin MarketplaceAdapter/ListingFinder/ListingExtractor, sin QThread y sin PySide6.

Uso (Windows, con sesión real de Facebook):
    $env:MM_FORENSICS="1"
    python diag_plain_playwright.py

Salida esperada si el problema está FUERA de nuestra arquitectura:
    EPIPE aparece aquí también  -> driver/Playwright/HD node embebido.
Si NO produce EPIPE -> problema en nuestra arquitectura/lifecycle/QThread.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import forensics
from app.core.config import BROWSER_PROFILE_DIR, facebook_config
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def main() -> int:
    import tempfile

    from playwright.sync_api import sync_playwright

    # SIEMPRE perfil persistente real (la sesión de Facebook vive ahí).
    profile = BROWSER_PROFILE_DIR

    pw = sync_playwright().start()
    forensics.evt("pw.start", f"driver={forensics.driver_proc_info(pw)}")
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        headless=False,
        viewport={"width": 1280, "height": 900},
        args=["--start-maximized"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    forensics.evt("page.new", f"page={id(page)}")

    # — Sesión / Marketplace / Tus publicaciones — (igual que el flujo real)
    page.goto(facebook_config.base_url, wait_until="domcontentloaded", timeout=30_000)
    page.goto(facebook_config.marketplace_url, wait_until="domcontentloaded", timeout=30_000)
    page.goto(facebook_config.your_listings_url, wait_until="domcontentloaded", timeout=30_000)

    # UNA page.evaluate
    value = page.evaluate("document.title")
    logger.info("evaluate -> %s", value)

    # Un par de evaluates más con algo de tráfico (como busca nuestra app).
    for i in range(3):
        n = page.evaluate(
            "() => Array.from(document.querySelectorAll(\"a[href*='/marketplace/item/']\")).length"
        )
        logger.info("ciclo %d -> %d enlaces", i, n)
        forensics.evt("isolado.evaluate", f"cycle={i} driver={forensics.driver_proc_info(pw)}")

    time.sleep(3)
    logger.info("driver alive: %s", forensics.driver_alive(pw))

    page.close()
    ctx.close()
    pw.stop()
    logger.info("aislamiento SIN QThread terminado: %s", forensics.driver_proc_info(pw))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("MM_FORENSICS", "1")
    sys.exit(main())