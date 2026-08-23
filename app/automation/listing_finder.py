"""Localización de publicaciones de un producto en "Tus publicaciones".

`ListingFinder` orquesta la búsqueda de una publicación concreta:

    Producto interno -> Localizar candidatos -> Coincidir -> Resultado

Respeta las invariantes de seguridad de la Iteración 3: TODO es
solo-lectura (no elimina, no edita, no publica), y ante cualquier duda
devuelve un resultado conservador (AMBIGUOUS o baja confianza) en vez de
decidir por el usuario.

Límites estrictos (nunca scroll infinito):
- `max_scrolls`: número máximo de operaciones de scroll.
- `search_timeout_ms`: presupuesto de tiempo total.
- Detención por ausencia de contenido nuevo: si después de un scroll no
  hay listings nuevos que no estén ya vistos, se termina.
- Deduplicación por `Listing.key` (URL/reference/texto).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.automation.listing_extractor import ListingExtractor
from app.automation.listing_matcher import (
    ConfidenceLevel,
    ListingMatcher,
    MatchOutcome,
    MatchResult,
    MatchStatus,
)
from app.core import forensics
from app.core.config import search_limits as default_limits
from app.core.exceptions import InterventionRequiredError
from app.core.logging_config import get_logger
from app.models.listing import Listing
from app.models.product import Product

logger = get_logger(__name__)


@dataclass
class StopReason:
    FOUND = "found"
    AMBIGUOUS = "ambiguous"
    NO_NEW_CONTENT = "no_new_content"
    MAX_SCROLLS = "max_scrolls"
    TIMEOUT = "timeout"
    KNOWN_REFERENCE = "known_reference"


@dataclass
class FindResult:
    """Resultado completo de una búsqueda de publicación."""

    status: MatchStatus
    outcome: MatchOutcome
    stopped_for: str = ""
    screenshot_path: str | None = None
    scanned_count: int = 0

    @property
    def best_listing(self) -> Listing | None:
        return self.outcome.best.listing if self.outcome.best is not None else None

    @property
    def best_match(self) -> MatchResult | None:
        return self.outcome.best


class ListingFinder:
    """Busca la publicación de un producto en la sección de listados."""

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
        self._navigator = navigator  # MarketplaceAdapter (o fake en tests)
        self._limits = limits or default_limits

    def find(self, product: Product, on_phase=None) -> FindResult:
        """Busca la publicación del producto. Devuelve siempre un resultado
        (nunca lanza por no encontrar). Puede lanzar InterventionRequiredError
        si Facebook pide una acción manual.

        `on_phase` es un callback opcional que se invoca con el nombre de la
        fase ("scanning" | "matching") en cada iteración del barrido, para
        que la capa de servicios/GUI pueda reflejar el avance en la FSM.
        """
        # Fase 0: señal prioritaria por URL/referencia conocida.
        if product.marketplace_url:
            self._check_intervention()
            listing = self._extractor.extract_from_url(self._page, product.marketplace_url)
            if listing is not None:
                result = self._matcher.match(product, listing)
                logger.info("Coincidencia por URL conocida: %s (%s)", listing.title, result.confidence.name)
                if result.confidence == ConfidenceLevel.HIGH:
                    outcome = MatchOutcome(status=MatchStatus.FOUND, best=result, candidates=[result], scanned_count=1)
                    return FindResult(status=MatchStatus.FOUND, outcome=outcome, stopped_for=StopReason.KNOWN_REFERENCE, scanned_count=1)

        # Fase 1: garantizar que estamos en "Tus publicaciones".
        self._check_intervention()
        if self._navigator:
            self._navigator.ensure_listings_section()
            self._check_intervention()

        # Fase 2: barrido acotado cargar -> extraer -> dedup -> coincidir.
        deadline = time.monotonic() + self._limits.search_timeout_ms / 1000.0
        seen: set[str] = set()
        candidates: list[Listing] = []
        scrolls = 0
        stopped_for = StopReason.MAX_SCROLLS

        while True:
            self._check_intervention()

            if on_phase:
                on_phase("scanning")
            forensics.evt("listing_finder.iteration", f"scrolls={scrolls}")
            batch = self._extractor.extract_listings(self._page)
            new_listings = [l for l in batch if l.key not in seen]
            seen.update(l.key for l in new_listings)
            candidates.extend(new_listings)

            # Evaluación según la decisión (conservador).
            if on_phase:
                on_phase("matching")
            outcome = self._matcher.resolve(product, candidates)

            logger.info(
                "SCROLL %d | extraídos en DOM: %d | nuevos: %d | total acumulado: %d | evaluados por matcher: %d",
                scrolls,
                len(batch),
                len(new_listings),
                len(candidates),
                len(outcome.candidates),
            )

            if outcome.status in (MatchStatus.FOUND, MatchStatus.AMBIGUOUS):
                stopped_for = StopReason.FOUND if outcome.status == MatchStatus.FOUND else StopReason.AMBIGUOUS
                logger.info(
                    "Búsqueda concluida (%s) con %d candidatos escaneados",
                    stopped_for,
                    len(candidates),
                )
                return FindResult(
                    status=outcome.status,
                    outcome=outcome,
                    stopped_for=stopped_for,
                    scanned_count=len(candidates),
                )

            if time.monotonic() >= deadline:
                stopped_for = StopReason.TIMEOUT
                break

            # Ausencia de nuevo contenido: no tiene sentido seguir haciendo scroll.
            if not new_listings:
                stopped_for = StopReason.NO_NEW_CONTENT
                break

            if scrolls >= self._limits.max_scrolls:
                stopped_for = StopReason.MAX_SCROLLS
                break

            moved = False
            if self._navigator:
                moved = self._navigator.scroll_feed() or False
            if not moved:
                stopped_for = StopReason.NO_NEW_CONTENT
                break
            scrolls += 1

        # Resolución final con todo lo escaneado.
        final = self._matcher.resolve(product, candidates)
        if stopped_for == StopReason.TIMEOUT or stopped_for == StopReason.MAX_SCROLLS:
            final = MatchOutcome(
                status=MatchStatus.SEARCH_LIMIT_REACHED,
                best=final.best,
                candidates=final.candidates,
                scanned_count=final.scanned_count,
            )
        logger.info(
            "Búsqueda terminada sin hallazgo concluyente (razón=%s, candidatos=%d)",
            stopped_for,
            len(candidates),
        )
        return FindResult(status=final.status, outcome=final, stopped_for=stopped_for, scanned_count=len(candidates))

    # -- seguridad -----------------------------------------------------------
    def _check_intervention(self) -> None:
        """Si la página pide una acción manual, se detiene la búsqueda de
        inmediato (la capa de servicios la convierte en WAITING_USER)."""
        if self._navigator is not None and callable(getattr(self._navigator, "requires_intervention", None)):
            if self._navigator.requires_intervention():
                raise InterventionRequiredError("Facebook requiere una acción manual durante la búsqueda")