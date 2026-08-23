"""Probe del menú de publicación de Marketplace (diagnóstico del fallo delete).

Reproduce la ruta EXACTA de producción (AsyncBridge + BrowserManager async):

1. Abre el navegador con el perfil persistente (sesión de Facebook).
2. Navega a "Tus publicaciones" (/marketplace/you/selling).
3. Vuelca TODOS los botones visibles con su aria-label/title/texto (JSON).
4. Abre el menú "Más opciones para <TÍTULO>" de la PRIMERA tarjeta y vuelca
   los botones del diálogo de menú (ahí está "Eliminar publicación").
5. Cierra el diálogo, abre la primera publicación (clic en la tarjeta) y
   vuelca los botones de la PÁGINA DEL ITEM (el menú que falla en delete).
6. Capturas y JSON a %TEMP%\\opencode\\werdumps.

USO (con el repo como directorio de trabajo):
    .venv\\Scripts\\python.exe diagnostics\\diag_menu_probe.py
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
from app.database.database import Database
from app.database.repositories import MatchedListingsRepository

configure_logging()
logger = get_logger("diag_menu_probe")

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")

BUTTON_DUMP_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('button, [role="button"], [role="menuitem"]')) {
    const r = el.getBoundingClientRect();
    const ariaLabel = el.getAttribute('aria-label') || '';
    const ariaHaspopup = el.getAttribute('aria-haspopup') || '';
    const visible = r.width > 0 && r.height > 0;
    if (!visible && !ariaLabel && !ariaHaspopup) continue;
    out.push({
      tag: el.tagName,
      role: el.getAttribute('role') || '',
      ariaLabel: ariaLabel.slice(0, 120),
      ariaHaspopup,
      title: (el.getAttribute('title') || '').slice(0, 60),
      text: (el.innerText || el.textContent || '').trim().slice(0, 40),
      visible,
    });
  }
  return out;
}
"""


def dump_buttons(bridge, page, label: str) -> None:
    try:
        data = bridge.submit(lambda: page.evaluate(BUTTON_DUMP_JS))
    except Exception as exc:
        logger.warning("No se pudo evaluar el DOM de %s: %s", label, exc)
        return
    out = {
        "label": label,
        "url": bridge.submit(lambda: page.url),
        "buttons": data if isinstance(data, list) else [],
    }
    path = DUMP_DIR / f"menu_probe_{STAMP}_{label.replace(' ', '_')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    total = len(out["buttons"])
    visible = [b for b in out["buttons"] if b.get("visible")]
    logger.info("%s: %d botones (visible=%d) -> %s", label, total, len(visible), path)


def screenshot(bridge, page, label: str) -> None:
    try:
        path = DUMP_DIR / f"menu_probe_{STAMP}_{label.replace(' ', '_')}.png"
        bridge.submit(lambda: page.screenshot(path=str(path), full_page=False))
        logger.info("Captura de %s -> %s", label, path)
    except Exception as exc:
        logger.warning("No se pudo capturar %s: %s", label, exc)


def dump_page_text(bridge, page, label: str) -> None:
    """Vuelca la URL final y el texto del <body> (lo que inspecciona
    verify_deletion_from_page) para diagnosticar señales post-eliminación."""
    try:
        body = bridge.submit(lambda: page.locator("body").inner_text(timeout=5000))
        url = bridge.submit(lambda: page.url)
    except Exception as exc:
        logger.warning("No se pudo volcar el texto de %s: %s", label, exc)
        return
    text = body if isinstance(body, str) else ""
    out = {"label": label, "url": url, "body_text": text[:2000]}
    path = DUMP_DIR / f"menu_probe_{STAMP}_{label.replace(' ', '_')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("%s: URL=%s (%d chars) -> %s", label, url, len(text), path)


async def _click_unlabeled_dialog_menu(page) -> bool:
    """Clic en el botón de menú del item: [role=button][aria-haspopup=dialog]
    visible y SIN aria-label/texto (el '...' de la página del item)."""
    try:
        locator = page.locator("[role='button'][aria-haspopup='dialog']")
        total = await locator.count()
        for i in range(total):
            el = locator.nth(i)
            if not await el.is_visible(timeout=500):
                continue
            label = (await el.get_attribute("aria-label") or "").strip()
            text = (await el.inner_text() or "").strip()
            if label or text:
                continue
            await el.click()
            return True
    except Exception as exc:
        logger.warning("No se pudo abrir el menú del item: %s", exc)
    return False


def item_url_from_db() -> str | None:
    """URL del target congelado más reciente (HIGH) con URL, si existe."""
    try:
        db = Database()
        db.initialize()
        repo = MatchedListingsRepository(db)
        rows = list(repo.list_historical(1)) + list(repo.list_active())
        for row in sorted(rows, key=lambda r: getattr(r, "id", 0) or 0, reverse=True):
            url = getattr(row, "listing_url", "") or ""
            if url:
                return url
    except Exception as exc:
        logger.warning("No se pudo leer el target de la BD: %s", exc)
    return None


def main() -> int:
    bridge = AsyncBridge(name="probe-loop")
    bridge.start()
    bm = BrowserManager()
    try:
        page = bridge.submit(lambda: bm.start())
        logger.info("Navegador abierto (node_pid=%s)", bm.node_pid)

        bridge.submit(
            lambda: page.goto(facebook_config.your_listings_url, wait_until="domcontentloaded", timeout=30000)
        )
        bridge.submit(lambda: page.wait_for_timeout(2500))
        dump_buttons(bridge, page, "tus_publicaciones")
        screenshot(bridge, page, "tus_publicaciones")

        item_url = item_url_from_db()
        logger.info("Item URL del target congelado: %s", item_url)
        if item_url:
            bridge.submit(lambda: page.goto(item_url, wait_until="domcontentloaded", timeout=30000))
            bridge.submit(lambda: page.wait_for_timeout(2500))
            dump_buttons(bridge, page, "pagina_item_url")
            dump_page_text(bridge, page, "pagina_item_url_texto")
            screenshot(bridge, page, "pagina_item_url")

            # Abrir el menú del item: botón [role=button][aria-haspopup=dialog]
            # visible y SIN aria-label/texto (el "..." de la página del item).
            clicked = bridge.submit(lambda: _click_unlabeled_dialog_menu(page))
            logger.info("Menú del item clickeado: %s", clicked)
            if clicked:
                bridge.submit(lambda: page.wait_for_timeout(1200))
                dump_buttons(bridge, page, "menu_dialog_item")
                screenshot(bridge, page, "menu_dialog_item")
                try:
                    bridge.submit(lambda: page.keyboard.press("Escape"))
                except Exception:
                    pass
        else:
            logger.warning("No hay target congelado con URL en la BD")
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