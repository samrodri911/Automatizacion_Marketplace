"""
Refinamiento y verificación exhaustiva de la extracción de listings en Facebook Marketplace.
"""

from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from playwright.sync_api import sync_playwright
from app.automation.listing_matcher import parse_price_from_text
from app.core.config import BROWSER_PROFILE_DIR, facebook_config
from app.models.listing import Listing


def test_refined_extractor() -> None:
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

        js_extract = """
        () => {
            const diagnostics = {
                strategyUsed: '',
                candidatesFound: 0,
                discarded: 0,
                listingsExtracted: 0,
                cards: []
            };

            const priceRegex = /\\$\\s*[\\d.,]+|[\\d.,]+\\s*(?:COP|USD|EUR|CLP|MXN|PEN|ARS|CRC|GTQ)/i;
            const menuAriaRegex = /^(?:Más opciones para|More options for|Opciones para|Options for)\\s+(.+)$/i;

            // -------------------------------------------------------------
            // Estrategia A: Botones de opciones del panel de vendedor
            // -------------------------------------------------------------
            const optionButtons = Array.from(document.querySelectorAll('div[role="button"], button, [aria-label]'));
            const cardsMap = new Map(); // key -> cardData

            for (const btn of optionButtons) {
                const aria = (btn.getAttribute('aria-label') || '').trim();
                const menuMatch = aria.match(menuAriaRegex);
                if (!menuMatch) continue;

                diagnostics.candidatesFound++;
                const title = menuMatch[1].trim();

                // Subir en el DOM hasta encontrar el contenedor que engloba precio e imagen
                let root = btn.parentElement;
                let foundPrice = false;
                let bestImg = '';
                let bestText = '';
                let bestUrl = '';
                let bestRef = '';

                for (let i = 0; i < 15 && root && root !== document.body; i++) {
                    const text = (root.innerText || '').trim();
                    if (!foundPrice && priceRegex.test(text)) {
                        foundPrice = true;
                        bestText = text;
                    }
                    if (foundPrice) {
                        const imgs = Array.from(root.querySelectorAll('img[src]'))
                            .map(img => img.src)
                            .filter(src => src && src.startsWith('http') && !src.includes('rsrc.php'));
                        if (imgs.length > 0) {
                            bestImg = imgs[0];
                            bestText = text;
                            break; // Contenedor ideal encontrado
                        }
                    }
                    root = root.parentElement;
                }

                if (!foundPrice) {
                    diagnostics.discarded++;
                    continue;
                }

                // Buscar posible referencia/target_id
                if (root) {
                    const links = Array.from(root.querySelectorAll('a[href]'));
                    for (const a of links) {
                        const href = a.getAttribute('href') || a.href || '';
                        const itemMatch = href.match(/\\/marketplace\\/item\\/(\\d+)/);
                        if (itemMatch) {
                            bestRef = itemMatch[1];
                            bestUrl = href;
                            break;
                        }
                        const targetMatch = href.match(/target_id=(\\d+)/);
                        if (targetMatch && !bestRef) {
                            bestRef = targetMatch[1];
                            bestUrl = `https://www.facebook.com/marketplace/item/${bestRef}/`;
                        }
                    }
                }

                // Clave de unicidad por título normalizado
                const normKey = title.toLowerCase().replace(/\\s+/g, ' ');
                if (!cardsMap.has(normKey) || (bestImg && !cardsMap.get(normKey).image_srcs.length)) {
                    cardsMap.set(normKey, {
                        strategy: 'seller_menu_aria',
                        title: title,
                        raw_text: bestText,
                        url: bestUrl,
                        reference: bestRef,
                        image_srcs: bestImg ? [bestImg] : [],
                    });
                }
            }

            if (cardsMap.size > 0) {
                diagnostics.strategyUsed = 'seller_menu_aria';
                diagnostics.cards = Array.from(cardsMap.values());
                diagnostics.listingsExtracted = diagnostics.cards.length;
                return diagnostics;
            }

            // -------------------------------------------------------------
            // Estrategia B: Enlaces directos a /marketplace/item/
            // -------------------------------------------------------------
            const itemLinks = Array.from(document.querySelectorAll('a[href*="/marketplace/item/"]'));
            if (itemLinks.length > 0) {
                diagnostics.strategyUsed = 'item_links';
                for (const link of itemLinks) {
                    diagnostics.candidatesFound++;
                    const href = link.getAttribute('href') || link.href || '';
                    const text = (link.innerText || '').trim();
                    if (!text) {
                        diagnostics.discarded++;
                        continue;
                    }
                    const imgs = Array.from(link.querySelectorAll('img')).map(i => i.src).filter(s => s && s.startsWith('http'));
                    const title = text.split('\\n')[0].trim();
                    const ref = (href.match(/\\/marketplace\\/item\\/(\\d+)/) || [])[1] || '';
                    diagnostics.cards.push({
                        strategy: 'item_links',
                        title: title,
                        raw_text: text,
                        url: href,
                        reference: ref,
                        image_srcs: imgs,
                    });
                }
                diagnostics.listingsExtracted = diagnostics.cards.length;
                return diagnostics;
            }

            // -------------------------------------------------------------
            // Estrategia C: Contenedores con precio + botones de acción
            // -------------------------------------------------------------
            const actionButtons = Array.from(document.querySelectorAll('div[role="button"], button'));
            for (const btn of actionButtons) {
                const bText = (btn.innerText || btn.getAttribute('aria-label') || '').toLowerCase();
                if (bText.includes('marcar como') || bText.includes('mark as') || bText.includes('impulsar') || bText.includes('promote')) {
                    diagnostics.candidatesFound++;
                    let root = btn.parentElement;
                    for (let i = 0; i < 10 && root && root !== document.body; i++) {
                        const txt = (root.innerText || '').trim();
                        if (priceRegex.test(txt)) {
                            const lines = txt.split('\\n').map(l => l.trim()).filter(Boolean);
                            // Tomar primera línea que no sea metadatos/sugerencia
                            const titleCandidate = lines.find(l => !l.toLowerCase().startsWith('sugerencia:') && !priceRegex.test(l)) || lines[0];
                            const imgs = Array.from(root.querySelectorAll('img[src]')).map(i => i.src).filter(s => s && s.startsWith('http'));
                            const normKey = titleCandidate.toLowerCase();
                            if (!cardsMap.has(normKey)) {
                                cardsMap.set(normKey, {
                                    strategy: 'action_button_card',
                                    title: titleCandidate,
                                    raw_text: txt,
                                    url: '',
                                    reference: '',
                                    image_srcs: imgs.slice(0, 1),
                                });
                            }
                            break;
                        }
                        root = root.parentElement;
                    }
                }
            }

            diagnostics.strategyUsed = cardsMap.size > 0 ? 'action_button_card' : 'none';
            diagnostics.cards = Array.from(cardsMap.values());
            diagnostics.listingsExtracted = diagnostics.cards.length;
            return diagnostics;
        }
        """

        raw_diag = page.evaluate(js_extract)
        print("\n" + "=" * 70)
        print(f"  DIAGNÓSTICO DEL EXTRACTOR REFINADO")
        print("=" * 70)
        print(f"  Estrategia utilizada:      {raw_diag.get('strategyUsed')}")
        print(f"  Candidatos DOM analizados: {raw_diag.get('candidatesFound')}")
        print(f"  Candidatos descartados:    {raw_diag.get('discarded')}")
        print(f"  Listings extraídos:        {raw_diag.get('listingsExtracted')}")
        print("-" * 70)

        for idx, card in enumerate(raw_diag.get("cards", []), 1):
            price, price_raw = parse_price_from_text(card.get("raw_text", ""))
            listing = Listing(
                title=card.get("title", ""),
                price=price,
                price_raw=price_raw,
                url=card.get("url", ""),
                reference=card.get("reference", ""),
                image_refs=card.get("image_srcs", []),
                raw_text=card.get("raw_text", ""),
            )
            print(f"\n[{idx}] Listing:")
            print(f"    Título:     {listing.title}")
            print(f"    Precio:     {listing.price} (raw: {listing.price_raw!r})")
            print(f"    URL:        {listing.url}")
            print(f"    Referencia: {listing.reference}")
            print(f"    Imágenes:   {len(listing.image_refs)}")
            print(f"    Key:        {listing.key}")

        context.close()


if __name__ == "__main__":
    test_refined_extractor()
