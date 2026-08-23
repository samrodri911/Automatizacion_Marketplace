"""Probe del paso 'audience': lista de grupos donde publicar, buscador y
estado del Marketplace por defecto."""

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
logger = get_logger("diag_audience_probe")

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")

PRODUCT_DIR = Path(r"C:\Users\User\Documents\Proyectos Personales\marketplace-manager\data\products\laptop-hp-pavilion-15-i5-12gb-ram")
IMAGES = [str(PRODUCT_DIR / f"0{i}.jpeg") for i in range(1, 8)]

AUDIENCE_JS = """
() => {
  const out = { groups: [], controls: [], marketplace: null };
  for (const el of document.querySelectorAll('[role="checkbox"]')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    out.groups.push({
      checked: el.getAttribute('aria-checked') || '',
      text: (el.innerText || '').trim().slice(0, 160),
    });
  }
  for (const el of document.querySelectorAll('input, textarea, [role="combobox"], [role="button"]')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const t = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim().slice(0, 60);
    if (!t) continue;
    out.controls.push({
      tag: el.tagName,
      type: el.getAttribute('type') || '',
      role: el.getAttribute('role') || '',
      text: t,
    });
  }
  const mp = document.body ? (document.body.innerText || '') : '';
  out.marketplace = mp.includes('Marketplace') && mp.includes('Mi Marketplace');
  return out;
}
"""


def settle(bridge, page, seconds: float = 1.2) -> None:
    bridge.submit(lambda: page.wait_for_timeout(int(seconds * 1000)))


def main() -> int:
    bridge = AsyncBridge(name="audience-loop")
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

        try:
            estado = page.get_by_role("combobox", name="Estado").first
            if bridge.submit(lambda: estado.is_visible(timeout=1500)):
                bridge.submit(lambda: estado.click())
                settle(bridge, page)
                opt = page.get_by_text("Usado - Aceptable", exact=False).first
                if bridge.submit(lambda: opt.is_visible(timeout=2000)):
                    bridge.submit(lambda: opt.click())
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
        settle(bridge, page, 1.0)

        try:
            el = page.get_by_role("button", name="Siguiente", exact=False).first
            if bridge.submit(lambda: el.is_visible(timeout=1500)):
                bridge.submit(lambda: el.click())
                logger.info("Pulsado 'Siguiente'")
        except Exception as exc:
            logger.warning("Siguiente: %s", exc)
        settle(bridge, page, 4.0)

        data = bridge.submit(lambda: page.evaluate(AUDIENCE_JS))
        out = {"label": "audience", "url": bridge.submit(lambda: page.url), **data}
        path = DUMP_DIR / f"audience_{STAMP}.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Audience -> %s", path)
        logger.info("Marketplace default: %s", out["marketplace"])
        logger.info("CONTROLS:")
        for c in out["controls"]:
            logger.info("  <%s %s role=%s> %r", c["tag"], c["type"], c["role"], c["text"])
        logger.info("GRUPOS (%d):", len(out["groups"]))
        for g in out["groups"]:
            logger.info("  [%s] %r", g["checked"], g["text"])

        try:
            shot = DUMP_DIR / f"audience_{STAMP}.png"
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