"""Probe exhaustivo del formulario lleno: texto visible + controles, para
localizar Estado, Marca, Etiquetas(tags), SKU y los switches de
Promocionar/Ocultar. Incluye scroll al fondo del formulario.
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
logger = get_logger("diag_exhaustive_probe")

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")

PRODUCT_DIR = Path(r"C:\Users\User\Documents\Proyectos Personales\marketplace-manager\data\products\laptop-hp-pavilion-15-i5-12gb-ram")
IMAGES = [str(PRODUCT_DIR / f"0{i}.jpeg") for i in range(1, 8)]

PAGE_TEXT_JS = """
() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('div, span, label, li, h1, h2, h3, a, p, [role="button"]')) {
    const t = (el.innerText || '').trim();
    if (!t || t.length < 2 || t.length > 120) continue;
    if (seen.has(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    seen.add(t);
    out.push(t);
  }
  return out;
}
"""

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
      checked: el.checked === true,
      value: (el.value || '').slice(0, 60),
      text: (el.innerText || el.textContent || '').trim().slice(0, 60),
      y: Math.round(r.top),
    });
  }
  return out;
}
"""


def dump(bridge, page, label: str) -> None:
    try:
        texts = bridge.submit(lambda: page.evaluate(PAGE_TEXT_JS))
        fields = bridge.submit(lambda: page.evaluate(FIELDS_JS))
        url = bridge.submit(lambda: page.url)
    except Exception as exc:
        logger.warning("No se pudo evaluar %s: %s", label, exc)
        return
    out = {"label": label, "url": url, "texts": texts or [], "fields": fields or []}
    path = DUMP_DIR / f"exhaustive_{STAMP}_{label.replace(' ', '_')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("%s -> %s", label, path)
    KEY = re_entrega()
    for t in out["texts"]:
        if KEY.search(t) or any(k in t.lower() for k in ("estado", "marca", "etiqueta", "sku", "disponib", "encuentro", "retiro", "promocionar", "ocultar")):
            logger.info("  TXT %r", t)
    for f in out["fields"]:
        ident = (f["labelledByText"] or f["ariaLabel"] or f["placeholder"] or f["text"] or f["role"] or f["tag"])
        logger.info("  FIELD %r (%s/%s) value=%r checked=%s y=%s",
                    ident[:40], f["tag"], f["role"], f["value"][:20], f["checked"], f["y"])


def re_entrega():
    import re
    return re.compile(r"entrega|encuentro|retiro|envio|recoger", re.I)


def settle(bridge, page, seconds: float = 1.2) -> None:
    bridge.submit(lambda: page.wait_for_timeout(int(seconds * 1000)))


def main() -> int:
    bridge = AsyncBridge(name="exhaustive-loop")
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

        combo = page.get_by_role("combobox", name="Categoría").first
        if bridge.submit(lambda: combo.is_visible(timeout=1500)):
            bridge.submit(lambda: combo.click())
            settle(bridge, page)
            opt = page.get_by_text("Electrónica e informática", exact=False).first
            if bridge.submit(lambda: opt.is_visible(timeout=2000)):
                bridge.submit(lambda: opt.click())
        settle(bridge, page, 2.0)

        dump(bridge, page, "form_tras_categoria")

        # Estado: abrir y volcar TODOS los botones visibles (picker de FB).
        try:
            estado = page.get_by_role("combobox", name="Estado").first
            if bridge.submit(lambda: estado.is_visible(timeout=1500)):
                bridge.submit(lambda: estado.click())
                settle(bridge, page)
                opts = bridge.submit(lambda: page.evaluate(PAGE_TEXT_JS))
                out = {"label": "estado_abierto", "options": opts or []}
                path = DUMP_DIR / f"exhaustive_{STAMP}_estado_abierto.json"
                path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                logger.info("Texto con Estado abierto: %d items", len(out["options"]))
                bridge.submit(lambda: page.keyboard.press("Escape"))
                settle(bridge, page)
        except Exception as exc:
            logger.warning("Estado: %s", exc)

        # Scroll al fondo del formulario y volver a volcar (campos inferiores).
        for _ in range(3):
            try:
                bridge.submit(lambda: page.mouse.wheel(0, 2500))
            except Exception:
                pass
            settle(bridge, page, 0.8)
        dump(bridge, page, "form_tras_scroll")

        try:
            shot = DUMP_DIR / f"exhaustive_{STAMP}.png"
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