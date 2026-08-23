"""Modelo de dominio: MatchedListing.

Representa el TARGET CONGELADO de eliminación dentro del flujo de
republicación.

Regla arquitectónica fundamental de esta fase:

    MatchedListing = OBJETIVO ORIGINAL DE ELIMINACIÓN  (congelado)
    Product        = DATOS ACTUALES PARA LA NUEVA PUBLICACIÓN  (mutable)

Una vez confirmado `Product -> Listing -> HIGH`, el `MatchedListing` NUNCA
se vuelve a derivar del producto editado: si el usuario cambia el título,
precio, descripción o fotos, esos cambios van al `Product` y quedan
registrados en `new_title`/`new_price` SOLO como trazabilidad. Los campos
`listing_url`, `listing_reference`, `matched_title`, `matched_price` y
`matched_price_raw` permanecen helados hasta completar o cancelar el
proceso. Cambiar el producto jamás puede hacer que el sistema pierda la
publicación original.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.models.listing import Listing

# ---------------------------------------------------------------------------
# Ciclo de vida del target (status). Único origen de verdad para la
# reanudación tras reinicio (sección 14 del spec): según el status, la app
# sabe exactamente en qué fase quedó y qué debe hacer a continuación.
# ---------------------------------------------------------------------------
STATUS_SELECTED = "selected"
STATUS_EDITING = "editing"
STATUS_AWAITING_CONFIRM = "awaiting_confirm"
STATUS_DELETING = "deleting"
STATUS_DELETED = "deleted"  # DELETED_CONFIRMED: la creación/publicación queda pendiente
STATUS_CREATING = "creating"
STATUS_PUBLISHING = "publishing"
STATUS_VERIFYING_PUBLICATION = "verifying_publication"
STATUS_REPUBLISHED = "republished"  # terminal (éxito)
STATUS_BLOCKED = "blocked"          # terminal (eliminación incierta/fallida u otro bloqueo)
STATUS_CANCELLED = "cancelled"      # terminal (cancelado por el usuario)

# Estados en los que el target sigue "vivo": se reanuda o continúa el flujo.
ACTIVE_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_SELECTED,
        STATUS_EDITING,
        STATUS_AWAITING_CONFIRM,
        STATUS_DELETING,
        STATUS_DELETED,
        STATUS_CREATING,
        STATUS_PUBLISHING,
        STATUS_VERIFYING_PUBLICATION,
    }
)

# Fases PRE-confirmación: todavía NO ocurrió ninguna acción destructiva ni de
# creación. Un target en estas fases se puede cancelar/descartar explícitamente
# sin riesgo (cambio de producto, flujo abandonado, reinicio de republicación).
PRECONFIRM_STATUSES: frozenset[str] = frozenset(
    {STATUS_SELECTED, STATUS_EDITING, STATUS_AWAITING_CONFIRM}
)

# Estados finales: ya no se reanuda nada desde aquí (salvo nueva selección).
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {STATUS_REPUBLISHED, STATUS_BLOCKED, STATUS_CANCELLED}
)

# Único nivel de confianza con el que se puede congelar un target.
ALLOWED_CONFIDENCE: frozenset[str] = frozenset({"HIGH"})


@dataclass
class MatchedListing:
    """Publicación encontrada y congelada para el flujo de republicación.

    Atributos:
        product_id:          producto local (fuente de la nueva publicación).
        listing_url:         URL del item en Facebook (helado).
        listing_reference:   id numérico del item (helado).
        matched_title:       título que mostró Facebook en el momento del match.
        matched_price:       precio COP que mostró Facebook (o None).
        matched_price_raw:   texto crudo del precio tal como lo mostró FB.
        confidence:          nivel de confianza del match (solo HIGH).
        status:              fase actual del ciclo de vida del target.
        matched_at:          cuándo se congeló (- identifica el momento del match).
        id:                  clave primaria (None si no está persistido aún).
        new_title / new_price: snapshot de los datos editados (trazabilidad).
        created_at / updated_at: timestamps de persistencia.
    """

    product_id: int
    listing_url: str = ""
    listing_reference: str = ""
    matched_title: str = ""
    matched_price: int | None = None
    matched_price_raw: str = ""
    confidence: str = ""
    status: str = STATUS_SELECTED
    matched_at: datetime | None = None
    id: int | None = None
    new_title: str | None = None
    new_price: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_preconfirm(self) -> bool:
        """True si el flujo todavía no realizó ninguna acción destructiva
        (selected/editing/awaiting_confirm): se puede cancelar sin riesgo."""
        return self.status in PRECONFIRM_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def has_locator(self) -> bool:
        """¿Tenemos una forma real de localizar la publicación en Facebook?"""
        return bool(self.listing_url or self.listing_reference)

    @property
    def display_listing_title(self) -> str:
        return self.matched_title or self.listing_reference or "(sin título)"

    def to_listing(self) -> Listing:
        """Reconstruye el `Listing` objetivo SIEMPRE desde los datos
        congelados. Esta es la única forma en que la capa de eliminación
        debe recibir el objetivo: nunca a partir del producto editado."""
        return Listing(
            title=self.matched_title,
            price=self.matched_price,
            price_raw=self.matched_price_raw,
            url=self.listing_url,
            reference=self.listing_reference,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "product_id": self.product_id,
            "listing_url": self.listing_url,
            "listing_reference": self.listing_reference,
            "matched_title": self.matched_title,
            "matched_price": self.matched_price,
            "matched_price_raw": self.matched_price_raw,
            "confidence": self.confidence,
            "status": self.status,
            "matched_at": self.matched_at.isoformat() if self.matched_at else None,
            "new_title": self.new_title,
            "new_price": self.new_price,
            "is_active": self.is_active,
            "has_locator": self.has_locator,
        }