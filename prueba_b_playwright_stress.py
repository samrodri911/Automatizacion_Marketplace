"""
PRUEBA B — Playwright puro, sin Qt.

Objetivo
--------
Aislar si el crash (ACCESS_VIOLATION 0xc0000005 / EPIPE) ocurre cuando
Playwright trabaja intensivamente con el DOM de Facebook Marketplace SIN
ningún QThread, QObject ni PySide6 de por medio.

Si este script crashea  → el problema es Playwright/Chromium bajo DOM
                           pesado, o la API sync de Playwright (no Qt).
Si NO crashea           → el problema es la interacción Qt/QThread ↔ Playwright.

Metodología
-----------
1. Abre Chromium con el MISMO perfil persistente que usa la app real.
2. Navega a "Tus publicaciones" (la misma URL que usa MarketplaceAdapter).
3. Repite 15 veces:
   a. Extracción atómica JS (misma lógica que listing_extractor._collect_link_cards).
   b. scroll_feed equivalente (con la versión CORREGIDA: sin `arguments[0]`).
   c. Log de resultados.
4. Registra memoria RSS del proceso Python en cada iteración.
5. Al terminar, imprime resumen.

Requisitos
----------
- Tener la sesión de Facebook ya activa en el perfil persistente.
- Ejecutar desde el directorio raíz del proyecto:
    python prueba_b_playwright_stress.py

Nota sobre scroll_feed:
  La versión original tiene un bug: `window.scrollBy(0, arguments[0])` en
  un contexto donde NO hay función Arrow. Aquí usamos la forma CORRECTA:
    page.evaluate(f"window.scrollBy(0, {step_px})")
  para no interferir con el diagnóstico.
"""

from __future__ import annotations

import os
import sys
import time
import traceback

# ── Aseguramos que el package "app" sea importable ──────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False
    print("[WARN] psutil no disponible — no se medirá RSS. Instala con: pip install psutil")

from playwright.sync_api import sync_playwright

# ── Configuración (igual que la app real) ───────────────────────────────────
from app.core.config import BROWSER_PROFILE_DIR, facebook_config, search_limits

YOUR_LISTINGS_URL = facebook_config.your_listings_url
SCROLL_STEP_PX    = search_limits.scroll_step_px
ITERATIONS        = 15
SETTLE_MS         = 2_000   # ms de espera post-scroll (igual que _settle())


# ── Helpers ─────────────────────────────────────────────────────────────────

def rss_mb() -> float:
    """RSS del proceso Python actual en MB."""
    if not _HAS_PSUTIL:
        return -1.0
    try:
        return psutil.Process(os.getpid()).memory_info().rss / 1_048_576
    except Exception:
        return -1.0


def atomic_extract(page) -> list[dict]:
    """Extracción atómica JS — idéntica a listing_extractor._collect_link_cards."""
    js = """
    () => {
        const results = [];
        const links = Array.from(document.querySelectorAll('a[href*="/marketplace/item/"]'));
        for (const link of links) {
            const href = link.href || link.getAttribute('href') || '';
            const text = (link.innerText || '').trim();
            if (!text) continue;
            const image_srcs = Array.from(link.querySelectorAll('img'))
                                    .map(img => img.src)
                                    .filter(src => src && src.startsWith('http'));
            results.push({ text: text, url: href, image_srcs: image_srcs });
        }
        return results;
    }
    """
    return page.evaluate(js) or []


def corrected_scroll(page, step_px: int) -> bool:
    """
    Scroll correcto (sin `arguments[0]` en arrow-function).
    La app original tiene el bug: page.evaluate("window.scrollBy(0, arguments[0])", step_px)
    dentro de una arrow-function, donde `arguments` no existe.
    Aquí se interpola directamente para aislar el diagnóstico del bug de scroll.
    """
    before = page.evaluate("window.scrollY")
    page.evaluate(f"window.scrollBy(0, {step_px})")
    try:
        page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    except Exception:
        pass
    after = page.evaluate("window.scrollY")
    return after > before


def buggy_scroll(page, step_px: int) -> bool:
    """
    Scroll ORIGINAL con el bug (arguments[0] en evaluate arrow-function).
    Se usa para verificar si ESTE bug específico causa el crash.
    """
    before = page.evaluate("window.scrollY")
    page.evaluate("window.scrollBy(0, arguments[0])", step_px)   # <-- BUG
    try:
        page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    except Exception:
        pass
    after = page.evaluate("window.scrollY")
    return after > before


# ── Script principal ─────────────────────────────────────────────────────────

def run_stress_test(use_buggy_scroll: bool = False) -> None:
    scroll_label = "BUGGY (original)" if use_buggy_scroll else "CORRECTO"
    print(f"\n{'='*60}")
    print(f"  PRUEBA B — Playwright puro, sin Qt")
    print(f"  scroll_feed: {scroll_label}")
    print(f"  Iteraciones: {ITERATIONS}")
    print(f"  Perfil: {BROWSER_PROFILE_DIR}")
    print(f"{'='*60}\n")

    results: list[dict] = []
    start_rss = rss_mb()

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=False,
            args=["--no-sandbox"],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        # ── Navegación inicial ──────────────────────────────────────────
        print(f"[0] Navegando a: {YOUR_LISTINGS_URL}")
        page.goto(YOUR_LISTINGS_URL, wait_until="domcontentloaded", timeout=30_000)
        try:
            page.wait_for_load_state("networkidle", timeout=5_000)
        except Exception:
            pass
        print(f"[0] URL actual: {page.url}")
        print(f"[0] RSS inicial Python: {start_rss:.1f} MB\n")

        # ── Iteraciones de estrés ───────────────────────────────────────
        for i in range(1, ITERATIONS + 1):
            iter_start = time.monotonic()
            rss_before = rss_mb()

            try:
                # Extracción atómica
                cards = atomic_extract(page)
                n_cards = len(cards)

                # Scroll
                if use_buggy_scroll:
                    moved = buggy_scroll(page, SCROLL_STEP_PX)
                else:
                    moved = corrected_scroll(page, SCROLL_STEP_PX)

                elapsed = time.monotonic() - iter_start
                rss_after = rss_mb()
                delta_rss = rss_after - rss_before

                row = {
                    "iter": i,
                    "cards": n_cards,
                    "moved": moved,
                    "elapsed_s": round(elapsed, 2),
                    "rss_mb": round(rss_after, 1),
                    "delta_rss_mb": round(delta_rss, 2),
                    "ok": True,
                    "error": None,
                }
                print(
                    f"[{i:02d}] cards={n_cards:3d}  moved={str(moved):5s}  "
                    f"t={elapsed:.2f}s  RSS={rss_after:.1f}MB (Δ{delta_rss:+.2f})"
                )

            except Exception as exc:
                elapsed = time.monotonic() - iter_start
                row = {
                    "iter": i,
                    "cards": 0,
                    "moved": False,
                    "elapsed_s": round(elapsed, 2),
                    "rss_mb": rss_mb(),
                    "delta_rss_mb": 0.0,
                    "ok": False,
                    "error": str(exc),
                }
                print(f"[{i:02d}] *** EXCEPCION Python *** {exc}")
                traceback.print_exc()

            results.append(row)

        # ── Cierre ordenado ─────────────────────────────────────────────
        print("\nCerrando páginas antes de cerrar contexto...")
        for p in browser.pages:
            try:
                p.close()
            except Exception as ce:
                print(f"  [WARN] Al cerrar página: {ce}")
        try:
            browser.close()
            print("Contexto cerrado limpiamente.")
        except Exception as ce:
            print(f"[WARN] Al cerrar contexto: {ce}")

    # ── Resumen ─────────────────────────────────────────────────────────
    end_rss = rss_mb()
    ok_count   = sum(1 for r in results if r["ok"])
    fail_count = sum(1 for r in results if not r["ok"])
    rss_values = [r["rss_mb"] for r in results if r["rss_mb"] > 0]
    rss_growth = (max(rss_values) - min(rss_values)) if len(rss_values) > 1 else 0.0
    total_cards = sum(r["cards"] for r in results)

    print(f"\n{'='*60}")
    print(f"  RESUMEN PRUEBA B")
    print(f"{'='*60}")
    print(f"  Iteraciones OK  : {ok_count}/{ITERATIONS}")
    print(f"  Excepciones Py  : {fail_count}")
    print(f"  Tarjetas totales: {total_cards}")
    print(f"  RSS inicio      : {start_rss:.1f} MB")
    print(f"  RSS final       : {end_rss:.1f} MB")
    print(f"  Crecimiento RSS : {rss_growth:.1f} MB (max-min durante test)")
    print(f"  scroll_feed     : {scroll_label}")
    print()

    if fail_count == 0:
        print("  ✅ SIN CRASHEO PYTHON — el problema parece estar en Qt/QThread.")
        print("     (Si la app crashea con Qt pero este script no, la causa es la")
        print("      interacción QThread <-> Playwright sync API, no Playwright solo.)")
    else:
        print("  ⛔ EXCEPCIONES DETECTADAS — Playwright puede tener problemas propios.")
        print("     Adjunta los errores arriba al análisis forense.")

    print()
    print("  IMPORTANTE: si el proceso terminó SIN traceback y sin mensaje de")
    print("  resumen, es que hubo un ACCESS_VIOLATION nativo (crash de proceso).")
    print("  En ese caso: el problema ES Playwright/Chromium, independiente de Qt.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    # Por defecto: scroll CORRECTO.
    # Para probar el bug original, pasa --buggy como argumento:
    #   python prueba_b_playwright_stress.py --buggy
    use_buggy = "--buggy" in sys.argv
    run_stress_test(use_buggy_scroll=use_buggy)
