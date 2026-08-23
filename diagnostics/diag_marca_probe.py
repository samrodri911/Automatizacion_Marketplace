"""Probe del fallo de 'Marca': evalúa FIELD_BY_LABEL_JS en el MISMO punto del
flujo donde corre _fill_label_field (tras expandir Más detalles y marcar
entrega) y vuelca los elementos con texto 'Marca'."""

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
logger = get_logger("diag_marca_probe")

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")

PRODUCT_DIR = Path(r"C:\Users\User\Documents\Proyectos Personales\marketplace-manager\data\products\laptop-hp-pavilion-15-i5-12gb-ram")
IMAGES = [str(PRODUCT_DIR / f"0{i}.jpeg") for i in range(1, 8)]

FIELD_BY_LABEL_JS = """
(label) => {
  const labelLower = String(label).toLowerCase();
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const labelOk = (t) => {
    if (!t) return false;
    const first = t.split('\\n')[0].trim().toLowerCase();
    return first === labelLower;
  };
  const findInput = (root) => {
    const c = root.querySelector('input[type="text"], textarea');
    if (!c) return null;
    const cr = c.getBoundingClientRect();
    if (cr.width <= 0 || cr.height <= 0) return null;
    return c;
  };
  const toSelector = (c) => {
    if (c.id) return '#' + CSS.escape(c.id);
    if (c.name) return 'input[name="' + c.name + '"], textarea[name="' + c.name + '"]';
    return c.tagName.toLowerCase();
  };
  for (const el of document.querySelectorAll('label, div, span, h1, h2, h3')) {
    if (!visible(el)) continue;
    if (!labelOk(el.innerText)) continue;
    const own = findInput(el);
    if (own) return toSelector(own);
    let node = el;
    for (let d = 0; d < 8 && node; d++) {
      node = node.parentElement;
      if (!node) break;
      const c = findInput(node);
      if (c) return toSelector(c);
    }
  }
  return null;
}
"""

DUMP_MARCA_JS = """
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const out = [];
  for (const el of document.querySelectorAll('label, div, span, h1, h2, h3, p')) {
    if (!visible(el)) continue;
    const t = (el.innerText || '').trim();
    if (!t) continue;
    const first = t.split('\\n')[0].trim();
    if (first.toLowerCase() !== 'marca') continue;
    const hasInput = !!el.querySelector('input[type="text"], textarea');
    const r = el.getBoundingClientRect();
    out.push({
      tag: el.tagName,
      y: Math.round(r.y),
      text: t.slice(0, 60),
      hasInput,
      childInputId: hasInput ? (el.querySelector('input[type="text"], textarea').id || '') : '',
    });
  }
  return { count: out.length, items: out };
}
"""


def settle(bridge, page, seconds: float = 1.0) -> None:
    bridge.submit(lambda: page.wait_for_timeout(int(seconds * 1000)))


def main() -> int:
    bridge = AsyncBridge(name="marca-loop")
    bridge.start()
    bm = BrowserManager()
    try:
        page = bridge.submit(lambda: bm.start())
        url = facebook_config.create_listing_url
        bridge.submit(lambda: page.goto(url, wait_until="domcontentloaded", timeout=30000))
        settle(bridge, page, 3.0)

        bridge.submit(lambda: page.locator("input[type=file]").first.set_input_files(IMAGES))
        settle(bridge, page, 2.0)

        for idx, value in enumerate(("Laptop HP Pavilion 15 - i5, 12GB RAM", "600000")):
            try:
                loc = bridge.submit(lambda: page.locator("input[type=text]").nth(idx))
                if bridge.submit(lambda: loc.is_visible(timeout=1500)):
                    bridge.submit(lambda: loc.fill(value))
            except Exception:
                pass
        settle(bridge, page)

        el = page.get_by_text("Más detalles", exact=False)
        if bridge.submit(lambda: el.first.is_visible(timeout=2000)):
            bridge.submit(lambda: el.first.click())
        settle(bridge, page, 1.0)

        for label in ("Encuentro en un lugar público", "Retiro en la puerta"):
            try:
                box = page.get_by_role("checkbox", name=label, exact=False).first
                if bridge.submit(lambda: box.is_visible(timeout=1500)):
                    bridge.submit(lambda: box.click())
            except Exception:
                pass
        settle(bridge, page, 0.5)

        # MISMO punto que _fill_label_field: evalúa el JS de Marca.
        sel = bridge.submit(lambda: page.evaluate(FIELD_BY_LABEL_JS, "Marca"))
        logger.info("FIELD_BY_LABEL_JS('Marca') -> %r", sel)

        dump = bridge.submit(lambda: page.evaluate(DUMP_MARCA_JS))
        logger.info("Elementos con primera línea 'Marca': count=%s", dump.get("count"))
        for item in dump.get("items", []):
            logger.info("  %s y=%s hasInput=%s childInputId=%r text=%r",
                        item["tag"], item["y"], item["hasInput"], item["childInputId"], item["text"])

        path = DUMP_DIR / f"marca_dump_{STAMP}.json"
        path.write_text(json.dumps({"selector": sel, **dump}, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Dump -> %s", path)
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