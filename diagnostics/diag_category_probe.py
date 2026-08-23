"""Probe del selector de Categoría del formulario de creación (diagnóstico
del fallo 'no pude rellenar el campo categoría').

1. Abre el navegador (AsyncBridge + BrowserManager).
2. Navega a /marketplace/create/item.
3. Abre el control de categoría (role=combobox "Categoría").
4. Vuelca las opciones visibles (texto, aria-*, data-*, estructura).
5. Captura y JSON a %TEMP%\\opencode\\werdumps.

USO (con el repo como directorio de trabajo):
    .venv\\Scripts\\python.exe diagnostics\\diag_category_probe.py
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
logger = get_logger("diag_category_probe")

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")

OPTIONS_DUMP_JS = """
() => {
  const out = [];
  const sel = '[role="option"], [role="menuitem"], [role="menuitemradio"], '
            + '[role="listbox"] *, [role="dialog"] [role="button"], '
            + '[role="dialog"] li, [role="dialog"] div[aria-label]';
  const seen = new Set();
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const text = (el.innerText || el.textContent || '').trim();
    if (!text || text.length > 80) continue;
    const key = text + '|' + (el.getAttribute('role') || '');
    if (seen.has(key)) continue;
    seen.add(key);
    const data = {};
    for (const attr of el.attributes) {
      if (attr.name.startsWith('data-')) data[attr.name] = attr.value.slice(0, 60);
    }
    out.push({
      tag: el.tagName,
      role: el.getAttribute('role') || '',
      ariaLabel: (el.getAttribute('aria-label') || '').slice(0, 80),
      ariaSelected: el.getAttribute('aria-selected') || '',
      ariaChecked: el.getAttribute('aria-checked') || '',
      text: text.slice(0, 80),
      data,
    });
  }
  return out;
}
"""

ALL_OPTIONS_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('[role="option"]')) {
    const r = el.getBoundingClientRect();
    out.push({
      role: el.getAttribute('role') || '',
      ariaLabel: (el.getAttribute('aria-label') || '').slice(0, 80),
      text: (el.innerText || el.textContent || '').trim().slice(0, 80),
      visible: r.width > 0 && r.height > 0,
    });
  }
  return out;
}
"""

PAGE_TEXT_JS = """
() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('div, span, label, li, h1, h2, h3, a, [role="button"]')) {
    const t = (el.innerText || '').trim();
    if (!t || t.length < 2 || t.length > 100) continue;
    if (seen.has(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    seen.add(t);
    out.push(t);
  }
  return out;
}
"""

DIALOGS_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('[role="dialog"]')) {
    const r = el.getBoundingClientRect();
    const d = {};
    for (const attr of el.attributes) {
      if (attr.name.startsWith('data-')) d[attr.name] = attr.value.slice(0, 50);
    }
    out.push({
      ariaLabel: (el.getAttribute('aria-label') || '').slice(0, 80),
      visible: r.width > 0 && r.height > 0,
      text: (el.innerText || '').trim().slice(0, 200),
      data: d,
    });
  }
  return out;
}
"""


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

        def dump(label: str, js: str) -> None:
            try:
                data = bridge.submit(lambda: page.evaluate(js))
            except Exception as exc:
                logger.warning("No se pudo evaluar %s: %s", label, exc)
                return
            out = {"label": label, "url": bridge.submit(lambda: page.url), "items": data if isinstance(data, list) else []}
            path = DUMP_DIR / f"category_probe_{STAMP}_{label.replace(' ', '_')}.json"
            path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("%s: %d items -> %s", label, len(out["items"]), path)

        async def _open_category():
            combo = page.get_by_role("combobox", name="Categoría")
            if await combo.first.is_visible(timeout=3000):
                await combo.first.click()
                return True
            btn = page.get_by_role("button", name="Categoría")
            if await btn.first.is_visible(timeout=2000):
                await btn.first.click()
                return True
            return False

        opened = bridge.submit(_open_category)
        logger.info("Selector de categoría abierto: %s", opened)
        bridge.submit(lambda: page.wait_for_timeout(2000))

        dump("selector_abierto", OPTIONS_DUMP_JS)
        dump("opciones_role", ALL_OPTIONS_JS)
        dump("dialogos", DIALOGS_JS)

        async def _click_category(name: str) -> bool:
            el = page.get_by_role("button", name=name, exact=False)
            if await el.first.is_visible(timeout=3000):
                await el.first.click()
                return True
            return False

        for name in ("Electrónica e informática", "Electrónica", "Vehículos"):
            if bridge.submit(lambda: _click_category(name)):
                bridge.submit(lambda: page.wait_for_timeout(2000))
                logger.info("URL tras clic en %r: %s", name, bridge.submit(lambda: page.url))
                dump(f"nivel2_{name.replace(' ', '_')}", OPTIONS_DUMP_JS)
                dump(f"nivel2_textos_{name.replace(' ', '_')}", PAGE_TEXT_JS)
                break
        else:
            logger.warning("No se pudo profundizar en ninguna categoría de nivel 1")

        try:
            shot = DUMP_DIR / f"category_probe_{STAMP}.png"
            bridge.submit(lambda: page.screenshot(path=str(shot), full_page=False))
            logger.info("Captura -> %s", shot)
        except Exception as exc:
            logger.warning("No se pudo capturar: %s", exc)

        # Cerrar el selector (Escape) para no dejar la UI abierta.
        try:
            bridge.submit(lambda: page.keyboard.press("Escape"))
        except Exception:
            pass
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