"""Probe de la sección 'Preferencia de entrega' del formulario de creación
(Encuentro en lugar público / Retiro en la puerta).

1. Abre el navegador, navega a /marketplace/create/item.
2. Rellena título/precio y expande 'Más detalles'.
3. Vuelca controles interactivos (button/checkbox/radio/switch/combobox) con
   su texto, aria-label, role, checked/aria-checked y visibilidad.
4. Captura y JSON a %TEMP%\\opencode\\werdumps.
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
logger = get_logger("diag_delivery_probe")

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")

INTERACTIVE_DUMP_JS = """
() => {
  const out = [];
  const sel = 'button, [role="button"], input[type="checkbox"], [role="checkbox"], '
    + '[role="radio"], [role="switch"], [role="combobox"], label';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const text = (el.innerText || el.textContent || '').trim().slice(0, 120);
    out.push({
      tag: el.tagName,
      type: el.getAttribute('type') || '',
      role: el.getAttribute('role') || '',
      ariaLabel: (el.getAttribute('aria-label') || '').slice(0, 80),
      ariaChecked: el.getAttribute('aria-checked') || '',
      ariaPressed: el.getAttribute('aria-pressed') || '',
      checked: el.checked === true,
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      text,
    });
  }
  return out;
}
"""

TEXT_DUMP_JS = """
() => {
  const out = [];
  const seen = new Set();
  const KEY = /entrega|encuentro|retiro|envio|recoger|domicilio|punto|acordar/i;
  for (const el of document.querySelectorAll('div, span, label, h1, h2, h3, p')) {
    const t = (el.innerText || '').trim();
    if (!t || t.length < 2 || t.length > 120) continue;
    if (seen.has(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    seen.add(t);
    if (KEY.test(t)) out.push(t);
  }
  return out;
}
"""


def dump(bridge, page, label: str) -> None:
    try:
        interactive = bridge.submit(lambda: page.evaluate(INTERACTIVE_DUMP_JS))
        texts = bridge.submit(lambda: page.evaluate(TEXT_DUMP_JS))
        url = bridge.submit(lambda: page.url)
    except Exception as exc:
        logger.warning("No se pudo evaluar el DOM de %s: %s", label, exc)
        return
    out = {
        "label": label,
        "url": url,
        "interactive": interactive if isinstance(interactive, list) else [],
        "delivery_texts": texts if isinstance(texts, list) else [],
    }
    path = DUMP_DIR / f"delivery_probe_{STAMP}_{label.replace(' ', '_')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("%s: %d interactivos, %d textos de entrega -> %s",
                label, len(out["interactive"]), len(out["delivery_texts"]), path)


def fill_first_visible_text_inputs(bridge, page, title: str, price: str) -> int:
    async def _run():
        filled = 0
        for value in (title, price):
            try:
                loc = page.locator("input[type=text]").nth(filled)
                if await loc.is_visible(timeout=1000):
                    await loc.fill(value)
                    filled += 1
            except Exception:
                break
        return filled

    return bridge.submit(_run)


def click_more_details(bridge, page) -> bool:
    async def _run():
        try:
            loc = page.get_by_text("Más detalles", exact=False)
            if await loc.first.is_visible(timeout=2000):
                await loc.first.click()
                return True
        except Exception as exc:
            logger.warning("No se pudo expandir 'Más detalles': %s", exc)
        return False

    return bridge.submit(_run)


def main() -> int:
    bridge = AsyncBridge(name="delivery-probe-loop")
    bridge.start()
    bm = BrowserManager()
    try:
        page = bridge.submit(lambda: bm.start())
        logger.info("Navegador abierto (node_pid=%s)", bm.node_pid)

        url = facebook_config.create_listing_url
        logger.info("Navegando al formulario: %s", url)
        bridge.submit(lambda: page.goto(url, wait_until="domcontentloaded", timeout=30000))
        bridge.submit(lambda: page.wait_for_timeout(3000))
        dump(bridge, page, "form_vacio")

        filled = fill_first_visible_text_inputs(bridge, page, "Laptop HP Pavilion 15", "600000")
        logger.info("Inputs de texto rellenados: %d", filled)
        bridge.submit(lambda: page.wait_for_timeout(1500))
        dump(bridge, page, "form_tras_titulo_precio")

        expanded = click_more_details(bridge, page)
        logger.info("'Más detalles' expandido: %s", expanded)
        bridge.submit(lambda: page.wait_for_timeout(1500))
        dump(bridge, page, "form_detalles_expandido")

        try:
            shot = DUMP_DIR / f"delivery_probe_{STAMP}.png"
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