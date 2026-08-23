"""Extracción de publicaciones (listings) desde la página de Facebook.

Responsabilidad: transformar los elementos del DOM de "Tus publicaciones"
de Facebook en objetos tipados `Listing`. No decide nada sobre
coincidencias (eso lo hace `listing_matcher.py`).

Estrategias de extracción (multivariante y semántica):

1. `seller_menu_aria`: Botones de opciones del panel de vendedor
   (`[aria-label*="Más opciones para <Título>"]` / `[aria-label*="More options for <Title>"]`).
   Es la señal semántica más robusta y exacta en Facebook Marketplace moderno.
2. `item_links`: Enlaces directos a `/marketplace/item/<id>` (para vistas de feed
   público o versiones web de Marketplace que usan etiquetas <a>).
3. `seller_action_card`: Contenedores con precio y botones de acción ("Marcar como vendido",
   "Compartir", etc.) cuando los aria-labels varían.
4. `mock_fallback`: Barrido semántico mediante locators de Playwright para compatibilidad
   con mocks de tests unitarios.

Todas las llamadas de extracción en el navegador real se ejecutan atómicamente
en una sola llamada JS IPC (`page.evaluate`) para máxima resiliencia.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.automation import selectors
from app.automation.listing_matcher import parse_price_from_text
from app.core import forensics
from app.core.logging_config import get_logger
from app.models.listing import Listing

logger = get_logger(__name__)

_HTTP_SRC_RE = re.compile(r"^https?://", re.IGNORECASE)

# Prefijos de metadatos o sugerencias de Facebook que no deben ser el título
_META_PREFIXES = (
    "sugerencia:",
    "suggestion:",
    "mantén tus publicaciones",
    "marca los artículos",
    "a partir del",
    "filtros",
    "ordenar por",
    "administrar",
    "panel para vendedores",
    "perfil de marketplace",
)


@dataclass
class CardRawData:
    """Material crudo de una tarjeta de publicación (antes de parsear)."""

    text: str
    url: str = ""
    reference: str = ""
    title: str = ""
    image_srcs: list[str] = field(default_factory=list)
    strategy: str = "unknown"


def _extract_title_from_text(text: str) -> str:
    """Obtiene el título más verosímil del texto crudo de la tarjeta,
    ignorando líneas de sugerencias o metadatos de Facebook."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in lines:
        low = line.casefold()
        if any(low.startswith(p) for p in _META_PREFIXES):
            continue
        # Descartar líneas que sean únicamente precios o separadores
        if line.startswith("$") or line in ("·", "•", "—", "-"):
            continue
        return line
    return lines[0] if lines else ""


def clean_image_refs(srcs: list[str]) -> list[str]:
    """Filtra srcs vacíos y no-HTTP (data URIs, recursos internos rsrc.php)."""
    seen: list[str] = []
    for src in srcs or []:
        if not src or not _HTTP_SRC_RE.match(src):
            continue
        if "rsrc.php" in src:
            continue
        if src not in seen:
            seen.append(src)
    return seen


def parse_card(raw: CardRawData | str, url: str = "", image_srcs: list[str] | None = None) -> Listing:
    """Convierte el contenido crudo de una tarjeta en un `Listing`.

    Función pura: testeable sin navegador.
    """
    if isinstance(raw, CardRawData):
        raw_text = (raw.text or "").strip()
        title = raw.title.strip() if raw.title else _extract_title_from_text(raw_text)
        card_url = raw.url or url
        card_ref = raw.reference or selectors.extract_item_reference(card_url)
        imgs = raw.image_srcs if raw.image_srcs else (image_srcs or [])
    else:
        raw_text = (raw or "").strip()
        title = _extract_title_from_text(raw_text)
        card_url = url
        card_ref = selectors.extract_item_reference(card_url)
        imgs = image_srcs or []

    price, price_raw = parse_price_from_text(raw_text)

    return Listing(
        title=title,
        price=price,
        price_raw=price_raw,
        url=(card_url or "").strip(),
        reference=card_ref.strip(),
        image_refs=clean_image_refs(imgs),
        raw_text=raw_text,
    )


class ListingExtractor:
    """Extrae listings de una `Page` de Playwright de forma robusta y semántica.

    No es thread-safe: debe usarse desde el hilo de automatización.
    """

    def __init__(self) -> None:
        self._link_pattern = selectors.LISTING_ITEM_URL_PATTERN

    # -- Extracción principal -------------------------------------------------
    def extract_listings(self, page) -> list[Listing]:
        """Devuelve los listings encontrados en la página actual."""
        forensics.evt("listing_extractor.start")
        raw_cards = self._collect_cards_atomic(page)

        listings: list[Listing] = []
        for c in raw_cards:
            item = parse_card(c)
            if item.title:
                listings.append(item)

        # Deduplicación por key en la tanda extraída
        unique_listings: list[Listing] = []
        seen_keys: set[str] = set()
        for item in listings:
            if item.key not in seen_keys:
                seen_keys.add(item.key)
                unique_listings.append(item)

        logger.info(
            "Extracción completada: %d listings únicos obtenidos (total crudo=%d)",
            len(unique_listings),
            len(raw_cards),
        )
        forensics.evt("listing_extractor.finish", f"n={len(unique_listings)}")
        return unique_listings

    def extract_from_url(self, page, url: str) -> Listing | None:
        """Navega a una URL concreta y extrae su listing (señal prioritaria
        por referencia conocida). Devuelve None si no se pudo extraer."""
        logger.info("Extrayendo listing desde URL directa: %s", url)
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:
            logger.warning("No se pudo abrir la URL directa del listing: %s", exc)
            return None

        listings = self.extract_listings(page)
        if not listings:
            logger.warning("No se extrajo ningún listing desde la URL directa")
            return None
        return listings[0]

    # -- Colección de tarjetas atómica (JS en el navegador) --------------------
    def _collect_cards_atomic(self, page) -> list[CardRawData]:
        """Ejecuta una evaluación JS multivariante atómica en el DOM de la página.
        Devuelve la lista de `CardRawData` con diagnósticos claros."""
        cards: list[CardRawData] = []
        try:
            js_script = """
            () => {
                const diagnostics = {
                    strategyUsed: 'none',
                    candidatesFound: 0,
                    discarded: 0,
                    cards: []
                };

                const priceRegex = /\\$\\s*[\\d.,]+|[\\d.,]+\\s*(?:COP|USD|EUR|CLP|MXN|PEN|ARS|CRC|GTQ)/i;
                const menuAriaRegex = /^(?:Más opciones para|More options for|Opciones para|Options for)\\s+(.+)$/i;

                // -------------------------------------------------------------
                // Estrategia 1: Menú de opciones de vendedor (Tus publicaciones)
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
                                break;
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

                    const priceMatch = bestText.match(priceRegex);
                    const priceKey = priceMatch ? priceMatch[0].replace(/\\s+/g, '') : '';
                    const cardKey = (bestRef ? ('ref:' + bestRef) : '') || (title.toLowerCase() + '|' + priceKey);

                    if (!cardsMap.has(cardKey) || (bestImg && !cardsMap.get(cardKey).image_srcs.length)) {
                        cardsMap.set(cardKey, {
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
                    return diagnostics;
                }

                // -------------------------------------------------------------
                // Estrategia 2: Enlaces directos a /marketplace/item/
                // -------------------------------------------------------------
                const itemLinks = Array.from(document.querySelectorAll('a[href*="/marketplace/item/"]'));
                if (itemLinks.length > 0) {
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
                    if (diagnostics.cards.length > 0) {
                        diagnostics.strategyUsed = 'item_links';
                        return diagnostics;
                    }
                }

                // -------------------------------------------------------------
                // Estrategia 3: Contenedores con precio + botones de acción de venta
                // -------------------------------------------------------------
                const actionButtons = Array.from(document.querySelectorAll('div[role="button"], button'));
                for (const btn of actionButtons) {
                    const bText = (btn.innerText || btn.getAttribute('aria-label') || '').toLowerCase();
                    if (bText.includes('marcar como') || bText.includes('mark as') || bText.includes('impulsar') || bText.includes('promote') || bText.includes('compartir')) {
                        diagnostics.candidatesFound++;
                        let root = btn.parentElement;
                        for (let i = 0; i < 12 && root && root !== document.body; i++) {
                            const txt = (root.innerText || '').trim();
                            if (priceRegex.test(txt)) {
                                const lines = txt.split('\\n').map(l => l.trim()).filter(Boolean);
                                const titleCandidate = lines.find(l => !l.toLowerCase().startsWith('sugerencia:') && !priceRegex.test(l)) || lines[0];
                                const imgs = Array.from(root.querySelectorAll('img[src]')).map(i => i.src).filter(s => s && s.startsWith('http'));
                                const normKey = titleCandidate.toLowerCase();
                                if (!cardsMap.has(normKey)) {
                                    cardsMap.set(normKey, {
                                        strategy: 'seller_action_card',
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

                diagnostics.strategyUsed = cardsMap.size > 0 ? 'seller_action_card' : 'none';
                diagnostics.cards = Array.from(cardsMap.values());
                return diagnostics;
            }
            """
            raw_diag = page.evaluate(js_script)
            if isinstance(raw_diag, dict):
                strategy = raw_diag.get("strategyUsed", "none")
                candidates = raw_diag.get("candidatesFound", 0)
                discarded = raw_diag.get("discarded", 0)
                card_list = raw_diag.get("cards", [])

                if card_list:
                    for item in card_list:
                        if isinstance(item, dict):
                            cards.append(
                                CardRawData(
                                    title=item.get("title", ""),
                                    text=item.get("raw_text", item.get("text", "")),
                                    url=item.get("url", ""),
                                    reference=item.get("reference", ""),
                                    image_srcs=item.get("image_srcs", []),
                                    strategy=item.get("strategy", strategy),
                                )
                            )
                    logger.info(
                        "Extracción JS exitosa: %d cards (estrategia=%s, candidatos=%d, descartados=%d)",
                        len(cards),
                        strategy,
                        candidates,
                        discarded,
                    )
                    return cards
                else:
                    logger.debug(
                        "Extracción JS devolvió 0 cards (estrategia=%s, candidatos=%d, descartados=%d)",
                        strategy,
                        candidates,
                        discarded,
                    )
            elif isinstance(raw_diag, list) and raw_diag:
                for item in raw_diag:
                    if isinstance(item, dict):
                        cards.append(
                            CardRawData(
                                title=item.get("title", ""),
                                text=item.get("raw_text", item.get("text", "")),
                                url=item.get("url", ""),
                                reference=item.get("reference", ""),
                                image_srcs=item.get("image_srcs", []),
                                strategy="mock_list",
                            )
                        )
                return cards
        except Exception as exc:
            logger.debug("Evaluación JS atómica devolvió excepción (usando fallback locators): %s", exc)

        # Fallback a locators de Playwright (para compatibilidad con Mocks de tests unitarios)
        return self._collect_fallback_locators(page)

    def _collect_fallback_locators(self, page) -> list[CardRawData]:
        """Fallback semántico mediante locators de Playwright para tests y entornos simulados."""
        cards: list[CardRawData] = []
        try:
            forensics.evt("locator", "link_cards")
            links = page.get_by_role("link")
            for link in links.all():
                href = link.get_attribute("href") or ""
                if not self._link_pattern.search(href):
                    continue
                try:
                    text = (link.inner_text() or "").strip()
                except Exception:
                    text = ""
                if not text:
                    continue
                img_srcs: list[str] = []
                try:
                    for img in link.locator("img").all():
                        src = img.get_attribute("src")
                        if src:
                            img_srcs.append(src)
                except Exception:
                    pass
                ref = selectors.extract_item_reference(href)
                cards.append(
                    CardRawData(
                        title=_extract_title_from_text(text),
                        text=text,
                        url=href,
                        reference=ref,
                        image_srcs=img_srcs,
                        strategy="mock_locator_link",
                    )
                )
        except Exception as exc:
            logger.debug("Fallo en fallback por locators de links: %s", exc)

        if not cards:
            try:
                imgs = page.get_by_role("img")
                for img in imgs.all():
                    src = img.get_attribute("src") or ""
                    if not _HTTP_SRC_RE.match(src):
                        continue
                    try:
                        container = img.evaluate("(el) => el.closest('a, [role=link], div')")
                    except Exception:
                        container = None
                    if container is None:
                        continue
                    try:
                        text = (img.evaluate("(el) => (el.closest('div, a') ? el.closest('div, a').innerText : '')") or "").strip()
                    except Exception:
                        text = ""
                    if not text:
                        continue
                    cards.append(
                        CardRawData(
                            title=_extract_title_from_text(text),
                            text=text,
                            url="",
                            reference="",
                            image_srcs=[src],
                            strategy="mock_locator_img",
                        )
                    )
            except Exception as exc:
                logger.debug("Fallo en fallback por locators de imgs: %s", exc)

        return cards