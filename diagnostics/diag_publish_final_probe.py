"""Probe tras pulsar 'Publicar': qué hace Facebook (URL, cuerpo, toasts).

Reutiliza el flujo de diag_publish_steps_probe y añade el clic final en
'Publicar', luego vuelca URL + body + posibles errores/toasts.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.automation.browser import BrowserManager
from app.core.async_bridge import AsyncBridge
from app.core.config import facebook_config
from app.core.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger("diag_publish_final_probe")

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")

PRODUCT_DIR = Path(r"C:\Users\User\Documents\Proyectos Personales\marketplace-manager\data\products\laptop-hp-pavilion-15-i5-12gb-ram")
IMAGES = [str(PRODUCT_DIR / f"0{i}.jpeg") for i in range(1, 8)]

BODY_JS = """
() => {
  const body = document.body ? (document.body.innerText || '') : '';
  return {
    url: location.href,
    text: body.slice(0, 6000),
    toasts: Array.from(document.querySelectorAll('[role="alert"], [role="dialog"]'))
      .map(e => (e.innerText || '').trim().slice(0, 300))
      .filter(Boolean),
  };
}
"""


def settle(bridge, page, seconds: float = 1.2) -> None:
    bridge.submit(lambda: page.wait_for_timeout(int(seconds * 1000)))


def main() -> int:
    bridge = AsyncBridge(name="publish-final-loop")
    bridge.start()
    bm = BrowserManager()
    try:
        page = bridge.submit(lambda: bm.start())
        url = facebook_config.create_listing_url
        bridge.submit(lambda: page.goto(url, wait_until="domcontentloaded", timeout=30000))
        settle(bridge, page, 3.0)

        bridge.submit(lambda: page.locator("input[type=file]").first.set_input_files(IMAGES))
        settle(bridge, page, 2.0)

        filled = 0
        for value in ("Laptop HP Pavilion 15 - i5, 12GB RAM", "600000"):
            try:
                loc = bridge.submit(lambda: page.locator("input[type=text]").nth(filled))
                if bridge.submit(lambda: loc.is_visible(timeout=1500)):
                    bridge.submit(lambda: loc.fill(value))
                    filled += 1
            except Exception:
                break
        settle(bridge, page)

        try:
            el = page.get_by_text("Más detalles", exact=False)
            if bridge.submit(lambda: el.first.is_visible(timeout=2000)):
                bridge.submit(lambda: el.first.click())
        except Exception:
            pass
        settle(bridge, page)

        try:
            ta = page.locator("textarea").first
            if bridge.submit(lambda: ta.is_visible(timeout=1500)):
                bridge.submit(lambda: ta.fill("Laptop HP Pavilion 15, 12GB RAM, i5. Excelente estado."))
        except Exception:
            pass
        settle(bridge, page)

        try:
            combo = page.get_by_role("combobox", name="Categoría").first
            if bridge.submit(lambda: combo.is_visible(timeout=1500)):
                bridge.submit(lambda: combo.click())
                settle(bridge, page)
                opt = page.get_by_text("Electrónica e informática", exact=False).first
                if bridge.submit(lambda: opt.is_visible(timeout=2000)):
                    bridge.submit(lambda: opt.click())
        except Exception:
            pass
        settle(bridge, page)

        try:
            combo = page.get_by_role("combobox", name="Estado").first
            if bridge.submit(lambda: combo.is_visible(timeout=1500)):
                bridge.submit(lambda: combo.click())
                settle(bridge, page)
                opt = page.get_by_text("Usado - Aceptable", exact=False).first
                if bridge.submit(lambda: opt.is_visible(timeout=2000)):
                    bridge.submit(lambda: opt.click())
        except Exception:
            pass
        settle(bridge, page)

        try:
            loc = page.get_by_role("combobox", name="Ubicación").first
            if bridge.submit(lambda: loc.is_visible(timeout=1500)):
                bridge.submit(lambda: loc.fill("Cali"))
        except Exception:
            pass
        settle(bridge, page)

        for label in ("Encuentro en un lugar público", "Retiro en la puerta"):
            try:
                box = page.get_by_role("checkbox", name=label, exact=False).first
                if bridge.submit(lambda: box.is_visible(timeout=1500)):
                    bridge.submit(lambda: box.click())
            except Exception:
                pass
        settle(bridge, page, 1.5)

        # Siguiente
        try:
            el = page.get_by_role("button", name="Siguiente", exact=False).first
            if bridge.submit(lambda: el.is_visible(timeout=1500)):
                bridge.submit(lambda: el.click())
                logger.info("Pulsado 'Siguiente'")
        except Exception as exc:
            logger.warning("No se pudo pulsar 'Siguiente': %s", exc)
        settle(bridge, page, 3.0)

        # Publicar (exacto)
        clicked = False
        try:
            btn = page.get_by_role("button", name="Publicar", exact=True).first
            if bridge.submit(lambda: btn.is_visible(timeout=3000)):
                bridge.submit(lambda: btn.click())
                clicked = True
                logger.info("Pulsado 'Publicar' (exacto)")
        except Exception as exc:
            logger.warning("No se pudo pulsar 'Publicar': %s", exc)
        if not clicked:
            try:
                btn = page.get_by_role("button", name="Publicar", exact=False).first
                if bridge.submit(lambda: btn.is_visible(timeout=2000)):
                    bridge.submit(lambda: btn.click())
                    clicked = True
                    logger.info("Pulsado 'Publicar' (parcial)")
            except Exception as exc:
                logger.warning("No se pudo pulsar 'Publicar' (parcial): %s", exc)

        # Observar qué pasa tras publicar (varias lecturas).
        for wait in (1, 2, 3, 5):
            settle(bridge, page, wait)
            try:
                data = bridge.submit(lambda: page.evaluate(BODY_JS))
                logger.info("t+%ds -> %s", wait, data["url"])
                out = {
                    "label": f"tras_publicar_t{wait}s",
                    "url": data.get("url", ""),
                    "body": data.get("text", ""),
                    "toasts": data.get("toasts", []),
                }
                path = DUMP_DIR / f"publish_final_{STAMP}_{wait}s.json"
                path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("Lectura t+%ds falló: %s", wait, exc)

        try:
            shot = DUMP_DIR / f"publish_final_{STAMP}.png"
            bridge.submit(lambda: page.screenshot(path=str(shot), full_page=False))
            logger.info("Captura -> %s", shot)
        except Exception as exc:
            logger.warning("No se pudo capturar: %s", exc)
    finally:
        try:
            bridge.submit(lambda: bm.stop())
        except Exception:
            pass
        bridge.stop()
    logger.info("Probe terminado. Revisa %s", DUMP_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())