"""Probe de TODOS los campos del formulario tras la selección de categoría:
Estado, Marca, Etiquetas, Disponibilidad, SKU, y estado de los toggles
'Promocionar tras publicar' / 'Ocultar a amigos'.
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
logger = get_logger("diag_full_fields_probe")

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")

PRODUCT_DIR = Path(r"C:\Users\User\Documents\Proyectos Personales\marketplace-manager\data\products\laptop-hp-pavilion-15-i5-12gb-ram")
IMAGES = [str(PRODUCT_DIR / f"0{i}.jpeg") for i in range(1, 8)]

FIELDS_JS = """
() => {
  const out = [];
  const sel = 'input, textarea, select, [contenteditable], [role="textbox"], [role="combobox"], [role="checkbox"], [role="switch"]';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const labelledby = el.getAttribute('aria-labelledby') || '';
    const resolve = (id) => {
      const n = document.getElementById(id);
      return n ? (n.innerText || n.textContent || '').trim().slice(0, 60) : '';
    };
    out.push({
      tag: el.tagName,
      type: el.getAttribute('type') || '',
      role: el.getAttribute('role') || '',
      ariaLabel: (el.getAttribute('aria-label') || '').slice(0, 60),
      labelledByText: labelledby ? labelledby.split(/\\s+/).map(resolve).join(' | ') : '',
      placeholder: (el.getAttribute('placeholder') || '').slice(0, 60),
      ariaChecked: el.getAttribute('aria-checked') || '',
      value: (el.value || '').slice(0, 60),
      text: (el.innerText || el.textContent || '').trim().slice(0, 60),
    });
  }
  return out;
}
"""

COMBO_OPTIONS_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('[role="dialog"] [role="button"]')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const t = (el.innerText || '').trim().split('\\n')[0].trim();
    if (!t) continue;
    out.push(t);
  }
  return out;
}
"""


def dump(bridge, page, label: str) -> None:
    try:
        fields = bridge.submit(lambda: page.evaluate(FIELDS_JS))
        url = bridge.submit(lambda: page.url)
    except Exception as exc:
        logger.warning("No se pudo evaluar %s: %s", label, exc)
        return
    out = {"label": label, "url": url, "fields": fields if isinstance(fields, list) else []}
    path = DUMP_DIR / f"full_fields_{STAMP}_{label.replace(' ', '_')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("%s -> %s", label, path)
    for f in out["fields"]:
        ident = (f["labelledByText"] or f["ariaLabel"] or f["placeholder"] or f["text"] or f["role"])
        extra = f["value"] or f["ariaChecked"]
        if ident:
            logger.info("  %s (%s/%s) = %r", ident[:40], f["tag"], f["role"], extra[:30])


def settle(bridge, page, seconds: float = 1.2) -> None:
    bridge.submit(lambda: page.wait_for_timeout(int(seconds * 1000)))


def main() -> int:
    bridge = AsyncBridge(name="full-fields-loop")
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

        el = page.get_by_text("Más detalles", exact=False)
        if bridge.submit(lambda: el.first.is_visible(timeout=2000)):
            bridge.submit(lambda: el.first.click())
        settle(bridge, page)

        ta = page.locator("textarea").first
        if bridge.submit(lambda: ta.is_visible(timeout=1500)):
            bridge.submit(lambda: ta.fill("Laptop HP Pavilion 15, 12GB RAM, i5. Excelente estado."))
        settle(bridge, page)

        # Categoría
        combo = page.get_by_role("combobox", name="Categoría").first
        if bridge.submit(lambda: combo.is_visible(timeout=1500)):
            bridge.submit(lambda: combo.click())
            settle(bridge, page)
            opt = page.get_by_text("Electrónica e informática", exact=False).first
            if bridge.submit(lambda: opt.is_visible(timeout=2000)):
                bridge.submit(lambda: opt.click())
                logger.info("Categoría: Electrónica e informática")
        settle(bridge, page, 2.0)

        dump(bridge, page, "campos_tras_categoria")

        # Abrir combo Estado y volcar sus opciones reales.
        try:
            estado = page.get_by_role("combobox", name="Estado").first
            if bridge.submit(lambda: estado.is_visible(timeout=1500)):
                bridge.submit(lambda: estado.click())
                settle(bridge, page)
                opts = bridge.submit(lambda: page.evaluate(COMBO_OPTIONS_JS))
                out = {"label": "opciones_estado", "options": opts if isinstance(opts, list) else []}
                path = DUMP_DIR / f"full_fields_{STAMP}_opciones_estado.json"
                path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info("Opciones Estado: %s", out["options"])
                # Cerrar el picker (clic fuera o Escape)
                try:
                    bridge.submit(lambda: page.keyboard.press("Escape"))
                except Exception:
                    pass
                settle(bridge, page)
        except Exception as exc:
            logger.warning("No se pudo abrir Estado: %s", exc)

        # Abrir combo Etiquetas si existe
        try:
            etq = page.get_by_role("combobox", name="Etiquetas", exact=False).first
            if bridge.submit(lambda: etq.is_visible(timeout=1500)):
                bridge.submit(lambda: etq.click())
                settle(bridge, page)
                opts = bridge.submit(lambda: page.evaluate(COMBO_OPTIONS_JS))
                out = {"label": "opciones_etiquetas", "options": opts if isinstance(opts, list) else []}
                path = DUMP_DIR / f"full_fields_{STAMP}_opciones_etiquetas.json"
                path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info("Opciones Etiquetas: %s", out["options"])
                try:
                    bridge.submit(lambda: page.keyboard.press("Escape"))
                except Exception:
                    pass
                settle(bridge, page)
        except Exception as exc:
            logger.warning("No se pudo abrir Etiquetas: %s", exc)

        dump(bridge, page, "campos_finales")

        try:
            shot = DUMP_DIR / f"full_fields_{STAMP}.png"
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