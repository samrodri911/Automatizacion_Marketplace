"""
Script de diagnóstico del DOM real de Facebook Marketplace en "Tus publicaciones".
Inspecciona qué elementos, enlaces, roles, textos y contenedores existen en la página real.
NO modifica ni elimina nada (100% solo lectura).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from playwright.sync_api import sync_playwright

from app.core.config import BROWSER_PROFILE_DIR, SCREENSHOTS_DIR, facebook_config


def inspect_real_dom() -> None:
    print("\n" + "=" * 70)
    print("  DIAGNÓSTICO DEL DOM REAL — Facebook Marketplace / Tus publicaciones")
    print("=" * 70 + "\n")

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    report_file = Path(_ROOT) / "debug_dom_report.json"

    with sync_playwright() as pw:
        print(f"[*] Abriendo Chromium con perfil: {BROWSER_PROFILE_DIR}")
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--start-maximized"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        target_url = facebook_config.your_listings_url
        print(f"[*] Navegando a: {target_url}")
        page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)

        # Espera activa observable para que React renderice
        print("[*] Esperando que la página asiente (load state + contenido)...")
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass

        # Espera extra de 3 segundos para que los componentes dinámicos terminen de hidratar
        time.sleep(3.0)

        print(f"[*] URL actual post-navegación: {page.url}")

        # Tomar captura de lo que se ve
        screenshot_path = SCREENSHOTS_DIR / "debug_selling_dom.png"
        page.screenshot(path=str(screenshot_path))
        print(f"[*] Captura guardada en: {screenshot_path}")

        # ── Script de inspección exhaustiva del DOM ──────────────────────────
        js_inspect = """
        () => {
            const report = {
                url: window.location.href,
                title: document.title,
                bodyTextSnippet: (document.body.innerText || '').slice(0, 500),
                scrollInfo: {
                    windowScrollY: window.scrollY,
                    windowScrollHeight: document.documentElement.scrollHeight,
                    windowClientHeight: document.documentElement.clientHeight,
                },
                scrollableContainers: [],
                allLinksSample: [],
                marketplaceItemLinks: [],
                marketplaceOtherLinks: [],
                elementsWithAria: [],
                cardCandidates: [],
            };

            // 1. Detectar contenedores scrollables internos
            const allElements = document.querySelectorAll('*');
            for (const el of allElements) {
                const style = window.getComputedStyle(el);
                if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 50) {
                    report.scrollableContainers.push({
                        tag: el.tagName,
                        role: el.getAttribute('role'),
                        ariaLabel: el.getAttribute('aria-label'),
                        scrollHeight: el.scrollHeight,
                        clientHeight: el.clientHeight,
                        scrollTop: el.scrollTop,
                        className: (el.className || '').toString().slice(0, 50),
                    });
                }
            }

            // 2. Analizar TODOS los enlaces (<a>)
            const links = Array.from(document.querySelectorAll('a'));
            report.totalLinks = links.length;

            for (const a of links) {
                const href = a.getAttribute('href') || a.href || '';
                const text = (a.innerText || a.textContent || '').trim();
                const ariaLabel = a.getAttribute('aria-label') || '';
                const role = a.getAttribute('role') || '';
                
                // Muestra de enlaces
                if (href.includes('/marketplace/item/')) {
                    const img = a.querySelector('img');
                    report.marketplaceItemLinks.push({
                        href: href,
                        text: text.slice(0, 150),
                        ariaLabel: ariaLabel,
                        role: role,
                        hasImg: !!img,
                        imgSrc: img ? (img.src || '').slice(0, 100) : null,
                        parentTag: a.parentElement ? a.parentElement.tagName : null,
                        parentRole: a.parentElement ? a.parentElement.getAttribute('role') : null,
                        parentTextSnippet: a.parentElement ? (a.parentElement.innerText || '').slice(0, 200) : null,
                    });
                } else if (href.includes('/marketplace/') || href.includes('selling') || href.includes('product')) {
                    report.marketplaceOtherLinks.push({
                        href: href.slice(0, 120),
                        text: text.slice(0, 100),
                        ariaLabel: ariaLabel,
                    });
                }
            }

            // 3. Buscar candidatos a "Tarjetas de Publicación" por diversos enfoques:
            // Enfoque A: elementos que contienen precios ($ / COP)
            const priceRegex = /\\$\\s*[\\d.,]+|[\\d.,]+\\s*(?:COP|USD|EUR|CLP|MXN)/i;
            const cardMap = new Set();
            
            // Buscar contenedores de artículos o elementos interactivos
            const potentialCards = document.querySelectorAll('[role="article"], [role="listitem"], [role="row"], [role="link"], div, a');
            for (const el of potentialCards) {
                const txt = (el.innerText || '').trim();
                if (!txt) continue;
                
                // Si contiene precio y título probable
                if (priceRegex.test(txt) && txt.length < 500 && txt.length > 5) {
                    // Buscar el contenedor más representativo (evitar duplicados de padres/hijos)
                    const aTags = Array.from(el.querySelectorAll('a[href]'));
                    const hrefs = aTags.map(a => a.getAttribute('href') || a.href);
                    const imgs = Array.from(el.querySelectorAll('img[src]')).map(i => i.src).filter(s => s && s.startsWith('http'));
                    const buttons = Array.from(el.querySelectorAll('div[role="button"], button')).map(b => (b.getAttribute('aria-label') || b.innerText || '').trim()).filter(Boolean);

                    // Solo registrar si tiene imagen o link o botón de opciones
                    if (imgs.length > 0 || hrefs.length > 0 || buttons.length > 0) {
                        const signature = txt.slice(0, 50);
                        if (!cardMap.has(signature)) {
                            cardMap.add(signature);
                            report.cardCandidates.push({
                                tagName: el.tagName,
                                role: el.getAttribute('role'),
                                ariaLabel: el.getAttribute('aria-label'),
                                fullText: txt,
                                lines: txt.split('\\n').map(l => l.trim()).filter(Boolean),
                                hrefs: hrefs,
                                imageCount: imgs.length,
                                firstImg: imgs[0] ? imgs[0].slice(0, 100) : null,
                                buttons: buttons,
                                outerHTMLSnippet: el.outerHTML.slice(0, 300),
                            });
                        }
                    }
                }
            }

            return report;
        }
        """

        print("[*] Ejecutando análisis JS en la página...")
        report_data = page.evaluate(js_inspect)

        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        print(f"[*] Reporte JSON guardado en: {report_file}")

        # ── Resumen en consola ───────────────────────────────────────────────
        print("\n" + "=" * 70)
        print("  RESULTADOS DE INSPECCIÓN")
        print("=" * 70)
        print(f"  URL analizada: {report_data.get('url')}")
        print(f"  Título página: {report_data.get('title')}")
        print(f"  Total links (<a>): {report_data.get('totalLinks')}")
        print(f"  Links con /marketplace/item/: {len(report_data.get('marketplaceItemLinks', []))}")
        print(f"  Otros links marketplace: {len(report_data.get('marketplaceOtherLinks', []))}")
        print(f"  Contenedores scrollables internos: {len(report_data.get('scrollableContainers', []))}")
        print(f"  Candidatos a tarjeta detectados: {len(report_data.get('cardCandidates', []))}")
        print("-" * 70)

        item_links = report_data.get("marketplaceItemLinks", [])
        if item_links:
            print("\n[+] EJEMPLOS DE LINKS /marketplace/item/ ENCONTRADOS:")
            for idx, item in enumerate(item_links[:5], 1):
                print(f"  {idx}. Href: {item.get('href')}")
                print(f"     Texto: {item.get('text')!r}")
                print(f"     Aria:  {item.get('ariaLabel')!r}")
                print(f"     Parent: <{item.get('parentTag')} role='{item.get('parentRole')}'> snippet: {item.get('parentTextSnippet')!r}")
        else:
            print("\n[-] NO SE ENCONTRARON enlaces con '/marketplace/item/'")

        other_links = report_data.get("marketplaceOtherLinks", [])
        if other_links:
            print("\n[+] OTROS ENLACES DE MARKETPLACE/SELLING ENCONTRADOS:")
            for idx, item in enumerate(other_links[:8], 1):
                print(f"  {idx}. Href: {item.get('href')} | Texto: {item.get('text')!r}")

        cards = report_data.get("cardCandidates", [])
        if cards:
            print(f"\n[+] CANDIDATOS A PUBLICACIONES POR TEXTO/PRECIO ({len(cards)} encontrados):")
            for idx, card in enumerate(cards[:5], 1):
                print(f"\n  --- Candidato {idx} ---")
                print(f"  Tag: <{card.get('tagName')} role='{card.get('role')}'>")
                print(f"  Líneas de texto: {card.get('lines')}")
                print(f"  Hrefs dentro: {card.get('hrefs')}")
                print(f"  Botones dentro: {card.get('buttons')}")
                print(f"  Imágenes: {card.get('imageCount')} (ej: {card.get('firstImg')})")
        else:
            print("\n[-] NO SE DETECTARON tarjetas con precios en el DOM")

        scrollables = report_data.get("scrollableContainers", [])
        if scrollables:
            print("\n[+] CONTENEDORES SCROLLABLES DETECTADOS:")
            for idx, sc in enumerate(scrollables, 1):
                print(f"  {idx}. Tag: {sc.get('tag')} role: {sc.get('role')} aria: {sc.get('ariaLabel')} scrollHeight: {sc.get('scrollHeight')} clientHeight: {sc.get('clientHeight')}")

        print("\n" + "=" * 70)
        print("Cerrando contexto de diagnóstico...")
        for p in context.pages:
            try:
                p.close()
            except Exception:
                pass
        context.close()
        print("Diagnóstico finalizado exitosamente.")


if __name__ == "__main__":
    inspect_real_dom()
