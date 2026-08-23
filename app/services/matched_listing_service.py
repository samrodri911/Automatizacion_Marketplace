"""Servicio del flujo de republicación (targets congelados).

Orquesta la persistencia del `MatchedListing` (objetivo de eliminación)
y la trazabilidad de cada fase en `automation_runs`.

REGLAS ARQUITECTÓNICAS (sección 3, 4, 18 del spec):

- `select_match()` CONGELA el target de una coincidencia HIGH: nunca se
  vuelve a derivar del producto editado.
- `save_edit_snapshot()` actualiza SOLO `new_title`/`new_price` del target
  (trazabilidad). NUNCA toca `listing_url`, `listing_reference`,
  `matched_title`, `matched_price` ni `matched_price_raw`.
- Tras editar el producto NO se vuelve a ejecutar el matcher ni se vuelve
  a escanear: el flujo siempre continúa desde el target congelado.
- Solo se puede avanzar con confianza HIGH.
"""

from __future__ import annotations

from datetime import datetime

from app.core.exceptions import RepublishBlockedError, RepublishError
from app.core.logging_config import get_logger
from app.database.repositories import MatchedListingsRepository
from app.models.matched_listing import (
    ALLOWED_CONFIDENCE,
    ACTIVE_STATUSES,
    PRECONFIRM_STATUSES,
    STATUS_AWAITING_CONFIRM,
    STATUS_BLOCKED,
    STATUS_CREATING,
    STATUS_DELETED,
    STATUS_DELETING,
    STATUS_EDITING,
    STATUS_PUBLISHING,
    STATUS_REPUBLISHED,
    STATUS_SELECTED,
    STATUS_VERIFYING_PUBLICATION,
    MatchedListing,
)
from app.models.product import Product
from app.services.product_service import ProductService

logger = get_logger(__name__)


class MatchedListingService:
    """Fachada de negocio sobre los targets congelados de republicación."""

    def __init__(
        self,
        repository: MatchedListingsRepository,
        product_service: ProductService,
    ) -> None:
        self._repository = repository
        self._product_service = product_service

    # ------------------------------------------------------------------
    # Selección del target (congelar)
    # ------------------------------------------------------------------
    def select_match(
        self,
        product_id: int,
        listing_url: str,
        listing_reference: str,
        matched_title: str,
        matched_price: int | None,
        matched_price_raw: str = "",
        confidence: str = "HIGH",
    ) -> MatchedListing:
        """Congela la publicación encontrada como objetivo de eliminación.

        Es el ÚNICO momento en el que se fija el target. A partir de aquí
        el flujo usa siempre estos datos, aunque el producto se edite.

        Reglas:
        - Solo `confidence == HIGH` (no MEDIUM/LOW/AMBIGUOUS/NO_MATCH).
        - Se necesita URL o referencia real extraída de Facebook.
        """
        if confidence not in ALLOWED_CONFIDENCE:
            raise RepublishBlockedError(
                f"No se puede iniciar republicación con confianza {confidence}; "
                f"solo se permite {sorted(ALLOWED_CONFIDENCE)}"
            )

        url = (listing_url or "").strip()
        ref = (listing_reference or "").strip()
        if not url and not ref:
            raise RepublishError(
                "No se puede congelar el target: falta URL/referencia real de la publicación"
            )

        title = (matched_title or "").strip()
        if not title:
            title = f"Publicación {ref or url}"

        matched = MatchedListing(
            product_id=product_id,
            listing_url=url,
            listing_reference=ref,
            matched_title=title,
            matched_price=matched_price,
            matched_price_raw=matched_price_raw or "",
            confidence=confidence,
            status=STATUS_SELECTED,
            matched_at=datetime.now(),
        )
        created = self._repository.create(matched)
        self._record_phase(
            product_id=created.product_id,
            operation="match",
            status="success",
            listing_url=created.listing_url,
            listing_reference=created.listing_reference,
            confidence=created.confidence,
            matched_title=created.matched_title,
            matched_price=created.matched_price,
            matched_at=created.matched_at,
        )
        logger.info(
            "Target congelado: product=%s ref=%s conf=%s", product_id, ref or url, confidence
        )
        return created

    # ------------------------------------------------------------------
    # Edición (no toca nunca el target congelado)
    # ------------------------------------------------------------------
    def start_edit(self, matched: MatchedListing) -> MatchedListing:
        """Marca el target como 'editing'. Solo cambia el status."""
        if matched.status not in (STATUS_SELECTED, STATUS_EDITING):
            return matched
        return self._transition(matched.id, STATUS_EDITING)

    def save_edit_snapshot(self, matched_id: int, updated_product: Product) -> MatchedListing:
        """Guarda la trazabilidad de la edición SIN modificar el objetivo.

        Actualiza únicamente `new_title`/`new_price` del target con los
        datos editados del producto. **Nunca** toca listing_url /
        listing_reference / matched_title / matched_price.

        Devuelve el MatchedListing actualizado (con los campos congelados
        intactos, verificable en tests).
        """
        matched = self._repository.get(matched_id)
        matched.new_title = updated_product.title
        matched.new_price = float(updated_product.price)
        if matched.status in (STATUS_SELECTED, STATUS_EDITING):
            matched.status = STATUS_AWAITING_CONFIRM
        updated = self._repository.update(matched)
        self._record_phase(
            product_id=updated.product_id,
            operation="edit",
            status="success",
            listing_url=updated.listing_url,
            listing_reference=updated.listing_reference,
            confidence=updated.confidence,
            matched_title=updated.matched_title,
            matched_price=updated.matched_price,
            matched_at=updated.matched_at,
            new_title=updated.new_title,
            new_price=updated.new_price,
        )
        return updated

    def mark_awaiting_confirm(self, matched_id: int) -> MatchedListing:
        return self._transition(matched_id, STATUS_AWAITING_CONFIRM)

    # ------------------------------------------------------------------
    # Eliminación
    # ------------------------------------------------------------------
    def mark_deleting(self, matched_id: int) -> MatchedListing:
        return self._transition(matched_id, STATUS_DELETING)

    def mark_deleted_confirmed(self, matched_id: int) -> MatchedListing:
        """DELETED_CONFIRMED: la eliminación fue verificada; creación pendiente."""
        return self._transition(matched_id, STATUS_DELETED)

    def mark_deletion_uncertain(self, matched_id: int, error: str | None = None) -> MatchedListing:
        """DELETE_UNCERTAIN / DELETE_FAILED: DETENER el flujo (sección 10).

        No se crea ninguna publicación nueva: el target queda bloqueado
        para que un humano decida cómo proceder.
        """
        blocked = self._transition(matched_id, STATUS_BLOCKED)
        self._record_phase(
            product_id=blocked.product_id,
            operation="delete",
            status="error",
            error=error or "Eliminación no confirmada; republicación bloqueada",
            listing_url=blocked.listing_url,
            listing_reference=blocked.listing_reference,
            confidence=blocked.confidence,
        )
        return blocked

    # ------------------------------------------------------------------
    # Creación / publicación
    # ------------------------------------------------------------------
    def mark_creating(self, matched_id: int) -> MatchedListing:
        return self._transition(matched_id, STATUS_CREATING)

    def mark_publishing(self, matched_id: int) -> MatchedListing:
        return self._transition(matched_id, STATUS_PUBLISHING)

    def mark_verifying_publication(self, matched_id: int) -> MatchedListing:
        return self._transition(matched_id, STATUS_VERIFYING_PUBLICATION)

    def mark_republished(self, matched_id: int) -> MatchedListing:
        """Publicación creada y verificada: el flujo terminó con éxito."""
        return self._transition(matched_id, STATUS_REPUBLISHED)

    def mark_blocked(self, matched_id: int, error: str | None = None) -> MatchedListing:
        return self._transition(matched_id, STATUS_BLOCKED)

    def cancel(self, matched_id: int) -> MatchedListing:
        return self._transition(matched_id, "cancelled")

    def cancel_pending(self, product_id: int) -> MatchedListing | None:
        """Cancela el target del producto SOLO si está en fase pre-confirmación.

        Ciclo de vida del target (fase actual): el target queda congelado
        desde el `select_match` hasta completar o cancelar el flujo. Editar
        el producto NUNCA lo invalida. Solo se limpia de forma EXPLÍCITA:
        - al cancelar la republicación (diálogo cancelado);
        - al cambiar de producto si quedó en pre-confirmación (selected/
          editing/awaiting_confirm: todavía no hubo acción destructiva);
        - al reiniciar una republicación sobre un target pre-confirmación
          huérfano.

        Si el flujo ya pasó a deleting/deleted/creating/publishing/
        verifying_publication NO se cancela: debe reanudarse (recovery).
        """
        matched = self.get_active_by_product(product_id)
        if matched is None or matched.status not in PRECONFIRM_STATUSES:
            return None
        return self.cancel(matched.id)

    def cancel_active(self, product_id: int) -> MatchedListing | None:
        """Cancela el target activo del producto sea cual sea su fase.

        Se usa cuando el target DEJA DE SER VÁLIDO (p. ej. el producto se
        elimina localmente): el flujo ya no puede continuar.
        """
        matched = self.get_active_by_product(product_id)
        if matched is None:
            return None
        return self.cancel(matched.id)

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------
    def get(self, matched_id: int) -> MatchedListing:
        return self._repository.get(matched_id)

    def get_active_by_product(self, product_id: int) -> MatchedListing | None:
        return self._repository.get_active_by_product(product_id)

    def list_active(self) -> list[MatchedListing]:
        return self._repository.list_active()

    def list_historical(self, product_id: int) -> list[MatchedListing]:
        return self._repository.list_historical(product_id)

    def require_active(self, product_id: int) -> MatchedListing:
        """Devuelve el target activo del producto o lanza un error claro.

        Es el punto de entrada de la reanudación: si el usuario pulsa
        "Continuar" y no existe un target activo, no hay nada que hacer.
        """
        matched = self.get_active_by_product(product_id)
        if matched is None:
            raise RepublishError(
                f"No hay ningún target de republicación activo para el producto {product_id}"
            )
        return matched

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _transition(self, matched_id: int, new_status: str) -> MatchedListing:
        matched = self._repository.transition(matched_id, new_status)
        logger.info("Target %s -> status=%s (product=%s)", matched_id, new_status, matched.product_id)
        return matched

    def _record_phase(self, **kwargs) -> None:
        # record_phase_run dentro de ProductService gestiona la ausencia de
        # run_repository (delegando en el que tenga el ProductService).
        self._product_service.record_phase_run(**kwargs)