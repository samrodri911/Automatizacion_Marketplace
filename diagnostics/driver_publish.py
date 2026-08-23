"""Driver de validación real: ejecuta ListingCreator.create con el navegador
real (la misma ruta que usa la GUI) sobre el producto 1 y reporta el resultado."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.automation.browser import BrowserManager
from app.automation.listing_creator import ListingCreator
from app.core.async_bridge import AsyncBridge, AsyncProxy
from app.core.config import PRODUCTS_DIR, ensure_directories
from app.core.logging_config import configure_logging, get_logger
from app.database.database import Database
from app.database.repositories import ProductRepository
from app.services.product_service import ProductService

configure_logging()
logger = get_logger("driver_publish")
ensure_directories()

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)


def resolve_images(product) -> list[str]:
    paths = []
    for img in product.images:
        p = Path(img)
        if not p.is_absolute():
            p = PRODUCTS_DIR / p
        if p.exists():
            paths.append(str(p))
    return paths


DUMP_FORM_JS = """
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const out = { inputs: [], textareas: [], combos: [], checkboxes: [], buttons: [] };
  for (const el of document.querySelectorAll('input[type="text"], textarea')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    out.inputs.push({
      tag: el.tagName,
      id: el.id,
      y: Math.round(r.y),
      aria: el.getAttribute('aria-label') || '',
      value: (el.value || '').slice(0, 40),
    });
  }
  for (const el of document.querySelectorAll('[role="combobox"]')) {
    if (!visible(el)) continue;
    out.combos.push({
      aria: el.getAttribute('aria-label') || '',
      text: (el.innerText || '').trim().slice(0, 40),
    });
  }
  for (const el of document.querySelectorAll('[role="checkbox"]')) {
    if (!visible(el)) continue;
    out.checkboxes.push({
      aria: el.getAttribute('aria-label') || '',
      checked: el.getAttribute('aria-checked'),
      text: (el.innerText || '').split('\\n')[0].trim().slice(0, 40),
    });
  }
  for (const el of document.querySelectorAll('[role="button"]')) {
    if (!visible(el)) continue;
    const t = (el.innerText || el.getAttribute('aria-label') || '').trim().split('\\n')[0].trim();
    if (!t) continue;
    out.buttons.push({ text: t.slice(0, 40), disabled: el.getAttribute('aria-disabled') });
  }
  return out;
}
"""


def main() -> int:
    db = Database()
    db.initialize()
    repo = ProductRepository(db)
    service = ProductService(repo)
    product = service.get(1)
    if product is None:
        logger.error("Producto 1 no encontrado")
        return 2
    image_paths = resolve_images(product)
    logger.info("Producto: %r | imágenes: %d", product.title, len(image_paths))

    bridge = AsyncBridge(name="driver-publish")
    bridge.start()
    bm = BrowserManager()
    try:
        async_page = bridge.submit(lambda: bm.start())
        page = AsyncProxy(async_page, bridge)
        creator = ListingCreator()
        res = creator.create(product, page, navigator=None, image_paths=image_paths)
        logger.info("RESULTADO: %s | %s | url=%s ref=%s", res.status, res.detail, res.new_url, res.new_reference)
        if "incompleto" in (res.detail or "") and res.status.name == "PUBLISH_FAILED":
            try:
                dump = page.evaluate(DUMP_FORM_JS)
                logger.info("DUMP FORMULARIO (campos al fallar): tipo=%s", type(dump).__name__)
                for entry in dump.get("inputs", []):
                    logger.info("  input[%s] id=%s y=%s aria=%r value=%r",
                                entry["tag"], entry["id"], entry["y"], entry["aria"], entry["value"])
                for entry in dump.get("combos", []):
                    logger.info("  combo aria=%r text=%r", entry["aria"], entry["text"])
                for entry in dump.get("checkboxes", []):
                    logger.info("  checkbox aria=%r checked=%r text=%r",
                                entry["aria"], entry["checked"], entry["text"])
                for entry in dump.get("buttons", []):
                    logger.info("  button text=%r disabled=%r", entry["text"], entry["disabled"])
                body = page.locator("body").inner_text()
                (DUMP_DIR / f"body_dump_{time.strftime('%H%M%S')}.txt").write_text(body, encoding="utf-8")
                logger.info("Body dump escrito (%d caracteres)", len(body))
            except Exception as exc:
                logger.exception("Error al volcar el formulario: %s", exc)
        if res.verification:
            logger.info("VERIFICACION: confirmed=%s señales=%s", res.verification.confirmed, res.verification_signals)
        if res.status.name == "PUBLISH_UNCERTAIN":
            try:
                cur_url = page.url
                body = page.locator("body").inner_text(timeout=3000) or ""
                logger.info("UNCERTAIN: url=%s | body_len=%d", cur_url, len(body))
                (DUMP_DIR / f"uncertain_body_{time.strftime('%H%M%S')}.txt").write_text(
                    f"URL: {cur_url}\n\n{body}", encoding="utf-8"
                )
                snippet = " ".join((body or "").split())[:400]
                logger.info("UNCERTAIN body snippet: %s", snippet)
            except Exception as exc:
                logger.exception("Error volcando cuerpo en UNCERTAIN: %s", exc)
        return 0 if res.is_confirmed else 1
    finally:
        try:
            bridge.submit(lambda: bm.stop())
        except Exception:
            pass
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())