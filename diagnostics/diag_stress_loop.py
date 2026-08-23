"""Reproducción local del patrón de búsqueda (evaluate + scroll) SIN Facebook.

Simula el bucle de ListingFinder sobre una página local de datos, con muchas
iteraciones, para ver si el patrón IPC (evaluate/scroll/locator) produce
EPIPE en el driver embebido incluso sin Facebook.

Uso:
    python diag_stress_loop.py
"""
from __future__ import annotations

import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import forensics


def main() -> int:
    import tempfile
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(Path(tempfile.mkdtemp())),
        headless=False,
        viewport={"width": 800, "height": 600},
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    # Página local con enlaces (imita el DOM de "Tus publicaciones").
    html = "<html><body>" + "".join(
        f'<a href="https://www.facebook.com/marketplace/item/{i}"><img src="https://img.invalid/{i}.jpg"><span>Producto {i} - $10</span></a><br>'
        for i in range(200)
    ) + "</body></html>"
    page.set_content(html)

    errors = []
    moved_last = 0
    for i in range(400):
        try:
            page.evaluate("() => { const links = document.querySelectorAll(\"a[href*='/marketplace/item/']\"); void links.length; }")
            page.evaluate("window.scrollBy(0, 200)")
            title = page.evaluate("document.title")
            raw = page.evaluate(
                "() => Array.from(document.querySelectorAll(\"a[href*='/marketplace/item/']\")).map(a => ({ text: a.innerText, url: a.href, src: a.querySelector('img') ? a.querySelector('img').src : '' }))"
            )
            body_text = page.locator("body").inner_text()
            n = len(raw) if isinstance(raw, list) else -1
            # mover el marcador para confirmar que el evaluate se ejecutó
            if i % 100 == 0:
                print(f"ciclo {i}: {n} items, title={title!r}, body_len={len(body_text)}", flush=True)
        except Exception as exc:
            errors.append((i, repr(exc)))
            print(f"EXCEPTION ciclo {i}: {exc!r}", flush=True)
            break

    print(f"driver alive={forensics.driver_alive(pw)}, errores={len(errors)}")
    if errors:
        print("Ultimo error:", errors[-1])
    page.close()
    ctx.close()
    pw.stop()
    return 0 if not errors else 1


if __name__ == "__main__":
    os.environ.setdefault("MM_FORENSICS", "1")
    sys.exit(main())
