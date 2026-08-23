"""Servicio de automatización.

Esta es la capa `AutomationService` del diagrama de arquitectura:

    GUI -> AutomationService -> MarketplaceAdapter -> Playwright -> Facebook

En la Iteración 1 el servicio solo arrancaba el navegador y comprobaba la
sesión. En la Iteración 2 añade la navegación guiada hasta "Tus
publicaciones" de Marketplace, delega en `MarketplaceAdapter` (que a su vez
usa `selectors.py`), y expone el estado vía señales Qt.

Se implementa como un `QObject` pensado para vivir en un `QThread` propio.
TODO lo que toca `Page`/`BrowserContext`/`MarketplaceAdapter` se ejecuta en
el hilo del loop asyncio dedicado (`AsyncBridge`): el worker usa la `Page`
a través de `AsyncProxy`, que delega cada operación a ese hilo. Así se
elimina la mezcla asyncio-Proactor + greenlets + event-loop de Qt en un
mismo hilo que causaba el crash nativo (0xC0000005).
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from app.automation.browser import BrowserManager
from app.automation.listing_creator import ListingCreator, PublishResult, PublishStatus
from app.automation.listing_deleter import DeleteResult, DeleteStatus, ListingDeleter
from app.automation.listing_finder import ListingFinder
from app.automation.listing_matcher import ConfidenceLevel, MatchStatus
from app.automation.listing_scanner import ListingScanner, ScanBatchResult
from app.automation.marketplace import MarketplaceAdapter
from app.automation.screenshots import save_screenshot
from app.automation.states import AutomationState, AutomationStateMachine
from app.core import forensics
from app.core.async_bridge import AsyncBridge, AsyncProxy
from app.core.exceptions import AutomationError, BrowserLaunchError, InterventionRequiredError
from app.core.logging_config import get_logger
from app.models.listing import Listing
from app.models.product import Product

logger = get_logger(__name__)

# Estados desde los que "Continuar" reanuda la navegación por Marketplace.
_NAVIGATION_STATES = frozenset(
    {
        AutomationState.OPENING_MARKETPLACE,
        AutomationState.OPENING_YOUR_LISTINGS,
    }
)

_SCAN_STATES = frozenset({AutomationState.SCANNING_LISTINGS})

# Estados de búsqueda individual
_SEARCH_STATES = frozenset(
    {
        AutomationState.SEARCHING_LISTING,
        AutomationState.MATCHING_LISTING,
    }
)

# Estados de eliminación que al reanudar ejecutan verificación (NO reintento ciego)
_DELETE_STATES = frozenset(
    {
        AutomationState.VERIFYING_DELETE,
        AutomationState.DELETING_LISTING,
        AutomationState.VERIFYING_DELETION,
    }
)

# Estados de creación/publicación que al reanudar verifican ANTES de
# re-publicar (sección 4 del spec: nunca se crea un segundo anuncio a ciegas).
_CREATE_STATES = frozenset(
    {
        AutomationState.CREATING_LISTING,
        AutomationState.UPLOADING_IMAGES,
        AutomationState.FILLING_LISTING,
        AutomationState.FILLING_DATA,
        AutomationState.PUBLISHING,
        AutomationState.VERIFYING_PUBLICATION,
    }
)


class AutomationService(QObject):
    """Orquesta el arranque del navegador, la comprobación de sesión y la
    navegación hasta "Tus publicaciones".

    Señales (todas seguras de conectar desde el hilo de la GUI):
        state_changed(str):              nombre del nuevo estado.
        log_message(str):                línea de log legible para la UI.
        session_checked(bool, str):      (logged_in, detalle) tras comprobar sesión.
        marketplace_opened(bool, str):   (ok, detalle) tras abrir Marketplace.
        listings_ready(bool, str):       (found, detalle) tras cargar "Tus publicaciones".
        search_listing_requested(object):La GUI pide buscar una publicación (dict del producto).
        search_listing_result(object):   resultado de la búsqueda (dict serializable).
        intervention_paused(str):        motivo por el que se pausó esperando al usuario.
        error_occurred(str):             error no recuperable.
        finished():                      el hilo terminó su trabajo (browser sigue abierto).
    """

    state_changed = Signal(str)
    log_message = Signal(str)
    session_checked = Signal(bool, str)
    marketplace_opened = Signal(bool, str)
    listings_ready = Signal(bool, str)
    listings_scan_started = Signal()
    listings_scan_progress = Signal(int, int, int)
    listings_scan_completed = Signal(object)
    scan_requested = Signal(object)
    search_listing_requested = Signal(object)
    search_listing_result = Signal(object)
    delete_listing_requested = Signal(object)
    delete_ready = Signal(object)
    delete_result = Signal(object)
    republish_progress = Signal(object)
    publication_result = Signal(object)
    intervention_paused = Signal(str)
    error_occurred = Signal(str)
    finished = Signal()

    # Señales de petición para el flujo de republicación (Iteración 5).
    # La GUI NO usa QMetaObject.invokeMethod con args (PySide6 lo rechaza);
    # envía la operación por estas señales, conectadas internamente a los
    # slots correspondientes (mismo patrón que search_listing_requested).
    republish_freeze_requested = Signal(object)          # -> freeze_match
    republish_mark_editing_requested = Signal(int)       # -> mark_editing
    republish_mark_edit_saved_requested = Signal(int)    # -> mark_edit_saved
    execute_delete_requested = Signal(object)            # -> execute_delete
    create_and_publish_requested = Signal(object)        # -> create_and_publish
    resume_republish_requested = Signal(object)          # -> resume_republish

    def __init__(self) -> None:
        super().__init__()
        self._browser_manager = BrowserManager()
        self._bridge = AsyncBridge(name="playwright-loop")
        self._state_machine = AutomationStateMachine()
        self._async_page = None
        self._page = None
        self._marketplace_adapter: MarketplaceAdapter | None = None
        self._pending_product: Product | None = None
        self._pending_listing: Listing | None = None
        self._pending_confidence: str = ""
        self._pending_matched_id: int | None = None
        self._pending_image_paths: list[str] = []
        self._cached_products: list[Product] = []
        # True si la búsqueda en curso (o la actual) fue interrumpida por una
        # intervención manual de Facebook (login/CAPTCHA/verificación).
        self._search_had_intervention = False
        # Conexiones queued automáticas de Qt
        self.search_listing_requested.connect(self.search_listing)
        self.delete_listing_requested.connect(self.prepare_delete)
        self.scan_requested.connect(self.scan_listings)
        self.republish_freeze_requested.connect(self.freeze_match)
        self.republish_mark_editing_requested.connect(self.mark_editing)
        self.republish_mark_edit_saved_requested.connect(self.mark_edit_saved)
        self.execute_delete_requested.connect(self.execute_delete)
        self.create_and_publish_requested.connect(self.create_and_publish)
        self.resume_republish_requested.connect(self.resume_republish)
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(1000)
        self._heartbeat_timer.timeout.connect(self._heartbeat)
        self._last_driver_snapshot = "inicial"

    def set_cached_products(self, products: list[Product]) -> None:
        """Almacena la lista de productos locales para el matching automático."""
        self._cached_products = list(products)

    @Slot()
    def _heartbeat(self) -> None:
        """Registra en forensics el estado del driver una vez por segundo,
        para detectar si el driver murió. No realiza IPC de Playwright ni toca
        objetos asyncio: solo verifica el PID de Windows."""
        if not forensics.is_enabled():
            return
        pid = self._browser_manager.node_pid
        if pid is None:
            return
        alive = forensics.driver_alive_by_pid(pid)
        snapshot = f"pid={pid},alive={alive}"
        if snapshot == self._last_driver_snapshot:
            return
        self._last_driver_snapshot = snapshot
        forensics.evt("driver.heartbeat", f"thread={threading.get_ident()} {snapshot}")

    def _set_state(self, state: AutomationState) -> None:
        self._state_machine.transition_to(state)
        self.state_changed.emit(state.name)

    def _ensure_adapter(self) -> MarketplaceAdapter:
        """Crea (o reutiliza) el adaptador de Marketplace para la página
        actual. Debe llamarse dentro del hilo de automatización."""
        if self._page is None:
            raise AutomationError("El navegador no está abierto todavía")
        if self._marketplace_adapter is None:
            self._marketplace_adapter = MarketplaceAdapter(self._page)
        return self._marketplace_adapter

    # -- Slots (ejecutarse dentro del QThread dedicado) ----------------------
    @Slot()
    def start_and_check_session(self) -> None:
        """Abre Chromium con perfil persistente, comprueba la sesión y, si
        hay sesión, continúa navegando a "Tus publicaciones"."""
        try:
            forensics.evt("slot.invoke", "start_and_check_session")
            self._set_state(AutomationState.STARTING_BROWSER)
            self.log_message.emit("Iniciando navegador")
            if not self._bridge.is_started:
                self._bridge.start()
            async_page = self._bridge.submit(lambda: self._browser_manager.start())
            self._async_page = async_page
            self._page = AsyncProxy(async_page, self._bridge)
            self._heartbeat_timer.start()

            self._set_state(AutomationState.CHECKING_SESSION)
            self.log_message.emit("Comprobando sesión de Facebook")
            status = self._bridge.submit(
                lambda: self._browser_manager.check_facebook_session(async_page)
            )

            if status.logged_in:
                self.log_message.emit("Sesión detectada")
                self.session_checked.emit(True, status.detail)
                self._navigate_to_marketplace()
            else:
                self.log_message.emit("Sesión no detectada: se requiere login manual")
                self._set_state(AutomationState.WAITING_LOGIN)
                self.session_checked.emit(False, status.detail)

        except BrowserLaunchError as exc:
            self._handle_error(str(exc))
        except AutomationError as exc:
            self._handle_error(str(exc))
        except Exception as exc:  # nunca dejar morir el hilo en silencio
            logger.exception("Error inesperado iniciando la automatización")
            self._handle_error(f"Error inesperado: {exc}")
        finally:
            forensics.evt("signal.emit", "finished (start_and_check_session)")
            self.finished.emit()

    @Slot()
    def recheck_session(self) -> None:
        """Vuelve a comprobar la sesión (p.ej. tras completar el login
        manual); si hay sesión, continúa con el flujo de Marketplace."""
        if self._page is None or self._async_page is None:
            self._handle_error("El navegador no está abierto todavía")
            self.finished.emit()
            return

        try:
            self._set_state(AutomationState.CHECKING_SESSION)
            self.log_message.emit("Volviendo a comprobar la sesión")
            status = self._bridge.submit(
                lambda: self._browser_manager.check_facebook_session(self._async_page)
            )

            if status.logged_in:
                self.log_message.emit("Sesión confirmada")
                self.session_checked.emit(True, status.detail)
                self._navigate_to_marketplace()
            else:
                self.log_message.emit("Todavía no se detecta sesión iniciada")
                self._set_state(AutomationState.WAITING_LOGIN)
                self.session_checked.emit(False, status.detail)
        except AutomationError as exc:
            self._handle_error(str(exc))
        except Exception as exc:
            logger.exception("Error inesperado comprobando la sesión")
            self._handle_error(f"Error inesperado: {exc}")
        finally:
            self.finished.emit()

    @Slot()
    def navigate_to_marketplace(self) -> None:
        """Navega cada vez que el usuario pide manualmente llegar a
        'Tus publicaciones' (botón de prueba de la Iteración 2)."""
        if self._page is None:
            self._handle_error("El navegador no está abierto todavía")
            self.finished.emit()
            return
        forensics.evt(
            "navigate.invoke",
            f"thread={threading.get_ident()} driver={self._driver_snapshot()}",
        )
        try:
            self._navigate_to_marketplace()
        except AutomationError as exc:
            self._handle_error(str(exc))
        except Exception as exc:
            logger.exception("Error inesperado navegando a Marketplace")
            self._handle_error(f"Error inesperado: {exc}")
        finally:
            self.finished.emit()

    @Slot()
    def continue_after_user_action(self) -> None:
        """Punto único de "Continuar". Según lo que estuviera esperando:

        - WAITING_LOGIN -> vuelve a comprobar la sesión.
        - WAITING_USER  -> reanuda el estado guardado. Si la pausa fue
          durante una navegación, se reanuda la navegación; si fue durante
          una búsqueda, se reanuda la búsqueda del MISMO producto.

        Nunca reinicia el flujo completo: usa el punto de reanudación de la
        máquina de estados y retoma exactamente donde se quedó.
        """
        state = self._state_machine.state
        if state == AutomationState.WAITING_LOGIN:
            self.recheck_session()
            return

        if state == AutomationState.WAITING_USER:
            try:
                resumed = self._state_machine.resume()
                self.log_message.emit(f"Reanudando desde {resumed.name}")
                if resumed in _NAVIGATION_STATES:
                    self._navigate_to_marketplace()
                elif resumed in _SCAN_STATES:
                    self.scan_listings()
                elif resumed == AutomationState.CHECKING_SESSION:
                    self.recheck_session()
                elif resumed in _SEARCH_STATES:
                    if self._pending_product is None:
                        self._handle_error("No hay ningún producto pendiente para reanudar la búsqueda")
                    else:
                        self._run_search(self._pending_product)
                elif resumed in _DELETE_STATES:
                    if self._pending_product is None or self._pending_listing is None:
                        self._handle_error("No hay datos pendientes para reanudar la verificación de eliminación")
                    else:
                        # MODIFICACIÓN 1: Al reanudar tras interrupción en eliminación, NO se reintenta delete ciegamente; se verifica.
                        self._verify_interrupted_deletion(self._pending_product, self._pending_listing)
                elif resumed in _CREATE_STATES:
                    if self._pending_product is None:
                        self._handle_error("No hay datos pendientes para reanudar la creación/publicación")
                    else:
                        # Verificar ANTES de re-publicar (nunca se crea un segundo anuncio a ciegas).
                        self._resume_publication(
                            self._pending_product,
                            self._pending_matched_id,
                            self._pending_image_paths,
                        )
                else:
                    self._handle_error(f"No hay continuidad implementada para {resumed.name}")
            except AutomationError as exc:
                self._handle_error(str(exc))
            except Exception as exc:
                logger.exception("Error inesperado reanudando el flujo")
                self._handle_error(f"Error inesperado: {exc}")
            finally:
                self.finished.emit()
            return

    @Slot(object)
    @Slot()
    def scan_listings(self, products_payload: list[dict] | None = None) -> None:
        """Escanea todas las publicaciones en 'Tus publicaciones' y las compara
        automáticamente contra los productos almacenados en SQLite."""
        try:
            if products_payload:
                self._cached_products = [self._product_from_payload(p) for p in products_payload]

            if self._page is None or not self._browser_manager.is_running:
                raise AutomationError("El navegador no está abierto o se desconectó")

            adapter = self._ensure_adapter()
            adapter.ensure_listings_section()

            self._set_state(AutomationState.SCANNING_LISTINGS)
            self.log_message.emit("Iniciando escaneo automático de publicaciones...")
            self.listings_scan_started.emit()

            scanner = ListingScanner(page=self._page, navigator=adapter)

            def _on_progress(scrolls: int, total: int, new_in_batch: int) -> None:
                self.listings_scan_progress.emit(scrolls, total, new_in_batch)
                self.log_message.emit(f"Escaneando: {total} publicaciones detectadas (scroll {scrolls})")

            scan_result = scanner.scan_and_match(self._cached_products, on_progress=_on_progress)

            self._set_state(AutomationState.LISTINGS_SCANNED)
            self.log_message.emit(
                f"Escaneo finalizado: {scan_result.total_listings} publicaciones encontradas, "
                f"{scan_result.matched_high_count} coincidencia(s) ALTA(S)"
            )
            self.listings_scan_completed.emit(scan_result.to_dict())

        except InterventionRequiredError as exc:
            self._request_intervention(str(exc))
        except AutomationError as exc:
            self._handle_error(str(exc))
        except Exception as exc:
            logger.exception("Error inesperado escaneando publicaciones")
            self._handle_error(f"Error escaneando publicaciones: {exc}")
        finally:
            self.finished.emit()

    @Slot(object)
    def search_listing(self, payload: dict) -> None:
        """Busca la publicación de un producto (solo lectura).

        `payload`: dict con los datos del producto tal como los conoce la
        GUI (title, price, images, marketplace_url/reference, ...). Se
        reconstruye un `Product` local, se ejecuta el `ListingFinder` y se
        emite el resultado por `search_listing_result`.
        """
        try:
            # Cada búsqueda arranca limpia: la intervención que suceda
            # durante ESTA búsqueda se reflejará en el resultado.
            self._search_had_intervention = False
            # El pending siempre se refresca con la última petición: si hay
            # una intervención y el usuario pulsa "Continuar", se reanuda la
            # búsqueda de este MISMO producto.
            product = self._product_from_payload(payload)
            self._pending_product = product
            forensics.evt(
                "search_listing.invoke",
                f"thread={threading.get_ident()} driver={self._driver_snapshot()}",
            )
            self._run_search(product)
        except InterventionRequiredError as exc:
            # Facebook pide una acción manual: pausamos; "Continuar"
            # reanudará la búsqueda del mismo producto (pending).
            self._search_had_intervention = True
            self._request_intervention(str(exc))
        except AutomationError as exc:
            self._handle_error(str(exc))
        except Exception as exc:
            logger.exception("Error inesperado buscando publicación")
            self._handle_error(f"Error inesperado: {exc}")
        finally:
            self.finished.emit()

    # ------------------------------------------------------------------
    # Flujo de republicación: congelar / editar (Iteración 5)
    # ------------------------------------------------------------------
    @Slot(object)
    def freeze_match(self, payload: dict) -> None:
        """Congela el ítem HIGH del escaneo como target de republicación.

        Es un slot LIVIANO: no re-escanea ni re-ejecuta el matcher. Solo
        valida la confianza (defensa en profundidad), pasa la FSM a MATCHED
        y deja constancia en el log. La persistencia del `MatchedListing`
        la hace la GUI vía `MatchedListingService` (reparto actual:
        el servicio emite, la GUI persiste).
        """
        try:
            confidence = payload.get("confidence") or ""
            if confidence != "HIGH":
                self._set_state(AutomationState.REPUBLISH_BLOCKED)
                msg = "Target congelado BLOQUEADO: la confianza debe ser HIGH"
                self.log_message.emit(msg)
                self.error_occurred.emit(msg)
                return
            url = payload.get("url") or ""
            ref = payload.get("reference") or ""
            if not url and not ref:
                self._set_state(AutomationState.REPUBLISH_BLOCKED)
                msg = "Target congelado BLOQUEADO: falta URL/referencia real de la publicación"
                self.log_message.emit(msg)
                self.error_occurred.emit(msg)
                return

            self._set_state(AutomationState.MATCHED)
            self.log_message.emit(
                f"Target congelado (MATCHED): {payload.get('title') or ref or url} "
                f"({confidence}) — no se vuelve a escanear ni a matchear"
            )
            self.republish_progress.emit(
                {"phase": "matched", "matched_id": payload.get("matched_id")}
            )
        except Exception as exc:
            logger.exception("Error congelando target")
            self._handle_error(f"Error congelando target: {exc}")
        finally:
            self.finished.emit()

    @Slot(int)
    def mark_editing(self, matched_id: int) -> None:
        """El usuario está editando el producto (FSM → EDITING_PRODUCT)."""
        try:
            self._pending_matched_id = matched_id
            self._set_state(AutomationState.EDITING_PRODUCT)
            self.log_message.emit(f"Editando producto para republicación (target {matched_id})")
        except Exception as exc:
            logger.exception("Error marcando edición")
            self._handle_error(f"Error marcando edición: {exc}")
        finally:
            self.finished.emit()

    @Slot(int)
    def mark_edit_saved(self, matched_id: int) -> None:
        """Edición guardada: esperando confirmación (FSM → AWAITING_REPUBLISH_CONFIRM)."""
        try:
            self._pending_matched_id = matched_id
            self._set_state(AutomationState.AWAITING_REPUBLISH_CONFIRM)
            self.log_message.emit(f"Edición guardada; esperando confirmación de republicación (target {matched_id})")
            self.republish_progress.emit({"phase": "edit_saved", "matched_id": matched_id})
        except Exception as exc:
            logger.exception("Error marcando edición guardada")
            self._handle_error(f"Error marcando edición guardada: {exc}")
        finally:
            self.finished.emit()

    @Slot(object)
    def prepare_delete(self, payload: dict) -> None:
        """Verifica la solicitud de eliminación antes de pedir confirmación humana.

        REGLA DE SEGURIDAD ABSOLUTA: Solo permite avanzar si confidence == HIGH
        y existe un listing identificado con URL/referencia.

        Acepta dos orígenes:
        - `payload["best"]`: flujo legacy "Buscar publicación → Eliminar"
          (el listing se reconstruye desde el resultado de la búsqueda).
        - `payload["matched_target"]`: target CONGELADO del flujo de
          republicación. El `Listing` se construye SIEMPRE desde los campos
          helados (listing_url/reference/title/price), NUNCA desde
          `product.title` ni desde el producto editado.
        """
        try:
            self._set_state(AutomationState.VERIFYING_DELETE)
            self.log_message.emit("Verificando condiciones de seguridad para eliminación")

            product = self._product_from_payload(payload.get("product") or {})

            matched_target = payload.get("matched_target") or {}
            if matched_target:
                confidence = matched_target.get("confidence") or ""
                url = matched_target.get("url") or ""
                ref = matched_target.get("reference") or ""
                matched_id = matched_target.get("matched_id")
            else:
                confidence = ""
                url = ""
                ref = ""
                matched_id = None

            if confidence != "HIGH":
                self._set_state(AutomationState.DELETE_FAILED)
                msg = f"Eliminación bloqueada: la confianza es {confidence} (se requiere HIGH)"
                self.log_message.emit(msg)
                self._emit_delete_result(
                    product,
                    None,
                    DeleteResult(status=DeleteStatus.DELETE_FAILED, listing=Listing("", None), error=msg, detail=msg),
                )
                return

            if not url and not ref:
                self._set_state(AutomationState.DELETE_FAILED)
                msg = "Eliminación bloqueada: no se identificó URL/referencia real de la publicación"
                self.log_message.emit(msg)
                self._emit_delete_result(
                    product,
                    None,
                    DeleteResult(status=DeleteStatus.DELETE_FAILED, listing=Listing("", None), error=msg, detail=msg),
                )
                return

            if matched_target:
                # Construcción del Listing SOLO desde los datos congelados.
                listing = Listing(
                    title=matched_target.get("title") or product.title,
                    price=matched_target.get("price"),
                    price_raw=matched_target.get("price_raw") or "",
                    url=url,
                    reference=ref,
                )
            else:
                best_dict = payload.get("best") or {}
                listing_dict = best_dict.get("listing") or {}
                listing = Listing(
                    title=listing_dict.get("title") or product.title,
                    price=listing_dict.get("price"),
                    price_raw=listing_dict.get("price_raw") or "",
                    url=url,
                    reference=ref,
                )

            self._pending_product = product
            self._pending_listing = listing
            self._pending_confidence = confidence
            self._pending_matched_id = matched_id

            self._set_state(AutomationState.AWAITING_DELETE_CONFIRM)
            self.log_message.emit(f"Publicación lista para confirmación: {listing.title} ({confidence})")

            ready_payload = {
                "product_id": product.id,
                "product_title": product.title,
                "listing_title": listing.title,
                "price": listing.price_raw or str(listing.price or "—"),
                "url": listing.url,
                "reference": listing.reference,
                "confidence": confidence,
                "from_republish": bool(matched_target),
                "matched_id": matched_id,
            }
            self.delete_ready.emit(ready_payload)
        except Exception as exc:
            logger.exception("Error en prepare_delete")
            self._handle_error(f"Error preparando eliminación: {exc}")
        finally:
            self.finished.emit()

    @Slot(object)
    def execute_delete(self, payload: dict) -> None:
        """Ejecuta la eliminación tras la confirmación explícita del usuario."""
        try:
            forensics.evt("slot.invoke", "execute_delete")
            if self._pending_product is None or self._pending_listing is None:
                raise AutomationError("No hay datos de eliminación preparados")

            product = self._pending_product
            listing = self._pending_listing

            if self._page is None:
                raise AutomationError("El navegador no está abierto para eliminar")

            adapter = self._ensure_adapter()

            self._set_state(AutomationState.DELETING_LISTING)
            self.log_message.emit(f"Ejecutando eliminación de: {listing.title}")

            deleter = ListingDeleter()
            res = deleter.delete(listing, self._page, navigator=adapter)

            if res.status == DeleteStatus.INTERVENTION_REQUIRED:
                self._request_intervention(res.error or "Intervención requerida durante la eliminación")
                return

            self._finish_deletion_process(product, listing, res)
        except InterventionRequiredError as exc:
            self._request_intervention(str(exc))
        except AutomationError as exc:
            self._handle_error(str(exc))
        except Exception as exc:
            logger.exception("Error inesperado en execute_delete")
            self._handle_error(f"Error ejecutando eliminación: {exc}")
        finally:
            self.finished.emit()

    def _verify_interrupted_deletion(self, product: Product, listing: Listing) -> None:
        """Reanudación segura (modificación 1): solo verifica, no vuelve a borrar."""
        try:
            if self._page is None:
                raise AutomationError("Navegador no abierto para verificación")

            self._set_state(AutomationState.VERIFYING_DELETION)
            self.log_message.emit(f"Verificando estado post-interrupción de: {listing.title}")

            deleter = ListingDeleter()
            res = deleter.verify_only(listing, self._page)
            self._finish_deletion_process(product, listing, res)
        except Exception as exc:
            logger.exception("Error verificando eliminación interrumpida")
            self._handle_error(f"Error verificando eliminación: {exc}")

    def _finish_deletion_process(self, product: Product, listing: Listing, res: DeleteResult) -> None:
        if res.status == DeleteStatus.DELETED_CONFIRMED:
            self._set_state(AutomationState.LISTING_DELETED)
            self.log_message.emit(f"ELIMINACIÓN CONFIRMADA: {listing.title}")
        elif res.status == DeleteStatus.DELETE_UNCERTAIN:
            self._set_state(AutomationState.DELETE_UNCERTAIN)
            self.log_message.emit(f"ELIMINACIÓN INCIERTA: {res.detail}")
        else:
            self._set_state(AutomationState.DELETE_FAILED)
            self.log_message.emit(f"FALLO EN ELIMINACIÓN: {res.detail}")

        self._emit_delete_result(product, listing, res)

    def _emit_delete_result(self, product: Product, listing: Listing | None, res: DeleteResult) -> None:
        out_payload = {
            "product_id": product.id,
            "product_title": product.title,
            "result": res.status.name,
            "confidence": self._pending_confidence,
            "error": res.error,
            "detail": res.detail,
            "listing_url": listing.url if listing else "",
            "listing_reference": listing.reference if listing else "",
            "verification_signals": list(res.verification_signals),
            "matched_id": self._pending_matched_id,
        }
        self.delete_result.emit(out_payload)

    # ------------------------------------------------------------------
    # Flujo de creación / publicación (Iteración 5)
    # ------------------------------------------------------------------
    @Slot(object)
    def create_and_publish(self, payload: dict) -> None:
        """Crea y publica la NUEVA publicación con los datos editados.

        SOLO se invoca tras DELETED_CONFIRMED del target congelado
        (regla sección 10: sin confirmación de eliminación no hay creación).
        `payload`:
            product: dict serializable del producto actualizado (leído de BD).
            matched_id: id del MatchedListing congelado (trazabilidad/reanudación).
            image_paths: rutas absolutas de las imágenes a subir.
        """
        try:
            product = self._product_from_payload(payload.get("product") or {})
            matched_id = payload.get("matched_id")
            image_paths = list(payload.get("image_paths") or [])

            self._pending_product = product
            self._pending_matched_id = matched_id
            self._pending_image_paths = image_paths

            if self._page is None:
                raise AutomationError("El navegador no está abierto para publicar")

            adapter = self._ensure_adapter()
            creator = ListingCreator()

            self._set_state(AutomationState.CREATING_LISTING)
            self.log_message.emit(f"Creando nueva publicación: {product.title}")
            self.republish_progress.emit({"phase": "creating_listing", "matched_id": matched_id})

            res = creator.create(product, self._page, navigator=adapter, image_paths=image_paths)

            if res.status == PublishStatus.INTERVENTION_REQUIRED:
                self._request_intervention(res.error or "Intervención requerida durante la creación/publicación")
                return

            self._finish_publication_process(product, matched_id, res)
        except InterventionRequiredError as exc:
            self._request_intervention(str(exc))
        except AutomationError as exc:
            self._handle_error(str(exc))
        except Exception as exc:
            logger.exception("Error inesperado en create_and_publish")
            self._handle_error(f"Error creando/publicando: {exc}")
        finally:
            self.finished.emit()

    @Slot(object)
    def resume_republish(self, payload: dict) -> None:
        """Reanuda un flujo de republicación interrumpido (recuperación al arrancar).

        Nunca re-elimina ni re-publica a ciegas: según la fase en la que
        quedó el target, primero VERIFICA el estado real:
        - phase == "delete": verifica la eliminación (verify_only, no re-delete).
        - phase in {"create", "publish"}: verifica la publicación ANTES de
          continuar (verify_only); si no existe, completa la creación.
        """
        try:
            product = self._product_from_payload(payload.get("product") or {})
            matched_id = payload.get("matched_id")
            phase = payload.get("phase") or ""
            image_paths = list(payload.get("image_paths") or [])

            self._pending_product = product
            self._pending_matched_id = matched_id
            self._pending_image_paths = image_paths

            if self._page is None:
                raise AutomationError("El navegador no está abierto para reanudar")

            if phase == "delete":
                target = payload.get("matched_target") or {}
                listing = Listing(
                    title=target.get("title") or product.title,
                    price=target.get("price"),
                    price_raw=target.get("price_raw") or "",
                    url=target.get("url") or "",
                    reference=target.get("reference") or "",
                )
                self._pending_listing = listing
                self._pending_confidence = target.get("confidence") or ""
                self._verify_interrupted_deletion(product, listing)
                return

            self._resume_publication(product, matched_id, image_paths)
        except InterventionRequiredError as exc:
            self._request_intervention(str(exc))
        except AutomationError as exc:
            self._handle_error(str(exc))
        except Exception as exc:
            logger.exception("Error inesperado reanudando republicación")
            self._handle_error(f"Error reanudando republicación: {exc}")
        finally:
            self.finished.emit()

    def _resume_publication(
        self,
        product: Product,
        matched_id: int | None,
        image_paths: list[str],
    ) -> None:
        """Reanudación de una publicación interrumpida: verifica ANTES de actuar."""
        if self._page is None:
            raise AutomationError("Navegador no abierto para verificar la publicación")

        adapter = self._ensure_adapter()
        creator = ListingCreator()

        self._set_state(AutomationState.VERIFYING_PUBLICATION)
        self.log_message.emit(f"Verificando si '{product.title}' ya fue publicado...")
        self.republish_progress.emit({"phase": "verifying_publication", "matched_id": matched_id})

        check = creator.verify_only(product, self._page, navigator=adapter)

        if check.status == PublishStatus.INTERVENTION_REQUIRED:
            self._request_intervention(check.error or "Intervención requerida para verificar la publicación")
            return

        if check.is_confirmed:
            self._finish_publication_process(product, matched_id, check)
            return

        # No está publicado: completar la creación del MISMO producto
        # (nunca se crea un segundo anuncio a ciegas: la verificación previa
        # descartó que el anterior haya quedado publicado).
        self.log_message.emit(
            "No se encontró la publicación pendiente; completando la creación con verificación previa"
        )
        self.republish_progress.emit({"phase": "creating_listing", "matched_id": matched_id})
        self._set_state(AutomationState.CREATING_LISTING)
        res = creator.create(product, self._page, navigator=adapter, image_paths=image_paths)
        self._finish_publication_process(product, matched_id, res)

    def _finish_publication_process(
        self,
        product: Product,
        matched_id: int | None,
        res: PublishResult,
    ) -> None:
        """Traduce el PublishResult a FSM + señal publication_result."""
        if res.status == PublishStatus.PUBLISHED_CONFIRMED:
            self._set_state(AutomationState.REPUBLISHED)
            self.log_message.emit(f"PUBLICACIÓN CONFIRMADA: {product.title}")
        elif res.status == PublishStatus.PUBLISH_UNCERTAIN:
            self._set_state(AutomationState.REPUBLISH_BLOCKED)
            self.log_message.emit(f"PUBLICACIÓN INCIERTA (bloqueada): {res.detail}")
        elif res.status == PublishStatus.PUBLISH_FAILED:
            self._set_state(AutomationState.REPUBLISH_BLOCKED)
            self.log_message.emit(f"FALLO EN PUBLICACIÓN: {res.detail}")
        else:
            self._set_state(AutomationState.REPUBLISH_BLOCKED)
            self.log_message.emit(f"Publicación sin completar: {res.detail}")

        out_payload = {
            "matched_id": matched_id,
            "product_id": product.id,
            "product_title": product.title,
            "result": res.status.name,
            "new_url": res.new_url,
            "new_reference": res.new_reference,
            "error": res.error,
            "detail": res.detail,
            "verification_signals": list(res.verification_signals),
        }
        self.publication_result.emit(out_payload)

    @Slot()
    def close_browser(self) -> None:
        """Cierra Chromium. Se debe llamar siempre al salir de la app."""
        forensics.evt("slot.invoke", "close_browser")
        self._heartbeat_timer.stop()
        if self._bridge.is_started:
            try:
                self._bridge.submit(lambda: self._browser_manager.stop())
            except Exception as exc:
                logger.warning("Excepción cerrando Chromium: %s", exc)
            self._bridge.stop()
        self._async_page = None
        self._page = None
        self._marketplace_adapter = None
        self._pending_product = None
        self._pending_listing = None
        self._pending_matched_id = None
        self._pending_image_paths = []
        self._set_state(AutomationState.IDLE)
        self.log_message.emit("Navegador cerrado")

    def _driver_snapshot(self) -> str:
        """Info del driver para forensics. Se evalúa en el hilo del loop
        (toca internos asyncio de Playwright), nunca en el hilo Qt."""
        if not forensics.is_enabled() or not self._bridge.is_started:
            return ""
        try:
            return str(
                self._bridge.submit(
                    lambda: forensics.driver_proc_info(self._browser_manager._playwright)
                )
            )
        except Exception as exc:
            return f"err={exc}"

    # -- Flujo real ----------------------------------------------------------
    def _navigate_to_marketplace(self) -> None:
        """Abre Marketplace y luego "Tus publicaciones", con logging y
        señales. Si Facebook pide una acción manual se pausa (WAITING_USER)."""
        adapter = self._ensure_adapter()

        self._set_state(AutomationState.OPENING_MARKETPLACE)
        self.log_message.emit("Abriendo Marketplace")

        if adapter.requires_intervention():
            self._request_intervention("Facebook pide una acción manual al entrar en Marketplace")
            return

        result = adapter.open_marketplace()
        self.marketplace_opened.emit(result.ok, result.detail)

        if not result.ok:
            # El navegador visible sigue abierto: es un buen momento para que
            # el usuario corrija manualmente y pulse "Continuar".
            reason = "No se detectó Marketplace en la página; revísalo en el navegador y pulsa Continuar"
            self._request_intervention(reason)
            return

        self._set_state(AutomationState.OPENING_YOUR_LISTINGS)
        self.log_message.emit("Abriendo 'Tus publicaciones'")

        if adapter.requires_intervention():
            self._request_intervention(
                "La página de Marketplace pide una acción manual (login/CAPTCHA/verificación)"
            )
            return

        state = adapter.open_your_listings()
        if state.found:
            self.log_message.emit("Sección 'Tus publicaciones' encontrada")
            self.listings_ready.emit(state.found, state.reason)
            # Iniciar escaneo automático de publicaciones al confirmar carga
            self.scan_listings()
        else:
            self.log_message.emit("No se confirmó la sección 'Tus publicaciones'")
            self.listings_ready.emit(state.found, state.reason)

    # -- Búsqueda de publicación (Iteración 3) --------------------------------
    @staticmethod
    def _map_find_status(status: MatchStatus) -> AutomationState:
        """Traduce el resultado del buscador a un estado de la máquina."""
        mapping = {
            MatchStatus.FOUND: AutomationState.LISTING_FOUND,
            MatchStatus.MEDIUM_CONFIDENCE: AutomationState.LISTING_FOUND,
            MatchStatus.LOW_CONFIDENCE: AutomationState.LISTING_NOT_FOUND,
            MatchStatus.NOT_FOUND: AutomationState.LISTING_NOT_FOUND,
            MatchStatus.AMBIGUOUS: AutomationState.AMBIGUOUS_LISTING,
            MatchStatus.SEARCH_LIMIT_REACHED: AutomationState.SEARCH_LIMIT_REACHED,
        }
        return mapping.get(status, AutomationState.LISTING_NOT_FOUND)

    @staticmethod
    def _product_from_payload(payload: dict) -> Product:
        """Reconstruye un `Product` desde el dict que envía la GUI
        (solo lectura: la GUI es la dueña de los datos de producto)."""
        return Product(
            id=payload.get("product_id"),
            title=payload.get("title") or "",
            description=payload.get("description") or "",
            price=float(payload.get("price") or 0),
            category=payload.get("category") or "",
            condition=payload.get("condition") or "",
            location=payload.get("location") or "",
            tags=list(payload.get("tags") or []),
            images=list(payload.get("images") or []),
            enabled=bool(payload.get("enabled", True)),
            marketplace_url=payload.get("marketplace_url"),
            marketplace_reference=payload.get("marketplace_reference"),
        )

    def _run_search(self, product: Product) -> None:
        """Ejecuta el buscar de publicación y emite el resultado.

        El resultado se emite con datos serializables (dicts), nunca con
        objetos del dominio, para mantener la conexión queued de Qt.
        """
        if self._page is None or not self._browser_manager.is_running:
            raise AutomationError("El navegador no está abierto o se desconectó")

        adapter = self._ensure_adapter()
        adapter.ensure_listings_section()

        self._set_state(AutomationState.SEARCHING_LISTING)
        self.log_message.emit(f"Buscando publicación: {product.title}")

        finder = ListingFinder(page=self._page, navigator=adapter)

        def _on_phase(phase: str) -> None:
            if phase == "matching":
                self._set_state(AutomationState.MATCHING_LISTING)

        try:
            forensics.evt("listing_finder.start", f"driver=pid:{self._browser_manager.node_pid}")
            result = finder.find(product, on_phase=_on_phase)
            forensics.evt("listing_finder.done", f"status={result.status.name} driver=pid:{self._browser_manager.node_pid}")
        except Exception as exc:
            err_str = str(exc)
            if "broken pipe" in err_str.lower() or "target closed" in err_str.lower() or "connection closed" in err_str.lower():
                logger.error("Se detectó desconexión del navegador (EPIPE/TargetClosed) durante la búsqueda: %s", exc)
                raise AutomationError("El motor del navegador se desconectó. Vuelve a iniciar la sesión.") from exc
            raise

        # Solo guardamos captura en resultados no concluyentes: aporta
        # diagnóstico cuando Facebook cambió la interfaz y no se encontró,
        # fue ambiguo o se alcanzó el límite. En hallazgos seguros no hace
        # falta.
        if result.status in (
            MatchStatus.NOT_FOUND,
            MatchStatus.AMBIGUOUS,
            MatchStatus.SEARCH_LIMIT_REACHED,
        ):
            try:
                result.screenshot_path = save_screenshot(self._page, tag=product.title)
            except Exception as ss_exc:
                logger.debug("No se pudo guardar la captura tras la búsqueda: %s", ss_exc)

        self._set_state(self._map_find_status(result.status))
        self.log_message.emit(
            f"Resultado de búsqueda de '{product.title}': {result.status.name} "
            f"({result.scanned_count} publicaciones revisadas)"
        )

        self._emit_search_result(product, result)

    def _emit_search_result(self, product: Product, find_result) -> None:
        """Convierte el `FindResult` a un dict serializable y lo emite."""
        best = find_result.best_match
        payload = {
            "product_id": product.id,
            "title": product.title,
            "status": find_result.status.name,
            "stopped_for": find_result.stopped_for,
            "scanned": find_result.scanned_count,
            "screenshot_path": find_result.screenshot_path,
            "best": best.to_dict() if best else None,
            "candidates": [c.to_dict() for c in find_result.outcome.candidates],
            "had_intervention": self._search_had_intervention,
        }
        self.search_listing_result.emit(payload)

    # -- Intervención manual ---------------------------------------------------
    def _request_intervention(self, reason: str) -> None:
        """Pasamos la máquina de estados a WAITING_USER guardando el punto
        de reanudación. El navegador queda abierto, tal cual está."""
        self._state_machine.request_intervention(reason)
        self.state_changed.emit(AutomationState.WAITING_USER.name)
        self.log_message.emit(f"PAUSA: {reason}")
        self.intervention_paused.emit(reason)

    def _handle_error(self, message: str) -> None:
        logger.error(message)
        self._set_state(AutomationState.ERROR)
        self.log_message.emit(f"ERROR: {message}")
        self.error_occurred.emit(message)

    @property
    def is_browser_running(self) -> bool:
        return self._browser_manager.is_running

    @property
    def state(self) -> AutomationState:
        return self._state_machine.state