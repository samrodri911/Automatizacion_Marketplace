"""
Verificación de extracción sobre el DOM real de Facebook Marketplace.
Prueba la nueva lógica de extracción multivariante contra la página real.
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from playwright.sync_api import sync_playwright
from app.core.config import BROWSER_PROFILE_DIR, facebook_config


def test_real_extractor() -> None:
    with sync_playwright() as pw:
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
        try:
            page.wait_for_load_state("networkidle", timeout=6_000)
        except Exception:
            pass

        # Script de extracción semántica avanzada y robusta
        js_extract = """
        () => {
            const results = [];
            const priceRegex = /\\$\\s*[\\d.,]+|[\\d.,]+\\s*(?:COP|USD|EUR|CLP|MXN|PEN|ARS|CRC|GTQ)/i;
            const menuRegex = /^(?:Más opciones para|More options for|Opciones para|Options for)\\s+(.+)$/i;
            const ignorePrefixes = [
                'sugerencia:',
                'mantén tus publicaciones',
                'marca los artículos',
                'a partir del',
                'filtros',
                'ordenar por',
                'administrar',
                'panel para vendedores',
                'perfil de marketplace'
            ];

            // Estrategia 1: Buscar botones de "Más opciones para <Título>"
            // Es la señal semántica MÁS FUERTE en el dashboard de vendedor de Facebook
            const allButtons = Array.from(document.querySelectorAll('div[role="button"], button, [aria-label]'));
            const processedCardRoots = new Set();

            for (const btn of allButtons) {
                const aria = (btn.getAttribute('aria-label') || '').trim();
                const menuMatch = aria.match(menuRegex);
                if (!menuMatch) continue;

                const titleFromAria = menuMatch[1].trim();

                // Encontrar el contenedor raíz de la tarjeta (subiendo en el DOM)
                let cardRoot = btn.parentElement;
                let foundPrice = false;
                let cardText = '';
                let cardImg = '';
                let cardUrl = '';
                let reference = '';

                // Subir hasta 12 niveles para encontrar el contenedor que engloba imagen, precio y botones
                for (let i = 0; i < 12 && cardRoot && cardRoot !== document.body; i++) {
                    cardText = (cardRoot.innerText || '').trim();
                    if (priceRegex.test(cardText)) {
                        foundPrice = true;
                        break;
                    }
                    cardRoot = cardRoot.parentElement;
                }

                if (!cardRoot || !foundPrice || processedCardRoots.has(cardRoot)) {
                    continue;
                }
                processedCardRoots.add(cardRoot);

                // Extraer imagen
                const imgs = Array.from(cardRoot.querySelectorAll('img[src]'))
                    .map(img => img.src)
                    .filter(src => src && src.startsWith('http') && !src.includes('rsrc.php'));
                if (imgs.length > 0) {
                    cardImg = imgs[0];
                }

                // Extraer posible enlace / referencia
                const links = Array.from(cardRoot.querySelectorAll('a[href]'));
                for (const a of links) {
                    const href = a.getAttribute('href') || a.href || '';
                    if (href.includes('/marketplace/item/')) {
                        cardUrl = href;
                        const match = href.match(/\\/marketplace\\/item\\/(\\d+)/);
                        if (match) reference = match[1];
                        break;
                    }
                    const targetMatch = href.match(/target_id=(\\d+)/);
                    if (targetMatch && !reference) {
                        reference = targetMatch[1];
                        cardUrl = `https://www.facebook.com/marketplace/item/${reference}/`;
                    }
                }

                results.push({
                    strategy: 'menu_aria',
                    title: titleFromAria,
                    raw_text: cardText,
                    url: cardUrl,
                    reference: reference,
                    image_srcs: cardImg ? [cardImg] : [],
                });
            }

            // Estrategia 2: Si por alguna razón no hay botones con ese aria-label (ej. vista pública o cambios de idioma),
            // escanear contenedores con precio e imagen que tengan botones de acción
            if (results.length === 0) {
                // Estrategia enlaces directos /marketplace/item/
                const itemLinks = Array.from(document.querySelectorAll('a[href*="/marketplace/item/"]'));
                for (const link of itemLinks) {
                    const href = link.getAttribute('href') || link.href || '';
                    const text = (link.innerText || '').trim();
                    if (!text) continue;
                    const imgs = Array.from(link.querySelectorAll('img')).map(i => i.src).filter(s => s && s.startsWith('http'));
                    results.push({
                        strategy: 'item_link',
                        title: text.split('\\n')[0].trim(),
                        raw_text: text,
                        url: href,
                        reference: (href.match(/\\/marketplace\\/item\\/(\\d+)/) || [])[1] || '',
                        image_srcs: imgs,
                    });
                }
            }

            return {
                totalExtracted: results.length,
                results: results
            };
        }
        """

        extraction = page.evaluate(js_extract)
        print("\n" + "=" * 70)
        print(f"  RESULTADO DE EXTRACCIÓN REAL: {extraction.get('totalExtracted')} listings")
        print("=" * 70)
        for idx, item in enumerate(extraction.get("results", []), 1):
            print(f"\n[{idx}] Estrategia: {item.get('strategy')}")
            print(f"    Título:     {item.get('title')}")
            print(f"    Referencia: {item.get('reference')}")
            print(f"    URL:        {item.get('url')}")
            print(f"    Imagen:     {item.get('image_srcs')}")
            print(f"    Texto:      {repr(item.get('raw_text')[:100])}...")

        context.close()


if __name__ == "__main__":
    test_real_extractor()
