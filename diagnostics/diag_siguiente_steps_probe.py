"""Probe incremental: tras cada campo, ¿"Siguiente" está habilitado?
Localiza qué campo deja el formulario incompleto."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.automation.browser import BrowserManager
from app.core.async_bridge import AsyncBridge
from app.core.config import facebook_config
from app.core.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger("diag_siguiente_steps_probe")

PRODUCT_DIR = Path(r"C:\Users\User\Documents\Proyectos Personales\marketplace-manager\data\products\laptop-hp-pavilion-15-i5-12gb-ram")
IMAGES = [str(PRODUCT_DIR / f"0{i}.jpeg") for i in range(1, 8)]

FIELD_BY_LABEL_JS = """
(label) => {
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const matches = [];
  for (const el of document.querySelectorAll('label, div, span, h1, h2, h3')) {
    if (!visible(el)) continue;
    const t = (el.innerText || '').trim();
    if (t !== label) continue;
    let node = el;
    for (let d = 0; d < 7 && node; d++) {
      node = node.parentElement;
      if (!node) break;
      const c = node.querySelector('input[type="text"], textarea');
      if (!c) continue;
      const cr = c.getBoundingClientRect();
      if (cr.width <= 0 || cr.height <= 0) continue;
      if (c.id) return '#' + CSS.escape(c.id);
      if (c.name) return 'input[name="' + c.name + '"], textarea[name="' + c.name + '"]';
      return c.tagName.toLowerCase();
    }
  }
  return null;
}
"""


def settle(bridge, page, seconds: float = 1.0) -> None:
    bridge.submit(lambda: page.wait_for_timeout(int(seconds * 1000)))


def check_siguiente(bridge, page, stage: str) -> None:
    try:
        btn = bridge.submit(
            lambda: page.get_by_role("button", name="Siguiente", exact=False).first
        )
        disabled = bridge.submit(lambda: btn.get_attribute("aria-disabled"))
        visible = bridge.submit(lambda: btn.is_visible(timeout=800))
        logger.info("SIGUIENTE [%s]: visible=%s aria-disabled=%r", stage, visible, disabled)
    except Exception as exc:
        logger.info("SIGUIENTE [%s]: no visible/error %s", stage, exc)


def main() -> int:
    bridge = AsyncBridge(name="siguiente-steps-loop")
    bridge.start()
    bm = BrowserManager()
    try:
        page = bridge.submit(lambda: bm.start())
        url = facebook_config.create_listing_url
        bridge.submit(lambda: page.goto(url, wait_until="domcontentloaded", timeout=30000))
        settle(bridge, page, 3.0)

        check_siguiente(bridge, page, "inicio")

        bridge.submit(lambda: page.locator("input[type=file]").first.set_input_files(IMAGES))
        settle(bridge, page, 2.0)

        # Título y precio.
        for idx, value in enumerate(("Laptop HP Pavilion 15 - i5, 12GB RAM", "600000")):
            try:
                loc = bridge.submit(lambda: page.locator("input[type=text]").nth(idx))
                if bridge.submit(lambda: loc.is_visible(timeout=1500)):
                    bridge.submit(lambda: loc.fill(value))
            except Exception:
                pass
        settle(bridge, page)
        check_siguiente(bridge, page, "titulo_precio")

        # Más detalles + descripción.
        el = page.get_by_text("Más detalles", exact=False)
        if bridge.submit(lambda: el.first.is_visible(timeout=2000)):
            bridge.submit(lambda: el.first.click())
        settle(bridge, page)
        ta = page.locator("textarea").first
        if bridge.submit(lambda: ta.is_visible(timeout=1500)):
            bridge.submit(lambda: ta.fill("Laptop HP Pavilion 15, 12GB RAM, i5. Excelente estado."))
        settle(bridge, page)
        check_siguiente(bridge, page, "descripcion")

        # Categoría.
        combo = page.get_by_role("combobox", name="Categoría").first
        if bridge.submit(lambda: combo.is_visible(timeout=1500)):
            bridge.submit(lambda: combo.click())
            settle(bridge, page)
            opt = page.get_by_text("Electrónica e informática", exact=False).first
            if bridge.submit(lambda: opt.is_visible(timeout=2000)):
                bridge.submit(lambda: opt.click())
        settle(bridge, page, 1.5)
        check_siguiente(bridge, page, "categoria")

        # Marca (mejorado, todos los matches).
        for label in ("Marca", "Brand"):
            try:
                sel = bridge.submit(lambda: page.evaluate(FIELD_BY_LABEL_JS, label))
                if sel:
                    bridge.submit(lambda: page.locator(sel).first.fill("HP"))
                    logger.info("MARCA rellenada via label %r -> %s", label, sel)
                    break
            except Exception as exc:
                logger.warning("Marca %r: %s", label, exc)
        settle(bridge, page)
        check_siguiente(bridge, page, "marca")

        # Estado/Condición.
        for name in ("Condición", "Estado"):
            try:
                c = page.get_by_role("combobox", name=name, exact=False).first
                if bridge.submit(lambda: c.is_visible(timeout=1200)):
                    bridge.submit(lambda: c.click())
                    settle(bridge, page)
                    opt = page.get_by_text("Usado - Aceptable", exact=False).first
                    if bridge.submit(lambda: opt.is_visible(timeout=2000)):
                        bridge.submit(lambda: opt.click())
                        logger.info("ESTADO seleccionado via combo %r", name)
                    break
            except Exception:
                continue
        settle(bridge, page, 1.0)
        check_siguiente(bridge, page, "estado")

        # Ubicación.
        try:
            loc = page.get_by_label("Ubicación").first
            if bridge.submit(lambda: loc.is_visible(timeout=1200)):
                bridge.submit(lambda: loc.fill("Cali"))
                logger.info("UBICACION rellenada")
        except Exception as exc:
            logger.warning("Ubicación: %s", exc)
        settle(bridge, page)
        check_siguiente(bridge, page, "ubicacion")

        # Etiquetas.
        for label in ("Etiquetas de productos", "Etiquetas", "Tags"):
            try:
                sel = bridge.submit(lambda: page.evaluate(FIELD_BY_LABEL_JS, label))
                if sel:
                    bridge.submit(lambda: page.locator(sel).first.fill("notebook, hp, usado, oferta"))
                    logger.info("ETIQUETAS rellenadas via label %r -> %s", label, sel)
                    break
            except Exception:
                continue
        settle(bridge, page)
        check_siguiente(bridge, page, "etiquetas")

        # SKU (opcional) si existe label.
        for label in ("SKU",):
            try:
                sel = bridge.submit(lambda: page.evaluate(FIELD_BY_LABEL_JS, label))
                if sel:
                    bridge.submit(lambda: page.locator(sel).first.fill("HP-PAV-15"))
                    logger.info("SKU rellenado via label -> %s", sel)
            except Exception:
                continue
        settle(bridge, page)
        check_siguiente(bridge, page, "sku")

        # Entrega.
        for label in ("Encuentro en un lugar público", "Retiro en la puerta"):
            try:
                box = page.get_by_role("checkbox", name=label, exact=False).first
                if bridge.submit(lambda: box.is_visible(timeout=1500)):
                    bridge.submit(lambda: box.click())
            except Exception:
                pass
        settle(bridge, page, 1.0)
        check_siguiente(bridge, page, "entrega")

        try:
            shot = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps") / f"siguiente_steps_{time.strftime('%H%M%S')}.png"
            bridge.submit(lambda: page.screenshot(path=str(shot), full_page=False))
            logger.info("Captura -> %s", shot)
        except Exception as exc:
            logger.warning("Captura: %s", exc)
    finally:
        try:
            bridge.submit(lambda: bm.stop())
        except Exception:
            pass
        bridge.stop()
    logger.info("Probe terminado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())