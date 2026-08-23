"""Modelo de dominio de la COLA de republicación múltiple.

Separa conceptualmente (requisito del plan aprobado):

    TARGET FACEBOOK CONGELADO  = URL, reference, título/precio originales, HIGH
    DATOS NUEVA PUBLICACIÓN    = título/precio/descripción/categoría/condición
                                 editados (viven en `Product` y en el snapshot
                                 `new_title`/`new_price` del target).

La edición ocurre COMPLETA en la preparación de la cola (ANTES de pulsar
"Iniciar republicación"), por lo que congelar el target nunca puede
invalidar un producto de la cola: `build()` excluye únicamente por
confianza, URL/referencia o target post-confirmación activo. Un producto
EDITADO jamás se descarta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class QueueItemStatus(Enum):
    """Fase de un ítem dentro de la cola."""

    PENDING = auto()
    READY = auto()
    DELETING = auto()
    CREATING = auto()
    COMPLETED = auto()
    SKIPPED = auto()
    FAILED = auto()
    UNCERTAIN = auto()
    WAITING_USER = auto()


# Estados terminales de un ítem (ya no se procesan más).
_ITEM_TERMINAL = frozenset(
    {
        QueueItemStatus.COMPLETED,
        QueueItemStatus.SKIPPED,
        QueueItemStatus.FAILED,
        QueueItemStatus.UNCERTAIN,
    }
)


class RepublishQueueState(Enum):
    """Estado general de la cola."""

    IDLE = auto()
    RUNNING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    CANCELLED = auto()
    FAILED = auto()


@dataclass
class QueueItem:
    """Un producto pendiente de republicar dentro de la cola.

    `scan_item` es el dict del escaneo (coincidencia HIGH con la publicación
    original que Facebook mostró). El target se congela desde AQUÍ, nunca
    desde el producto editado. `product_title` refleja el título actual del
    producto (puede cambiar si el usuario lo edita en la preparación).
    """

    product_id: int
    product_title: str
    scan_item: dict = field(default_factory=dict)
    status: QueueItemStatus = QueueItemStatus.PENDING
    matched_id: int | None = None
    result: str = ""
    reason: str = ""

    @property
    def display_title(self) -> str:
        return self.product_title or f"Producto #{self.product_id}"

    @property
    def is_critical(self) -> bool:
        """Operación destructiva/creativa en curso: no se debe cancelar a
        medias; la cancelación se aplica en el siguiente punto seguro."""
        return self.status in (QueueItemStatus.DELETING, QueueItemStatus.CREATING)

    @property
    def is_terminal(self) -> bool:
        return self.status in _ITEM_TERMINAL