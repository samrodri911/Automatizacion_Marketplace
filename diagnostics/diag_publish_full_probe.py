"""Probe diagnóstico del momento de publicar: estructura de 'Marca',
botones disponibles (Siguiente/Publicar), grupos del paso final y
resultado tras pulsar Publicar (URL + texto)."""

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
logger = get_logger("diag_publish_full_probe")

DUMP_DIR = Path(r"C:\Users\User\AppData\Local\Temp\opencode\werdumps")
DUMP_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")

PRODUCT_DIR = Path(r"C:\Users\User\Documents\Proyectos Personales\marketplace-manager\data\products\laptop-hp-pavilion-15-i5-12gb-ram")
IMAGES = [str(PRODUCT_DIR / f"0{i}.jpeg") for i in range(1, 8)]

# Estructura alrededor de la etiqueta "Marca" y todos los inputs visibles.
MARCA_STRUCTURE_JS = """
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const out = { marca_chain: [], inputs: [], textareas: [], switches: [], buttons: [] };
  for (const el of document.querySelectorAll('label, div, span')) {
    if (!visible(el)) continue;
    const t = (el.innerText || '').trim();
    if (t !== 'Marca') continue;
    const chain = [];
    let node = el;
    for (let d = 0; d < 7 && node; d++) {
      const c = node.querySelector('input[type="text"], textarea');
      chain.push({
        depth: d,
        tag: node.tagName,
        hasInput: !!c,
        inputId: c && c.id ? c.id : null,
        inputName: c && c.name ? c.name : null,
      });
      node = node.parentElement;
    }
    out.marca_chain.push(chain);
    break;
  }
  for (const el of document.querySelectorAll('input[type="text"], textarea')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    out.inputs.push({
      tag: el.tagName, id: el.id, name: el.name,
      aria: el.getAttribute('aria-label') || '',
      labelledby: el.getAttribute('aria-labelledby') || '',
      placeholder: el.getAttribute('placeholder') || '',
      y: Math.round(r.y),
    });
  }
  for (const el of document.querySelectorAll('[role="switch"]')) {
    if (!visible(el)) continue;
    out.switches.push({ aria: el.getAttribute('aria-label') || '', checked: el.getAttribute('aria-checked') });
  }
  for (const el of document.querySelectorAll('[role="button"]')) {
    if (!visible(el)) continue;
    const t = (el.innerText || el.getAttribute('aria-label') || '').trim().split('\\n')[0].trim();
    if (t) out.buttons.push(t);
  }
  return out;
}
"""

AUDIENCE_JS = """
() => {
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const out = { groups: [], buttons: [] };
  for (const el of document.querySelectorAll('[role="checkbox"]')) {
    if (!visible(el)) continue;
    const raw = (el.innerText || '').trim();
    if (!raw || !(raw.toLowerCase().includes('miembro') || raw.toLowerCase().includes('publico'))) continue;
    out.groups.push({ checked: el.getAttribute('aria-checked'), name: raw.split('\\n')[0].trim() });
  }
  for (const el of document.querySelectorAll('[role="button"]')) {
    if (!visible(el)) continue;
    const t = (el.innerText || el.getAttribute('aria-label') || '').trim().split('\\n')[0].trim();
    if (t) out.buttons.push(t);
  }
  return out;
}
"""


def settle(bridge, page, seconds: float = 1.2) -> None:
    bridge.submit(lambda: page.wait_for_timeout(int(seconds * 1000)))


def main() -> int:
    bridge = AsyncBridge(name="publish-full-loop")
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

        for label in ("Encuentro en un lugar público", "Retiro en la puerta"):
            try:
                box = page.get_by_role("checkbox", name=label, exact=False).first
                if bridge.submit(lambda: box.is_visible(timeout=1500)):
                    bridge.submit(lambda: box.click())
            except Exception:
                pass
        settle(bridge, page, 1.0)

        # Estructura de Marca + controles del formulario.
        data = bridge.submit(lambda: page.evaluate(MARCA_STRUCTURE_JS))
        path = DUMP_DIR / f"marca_structure_{STAMP}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Estructura Marca -> %s", path)
        logger.info("CADENA MARCA: %s", json.dumps(data.get("marca_chain", []), ensure_ascii=False))
        logger.info("INPUTS:")
        for i in data["inputs"]:
            logger.info("  %s id=%s y=%s aria=%r ph=%r", i["tag"], i["id"], i["y"], i["aria"], i["placeholder"])
        logger.info("SWITCHES: %s", data["switches"])
        logger.info("BOTONES (%d):", len(data["buttons"]))
        for b in data["buttons"]:
            logger.info("  %r", b)

        shot = DUMP_DIR / f"form_details_{STAMP}.png"
        bridge.submit(lambda: page.screenshot(path=str(shot), full_page=False))
        logger.info("Captura formulario -> %s", shot)

        # Paso a Siguiente si existe.
        next_clicked = False
        try:
            el = page.get_by_role("button", name="Siguiente", exact=False).first
            if bridge.submit(lambda: el.is_visible(timeout=2000)):
                bridge.submit(lambda: el.click())
                next_clicked = True
                settle(bridge, page, 3.0)
                logger.info("Pulsado 'Siguiente'")
        except Exception as exc:
            logger.warning("Siguiente: %s", exc)

        if next_clicked:
            aud = bridge.submit(lambda: page.evaluate(AUDIENCE_JS))
            logger.info("URL tras Siguiente: %s", bridge.submit(lambda: page.url))
            logger.info("GRUPOS (%d):", len(aud["groups"]))
            for g in aud["groups"]:
                logger.info("  [%s] %r", g["checked"], g["name"])
            logger.info("BOTONES AUDIENCE: %s", aud["buttons"])
            shot = DUMP_DIR / f"audience_{STAMP}.png"
            bridge.submit(lambda: page.screenshot(path=str(shot), full_page=False))
            logger.info("Captura audience -> %s", shot)

        # Publicar.
        for token in ("Publicar", "Publish", "Publicar en Marketplace"):
            try:
                el = page.get_by_role("button", name=token, exact=True).first
                if bridge.submit(lambda: el.is_visible(timeout=2000)):
                    bridge.submit(lambda: el.click())
                    logger.info("Pulsado Publicar EXACTO (%s)", token)
                    break
            except Exception:
                continue
        settle(bridge, page, 3.0)

        final_url = bridge.submit(lambda: page.url)
        body = ""
        try:
            body = bridge.submit(lambda: page.locator("body").inner_text(timeout=5000))[:3000]
        except Exception:
            pass
        out = {"label": "post_publish", "url": final_url, "body": body}
        path = DUMP_DIR / f"post_publish_{STAMP}.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("POST-PUBLICAR URL: %s", final_url)
        logger.info("POST-PUBLICAR BODY (primeros 1500):")
        logger.info("%s", body[:1500])
        try:
            shot = DUMP_DIR / f"post_publish_{STAMP}.png"
            bridge.submit(lambda: page.screenshot(path=str(shot), full_page=False))
            logger.info("Captura post-publicar -> %s", shot)
        except Exception as exc:
            logger.warning("Captura post: %s", exc)
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