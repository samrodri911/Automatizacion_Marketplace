"""Probe integral del flujo de publicación: preferencias de entrega + Siguiente
+ Publicar.

1. Abre el formulario, sube fotos, rellena título/precio, expande
   'Más detalles', rellena descripción, elige categoría y estado,
   marca las preferencias de entrega.
2. Vuelca los botones antes y después de pulsar 'Siguiente' para localizar
   el botón 'Publicar'.
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
logger = get_logger("diag_publish_steps_probe")

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")

PRODUCT_DIR = Path(r"C:\Users\User\Documents\Proyectos Personales\marketplace-manager\data\products\laptop-hp-pavilion-15-i5-12gb-ram")
IMAGES = [str(PRODUCT_DIR / f"0{i}.jpeg") for i in range(1, 8)]

BUTTONS_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('[role="button"], button')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const t = (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 90);
    if (!t) continue;
    out.push({
      role: el.getAttribute('role') || el.tagName,
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      text: t,
    });
  }
  return out;
}
"""

CHECKBOXES_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('[role="checkbox"]')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    out.push({
      checked: el.getAttribute('aria-checked') || '',
      text: (el.innerText || '').trim().slice(0, 90),
    });
  }
  return out;
}
"""


def dump(bridge, page, label: str) -> None:
    try:
        buttons = bridge.submit(lambda: page.evaluate(BUTTONS_JS))
        boxes = bridge.submit(lambda: page.evaluate(CHECKBOXES_JS))
        url = bridge.submit(lambda: page.url)
    except Exception as exc:
        logger.warning("No se pudo evaluar %s: %s", label, exc)
        return
    out = {
        "label": label,
        "url": url,
        "buttons": buttons if isinstance(buttons, list) else [],
        "checkboxes": boxes if isinstance(boxes, list) else [],
    }
    path = DUMP_DIR / f"publish_steps_{STAMP}_{label.replace(' ', '_')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("%s -> %s", label, path)
    for b in out["buttons"]:
        if b["text"] in ("Siguiente", "Publicar") or "Publicar" in b["text"] or "Siguiente" in b["text"]:
            logger.info("  BTN %s %s", b["text"], "DISABLED" if b["disabled"] else "")
    for b in out["checkboxes"]:
        logger.info("  CHK %s -> %s", b["text"].split("\\n")[0], b["checked"])


def settle(bridge, page, seconds: float = 1.2) -> None:
    bridge.submit(lambda: page.wait_for_timeout(int(seconds * 1000)))


def main() -> int:
    bridge = AsyncBridge(name="publish-steps-loop")
    bridge.start()
    bm = BrowserManager()
    try:
        page = bridge.submit(lambda: bm.start())
        logger.info("Navegador abierto (node_pid=%s)", bm.node_pid)

        url = facebook_config.create_listing_url
        bridge.submit(lambda: page.goto(url, wait_until="domcontentloaded", timeout=30000))
        settle(bridge, page, 3.0)

        # Fotos
        try:
            bridge.submit(lambda: page.locator("input[type=file]").first.set_input_files(IMAGES))
            logger.info("Fotos subidas: %d", len(IMAGES))
        except Exception as exc:
            logger.warning("No se pudieron subir fotos: %s", exc)
        settle(bridge, page, 2.0)

        # Título y precio
        filled = 0
        for value in ("Laptop HP Pavilion 15 - i5, 12GB RAM", "600000"):
            try:
                loc = bridge.submit(lambda: page.locator("input[type=text]").nth(filled))
                if bridge.submit(lambda: loc.is_visible(timeout=1500)):
                    bridge.submit(lambda: loc.fill(value))
                    filled += 1
            except Exception:
                break
        logger.info("Título/precio rellenados: %d", filled)
        settle(bridge, page)

        # Expandir Más detalles
        try:
            el = page.get_by_text("Más detalles", exact=False)
            if bridge.submit(lambda: el.first.is_visible(timeout=2000)):
                bridge.submit(lambda: el.first.click())
                logger.info("'Más detalles' expandido")
        except Exception as exc:
            logger.warning("No se pudo expandir 'Más detalles': %s", exc)
        settle(bridge, page)

        # Descripción (primer textarea visible)
        try:
            ta = page.locator("textarea").first
            if bridge.submit(lambda: ta.is_visible(timeout=1500)):
                bridge.submit(lambda: ta.fill("Laptop HP Pavilion 15, 12GB RAM, i5. Excelente estado."))
                logger.info("Descripción rellenada")
        except Exception as exc:
            logger.warning("No se pudo rellenar descripción: %s", exc)
        settle(bridge, page)

        # Categoría
        try:
            combo = page.get_by_role("combobox", name="Categoría").first
            if bridge.submit(lambda: combo.is_visible(timeout=1500)):
                bridge.submit(lambda: combo.click())
                settle(bridge, page)
                opt = page.get_by_text("Electrónica e informática", exact=False).first
                if bridge.submit(lambda: opt.is_visible(timeout=2000)):
                    bridge.submit(lambda: opt.click())
                    logger.info("Categoría: Electrónica e informática")
        except Exception as exc:
            logger.warning("No se pudo elegir categoría: %s", exc)
        settle(bridge, page)

        # Estado (condición)
        try:
            combo = page.get_by_role("combobox", name="Estado").first
            if bridge.submit(lambda: combo.is_visible(timeout=1500)):
                bridge.submit(lambda: combo.click())
                settle(bridge, page)
                opt = page.get_by_text("Usado - Aceptable", exact=False).first
                if bridge.submit(lambda: opt.is_visible(timeout=2000)):
                    bridge.submit(lambda: opt.click())
                    logger.info("Estado: Usado - Aceptable")
        except Exception as exc:
            logger.warning("No se pudo elegir estado: %s", exc)
        settle(bridge, page)

        # Ubicación
        try:
            loc = page.get_by_role("combobox", name="Ubicación").first
            if bridge.submit(lambda: loc.is_visible(timeout=1500)):
                bridge.submit(lambda: loc.fill("Cali"))
                logger.info("Ubicación: Cali")
        except Exception as exc:
            logger.warning("No se pudo rellenar ubicación: %s", exc)
        settle(bridge, page)

        dump(bridge, page, "form_completo_antes_entrega")

        # Preferencias de entrega: marcar Encuentro y Retiro en la puerta.
        for label in ("Encuentro en un lugar público", "Retiro en la puerta"):
            try:
                box = page.get_by_role("checkbox", name=label, exact=False).first
                if bridge.submit(lambda: box.is_visible(timeout=1500)):
                    bridge.submit(lambda: box.click())
                    logger.info("Entrega marcada: %s", label)
            except Exception as exc:
                logger.warning("No se pudo marcar %s: %s", label, exc)
        settle(bridge, page, 1.5)
        dump(bridge, page, "form_con_entrega")

        # Siguiente
        clicked_next = False
        for tok in ("Siguiente", "Next"):
            try:
                el = page.get_by_role("button", name=tok, exact=False).first
                if bridge.submit(lambda: el.is_visible(timeout=1500)):
                    bridge.submit(lambda: el.click())
                    clicked_next = True
                    logger.info("Pulsado 'Siguiente' (%s)", tok)
                    break
            except Exception:
                continue
        settle(bridge, page, 3.0)
        dump(bridge, page, "tras_siguiente")

        try:
            shot = DUMP_DIR / f"publish_steps_{STAMP}.png"
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