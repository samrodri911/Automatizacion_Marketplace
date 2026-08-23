"""Probe del formulario de creación de publicación (diagnóstico del fallo
'no pude rellenar el campo descripción').

1. Abre el navegador (AsyncBridge + BrowserManager).
2. Navega a /marketplace/create/item.
3. Vuelca TODOS los controles de formulario (input/textarea/contenteditable/
   combobox/select) con su aria-label, placeholder, role e id.
4. Captura y JSON a %TEMP%\\opencode\\werdumps.

USO (con el repo como directorio de trabajo):
    .venv\\Scripts\\python.exe diagnostics\\diag_create_form_probe.py
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
logger = get_logger("diag_create_form_probe")

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")

FORM_DUMP_JS = """
() => {
  const out = [];
  const sel = 'input, textarea, select, [contenteditable], [role="textbox"], [role="combobox"]';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const labelledby = el.getAttribute('aria-labelledby') || '';
    const resolve = (id) => {
      const n = document.getElementById(id);
      return n ? (n.innerText || n.textContent || '').trim().slice(0, 80) : '';
    };
    out.push({
      tag: el.tagName,
      type: el.getAttribute('type') || '',
      role: el.getAttribute('role') || '',
      ariaLabel: (el.getAttribute('aria-label') || '').slice(0, 80),
      ariaLabelledby: labelledby,
      labelledByText: labelledby ? labelledby.split(/\\s+/).map(resolve).join(' | ') : '',
      placeholder: (el.getAttribute('placeholder') || '').slice(0, 80),
      contenteditable: el.getAttribute('contenteditable') || '',
      id: (el.id || '').slice(0, 40),
      value: (el.value || '').slice(0, 60),
      text: (el.innerText || el.textContent || '').trim().slice(0, 40),
      visible: r.width > 0 && r.height > 0,
      editable: el.isContentEditable,
    });
  }
  return out;
}
"""

FORM_TEXT_JS = """
() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('div, span, label, h1, h2, h3, p')) {
    const t = (el.innerText || '').trim();
    if (!t || t.length < 3 || t.length > 60) continue;
    if (seen.has(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    seen.add(t);
    out.push(t);
  }
  return out;
}
"""


def dump(bridge, page, label: str) -> None:
    try:
        data = bridge.submit(lambda: page.evaluate(FORM_DUMP_JS))
        texts = bridge.submit(lambda: page.evaluate(FORM_TEXT_JS))
    except Exception as exc:
        logger.warning("No se pudo evaluar el DOM de %s: %s", label, exc)
        return
    out = {
        "label": label,
        "url": bridge.submit(lambda: page.url),
        "fields": data if isinstance(data, list) else [],
        "visible_texts": texts if isinstance(texts, list) else [],
    }
    path = DUMP_DIR / f"create_form_{STAMP}_{label.replace(' ', '_')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    total = len(out["fields"])
    visible = [f for f in out["fields"] if f.get("visible")]
    logger.info("%s: %d controles (visible=%d), %d textos -> %s", label, total, len(visible), len(out["visible_texts"]), path)


def fill_first_visible_text_inputs(bridge, page, title: str, price: str) -> int:
    """Rellena los inputs[type=text] visibles en orden con título y precio.

    Corre DENTRO del loop del bridge (page async crudo)."""
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
    """Clic en 'Más detalles' para expandir la sección de descripción
    (corre DENTRO del loop del bridge)."""
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
    bridge = AsyncBridge(name="probe-loop")
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
            shot = DUMP_DIR / f"create_form_{STAMP}.png"
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