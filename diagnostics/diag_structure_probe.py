"""Probe estructural: como estan asociados los labels 'Marca', 'Etiquetas',
'SKU', 'Disponibilidad' con sus inputs/textarea en el DOM del formulario."""

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
logger = get_logger("diag_structure_probe")

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")

PRODUCT_DIR = Path(r"C:\Users\User\Documents\Proyectos Personales\marketplace-manager\data\products\laptop-hp-pavilion-15-i5-12gb-ram")
IMAGES = [str(PRODUCT_DIR / f"0{i}.jpeg") for i in range(1, 8)]

STRUCTURE_JS = """
() => {
  const targets = ['Marca', 'Etiquetas de productos', 'SKU', 'Disponibilidad'];
  const out = [];
  for (const el of document.querySelectorAll('label, div, span')) {
    const t = (el.innerText || '').trim();
    if (targets.indexOf(t) < 0) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const info = { label: t, tag: el.tagName, y: Math.round(r.top) };
    let node = el;
    for (let d = 0; d < 5 && node; d++) {
      node = node.parentElement;
      if (!node) break;
      const control = node.querySelector('input[type="text"], textarea, [role="combobox"]');
      if (control) {
        const cr = control.getBoundingClientRect();
        info.control = {
          tag: control.tagName,
          role: control.getAttribute('role') || '',
          type: control.getAttribute('type') || '',
          ariaLabel: (control.getAttribute('aria-label') || '').slice(0, 50),
          placeholder: (control.getAttribute('placeholder') || '').slice(0, 50),
          id: control.id ? control.id.slice(0, 40) : '',
          labelledby: (control.getAttribute('aria-labelledby') || '').slice(0, 40),
          y: Math.round(cr.top),
        };
        break;
      }
    }
    if (info.control) out.push(info);
  }
  return out;
}
"""

ALL_CONTROLS_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('input[type="text"], textarea, [role="combobox"], [role="checkbox"], [role="switch"]')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const labelledby = el.getAttribute('aria-labelledby') || '';
    const resolve = (id) => {
      const n = document.getElementById(id);
      return n ? (n.innerText || n.textContent || '').trim().slice(0, 40) : '';
    };
    out.push({
      tag: el.tagName,
      role: el.getAttribute('role') || '',
      type: el.getAttribute('type') || '',
      ariaLabel: (el.getAttribute('aria-label') || '').slice(0, 50),
      labelledby: labelledby ? labelledby.split(' ').map(resolve).join(' | ') : '',
      placeholder: (el.getAttribute('placeholder') || '').slice(0, 50),
      y: Math.round(r.top),
      id: el.id ? el.id.slice(0, 40) : '',
    });
  }
  return out;
}
"""


def settle(bridge, page, seconds: float = 1.2) -> None:
    bridge.submit(lambda: page.wait_for_timeout(int(seconds * 1000)))


def main() -> int:
    bridge = AsyncBridge(name="structure-loop")
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

        struct = bridge.submit(lambda: page.evaluate(STRUCTURE_JS))
        controls = bridge.submit(lambda: page.evaluate(ALL_CONTROLS_JS))
        out = {"label": "estructura", "structure": struct or [], "controls": controls or []}
        path = DUMP_DIR / f"structure_{STAMP}.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Estructura -> %s", path)
        logger.info("STRUCTURE:")
        for s in out["structure"]:
            logger.info("  %s (y=%s) -> %s", s["label"], s["y"], s.get("control"))
        logger.info("CONTROLS (y ordenados):")
        for c in sorted(out["controls"], key=lambda c: c["y"]):
            ident = c["labelledby"] or c["ariaLabel"] or c["placeholder"] or c["tag"]
            logger.info("  y=%s %s %s <%s role=%s> id=%s", c["y"], ident[:35], c["type"], c["tag"], c["role"], c["id"])
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