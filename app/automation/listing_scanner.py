"""Escaneo automático de publicaciones y matching por lote (Batch Matching).

Responsabilidades:
1. Recorrer de forma controlada la sección "Tus publicaciones" de Facebook Marketplace
   sin requerir intervención manual producto a producto.
2. Extraer todas las publicaciones disponibles (mediante scroll acotado, límites
   estrictos y deduplicación atómica).
3. Comparar todas las publicaciones detectadas contra la lista completa de
   productos almacenados en SQLite utilizando `ListingMatcher`.
4. Identificar coincidencias y marcar `auto_selected=True` ÚNICAMENTE para aquellas
   con confianza ALTA (HIGH) inequívoca.
5. Devolver una estructura de datos lista para ser consumida por la GUI (solo lectura).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from app.automation.listing_extractor import ListingExtractor
from app.automation.listing_matcher import (
    ConfidenceLevel,
    ListingMatcher,
    MatchResult,
    _CONFIDENCE_RANK,
)
from app.core import forensics
from app.core.config import search_limits as default_limits
from app.core.exceptions import InterventionRequiredError
from app.core.logging_config import get_logger
from app.models.listing import Listing
from app.models.product import Product

logger = get_logger(__name__)


@dataclass
class ScannedListingItem:
    """Representa una publicación de Facebook escaneada y su evaluación de coincidencia."""

    listing: Listing
    matched_product_id: int | None = None
    matched_product_title: str = ""
    confidence: str = "NO_MATCH"
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    auto_selected: bool = False

    def to_dict(self) -> dict:
        return {
            "listing": {
                "title": self.listing.title,
                "price": self.listing.price,
                "price_raw": self.listing.price_raw,
                "url": self.listing.url,
                "reference": self.listing.reference,
                "image_refs": self.listing.image_refs,
                "key": self.listing.key,
            },
            "matched_product_id": self.matched_product_id,
            "matched_product_title": self.matched_product_title,
            "confidence": self.confidence,
            "score": round(self.score, 1),
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "auto_selected": self.auto_selected,
        }


@dataclass
class ScanBatchResult:
    """Resultado global del escaneo y matching por lote."""

    items: list[ScannedListingItem]
    total_listings: int
    matched_high_count: int
    matched_medium_count: int
    unmatched_count: int
    scrolls_executed: int
    stopped_for: str

    def to_dict(self) -> dict:
        return {
            "items": [item.to_dict() for item in self.items],
            "total_listings": self.total_listings,
            "matched_high_count": self.matched_high_count,
            "matched_medium_count": self.matched_medium_count,
            "unmatched_count": self.unmatched_count,
            "scrolls_executed": self.scrolls_executed,
            "stopped_for": self.stopped_for,
        }


class ListingScanner:
    """Orquesta el barrido completo de publicaciones en Facebook Marketplace."""

    def __init__(
        self,
        page,
        extractor: ListingExtractor | None = None,
        matcher: ListingMatcher | None = None,
        navigator=None,
        limits=None,
    ) -> None:
        self._page = page
        self._extractor = extractor or ListingExtractor()
        self._matcher = matcher or ListingMatcher()
        self._navigator = navigator
        self._limits = limits or default_limits

    def scan_all_listings(self, on_progress: Callable[[int, int, int], None] | None = None) -> list[Listing]:
        """Extrae todas las publicaciones visibles y scrolleables de 'Tus publicaciones'.

        `on_progress(scroll_idx, total_acumulado, nuevos_en_batch)`
        """
        logger.info("[SCAN] Iniciando escaneo automático de 'Tus publicaciones'")
        forensics.evt("listing_scanner.start")

        if self._navigator:
            self._navigator.ensure_listings_section()
            self._check_intervention()

        deadline = time.monotonic() + self._limits.search_timeout_ms / 1000.0
        seen: set[str] = set()
        all_listings: list[Listing] = []
        scrolls = 0
        consecutive_empty_scrolls = 0
        max_stable_scrolls = 2  # Si tras 2 scrolls no hay contenido nuevo, detenerse

        while True:
            self._check_intervention()

            forensics.evt("listing_scanner.iteration", f"scrolls={scrolls}")
            batch = self._extractor.extract_listings(self._page)
            new_listings = [l for l in batch if l.key not in seen]
            seen.update(l.key for l in new_listings)
            all_listings.extend(new_listings)

            logger.info(
                "[SCAN] Scroll %d | candidatos DOM: %d | nuevos listings: %d | total acumulado: %d",
                scrolls,
                len(batch),
                len(new_listings),
                len(all_listings),
            )

            if on_progress:
                on_progress(scrolls, len(all_listings), len(new_listings))

            if not new_listings:
                consecutive_empty_scrolls += 1
            else:
                consecutive_empty_scrolls = 0

            if consecutive_empty_scrolls >= max_stable_scrolls:
                logger.info("[SCAN] Detenido: no se detectó contenido nuevo tras %d scrolls", max_stable_scrolls)
                break

            if time.monotonic() >= deadline:
                logger.info("[SCAN] Detenido por timeout (%s ms)", self._limits.search_timeout_ms)
                break

            if scrolls >= self._limits.max_scrolls:
                logger.info("[SCAN] Detenido: alcanzado límite máximo de scrolls (%d)", self._limits.max_scrolls)
                break

            moved = False
            if self._navigator:
                moved = self._navigator.scroll_feed() or False
            if not moved:
                logger.info("[SCAN] Fin del feed: la página no avanzó más con scroll")
                break

            scrolls += 1

        logger.info("[SCAN] Escaneo finalizado. Total de publicaciones únicas encontradas: %d", len(all_listings))
        forensics.evt("listing_scanner.finish", f"total={len(all_listings)}")
        return all_listings

    def scan_and_match(
        self,
        products: list[Product],
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> ScanBatchResult:
        """Escanea todas las publicaciones y realiza el matching contra todos los productos de SQLite."""
        listings = self.scan_all_listings(on_progress=on_progress)
        logger.info("[MATCH] Iniciando comparación de %d listings contra %d productos de SQLite", len(listings), len(products))

        items: list[ScannedListingItem] = []
        high_count = 0
        medium_count = 0
        unmatched_count = 0

        for listing in listings:
            # Evaluar este listing contra cada producto
            candidate_matches: list[tuple[Product, MatchResult]] = []
            for prod in products:
                match_res = self._matcher.match(prod, listing)
                if match_res.matched:
                    candidate_matches.append((prod, match_res))

            # Ordenar de mayor a menor confianza y score
            candidate_matches.sort(
                key=lambda x: (_CONFIDENCE_RANK[x[1].confidence], x[1].score),
                reverse=True,
            )

            if not candidate_matches:
                unmatched_count += 1
                items.append(
                    ScannedListingItem(
                        listing=listing,
                        matched_product_id=None,
                        matched_product_title="",
                        confidence=ConfidenceLevel.NO_MATCH.name,
                        score=0.0,
                        reasons=[],
                        warnings=["Sin coincidencia con ningún producto guardado"],
                        auto_selected=False,
                    )
                )
                logger.info("[MATCH] Listing '%s' ($%s) -> NO_MATCH", listing.title, listing.price)
                continue

            best_prod, best_match = candidate_matches[0]

            # Verificar ambigüedad (dos productos compitiendo con HIGH)
            is_ambiguous = False
            if len(candidate_matches) > 1:
                second_prod, second_match = candidate_matches[1]
                if (
                    best_match.confidence == ConfidenceLevel.HIGH
                    and second_match.confidence == ConfidenceLevel.HIGH
                    and abs(best_match.score - second_match.score) < 12.0
                ):
                    is_ambiguous = True

            if is_ambiguous:
                items.append(
                    ScannedListingItem(
                        listing=listing,
                        matched_product_id=None,
                        matched_product_title=f"Ambigüedad entre '{best_prod.title}' y '{candidate_matches[1][0].title}'",
                        confidence="AMBIGUOUS",
                        score=best_match.score,
                        reasons=["Múltiples productos guardados coinciden con alta confianza"],
                        warnings=["Requiere revisión manual del usuario antes de seleccionar"],
                        auto_selected=False,
                    )
                )
                logger.info("[MATCH] Listing '%s' -> AMBIGUOUS", listing.title)
                continue

            conf_name = best_match.confidence.name
            auto_sel = best_match.confidence == ConfidenceLevel.HIGH

            if auto_sel:
                high_count += 1
            elif best_match.confidence == ConfidenceLevel.MEDIUM:
                medium_count += 1
            else:
                unmatched_count += 1

            logger.info(
                "[MATCH] Listing '%s' ($%s) -> Producto '%s' | %s (score=%.1f, auto_select=%s)",
                listing.title,
                listing.price,
                best_prod.title,
                conf_name,
                best_match.score,
                auto_sel,
            )

            items.append(
                ScannedListingItem(
                    listing=listing,
                    matched_product_id=best_prod.id,
                    matched_product_title=best_prod.title,
                    confidence=conf_name,
                    score=best_match.score,
                    reasons=best_match.reasons,
                    warnings=best_match.warnings,
                    auto_selected=auto_sel,
                )
            )

        logger.info(
            "[SCAN_SUMMARY] Total=%d | HIGH (Auto-seleccionadas)=%d | MEDIUM=%d | Sin coincidencia=%d",
            len(listings),
            high_count,
            medium_count,
            unmatched_count,
        )

        return ScanBatchResult(
            items=items,
            total_listings=len(listings),
            matched_high_count=high_count,
            matched_medium_count=medium_count,
            unmatched_count=unmatched_count,
            scrolls_executed=0,
            stopped_for="complete",
        )

    def _check_intervention(self) -> None:
        if self._navigator is not None and callable(getattr(self._navigator, "requires_intervention", None)):
            if self._navigator.requires_intervention():
                raise InterventionRequiredError("Facebook requiere una acción manual durante el escaneo")
