"""Lógica pura de la cola de republicación múltiple (sin Qt).

La cola vive EN MEMORIA por sesión: cada resultado individual se persiste
igual que hoy (`MatchedListingService`/`ProductService`), de modo que si la
app se cierra a mitad de la cola, la reanudación automática existente sigue
funcionando target a target tras el reinicio.

REGLAS (plan aprobado):
- Procesamiento ESTRICTAMENTE secuencial: un producto a la vez; el siguiente
  solo empieza tras `PUBLISHED_CONFIRMED` (o decisión explícita del usuario).
- `build()` excluye solo por confianza != HIGH, sin URL/referencia, o target
  post-confirmación activo. Editar un producto NUNCA lo excluye.
- Ante un resultado no confirmado la cola se PAUSA y el usuario decide
  (Reintentar verificado / Omitir / Detener): nunca hay avance automático.
- Cancelación en puntos seguros: una operación crítica (deleting/creating)
  termina y se registra antes de detenerse.
"""

from __future__ import annotations

from collections.abc import Callable

from app.models.product import Product
from app.models.republish_queue import (
    QueueItem,
    QueueItemStatus,
    RepublishQueueState,
)

HIGH = "HIGH"

# Resultados que confirman de forma inequívoca.
_DELETED_CONFIRMED = "DELETED_CONFIRMED"
_PUBLISHED_CONFIRMED = "PUBLISHED_CONFIRMED"
_DELETE_UNCERTAIN = "DELETE_UNCERTAIN"
_PUBLISH_UNCERTAIN = "PUBLISH_UNCERTAIN"


class QueueBuildResult:
    """Resultado de construir la cola: elegibles + excluidos (con motivo)."""

    def __init__(self, eligible: list[QueueItem], excluded: list[QueueItem]) -> None:
        self.eligible = eligible
        self.excluded = excluded

    @property
    def count(self) -> int:
        return len(self.eligible)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)


def build_queue(
    selected_products: list[Product],
    scan_lookup: Callable[[int], dict | None],
    active_by_product: Callable[[int], object | None],
) -> QueueBuildResult:
    """Construye la cola SOLO con los productos elegibles.

    Un producto es elegible si su ítem del escaneo es HIGH con URL/referencia
    real y NO tiene un target post-confirmación activo (ese debe reanudarse).
    La edición previa (preparación) no afecta la elegibilidad.
    """
    eligible: list[QueueItem] = []
    excluded: list[QueueItem] = []

    for product in selected_products:
        product_id = product.id
        if product_id is None:
            excluded.append(
                QueueItem(0, product.title, reason="producto sin identificador")
            )
            continue
        scan = scan_lookup(product_id)
        if not scan:
            excluded.append(QueueItem(product_id, product.title, reason="sin escaneo"))
            continue
        confidence = scan.get("confidence") or "NO_MATCH"
        listing = scan.get("listing") or {}
        has_locator = bool(listing.get("url") or listing.get("reference"))
        if confidence != HIGH:
            excluded.append(
                QueueItem(product_id, product.title, scan, reason=f"confianza {confidence}")
            )
            continue
        if not has_locator:
            excluded.append(
                QueueItem(product_id, product.title, scan, reason="sin URL/referencia real")
            )
            continue
        active = active_by_product(product_id)
        if active is not None and not getattr(active, "is_preconfirm", False):
            excluded.append(
                QueueItem(
                    product_id,
                    product.title,
                    scan,
                    reason=f"flujo en curso ({getattr(active, 'status', '?')})",
                )
            )
            continue
        eligible.append(QueueItem(product_id, product.title, scan))

    return QueueBuildResult(eligible, excluded)


class RepublishQueue:
    """Cola estrictamente secuencial de republicación (un producto a la vez)."""

    def __init__(self) -> None:
        self.items: list[QueueItem] = []
        self.state = RepublishQueueState.IDLE
        self._index = -1
        self._cancel_requested = False

    # ------------------------------------------------------------------
    # Construcción / arranque
    # ------------------------------------------------------------------
    @staticmethod
    def build(
        selected_products: list[Product],
        scan_lookup: Callable[[int], dict | None],
        active_by_product: Callable[[int], object | None],
    ) -> QueueBuildResult:
        return build_queue(selected_products, scan_lookup, active_by_product)

    def set_items(self, items: list[QueueItem]) -> None:
        self.items = list(items)
        self.state = RepublishQueueState.IDLE
        self._index = -1
        self._cancel_requested = False

    def start(self) -> QueueItem | None:
        """Arranca la cola en el primer ítem. Devuelve el ítem o None."""
        if not self.items:
            self.state = RepublishQueueState.COMPLETED
            return None
        self.state = RepublishQueueState.RUNNING
        self._index = 0
        self.items[0].status = QueueItemStatus.READY
        return self.items[0]

    def set_matched(self, index: int, matched_id: int) -> None:
        """Vincula el target congelado (persistido) al ítem."""
        item = self.items[index]
        item.matched_id = matched_id
        if item.status == QueueItemStatus.PENDING:
            item.status = QueueItemStatus.READY

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------
    def current_item(self) -> QueueItem | None:
        if self.state in (
            RepublishQueueState.COMPLETED,
            RepublishQueueState.CANCELLED,
            RepublishQueueState.FAILED,
        ):
            return None
        if self._index < 0 or self._index >= len(self.items):
            return None
        return self.items[self._index]

    def counts(self) -> dict:
        completed = sum(1 for i in self.items if i.status == QueueItemStatus.COMPLETED)
        skipped = sum(1 for i in self.items if i.status == QueueItemStatus.SKIPPED)
        failed = sum(1 for i in self.items if i.status == QueueItemStatus.FAILED)
        uncertain = sum(1 for i in self.items if i.status == QueueItemStatus.UNCERTAIN)
        pending = sum(1 for i in self.items if not i.is_terminal)
        return {
            "completed": completed,
            "skipped": skipped,
            "failed": failed,
            "uncertain": uncertain,
            "pending": pending,
        }

    # ------------------------------------------------------------------
    # Transiciones durante el procesamiento
    # ------------------------------------------------------------------
    def on_delete_result(self, result_name: str, detail: str = "") -> str:
        """Procesa el resultado de eliminación del ítem actual.

        Devuelve:
          "create"    -> DELETED_CONFIRMED: crear/publicar la nueva publicación.
          "uncertain" -> DELETE_UNCERTAIN: cola PAUSED (decide el usuario).
          "failed"    -> DELETE_FAILED: cola PAUSED (decide el usuario).
        """
        item = self.current_item()
        if item is None:
            return "failed"
        item.result = result_name
        item.reason = detail
        if result_name == _DELETED_CONFIRMED:
            item.status = QueueItemStatus.CREATING
            return "create"
        if result_name == _DELETE_UNCERTAIN:
            item.status = QueueItemStatus.UNCERTAIN
            self.state = RepublishQueueState.PAUSED
            return "uncertain"
        item.status = QueueItemStatus.FAILED
        self.state = RepublishQueueState.PAUSED
        return "failed"

    def on_publication_result(self, result_name: str, detail: str = "") -> str:
        """Procesa el resultado de creación/publicación del ítem actual.

        Devuelve:
          "next"       -> éxito: avanzar al siguiente ítem.
          "done"       -> éxito y no hay más ítems (cola COMPLETED/CANCELLED).
          "uncertain"  -> PUBLISH_UNCERTAIN: cola PAUSED (decide el usuario).
          "failed"     -> PUBLISH_FAILED: cola PAUSED (decide el usuario).
        """
        item = self.current_item()
        if item is None:
            return "done"
        item.result = result_name
        item.reason = detail
        if result_name == _PUBLISHED_CONFIRMED:
            item.status = QueueItemStatus.COMPLETED
            return self._advance()
        if result_name == _PUBLISH_UNCERTAIN:
            item.status = QueueItemStatus.UNCERTAIN
            self.state = RepublishQueueState.PAUSED
            return "uncertain"
        item.status = QueueItemStatus.FAILED
        self.state = RepublishQueueState.PAUSED
        return "failed"

    def skip_current(self) -> str:
        """Omitir el ítem actual y avanzar. Devuelve "next" o "done"."""
        item = self.current_item()
        if item is not None and item.status != QueueItemStatus.COMPLETED:
            item.status = QueueItemStatus.SKIPPED
            item.result = "skipped"
        return self._advance()

    def _advance(self) -> str:
        """Avanza al siguiente ítem. Si hay cancelación pendiente, la aplica."""
        if self._cancel_requested:
            self.state = RepublishQueueState.CANCELLED
            return "done"
        self._index += 1
        if self._index >= len(self.items):
            self.state = RepublishQueueState.COMPLETED
            return "done"
        self.items[self._index].status = QueueItemStatus.READY
        return "next"

    # ------------------------------------------------------------------
    # Control del usuario
    # ------------------------------------------------------------------
    def request_cancel(self) -> None:
        """Solicita detener la cola. Se aplica en el siguiente punto seguro
        (tras terminar la operación crítica en curso)."""
        self._cancel_requested = True

    def cancel_now(self) -> None:
        """Detiene la cola de inmediato (solo en un punto seguro, p. ej. tras
        una decisión del usuario en el diálogo de fallo)."""
        self._cancel_requested = True
        self.state = RepublishQueueState.CANCELLED

    def handle_intervention(self) -> None:
        """Intervención manual de Facebook (login/2FA/CAPTCHA): la cola queda
        PAUSED e intacta; "Continuar" reanuda exactamente donde iba."""
        if self.state != RepublishQueueState.RUNNING:
            return
        self.state = RepublishQueueState.PAUSED
        item = self.current_item()
        if item is not None and item.is_critical:
            item.status = QueueItemStatus.WAITING_USER

    def handle_error(self) -> None:
        self.state = RepublishQueueState.FAILED