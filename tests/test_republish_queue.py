"""Tests de la lógica pura de la cola de republicación múltiple.

Sin Qt: se ejercita `build_queue` y `RepublishQueue` (selección de
elegibles, avance secuencial, pausas por resultado no confirmado, cancelación
en puntos seguros e intervención manual).
"""

from __future__ import annotations

from app.models.product import Product
from app.models.republish_queue import QueueItem, QueueItemStatus, RepublishQueueState
from app.services.republish_queue import RepublishQueue, build_queue


class _FakeTarget:
    def __init__(self, status: str, is_preconfirm: bool) -> None:
        self.status = status
        self.is_preconfirm = is_preconfirm


def _product(pid: int, title: str = "iPhone 13") -> Product:
    product = Product(
        title=title,
        description="Celular en buen estado",
        price=1850000.0,
        category="Electrónica",
        condition="Usado - Como nuevo",
        location="Cali",
        images=["iphone/01.jpg"],
        enabled=True,
    )
    product.id = pid
    return product


def _scan(pid: int, confidence: str = "HIGH", has_locator: bool = True) -> dict:
    return {
        "listing": {
            "title": "iPhone 13 128GB",
            "price": 1850000,
            "price_raw": "$1.850.000",
            "url": "https://www.facebook.com/marketplace/item/777" if has_locator else "",
            "reference": "777" if has_locator else "",
        },
        "matched_product_id": pid,
        "matched_product_title": "iPhone 13 128GB",
        "confidence": confidence,
    }


def _no_scan(_pid: int) -> dict | None:
    return None


def _no_target(_pid: int):
    return None


# ---------------------------------------------------------------------------
# build_queue
# ---------------------------------------------------------------------------
def test_build_keeps_high_with_locator_and_no_active_postconfirm():
    products = [_product(1), _product(2)]
    scan_lookup = lambda pid: _scan(pid, "HIGH")  # noqa: E731
    build = build_queue(products, scan_lookup, _no_target)
    assert build.count == 2
    assert build.excluded_count == 0


def test_build_excludes_non_high_confidence():
    products = [_product(1)]
    scan_lookup = lambda pid: _scan(pid, "MEDIUM")  # noqa: E731
    build = build_queue(products, scan_lookup, _no_target)
    assert build.count == 0
    assert build.excluded[0].reason.startswith("confianza")


def test_build_excludes_without_locator():
    products = [_product(1)]
    scan_lookup = lambda pid: _scan(pid, "HIGH", has_locator=False)  # noqa: E731
    build = build_queue(products, scan_lookup, _no_target)
    assert build.count == 0
    assert "URL/referencia" in build.excluded[0].reason


def test_build_excludes_without_scan():
    build = build_queue([_product(1)], _no_scan, _no_target)
    assert build.count == 0
    assert "sin escaneo" in build.excluded[0].reason


def test_build_excludes_postconfirm_active_target():
    products = [_product(1)]
    scan_lookup = lambda pid: _scan(pid, "HIGH")  # noqa: E731
    active = lambda pid: _FakeTarget("deleting", is_preconfirm=False)  # noqa: E731
    build = build_queue(products, scan_lookup, active)
    assert build.count == 0
    assert "flujo en curso" in build.excluded[0].reason


def test_build_keeps_product_with_preconfirm_target():
    """Un target pre-confirmación (ya congelado/awaiting_confirm) NO excluye:
    la edición nunca invalida un producto de la cola."""
    products = [_product(1)]
    scan_lookup = lambda pid: _scan(pid, "HIGH")  # noqa: E731
    active = lambda pid: _FakeTarget("awaiting_confirm", is_preconfirm=True)  # noqa: E731
    build = build_queue(products, scan_lookup, active)
    assert build.count == 1


def test_build_keeps_edited_product():
    """Un producto editado sigue siendo elegible: la elegibilidad depende del
    escaneo HIGH, no de la edición."""
    product = _product(1)
    product.title = "iPhone 13 Editado 2.0"  # edición previa en preparación
    scan_lookup = lambda pid: _scan(pid, "HIGH")  # noqa: E731
    build = build_queue([product], scan_lookup, _no_target)
    assert build.count == 1
    assert build.eligible[0].product_title == "iPhone 13 Editado 2.0"


# ---------------------------------------------------------------------------
# RepublishQueue: flujo secuencial
# ---------------------------------------------------------------------------
def _queue_item(pid: int, title: str | None = None) -> QueueItem:
    return QueueItem(product_id=pid, product_title=title or f"Producto {pid}")


def _filled_queue(size: int = 2) -> RepublishQueue:
    queue = RepublishQueue()
    queue.set_items([_queue_item(i) for i in range(1, size + 1)])
    return queue


def test_start_returns_first_item_and_running():
    queue = _filled_queue(2)
    first = queue.start()
    assert first is not None
    assert first.product_id == 1
    assert queue.state == RepublishQueueState.RUNNING
    assert queue.current_item().product_id == 1


def test_full_sequential_flow_advances_only_after_confirm():
    queue = _filled_queue(2)
    queue.start()

    # Ítem 1: eliminar → crear → publicado.
    assert queue.on_delete_result("DELETED_CONFIRMED") == "create"
    assert queue.current_item().status == QueueItemStatus.CREATING
    action = queue.on_publication_result("PUBLISHED_CONFIRMED")
    assert action == "next"
    assert queue.current_item().product_id == 2  # solo avanza tras confirmar

    # Ítem 2: ídem; al terminar, cola COMPLETED.
    assert queue.on_delete_result("DELETED_CONFIRMED") == "create"
    assert queue.on_publication_result("PUBLISHED_CONFIRMED") == "done"
    assert queue.state == RepublishQueueState.COMPLETED
    assert queue.current_item() is None
    counts = queue.counts()
    assert counts["completed"] == 2
    assert counts["pending"] == 0


def test_delete_uncertain_pauses_and_never_advances():
    queue = _filled_queue(2)
    queue.start()
    assert queue.on_delete_result("DELETE_UNCERTAIN", "no verificada") == "uncertain"
    assert queue.state == RepublishQueueState.PAUSED
    assert queue.current_item().status == QueueItemStatus.UNCERTAIN
    assert queue.current_item().product_id == 1  # no avanza solo


def test_publish_failed_pauses():
    queue = _filled_queue(1)
    queue.start()
    queue.on_delete_result("DELETED_CONFIRMED")
    assert queue.on_publication_result("PUBLISH_FAILED", "formulario incompleto") == "failed"
    assert queue.state == RepublishQueueState.PAUSED
    assert queue.current_item().status == QueueItemStatus.FAILED


def test_skip_current_advances_and_counts():
    queue = _filled_queue(2)
    queue.start()
    action = queue.skip_current()
    assert action == "next"
    assert queue.current_item().product_id == 2
    assert queue.items[0].status == QueueItemStatus.SKIPPED
    assert queue.counts()["skipped"] == 1


def test_cancel_requested_applies_on_next_safe_point():
    """La cancelación NO corta la operación crítica: se aplica en el siguiente
    avance (tras el resultado del ítem actual)."""
    queue = _filled_queue(2)
    queue.start()
    queue.request_cancel()
    # El ítem 1 aún termina (eliminar + publicar) y luego la cola se cancela.
    assert queue.on_delete_result("DELETED_CONFIRMED") == "create"
    action = queue.on_publication_result("PUBLISHED_CONFIRMED")
    assert action == "done"
    assert queue.state == RepublishQueueState.CANCELLED
    assert queue.current_item() is None


def test_intervention_pauses_queue_intact():
    queue = _filled_queue(2)
    queue.start()
    queue.on_delete_result("DELETED_CONFIRMED")  # pasa a CREATING
    queue.handle_intervention()
    assert queue.state == RepublishQueueState.PAUSED
    assert queue.current_item().status == QueueItemStatus.WAITING_USER
    # Al llegar el resultado, la cola sigue exactamente donde iba.
    action = queue.on_publication_result("PUBLISHED_CONFIRMED")
    assert action == "next"
    assert queue.current_item().product_id == 2


def test_counts_mixed():
    queue = _filled_queue(3)
    queue.start()
    queue.on_delete_result("DELETED_CONFIRMED")
    queue.on_publication_result("PUBLISHED_CONFIRMED")  # 1 completado
    queue.skip_current()  # 2 omitido
    queue.on_delete_result("DELETE_UNCERTAIN")  # 3 incierto
    counts = queue.counts()
    assert counts["completed"] == 1
    assert counts["skipped"] == 1
    assert counts["uncertain"] == 1
    assert counts["pending"] == 0  # el incierto ya no se procesará solo


def test_empty_queue_starts_completed():
    queue = RepublishQueue()
    assert queue.start() is None
    assert queue.state == RepublishQueueState.COMPLETED