"""Tests del flujo de republicación a nivel de servicio (Iteración 5).

Se ejecutan los slots de `AutomationService` con fakes de Playwright
(monkeypatch de ListingDeleter/ListingCreator), sin navegador real. Cubren
los ítems 4-10 del plan:

4. ListingDeleter recibe la Listing construida desde matched_listing.url
   (nunca product.title); se aserta el objeto pasado al mock.
5. DELETE_UNCERTAIN bloquea la publicación.
6. DELETE_FAILED bloquea la publicación.
7. DELETED_CONFIRMED permite la creación.
8. CAPTCHA → WAITING_USER (sin cerrar navegador).
9. Reanudación tras eliminación: verify_only, no re-delete.
10. Reanudación tras publicación: verifica antes de crear otra.
"""

from __future__ import annotations

import pytest

from app.automation.listing_creator import ListingCreator, PublishResult, PublishStatus
from app.automation.listing_deleter import DeleteResult, DeleteStatus, ListingDeleter
from app.automation.states import AutomationState
from app.services.automation_service import AutomationService


class FakeNavigator:
    def requires_intervention(self) -> bool:
        return False

    def ensure_listings_section(self) -> None:
        pass


class Behavior:
    delete_status = DeleteStatus.DELETED_CONFIRMED
    verify_delete_status = DeleteStatus.DELETED_CONFIRMED
    publish_status = PublishStatus.PUBLISHED_CONFIRMED
    verify_publish_status = PublishStatus.PUBLISHED_CONFIRMED


def _target_payload(matched_id: int = 42, title: str = "iPhone 13 128GB") -> dict:
    return {
        "matched_id": matched_id,
        "title": title,
        "price": 1850000,
        "price_raw": "$1.850.000",
        "url": "https://www.facebook.com/marketplace/item/777",
        "reference": "777",
        "confidence": "HIGH",
    }


def _product_payload(product_id: int = 1, title: str = "iPhone 13 Editado 2.0") -> dict:
    return {
        "product_id": product_id,
        "title": title,
        "description": "Descripción editada",
        "price": 2000000.0,
        "category": "Electrónica",
        "condition": "Usado - Como nuevo",
        "location": "Cali",
        "tags": [],
        "images": ["iphone/01.jpg"],
        "enabled": True,
        "marketplace_url": None,
        "marketplace_reference": None,
    }


@pytest.fixture
def service(monkeypatch):
    """AutomationService con fakes: deleter/creator grabados y respuestas
    configurables vía `behavior`."""
    svc = AutomationService()
    svc._page = object()  # noqa: SLF001
    svc._marketplace_adapter = FakeNavigator()  # noqa: SLF001

    behavior = Behavior()
    calls = {"delete": [], "verify_delete": [], "create": [], "verify_publish": []}

    def fake_delete(self, listing, page, navigator=None):
        calls["delete"].append(listing)
        return DeleteResult(status=behavior.delete_status, listing=listing, detail="delete detail")

    def fake_verify_delete(self, listing, page):
        calls["verify_delete"].append(listing)
        return DeleteResult(status=behavior.verify_delete_status, listing=listing, detail="verify detail")

    def fake_create(self, product, page, navigator=None, image_paths=None):
        calls["create"].append(product)
        return PublishResult(
            status=behavior.publish_status,
            title=product.title,
            new_url="https://www.facebook.com/marketplace/item/555",
            new_reference="555",
            detail="create detail",
        )

    def fake_verify_publish(self, product, page, navigator=None):
        calls["verify_publish"].append(product)
        return PublishResult(
            status=behavior.verify_publish_status,
            title=product.title,
            new_url="https://www.facebook.com/marketplace/item/555",
            new_reference="555",
            detail="verify detail",
        )

    monkeypatch.setattr(ListingDeleter, "delete", fake_delete)
    monkeypatch.setattr(ListingDeleter, "verify_only", fake_verify_delete)
    monkeypatch.setattr(ListingCreator, "create", fake_create)
    monkeypatch.setattr(ListingCreator, "verify_only", fake_verify_publish)

    ready_payloads: list[dict] = []
    delete_results: list[dict] = []
    publish_results: list[dict] = []
    interventions: list[str] = []
    svc.delete_ready.connect(ready_payloads.append)
    svc.delete_result.connect(delete_results.append)
    svc.publication_result.connect(publish_results.append)
    svc.intervention_paused.connect(interventions.append)

    svc._ready_payloads = ready_payloads  # noqa: SLF001
    svc._delete_results = delete_results  # noqa: SLF001
    svc._publish_results = publish_results  # noqa: SLF001
    svc._interventions = interventions  # noqa: SLF001
    svc._behavior = behavior  # noqa: SLF001
    svc._calls = calls  # noqa: SLF001
    return svc


# ---------------------------------------------------------------------------
# 4. El Listing se construye SIEMPRE desde el target congelado
# ---------------------------------------------------------------------------
def test_prepare_and_execute_uses_frozen_target(service):
    svc = service
    svc.prepare_delete({"product": _product_payload(), "matched_target": _target_payload()})

    ready = svc._ready_payloads[0]
    assert ready["from_republish"] is True
    assert ready["matched_id"] == 42
    assert ready["listing_title"] == "iPhone 13 128GB"

    svc.execute_delete({})
    listing = svc._calls["delete"][0]
    assert listing.url == "https://www.facebook.com/marketplace/item/777"
    assert listing.reference == "777"
    assert listing.title == "iPhone 13 128GB"
    assert listing.price == 1850000
    assert listing.title != "iPhone 13 Editado 2.0"

    assert svc._delete_results[0]["matched_id"] == 42
    assert svc._delete_results[0]["result"] == "DELETED_CONFIRMED"


# ---------------------------------------------------------------------------
# 5 y 6. DELETE_UNCERTAIN / DELETE_FAILED bloquean la publicación
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", [DeleteStatus.DELETE_UNCERTAIN, DeleteStatus.DELETE_FAILED])
def test_uncertain_or_failed_delete_blocks_creation(service, status):
    svc = service
    svc._behavior.delete_status = status
    svc.prepare_delete({"product": _product_payload(), "matched_target": _target_payload()})
    svc.execute_delete({})

    assert svc._delete_results[0]["result"] == status.name
    assert svc._delete_results[0]["matched_id"] == 42
    # No se crea ninguna publicación nueva (spec §10).
    assert svc._calls["create"] == []
    assert svc._publish_results == []
    # FSM queda en estado terminal de eliminación, no de creación.
    assert svc.state in (AutomationState.DELETE_UNCERTAIN, AutomationState.DELETE_FAILED)


# ---------------------------------------------------------------------------
# 7. DELETED_CONFIRMED permite la creación
# ---------------------------------------------------------------------------
def test_deleted_confirmed_allows_creation(service):
    svc = service
    svc.prepare_delete({"product": _product_payload(), "matched_target": _target_payload()})
    svc.execute_delete({})
    assert svc._delete_results[0]["result"] == "DELETED_CONFIRMED"

    # La GUI encadena create_and_publish con el producto editado.
    svc.create_and_publish(
        {
            "product": _product_payload(),
            "matched_id": 42,
            "image_paths": ["/tmp/a.jpg"],
        }
    )
    created = svc._calls["create"][0]
    assert created.title == "iPhone 13 Editado 2.0"
    result = svc._publish_results[0]
    assert result["result"] == "PUBLISHED_CONFIRMED"
    assert result["matched_id"] == 42
    assert result["new_reference"] == "555"


# ---------------------------------------------------------------------------
# 8. CAPTCHA/intervención → WAITING_USER sin cerrar navegador
# ---------------------------------------------------------------------------
def test_intervention_during_delete_pauses_without_closing_browser(service):
    svc = service
    svc._behavior.delete_status = DeleteStatus.INTERVENTION_REQUIRED
    svc.prepare_delete({"product": _product_payload(), "matched_target": _target_payload()})
    svc.execute_delete({})

    assert svc.state == AutomationState.WAITING_USER
    assert svc._interventions
    # El navegador sigue abierto para que el usuario resuelva el CAPTCHA.
    assert svc._page is not None
    assert svc._calls["create"] == []


def test_intervention_during_create_pauses_without_closing_browser(service):
    svc = service
    svc._behavior.publish_status = PublishStatus.INTERVENTION_REQUIRED
    svc.create_and_publish({"product": _product_payload(), "matched_id": 42, "image_paths": []})

    assert svc.state == AutomationState.WAITING_USER
    assert svc._page is not None
    assert svc._publish_results == []


# ---------------------------------------------------------------------------
# 9. Reanudación tras eliminación: verifica, no re-elimina
# ---------------------------------------------------------------------------
def test_resume_after_delete_verifies_without_redeleting(service):
    svc = service
    svc.resume_republish(
        {
            "product": _product_payload(),
            "matched_id": 42,
            "phase": "delete",
            "matched_target": _target_payload(),
        }
    )
    # verify_only fue llamado; delete NO.
    assert len(svc._calls["verify_delete"]) == 1
    assert svc._calls["delete"] == []
    listing = svc._calls["verify_delete"][0]
    assert listing.url == "https://www.facebook.com/marketplace/item/777"
    assert svc._delete_results[0]["matched_id"] == 42


# ---------------------------------------------------------------------------
# 10. Reanudación tras publicación: verifica antes de crear otra
# ---------------------------------------------------------------------------
def test_resume_after_publish_verifies_first(service):
    svc = service
    svc._behavior.verify_publish_status = PublishStatus.PUBLISHED_CONFIRMED
    svc.resume_republish({"product": _product_payload(), "matched_id": 42, "phase": "publish"})

    assert len(svc._calls["verify_publish"]) == 1
    # Al estar ya publicado, NO se crea un segundo anuncio.
    assert svc._calls["create"] == []
    assert svc._publish_results[0]["result"] == "PUBLISHED_CONFIRMED"


def test_resume_after_publish_continues_creating_when_not_found(service):
    svc = service
    svc._behavior.verify_publish_status = PublishStatus.PUBLISH_UNCERTAIN
    svc._behavior.publish_status = PublishStatus.PUBLISHED_CONFIRMED
    svc.resume_republish({"product": _product_payload(), "matched_id": 42, "phase": "publish"})

    # Verificó primero; al no encontrar la publicación, completó la creación.
    assert len(svc._calls["verify_publish"]) == 1
    assert len(svc._calls["create"]) == 1
    assert svc._publish_results[0]["result"] == "PUBLISHED_CONFIRMED"


# ---------------------------------------------------------------------------
# Congelado y edición (slots livianos)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Edición drástica: el target congelado NO se invalida ni se re-matchea
# ---------------------------------------------------------------------------
def test_drastic_product_edits_do_not_invalidate_frozen_target(service):
    """Aunque el producto cambie título/precio/descripción por completo, el
    Listing que se elimina se construye SIEMPRE desde matched_target."""
    svc = service
    edited = _product_payload(title="Nombre Completamente Distinto", product_id=7)
    edited["price"] = 99.0
    edited["description"] = "Otra descripción radical"

    svc.prepare_delete({"product": edited, "matched_target": _target_payload()})
    ready = svc._ready_payloads[0]
    assert ready["from_republish"] is True
    assert ready["listing_title"] == "iPhone 13 128GB"

    svc.execute_delete({})
    listing = svc._calls["delete"][0]
    # El target congelado manda (url/referencia/título/precio de Facebook).
    assert listing.url == "https://www.facebook.com/marketplace/item/777"
    assert listing.reference == "777"
    assert listing.title == "iPhone 13 128GB"
    assert listing.price == 1850000
    # Los datos editados NO se filtran al objeto que se va a eliminar.
    assert listing.title != "Nombre Completamente Distinto"
    assert listing.price != 99.0


def test_create_uses_edited_product_but_keeps_matched_id(service):
    """create_and_publish recibe el producto EDITADO para la NUEVA publicación,
    pero conserva el matched_id del target congelado para trazabilidad."""
    svc = service
    svc.prepare_delete({"product": _product_payload(), "matched_target": _target_payload()})
    svc.execute_delete({})
    assert svc._delete_results[0]["result"] == "DELETED_CONFIRMED"

    edited = _product_payload(title="iPhone 13 BNA 128GB Nuevo Precio", product_id=7)
    edited["price"] = 2050000.0
    svc.create_and_publish({"product": edited, "matched_id": 42, "image_paths": []})

    created = svc._calls["create"][0]
    assert created.title == "iPhone 13 BNA 128GB Nuevo Precio"
    assert created.price == 2050000.0
    result = svc._publish_results[0]
    assert result["result"] == "PUBLISHED_CONFIRMED"
    assert result["matched_id"] == 42
    assert result["new_reference"] == "555"


def test_freeze_match_sets_matched_state(service):
    svc = service
    svc.freeze_match(
        {
            "product_id": 1,
            "title": "iPhone 13 128GB",
            "url": "https://www.facebook.com/marketplace/item/777",
            "reference": "777",
            "confidence": "HIGH",
        }
    )
    assert svc.state == AutomationState.MATCHED


def test_freeze_match_rejects_non_high(service):
    svc = service
    svc.freeze_match(
        {
            "product_id": 1,
            "title": "iPhone 13 128GB",
            "url": "https://www.facebook.com/marketplace/item/777",
            "reference": "777",
            "confidence": "MEDIUM",
        }
    )
    assert svc.state == AutomationState.REPUBLISH_BLOCKED


def test_mark_edit_saved_sets_awaiting_confirm(service):
    svc = service
    svc.mark_editing(42)
    assert svc.state == AutomationState.EDITING_PRODUCT
    svc.mark_edit_saved(42)
    assert svc.state == AutomationState.AWAITING_REPUBLISH_CONFIRM