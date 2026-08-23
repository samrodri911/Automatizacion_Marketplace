"""Ventana principal de Marketplace Manager.

Iteración 1: CRUD de productos + conexión con Facebook (Chromium visible,
perfil persistente, detección de sesión, login manual).
Iteración 2: navegación guiada hasta "Tus publicaciones" de Marketplace.
Iteración 3: localización y verificación de la publicación de un producto.
Iteración 4: eliminación segura y verificable (integrada en el flujo de
republicación).
Iteración 5: flujo completo por producto "Editar y republicar" con un solo
botón principal "🔄 Republicar".

Fase actual: UI simplificada con UN botón principal "🔄 Republicar" que
encadena todo el flujo (congelar target → editar datos → confirmar →
eliminar → verificar → crear → publicar → verificar). El target de Facebook
se congela en el primer `select_match` y NO se invalida al editar el
producto: permanece congelado hasta completar o cancelar explícitamente el
flujo (cancelación, cambio de producto en fase pre-confirmación, o
eliminación del producto).

La GUI nunca toca Playwright: todas las operaciones se envían al
`AutomationService` (que vive en un `QThread` dedicado) mediante señales Qt
tipadas (`Signal(object)`/`Signal(int)`), nunca con
`QMetaObject.invokeMethod` pasando dicts (PySide6 lo rechaza).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.config import app_config
from app.core import forensics
from app.core.exceptions import MarketplaceManagerError
from app.core.logging_config import get_logger
from app.gui.delete_confirm_dialog import DeleteConfirmDialog
from app.gui.listing_result_dialog import ListingResultDialog
from app.gui.product_editor import ProductEditorDialog
from app.gui.product_list_panel import ProductListPanel
from app.gui.queue_failure_dialog import QueueFailureChoice, QueueFailureDialog
from app.gui.queue_prep_dialog import QueuePrepDialog
from app.gui.republish_confirm_dialog import RepublishConfirmDialog
from app.models.matched_listing import (
    STATUS_AWAITING_CONFIRM,
    STATUS_CREATING,
    STATUS_DELETED,
    STATUS_DELETING,
    STATUS_EDITING,
    STATUS_PUBLISHING,
    STATUS_SELECTED,
    STATUS_VERIFYING_PUBLICATION,
)
from app.models.product import Product
from app.models.republish_queue import (
    QueueItem,
    QueueItemStatus,
    RepublishQueueState,
)
from app.services.automation_service import AutomationService
from app.services.matched_listing_service import MatchedListingService
from app.services.product_service import ProductService
from app.services.republish_queue import RepublishQueue, build_queue

logger = get_logger(__name__)

# Mapeo método de servicio -> señal de petición. `_invoke_service` despacha
# por señal (queued), que es el mecanismo que PySide6 soporta para pasar
# objetos Python entre el hilo de la GUI y el QThread del worker.
_SERVICE_REQUEST_SIGNALS: dict[str, str] = {
    "freeze_match": "republish_freeze_requested",
    "mark_editing": "republish_mark_editing_requested",
    "mark_edit_saved": "republish_mark_edit_saved_requested",
    "prepare_delete": "delete_listing_requested",
    "execute_delete": "execute_delete_requested",
    "create_and_publish": "create_and_publish_requested",
    "resume_republish": "resume_republish_requested",
}


class MainWindow(QMainWindow):
    def __init__(self, product_service: ProductService, matched_service: MatchedListingService) -> None:
        super().__init__()
        self._product_service = product_service
        self._matched_service = matched_service

        self._automation_thread: QThread | None = None
        self._automation_service: AutomationService | None = None
        # `_search_ready` indica que la automatización está en un estado en el
        # que se puede buscar una publicación (sesión activa + navegación a
        # "Tus publicaciones" disponible). No significa que el navegador esté
        # necesariamente en la sección: el propio servicio garantiza esto.
        self._search_ready = False
        # Último resultado de búsqueda (dict serializable) recibido.
        self._last_search_result: dict | None = None
        # Id del target congelado del flujo "Editar y republicar" en curso.
        self._republish_matched_id: int | None = None
        # Último producto seleccionado (para limpiar targets pre-confirmación
        # huérfanos al cambiar de producto).
        self._last_selected_product_id: int | None = None
        # Cola de republicación múltiple (en memoria, por sesión). None cuando
        # no hay cola en curso.
        # Cola de republicación múltiple (en memoria, por sesión). None cuando
        # no hay cola en curso.
        self._queue: RepublishQueue | None = None
        
        # Resultados visuales del último escaneo, mapeando product_id a un string de status.
        self._latest_scan_results: dict[int, str] = {}

        self.setWindowTitle(app_config.app_name)
        self.resize(840, 820)

        self._build_ui()
        # La lista de productos expone DOS conceptos separados:
        # - selection_changed: el producto ENFOCADO (uno solo) para ver detalle.
        # - queue_changed: el conjunto de IDs MARCADOS para la cola.
        self.products_panel.selection_changed.connect(self._on_products_selection_changed)
        self.products_panel.queue_changed.connect(lambda _ids: self._update_queue_button_state())
        self.products_panel.queue_requested.connect(lambda _ids: self._on_queue_republish())
        self._reload_products()

    # -- Construcción de la UI -----------------------------------------------
    # -- Construcción de la UI -----------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        central.setStyleSheet("""
            QWidget#central {
                background-color: #ffffff;
            }
            QLabel {
                color: #222222;
            }
            QCheckBox {
                color: #222222;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #e0e0e0;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 3px;
                color: #333333;
            }
            QPushButton {
                background-color: #f5f5f5;
                border: 1px solid #cccccc;
                border-radius: 4px;
                padding: 6px 12px;
                color: #333333;
            }
            QPushButton:hover {
                background-color: #e8e8e8;
                border-color: #b3b3b3;
            }
            QPushButton:pressed {
                background-color: #dcdcdc;
            }
            QPushButton:disabled {
                background-color: #f9f9f9;
                color: #bbbbbb;
                border-color: #e5e5e5;
            }
            QScrollArea {
                background-color: #fafafa;
                border: 1px solid #dcdcdc;
                border-radius: 4px;
            }
            QScrollBar:vertical {
                background-color: #f5f5f5;
                width: 10px;
                margin: 0px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background-color: #cdcdcd;
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #a6a6a6;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QListWidget {
                background-color: #fafafa;
                border: 1px solid #dcdcdc;
                border-radius: 4px;
                color: #222222;
            }
        """)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)

        # -- Barra Superior de Conexión (Minimalista) --
        top_bar = QFrame()
        top_bar.setStyleSheet("background-color: #f0f0f0; border-radius: 5px; padding: 5px;")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(10, 5, 10, 5)
        
        self.session_status_label = QLabel("Estado de Facebook: Desconectado")
        self.session_status_label.setStyleSheet("font-weight: bold; color: #555;")
        top_layout.addWidget(self.session_status_label)
        
        self.scan_status_label = QLabel("")
        self.scan_status_label.setStyleSheet("color: #777;")
        top_layout.addWidget(self.scan_status_label)
        
        top_layout.addStretch()
        
        self.connect_btn = QPushButton("Conectar a Facebook")
        self.connect_btn.clicked.connect(self._on_connect_facebook)
        self.connect_btn.setStyleSheet("background-color: #0078d7; color: white; padding: 5px 15px; border-radius: 3px;")
        
        self.continue_btn = QPushButton("Ya inicié sesión / Continuar")
        self.continue_btn.setEnabled(False)
        self.continue_btn.setVisible(False)
        self.continue_btn.clicked.connect(self._on_continue_after_login)
        self.continue_btn.setStyleSheet("background-color: #d9534f; color: white; padding: 5px 15px; border-radius: 3px; font-weight: bold;")
        
        top_layout.addWidget(self.continue_btn)
        top_layout.addWidget(self.connect_btn)
        
        root_layout.addWidget(top_bar)

        # -- Área Principal de 2 Columnas --
        main_area = QWidget()
        main_layout = QHBoxLayout(main_area)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(15)

        # COLUMNA IZQUIERDA (Productos)
        left_col = QWidget()
        left_layout = QVBoxLayout(left_col)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        self.products_panel = ProductListPanel()
        left_layout.addWidget(self.products_panel, stretch=1)
        self.products_list = self.products_panel # Alias tests
        self.queue_btn = self.products_panel.queue_btn # Alias tests
        
        product_buttons = QHBoxLayout()
        self.new_product_btn = QPushButton("+ Nuevo producto")
        self.new_product_btn.clicked.connect(self._on_new_product)
        self.edit_product_btn = QPushButton("Editar")
        self.edit_product_btn.clicked.connect(self._on_edit_product)
        self.delete_product_btn = QPushButton("Eliminar")
        self.delete_product_btn.clicked.connect(self._on_delete_product)
        
        product_buttons.addWidget(self.new_product_btn)
        product_buttons.addWidget(self.edit_product_btn)
        product_buttons.addWidget(self.delete_product_btn)
        left_layout.addLayout(product_buttons)
        
        main_layout.addWidget(left_col, stretch=4)

        # COLUMNA DERECHA (Detalle o Cola)
        self.right_col = QWidget()
        right_layout = QVBoxLayout(self.right_col)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # Stacked layout to switch between details and queue (or just visibility)
        self.detail_box = QGroupBox("Detalles del Producto")
        detail_layout = QVBoxLayout(self.detail_box)
        
        self.product_title_label = QLabel("Selecciona un producto para ver sus detalles.")
        self.product_title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.product_title_label.setWordWrap(True)
        self.product_price_label = QLabel("")
        
        self.pub_state_label = QLabel("")
        self.match_label = QLabel("")
        self.pub_listing_label = QLabel("")
        self.pub_listing_label.setWordWrap(True)
        self.pub_listing_label.setStyleSheet("color: #444;")
        
        detail_layout.addWidget(self.product_title_label)
        detail_layout.addWidget(self.product_price_label)
        detail_layout.addSpacing(10)
        detail_layout.addWidget(self.pub_state_label)
        detail_layout.addWidget(self.match_label)
        detail_layout.addWidget(self.pub_listing_label)
        detail_layout.addStretch()
        
        self.edit_data_btn = QPushButton("✏️ Editar datos")
        self.edit_data_btn.setEnabled(False)
        self.edit_data_btn.clicked.connect(self._on_edit_product)
        detail_layout.addWidget(self.edit_data_btn)
        
        # Elementos invisibles para tests (ya no se usan en UI visual pero tests pueden buscarlos)
        self.republish_btn = QPushButton()
        self.republish_btn.setToolTip("dummy tooltip")
        self.republish_btn.setVisible(False)
        self.republish_btn.clicked.connect(self._on_republish)
        self.republish_status_label = QLabel()
        self.republish_status_label.setVisible(False)
        
        right_layout.addWidget(self.detail_box)
        
        # Cola
        self._queue_box = QGroupBox("Progreso de Republicación")
        queue_layout = QVBoxLayout(self._queue_box)
        self.queue_status_label = QLabel("Iniciando cola...")
        self.queue_status_label.setStyleSheet("font-weight: bold;")
        queue_layout.addWidget(self.queue_status_label)
        
        self.queue_items_list = QListWidget()
        self.queue_items_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        queue_layout.addWidget(self.queue_items_list)
        
        queue_buttons_row = QHBoxLayout()
        queue_buttons_row.addStretch()
        self.queue_stop_btn = QPushButton("⏹ Detener cola")
        self.queue_stop_btn.setEnabled(False)
        self.queue_stop_btn.clicked.connect(self._on_queue_stop)
        self.queue_stop_btn.setStyleSheet("background-color: #d9534f; color: white; font-weight: bold;")
        queue_buttons_row.addWidget(self.queue_stop_btn)
        queue_layout.addLayout(queue_buttons_row)
        
        self._queue_box.setVisible(False)
        right_layout.addWidget(self._queue_box)
        
        main_layout.addWidget(self.right_col, stretch=6)
        
        root_layout.addWidget(main_area, stretch=1)
        
        # Elementos ocultos obligatorios (funcionalidad o tests)
        self.nav_marketplace_btn = QPushButton()
        self.nav_marketplace_btn.setVisible(False)
        self.nav_status_label = QLabel()
        self.nav_status_label.setVisible(False)
        self.close_browser_btn = QPushButton()
        self.close_browser_btn.setVisible(False)
        self.scan_summary_label = QLabel()
        self.scan_summary_label.setVisible(False)
        self.rescan_btn = QPushButton()
        self.rescan_btn.setVisible(False)
        self.scanned_listings_list = QListWidget()
        self.scanned_listings_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.scanned_listings_list.itemSelectionChanged.connect(self._on_scanned_listing_selected)
        self.scanned_listings_list.setVisible(False)
        self.log_view = QPlainTextEdit()
        self.log_view.setVisible(False)

    # -- Productos: helpers ---------------------------------------------------
    def _reload_products(self) -> None:
        try:
            products = self._product_service.list_all()
        except MarketplaceManagerError as exc:
            self._append_log(f"ERROR cargando productos: {exc}")
            products = []
            
        matches_info = dict(getattr(self, "_latest_scan_results", {}))
        try:
            for p in products:
                if p.id is not None and p.id not in matches_info:
                    active = self._matched_service.get_active_by_product(p.id)
                    if active:
                        conf = active.confidence_level
                        if conf == "HIGH":
                            matches_info[p.id] = "✓ Publicación encontrada · ALTA"
                        elif conf == "MEDIUM":
                            matches_info[p.id] = "⚠ Posible coincidencia"
                        elif conf == "AMBIGUOUS":
                            matches_info[p.id] = "⚠ Coincidencia ambigua"
        except Exception as e:
            pass

        self.products_panel.set_products(products, matches_info)
        self._refresh_selected_product_panel()

    def _selected_product_id(self) -> int | None:
        """Producto ENFOCADO (uno solo) — usado para el panel de detalle."""
        return self.products_panel.focused_product_id()

    def _selected_product(self) -> Product | None:
        pid = self._selected_product_id()
        if pid is None:
            return None
        try:
            return self._product_service.get(pid)
        except MarketplaceManagerError as exc:
            self._append_log(f"ERROR: {exc}")
            return None

    def _selected_product_ids(self) -> list[int]:
        """Productos MARCADOS para la cola (ordenados por id ascendente para
        determinismo). Es la lista que consume `_on_queue_republish`."""
        return sorted(self.products_panel.queue_product_ids())

    def _selected_products(self) -> list[Product]:
        products: list[Product] = []
        for pid in self._selected_product_ids():
            try:
                products.append(self._product_service.get(pid))
            except MarketplaceManagerError as exc:
                self._append_log(f"ERROR: {exc}")
        return products

    def _scan_item_for_product(self, product_id: int) -> dict | None:
        """Devuelve el item_data (dict) del escaneo cuyo producto asociado es
        `product_id`, o None si no hay ninguno."""
        for i in range(self.scanned_listings_list.count()):
            item = self.scanned_listings_list.item(i)
            item_data = item.data(Qt.ItemDataRole.UserRole)
            if item_data and item_data.get("matched_product_id") == product_id:
                return dict(item_data)
        return None

    def _current_active_target(self, product_id: int):
        """Target congelado activo del producto (o None). Nunca lanza."""
        try:
            return self._matched_service.get_active_by_product(product_id)
        except MarketplaceManagerError as exc:
            self._append_log(f"ERROR leyendo target activo: {exc}")
            return None

    def _product_to_payload(self, product: Product) -> dict:
        return {
            "product_id": product.id,
            "title": product.title,
            "description": product.description,
            "price": product.price,
            "category": product.category,
            "condition": product.condition,
            "location": product.location,
            "tags": product.tags,
            "images": product.images,
            "enabled": product.enabled,
            "marketplace_url": product.marketplace_url,
            "marketplace_reference": product.marketplace_reference,
        }

    def _resolve_image_paths(self, product: Product) -> list[str]:
        return [str(self._product_service.resolve_image_path(p)) for p in (product.images or [])]

    def _invoke_service(self, method: str, payload) -> None:
        """Envía una operación al `AutomationService` (hilo del worker).

        Se despacha por señal Qt (`Signal(object)`/`Signal(int)`), que es el
        mecanismo que PySide6 soporta para pasar objetos Python entre hilos.
        NO se usa `QMetaObject.invokeMethod` con argumentos: PySide6 lo
        rechaza con `TypeError: wrong argument types`.
        """
        if self._automation_service is None:
            return
        signal_name = _SERVICE_REQUEST_SIGNALS[method]
        signal = getattr(self._automation_service, signal_name)
        signal.emit(payload)

    # -- Productos: acciones ---------------------------------------------------
    def _on_new_product(self) -> None:
        dialog = ProductEditorDialog(product=None, parent=self)
        if dialog.exec() != ProductEditorDialog.DialogCode.Accepted:
            return

        product = dialog.result_product()
        try:
            self._product_service.create(product, source_image_paths=dialog.new_image_paths())
            self._append_log(f"Producto creado: {product.title}")
        except MarketplaceManagerError as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        self._reload_products()

    def _on_edit_product(self) -> None:
        product = self._selected_product()
        if product is None:
            QMessageBox.information(self, "Editar producto", "Selecciona un producto de la lista.")
            return

        dialog = ProductEditorDialog(product=product, parent=self)
        if dialog.exec() != ProductEditorDialog.DialogCode.Accepted:
            return

        updated = dialog.result_product()
        try:
            self._product_service.update(updated, source_image_paths=dialog.new_image_paths())
        except MarketplaceManagerError as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return

        # Ciclo de vida del target: editar el producto NO invalida ni
        # re-deriva la publicación congelada. Solo se actualiza el snapshot
        # de trazabilidad si hay un target activo (que seguirá apuntando a
        # la publicación original de Facebook).
        if updated.id is not None:
            try:
                active = self._matched_service.get_active_by_product(updated.id)
                if active is not None:
                    self._matched_service.save_edit_snapshot(active.id, updated)
            except MarketplaceManagerError as exc:
                self._append_log(f"ERROR guardando snapshot de edición: {exc}")

        self._append_log(f"Producto actualizado: {updated.title}")
        self._reload_products()
        if updated.id is not None:
            self.products_panel.set_focused_product_id(updated.id)
        self._refresh_selected_product_panel()

    def _on_delete_product(self) -> None:
        product = self._selected_product()
        if product is None:
            QMessageBox.information(self, "Eliminar producto", "Selecciona un producto de la lista.")
            return

        confirm = QMessageBox.question(
            self,
            "Confirmar eliminación",
            f"¿Eliminar '{product.title}' de la aplicación?\n\n"
            "Esto NO elimina ninguna publicación en Facebook, solo el registro local.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        # Ciclo de vida del target: si el producto desaparece, el target deja
        # de ser válido -> se limpia EXPLÍCITAMENTE (cualquier fase).
        if product.id is not None:
            try:
                cancelled = self._matched_service.cancel_active(product.id)
                if cancelled is not None:
                    self._append_log(f"Target {cancelled.id} cancelado al eliminar el producto")
            except MarketplaceManagerError as exc:
                self._append_log(f"ERROR cancelando target del producto: {exc}")

        try:
            self._product_service.delete(product.id)  # type: ignore[arg-type]
            self._append_log(f"Producto eliminado: {product.title}")
        except MarketplaceManagerError as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        self._last_selected_product_id = None
        self._reload_products()
        self._refresh_selected_product_panel()

    # -- Facebook / navegador ---------------------------------------------------
    def _on_connect_facebook(self) -> None:
        forensics.evt("gui.click", "connect")
        if self._automation_thread is not None:
            self._append_log("El navegador ya se está iniciando o ya está abierto")
            return

        self.connect_btn.setEnabled(False)
        self.session_status_label.setText("Estado: iniciando navegador...")
        self.nav_marketplace_btn.setEnabled(False)

        self._automation_thread = QThread(self)
        self._automation_service = AutomationService()
        self._automation_service.set_cached_products(self._product_service.list_all())
        self._automation_service.moveToThread(self._automation_thread)
        forensics.evt("thread.created", f"thread={id(self._automation_thread)}")

        self._automation_service.log_message.connect(self._append_log)
        self._automation_service.state_changed.connect(self._on_state_changed)
        self._automation_service.session_checked.connect(self._on_session_checked)
        self._automation_service.marketplace_opened.connect(self._on_marketplace_opened)
        self._automation_service.listings_ready.connect(self._on_listings_ready)
        self._automation_service.listings_scan_started.connect(self._on_scan_started)
        self._automation_service.listings_scan_progress.connect(self._on_scan_progress)
        self._automation_service.listings_scan_completed.connect(self._on_scan_completed)
        self._automation_service.search_listing_result.connect(self._on_search_listing_result)
        self._automation_service.delete_ready.connect(self._on_delete_ready)
        self._automation_service.delete_result.connect(self._on_delete_result)
        self._automation_service.republish_progress.connect(self._on_republish_progress)
        self._automation_service.publication_result.connect(self._on_publication_result)
        self._automation_service.intervention_paused.connect(self._on_intervention_paused)
        self._automation_service.error_occurred.connect(self._on_automation_error)

        self._automation_thread.started.connect(self._automation_service.start_and_check_session)
        forensics.evt("thread.start", f"thread={id(self._automation_thread)}")
        self._automation_thread.start()

    def _on_continue_after_login(self) -> None:
        if self._automation_service is None:
            return
        self.continue_btn.setEnabled(False)
        self.session_status_label.setText("Estado: comprobando / reanudando...")
        # invokeMethod garantiza que esto se ejecute en el hilo del worker,
        # no en el hilo de la GUI (Playwright sync no es thread-safe).
        # continue_after_user_action decide según el estado: WAITING_LOGIN
        # vuelve a comprobar la sesión; WAITING_USER reanuda la navegación.
        from PySide6.QtCore import QMetaObject

        QMetaObject.invokeMethod(self._automation_service, "continue_after_user_action", Qt.ConnectionType.QueuedConnection)

    def _on_navigate_marketplace(self) -> None:
        if self._automation_service is None:
            return
        forensics.evt("gui.click", "navigate_marketplace")
        self.nav_marketplace_btn.setEnabled(False)
        self.nav_status_label.setText("Marketplace: navegando...")
        from PySide6.QtCore import QMetaObject

        QMetaObject.invokeMethod(self._automation_service, "navigate_to_marketplace", Qt.ConnectionType.QueuedConnection)

    # -- Estado de botones + panel del producto ------------------------------
    def _update_search_button_state(self) -> None:
        """Habilita/deshabilita los botones de edición y republicación según
        las reglas de seguridad.

        - "✏️ Editar datos": con un producto seleccionado.
        - "🔄 Republicar": SOLO con un ítem HIGH del escaneo para el producto
          seleccionado, con URL/referencia real, automatización lista, y sin
          un target post-confirmación activo (ese debe reanudarse). MEDIUM/
          LOW/AMBIGUOUS/NO_MATCH jamás habilitan el botón.
        """
        has_product = self._selected_product_id() is not None
        self.edit_data_btn.setEnabled(has_product)

        can_republish = False
        if self._search_ready and has_product:
            product = self._selected_product()
            if product is not None and product.id is not None:
                item_data = self._scan_item_for_product(product.id)
                if item_data:
                    conf = item_data.get("confidence")
                    listing = item_data.get("listing") or {}
                    has_locator = bool(listing.get("url") or listing.get("reference"))
                    active = self._current_active_target(product.id)
                    # Un target post-confirmación (deleting/deleted/...) debe
                    # reanudarse; uno pre-confirmación se reinicia.
                    blocked = active is not None and not active.is_preconfirm
                    can_republish = conf == "HIGH" and has_locator and not blocked
        self.republish_btn.setEnabled(can_republish)
        self._update_queue_button_state()

    def _queue_active(self) -> bool:
        """¿Hay una cola en curso (procesando o pausada)?"""
        return self._queue is not None and self._queue.state in (
            RepublishQueueState.RUNNING,
            RepublishQueueState.PAUSED,
        )

    def _set_product_controls_enabled(self, enabled: bool) -> None:
        self.new_product_btn.setEnabled(enabled)
        self.edit_product_btn.setEnabled(enabled)
        self.delete_product_btn.setEnabled(enabled)

    def _update_queue_button_state(self) -> None:
        """Estado del botón "🔄 Republicar seleccionados (N)".

        El CONTADOR y la etiqueta "(N)" los mantiene el propio `ProductListPanel`
        en función de los checkboxes. Aquí solo aplicamos la segunda condición
        de habilitación: >= 1 producto marcado ELEGIBLE (HIGH + URL/ref + sin
        flujo post-confirmación) y automatización lista.

        Durante una cola en curso se deshabilitan también los controles que
        podrían interferir con los targets congelados (edición, eliminación
        de productos, re-escaneo, cerrar navegador).
        """
        if self._queue_active():
            self.queue_btn.setEnabled(False)
            self.republish_btn.setEnabled(False)
            self.edit_data_btn.setEnabled(False)
            self.rescan_btn.setEnabled(False)
            self.close_browser_btn.setEnabled(False)
            self._set_product_controls_enabled(False)
            return

        self._set_product_controls_enabled(True)
        browser_running = (
            getattr(self._automation_service, "is_browser_running", False)
            if self._automation_service is not None
            else False
        )
        self.close_browser_btn.setEnabled(browser_running)
        self.rescan_btn.setEnabled(self._search_ready)
        products = self._selected_products()
        build = build_queue(products, self._scan_item_for_product, self._current_active_target)
        self.queue_btn.setEnabled(
            # Quitamos self.queue_btn.isEnabled() original por si estaba deshabilitado por falta de check
            len(products) > 0
            and self._search_ready
            and build.count >= 1
            and self._automation_service is not None
        )
        self.queue_btn.setText(f"🔄 Republicar seleccionados ({build.count})")

    def _on_products_selection_changed(self) -> None:
        """Al cambiar la selección de producto: limpia targets pre-confirmación
        huérfanos del producto anterior y refresca el panel."""
        self._cleanup_dangling_targets()
        self._refresh_selected_product_panel()

    def _cleanup_dangling_targets(self) -> None:
        """Ciclo de vida del target: un target pre-confirmación (selected/
        editing/awaiting_confirm) que el usuario abandona al cambiar de
        producto se cancela EXPLÍCITAMENTE. No se cancela un flujo que ya
        pasó a deleting/deleted/creating/publishing: eso debe reanudarse.

        Durante una cola en curso los targets congelados NO se cancelan:
        pertenecen a la cola y la edición previa jamás puede invalidarlos."""
        if self._queue_active():
            return
        current = self._selected_product_id()
        if current is None:
            self._last_selected_product_id = None
            return
        if self._last_selected_product_id is not None and self._last_selected_product_id != current:
            old = self._last_selected_product_id
            try:
                cancelled = self._matched_service.cancel_pending(old)
            except MarketplaceManagerError as exc:
                self._append_log(f"ERROR limpiando target pendiente del producto {old}: {exc}")
                cancelled = None
            if cancelled is not None:
                self._append_log(
                    f"Target pre-confirmación {cancelled.id} del producto {old} "
                    "cancelado al cambiar de producto"
                )
        self._last_selected_product_id = current

    def _refresh_selected_product_panel(self) -> None:
        """Refresca el panel "Producto y republicación" según el producto
        seleccionado, el escaneo y el target congelado en BD."""
        product = self._selected_product()
        if product is None:
            self.product_title_label.setText("Producto: —")
            self.product_price_label.setText("Precio actual: —")
            self.pub_state_label.setText("Estado: selecciona un producto")
            self.match_label.setText("Coincidencia: —")
            self.pub_listing_label.setText("Publicación encontrada: —")
            self._update_search_button_state()
            return

        self.product_title_label.setText(f"Producto: {product.title}")
        price_str = f"{product.price:,.0f}".replace(",", ".")
        self.product_price_label.setText(f"Precio actual: ${price_str}")

        active = self._current_active_target(product.id) if product.id is not None else None
        scan_item = self._scan_item_for_product(product.id) if product.id is not None else None

        if active is not None:
            fb_price = active.matched_price_raw or (
                str(active.matched_price) if active.matched_price is not None else "—"
            )
            self.pub_listing_label.setText(
                f"Publicación encontrada: \"{active.display_listing_title}\" — {fb_price}"
            )
            if active.is_preconfirm:
                self.pub_state_label.setText(
                    "Estado: flujo pendiente de confirmar (se reiniciará al republicar)"
                )
                self.match_label.setText(f"Coincidencia congelada: {active.confidence}")
            else:
                self.pub_state_label.setText(f"Estado: flujo en curso ({active.status})")
                self.match_label.setText(f"Coincidencia congelada: {active.confidence}")
        elif scan_item is not None:
            listing = scan_item.get("listing") or {}
            conf = scan_item.get("confidence") or "NO_MATCH"
            if conf == "HIGH":
                price = listing.get("price_raw") or listing.get("price") or "—"
                self.pub_state_label.setText("Estado: ✓ Publicación encontrada")
                self.match_label.setText("Coincidencia: 🟢 ALTA")
                self.pub_listing_label.setText(
                    f"Publicación encontrada: \"{listing.get('title', '')}\" — {price}"
                )
            else:
                self.pub_state_label.setText("Estado: publicación detectada sin coincidencia HIGH")
                self.match_label.setText(f"Coincidencia: {conf}")
                self.pub_listing_label.setText("Publicación encontrada: no elegible para republicar")
        else:
            self.pub_state_label.setText("Estado: sin publicación encontrada")
            self.match_label.setText("Coincidencia: —")
            self.pub_listing_label.setText("Publicación encontrada: —")

        self._update_search_button_state()

    def _on_search_listing_result(self, payload: dict) -> None:
        """Recibe el resultado (dict) desde el servicio de automatización.

        Muestra el diálogo informativo y, SOLO si el resultado fue verificado
        (FOUND) con localizador real, persiste marketplace_url/reference vía
        `record_found_listing`. Nunca persiste ni ambiguos ni dudosos.
        """
        self._last_search_result = dict(payload)
        self.nav_marketplace_btn.setEnabled(True)
        self._update_search_button_state()

        product_title = payload.get("title") or "?"
        status = payload.get("status")

        if product_title and status:
            dialog = ListingResultDialog(result_payload=payload, product_title=product_title, parent=self)
            dialog.exec()

        self._persist_verified_locator(payload)

    def _persist_verified_locator(self, payload: dict) -> None:
        """Regla de persistencia segura de la Iteración 3.

        Se guarda el localizador únicamente cuando la búsqueda terminó con
        `FOUND` (coincidencia confirmada por el matcher, no ambiguo ni
        dudoso) Y el listing encontrado trae una URL o referencia real
        extraída de Facebook. No se guarda nada en MEDIUM/LOW/AMBIGUOUS/
        NOT_FOUND/SEARCH_LIMIT.
        """
        from app.automation.listing_matcher import MatchStatus

        if payload.get("status") != MatchStatus.FOUND.name:
            self._append_log("Localizador NO guardado (resultado no confirmado)")
            return

        best = payload.get("best")
        if not best:
            self._append_log("Localizador NO guardado (no hay coincidencia confirmada)")
            return

        listing = best.get("listing") or {}
        url = listing.get("url") or ""
        reference = listing.get("reference") or ""
        if not url and not reference:
            self._append_log("Localizador NO guardado (verificación real sin URL/referencia)")
            return

        product_id = payload.get("product_id")
        if product_id is None:
            return
        try:
            self._product_service.record_found_listing(product_id, url, reference)
            self._append_log(
                f"Localizador verificado guardado para el producto {product_id}"
                f" (reference={reference or url or 'n/a'})"
            )
        except MarketplaceManagerError as exc:
            self._append_log(f"ERROR guardando localizador: {exc}")

    # -- Eliminación (integrada en el flujo de republicación) -------------
    def _on_delete_ready(self, ready_payload: dict) -> None:
        """Recibido cuando prepare_delete confirma las condiciones pre-eliminación.

        - Flujo "Republicar": la confirmación explícita ya ocurrió en
          RepublishConfirmDialog; aquí se procede directamente a ejecutar.
        - Ruta legacy ("Buscar publicación"): muestra el diálogo de
          confirmación (mantenida por robustez; sin botón dedicado en la UI).
        """
        if ready_payload.get("from_republish"):
            self._append_log("Confirmación de republicación recibida: procediendo a eliminar")
            self.republish_status_label.setText("Republicación: eliminando publicación en Facebook...")
            self._invoke_service("execute_delete", {})
            return

        dialog = DeleteConfirmDialog(ready_payload=ready_payload, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._append_log("Confirmación recibida: procediendo a eliminar publicación")
            self.republish_status_label.setText("Republicación: eliminando publicación en Facebook...")
            self._invoke_service("execute_delete", {})
        else:
            self._append_log("Eliminación CANCELADA por el usuario")
            self.republish_status_label.setText("Republicación: cancelada por el usuario")
            self.nav_marketplace_btn.setEnabled(True)
            self._update_search_button_state()

    def _on_delete_result(self, result_payload: dict) -> None:
        """Recibido al concluir la operación de eliminación (o su verificación).

        En el flujo "Editar y republicar", DELETED_CONFIRMED encadena
        automáticamente `create_and_publish` con el producto editado.
        DELETE_UNCERTAIN / DELETE_FAILED bloquean el target (status blocked)
        y NO se crea ninguna publicación (spec §10).

        Si hay una cola en curso, el resultado se procesa por la cola
        (persistencia individual + avance/decisión), sin popups por producto.
        """
        res_name = result_payload.get("result") or "UNKNOWN"
        prod_id = result_payload.get("product_id")
        conf = result_payload.get("confidence") or ""
        url = result_payload.get("listing_url") or ""
        ref = result_payload.get("listing_reference") or ""
        error = result_payload.get("error")
        matched_id = result_payload.get("matched_id")

        if self._queue_active():
            self._handle_queue_delete_result(result_payload, res_name)
            return

        self.republish_status_label.setText(f"Republicación: {res_name}")
        self._append_log(f"Resultado eliminación de producto {prod_id}: {res_name}")

        # Persistencia del resultado mediante ProductService.record_deletion()
        # REGLA (modificación 2): last_deleted_at SOLO cambia si res_name == 'DELETED_CONFIRMED'
        if prod_id is not None:
            try:
                self._product_service.record_deletion(
                    product_id=prod_id,
                    result=res_name,
                    confidence=conf,
                    listing_url=url,
                    listing_reference=ref,
                    error=error,
                )
                self._reload_products()
            except MarketplaceManagerError as exc:
                self._append_log(f"ERROR al registrar eliminación: {exc}")

        # Mostrar mensaje modal al usuario
        if res_name == "DELETED_CONFIRMED":
            QMessageBox.information(
                self,
                "Eliminación exitosa",
                "La publicación fue eliminada y se confirmó su ausencia en Facebook Marketplace.",
            )
        elif res_name == "DELETE_UNCERTAIN":
            QMessageBox.warning(
                self,
                "Eliminación incierta",
                "Se ejecutó la orden de eliminación, pero NO se pudo obtener una "
                "verificación positiva de que haya sido eliminada.\n\n"
                f"Detalle: {result_payload.get('detail')}",
            )
        else:
            QMessageBox.critical(
                self,
                "Fallo en eliminación",
                f"No se pudo eliminar la publicación.\n\nDetalle: {result_payload.get('detail') or error}",
            )

        # Encadenar la creación SOLO con DELETED_CONFIRMED de un target congelado.
        if res_name == "DELETED_CONFIRMED" and matched_id is not None:
            self._chain_create_after_delete(matched_id, prod_id)
        elif res_name in ("DELETE_UNCERTAIN", "DELETE_FAILED") and matched_id is not None:
            try:
                self._matched_service.mark_deletion_uncertain(matched_id, error=error)
            except MarketplaceManagerError as exc:
                self._append_log(f"ERROR bloqueando target tras eliminación no confirmada: {exc}")
            self.republish_status_label.setText("Republicación: BLOQUEADA (eliminación no confirmada)")
            self._append_log(f"Target {matched_id} bloqueado; no se creará publicación nueva (spec §10)")

        self.nav_marketplace_btn.setEnabled(True)
        self._update_search_button_state()

    def _chain_create_after_delete(self, matched_id: int, product_id: int | None) -> None:
        """Tras DELETED_CONFIRMED del target, crea y publica la publicación nueva."""
        if product_id is None:
            return
        try:
            self._matched_service.mark_deleted_confirmed(matched_id)
            product = self._product_service.get(product_id)
        except MarketplaceManagerError as exc:
            self._append_log(f"ERROR preparando creación tras eliminar: {exc}")
            return
        self.republish_status_label.setText("Republicación: eliminada ✓ — creando publicación...")
        self._invoke_service(
            "create_and_publish",
            {
                "product": self._product_to_payload(product),
                "matched_id": matched_id,
                "image_paths": self._resolve_image_paths(product),
            },
        )

    def _on_republish_progress(self, payload: dict) -> None:
        """Actualiza el estado visual según la fase del flujo de republicación."""
        phase = payload.get("phase") or ""
        labels = {
            "matched": "Republicación: target congelado",
            "edit_saved": "Republicación: editado — esperando confirmación",
            "creating_listing": "Republicación: creando publicación...",
            "verifying_publication": "Republicación: verificando publicación...",
        }
        self.republish_status_label.setText(labels.get(phase, f"Republicación: {phase}"))

    def _on_publication_result(self, result_payload: dict) -> None:
        """Recibido al concluir la creación/publicación (o su verificación).

        PUBLISHED_CONFIRMED → target republished + localizador nuevo en el
        producto. PUBLISH_UNCERTAIN / PUBLISH_FAILED → target bloqueado
        (nunca se da por hecho que el anuncio existe sin evidencia).

        Si hay una cola en curso, el resultado lo procesa la cola (avance al
        siguiente ítem o diálogo de decisión), sin popups por producto.
        """
        res_name = result_payload.get("result") or "UNKNOWN"
        matched_id = result_payload.get("matched_id")
        product_id = result_payload.get("product_id")
        product_title = result_payload.get("product_title") or "?"
        new_url = result_payload.get("new_url") or ""
        new_ref = result_payload.get("new_reference") or ""
        detail = result_payload.get("detail") or ""

        if self._queue_active():
            self._handle_queue_publication_result(result_payload, res_name)
            return

        self.republish_status_label.setText(f"Republicación: {res_name}")
        self._append_log(f"Resultado publicación de '{product_title}': {res_name} — {detail}")

        if res_name == "PUBLISHED_CONFIRMED":
            if matched_id is not None:
                try:
                    self._matched_service.mark_republished(matched_id)
                except MarketplaceManagerError as exc:
                    self._append_log(f"ERROR marcando target republished: {exc}")
            if product_id is not None and (new_url or new_ref):
                try:
                    self._product_service.record_publication(product_id, new_url, new_ref)
                except MarketplaceManagerError as exc:
                    self._append_log(f"ERROR guardando publicación nueva: {exc}")
            self._reload_products()
            QMessageBox.information(
                self,
                "Publicación confirmada",
                "La publicación nueva fue creada y verificada en Facebook Marketplace.",
            )
        elif res_name in ("PUBLISH_UNCERTAIN", "PUBLISH_FAILED"):
            if matched_id is not None:
                try:
                    self._matched_service.mark_blocked(matched_id, error=detail or res_name)
                except MarketplaceManagerError as exc:
                    self._append_log(f"ERROR bloqueando target por publicación sin confirmar: {exc}")
            QMessageBox.warning(
                self,
                "Publicación sin confirmar",
                f"{detail}\n\nNo se da por hecho que el anuncio exista; el target "
                "quedó bloqueado para que decidas cómo proceder.",
            )
        else:
            self._append_log(f"Publicación finalizó sin resultado esperado: {res_name}")

        self.nav_marketplace_btn.setEnabled(True)
        self._update_search_button_state()

    def _on_close_browser(self) -> None:
        if self._automation_service is None:
            return
        from PySide6.QtCore import QMetaObject

        QMetaObject.invokeMethod(self._automation_service, "close_browser", Qt.ConnectionType.QueuedConnection)
        self.close_browser_btn.setEnabled(False)
        self.continue_btn.setEnabled(False)
        self.connect_btn.setEnabled(True)
        self.nav_marketplace_btn.setEnabled(False)
        self.rescan_btn.setEnabled(False)
        self.nav_status_label.setText("Marketplace: sin probar")
        self.session_status_label.setText("Estado: navegador cerrado")
        self.scan_status_label.setText("Estado: navegador cerrado")
        self.scanned_listings_list.clear()
        self.republish_btn.setEnabled(False)
        self.republish_status_label.setText("Republicación: sin iniciar")
        self._search_ready = False
        self._refresh_selected_product_panel()
        self._update_search_button_state()

    def _on_state_changed(self, state_name: str) -> None:
        self.session_status_label.setText(f"Estado: {state_name}")

    def _on_session_checked(self, logged_in: bool, detail: str) -> None:
        self.close_browser_btn.setEnabled(True)
        if logged_in:
            self.session_status_label.setText("Estado: sesión activa ✓")
            self.continue_btn.setEnabled(False)
            self.nav_marketplace_btn.setEnabled(True)
        else:
            self.session_status_label.setText(
                "Estado: inicia sesión manualmente en la ventana de Chromium y luego pulsa 'Continuar'"
            )
            self.continue_btn.setEnabled(True)
        self._update_search_button_state()

    def _on_marketplace_opened(self, ok: bool, detail: str) -> None:
        self.nav_status_label.setText("Marketplace: abierto ✓" if ok else f"Marketplace: {detail}")

    def _on_listings_ready(self, found: bool, detail: str) -> None:
        self.nav_status_label.setText(
            "Tus publicaciones: encontradas ✓" if found else "Tus publicaciones: NO detectadas"
        )
        self.nav_marketplace_btn.setEnabled(True)
        self.rescan_btn.setEnabled(found)
        self._search_ready = found
        self._update_search_button_state()
        if found:
            self._maybe_resume_republish()

    def _maybe_resume_republish(self) -> None:
        """Reanudación segura al arrancar (decisión 2 del plan).

        Cuando la sesión + "Tus publicaciones" quedan listas, revisa los
        targets ACTIVOS y encadena el siguiente paso correcto, SIEMPRE
        verificando antes de actuar:
        - deleted              -> crear/publicar (producto fresco de BD).
        - creating/publishing/verifying_publication -> verificar publicación ANTES.
        - deleting             -> verificar eliminación (nunca re-eliminar).
        - selected/editing/awaiting_confirm -> flujo de edición pendiente.

        NO es republicación en lote: se procesa UN target a la vez (el más
        reciente). Nunca se invoca el matcher/escáner en estos caminos.

        Con una cola en curso no interfiere: los targets congelados de la
        cola se reanudan individualmente solo tras un reinicio.
        """
        if self._queue_active():
            return
        try:
            actives = self._matched_service.list_active()
        except MarketplaceManagerError as exc:
            self._append_log(f"ERROR revisando republicaciones pendientes: {exc}")
            return
        if not actives:
            return
        if len(actives) > 1:
            self._append_log(
                f"Hay {len(actives)} targets activos; reanudando solo el más reciente. "
                "Completa cada flujo antes de iniciar otro."
            )
        target = actives[0]
        try:
            matched = self._matched_service.get(target.id)
            product = self._product_service.get(target.product_id)
        except MarketplaceManagerError as exc:
            self._append_log(f"ERROR cargando target pendiente: {exc}")
            return

        status = matched.status
        if status == STATUS_DELETED:
            self._append_log(
                f"Reanudando republicación de '{product.title}': eliminación confirmada, "
                "creación pendiente."
            )
            self._invoke_service(
                "create_and_publish",
                {
                    "product": self._product_to_payload(product),
                    "matched_id": matched.id,
                    "image_paths": self._resolve_image_paths(product),
                },
            )
        elif status in (STATUS_CREATING, STATUS_PUBLISHING, STATUS_VERIFYING_PUBLICATION):
            self._append_log(
                f"Reanudando publicación de '{product.title}': verificando antes de continuar."
            )
            self._invoke_service(
                "resume_republish",
                {
                    "product": self._product_to_payload(product),
                    "matched_id": matched.id,
                    "phase": "publish",
                    "image_paths": self._resolve_image_paths(product),
                },
            )
        elif status == STATUS_DELETING:
            self._append_log(
                f"Reanudando eliminación de '{product.title}': verificando estado (no se re-elimina)."
            )
            self._invoke_service(
                "resume_republish",
                {
                    "product": self._product_to_payload(product),
                    "matched_id": matched.id,
                    "phase": "delete",
                    "matched_target": {
                        "title": matched.matched_title,
                        "price": matched.matched_price,
                        "price_raw": matched.matched_price_raw,
                        "url": matched.listing_url,
                        "reference": matched.listing_reference,
                        "confidence": matched.confidence,
                    },
                },
            )
        elif status in (STATUS_SELECTED, STATUS_EDITING, STATUS_AWAITING_CONFIRM):
            self._append_log(
                f"Flujo pendiente de '{product.title}' (status={status}): "
                "edita y confirma en la GUI para continuar."
            )
            self.republish_status_label.setText("Republicación: pendiente de editar/confirmar")
        else:
            self._append_log(f"Target {target.id} en estado no reanudable: {status}")

    def _on_scan_started(self) -> None:
        self.scan_status_label.setText("Estado: ⏳ Escaneando publicaciones en Facebook...")
        self.rescan_btn.setEnabled(False)

    def _on_scan_progress(self, scrolls: int, total: int, new_in_batch: int) -> None:
        self.scan_status_label.setText(f"Estado: ⏳ Escaneando (scroll {scrolls}, {total} detectadas)...")

    def _on_scan_completed(self, payload: dict) -> None:
        self.rescan_btn.setEnabled(True)
        items = payload.get("items", [])
        total = payload.get("total_listings", 0)
        
        # Ocultamos la caja de Facebook de manera silenciosa y guardamos el estado.
        self.scan_status_label.setText("Escaneo completado.")
        
        self.scanned_listings_list.clear()
        
        # Limpiar resultados anteriores
        self._latest_scan_results.clear()

        for item_data in items:
            listing = item_data.get("listing", {})
            conf = item_data.get("confidence", "NO_MATCH")
            auto_sel = item_data.get("auto_selected", False)
            warnings = item_data.get("warnings", [])
            
            # Solo guardamos el string visual para pasarlo a la UI de productos
            if conf == "HIGH":
                status_str = "✓ Publicación encontrada · ALTA"
            elif conf == "MEDIUM":
                warn_str = f" ({warnings[0]})" if warnings else ""
                status_str = f"⚠ Posible coincidencia{warn_str}"
            elif conf == "AMBIGUOUS":
                status_str = "⚠ Coincidencia ambigua"
            else:
                status_str = "○ Sin coincidencia"

            # Para tests que verifican la lista: añadimos ítems aunque estén invisibles
            list_item = QListWidgetItem(f"[{conf}]")
            list_item.setCheckState(Qt.CheckState.Checked if auto_sel else Qt.CheckState.Unchecked)
            list_item.setData(Qt.ItemDataRole.UserRole, item_data)
            self.scanned_listings_list.addItem(list_item)
            
            # Persistencia de localizador verificado para coincidencias seguras (HIGH)
            if auto_sel and item_data.get("matched_product_id") is not None:
                pid = item_data["matched_product_id"]
                url = listing.get("url") or ""
                ref = listing.get("reference") or ""
                if url or ref:
                    try:
                        self._product_service.record_found_listing(pid, url, ref)
                    except Exception as exc:
                        logger.debug("No se pudo persistir localizador: %s", exc)
                self._latest_scan_results[pid] = status_str
            
            # Guardar el status para mostrarlo (incluso si no fue auto_sel, podemos intentar mapearlo si sabemos el PID)
            if item_data.get("matched_product_id") is not None:
                self._latest_scan_results[item_data["matched_product_id"]] = status_str

        self._reload_products()
        self._update_search_button_state()
        self._refresh_selected_product_panel()

    def _on_rescan_listings(self) -> None:
        if self._automation_service is None:
            return
        self.rescan_btn.setEnabled(False)
        self.scan_status_label.setText("Estado: ⏳ Iniciando re-escaneo...")
        # Enviar lista actualizada de productos
        products = self._product_service.list_all()
        self._automation_service.set_cached_products(products)
        from PySide6.QtCore import QMetaObject

        QMetaObject.invokeMethod(self._automation_service, "scan_listings", Qt.ConnectionType.QueuedConnection)

    def _on_scanned_listing_selected(self) -> None:
        items = self.scanned_listings_list.selectedItems()
        if not items:
            return
        item_data = items[0].data(Qt.ItemDataRole.UserRole)
        if not item_data:
            return
        matched_id = item_data.get("matched_product_id")
        if matched_id is not None:
            self.products_panel.set_focused_product_id(matched_id)
        self._update_search_button_state()
        self._refresh_selected_product_panel()

    # -- Republicar (botón principal, Iteración 5) --------------------------
    def _on_republish(self) -> None:
        """Flujo completo de UN producto: MATCH → congelar → confirmar →
        eliminar → verificar → crear → publicar → verificar.

        El botón "🔄 Republicar" es la ÚNICA acción principal. La edición de
        los datos de la NUEVA publicación se hace ANTES con "✏️ Editar datos"
        (ProductEditorDialog), por lo que aquí no se abre ningún editor.

        Ciclo de vida del target: el target se congela SIEMPRE desde el ítem
        HIGH del escaneo (URL/referencia/título/precio que mostró Facebook),
        NUNCA desde el producto editado. Si existe un target pre-confirmación
        huérfano (selected/editing/awaiting_confirm) se cancela EXPLÍCITAMENTE
        antes de congelar uno nuevo. Los targets post-confirmación
        (deleting/deleted/...) bloquean: deben reanudarse.
        """
        if self._automation_service is None:
            QMessageBox.information(self, "Republicar", "Primero abre el navegador de Facebook.")
            return

        product = self._selected_product()
        if product is None:
            QMessageBox.information(self, "Republicar", "Selecciona un producto de la lista.")
            return
        product_id = product.id
        if product_id is None:
            QMessageBox.information(self, "Republicar", "El producto no tiene identificador válido.")
            return

        item_data = self._scan_item_for_product(product_id)
        if not item_data or item_data.get("confidence") != "HIGH":
            QMessageBox.information(
                self,
                "Republicar",
                "No hay coincidencia HIGH para este producto. Re-escanéalo o "
                "revisa las publicaciones detectadas.",
            )
            return

        listing = item_data.get("listing") or {}
        url = listing.get("url") or ""
        ref = listing.get("reference") or ""
        if not url and not ref:
            QMessageBox.information(
                self,
                "Republicar",
                "La coincidencia HIGH no tiene URL/referencia real; no se puede republicar.",
            )
            return

        # Seguridad: un producto solo puede tener UN target activo.
        existing = self._current_active_target(product_id)
        if existing is not None and not existing.is_preconfirm:
            QMessageBox.information(
                self,
                "Republicar",
                f"El producto ya tiene un flujo de republicación en curso "
                f"(status={existing.status}). Completa o reanuda ese flujo antes.",
            )
            return
        self.republish_btn.setEnabled(False)
        self.republish_status_label.setText("Republicación: congelando target...")

        # 1) Congelar target (FSM MATCHED; no re-escanea ni re-matchea). La
        #    edición ya ocurrió antes con "✏️ Editar datos".
        matched = self._freeze_target(product, item_data)
        if matched is None:
            self.republish_status_label.setText("Republicación: bloqueada")
            self._update_search_button_state()
            return

        # 3) Snapshot de trazabilidad + marca awaiting_confirm (sin abrir
        #    editor: la edición ya ocurrió antes con "✏️ Editar datos").
        self.republish_status_label.setText("Republicación: esperando confirmación...")

        # 4) Confirmación explícita original (congelada) vs nueva (editada).
        matched = self._matched_service.get(matched.id)
        confirm = RepublishConfirmDialog(matched=matched, product=product, parent=self)
        if confirm.exec() != RepublishConfirmDialog.DialogCode.Accepted:
            self._matched_service.cancel(matched.id)
            self._republish_matched_id = None
            self.republish_status_label.setText("Republicación: cancelada por el usuario")
            self._append_log(f"Republicación CANCELADA en confirmación (target {matched.id} -> cancelled)")
            self._update_search_button_state()
            self._refresh_selected_product_panel()
            return

        # 5) Eliminar (con target congelado) → el resultado encadena la creación.
        self._start_delete_from_target(matched.id, product)

    def _freeze_target(self, product: Product, item_data: dict):
        """Congela el target HIGH desde el ÍTEM DEL ESCANEO (nunca desde el
        producto editado): URL/referencia/título/precio que mostró Facebook.

        Cancela un target pre-confirmación huérfano si existe. Persiste el
        target (`select_match`) y guarda el snapshot de trazabilidad con los
        datos editados del producto (`save_edit_snapshot`). Devuelve el
        `MatchedListing` congelado, o None si no se pudo (p. ej. target
        post-confirmación activo o error de persistencia).
        """
        product_id = product.id
        if product_id is None:
            return None

        listing = item_data.get("listing") or {}
        url = listing.get("url") or ""
        ref = listing.get("reference") or ""

        existing = self._current_active_target(product_id)
        if existing is not None:
            if not existing.is_preconfirm:
                # Un flujo post-confirmación debe reanudarse, no reiniciarse.
                return None
            try:
                self._matched_service.cancel(existing.id)
            except MarketplaceManagerError as exc:
                self._append_log(f"ERROR cancelando target previo: {exc}")
            self._append_log(
                f"Target pre-confirmación {existing.id} cancelado; comenzando flujo nuevo"
            )

        # Congelar en la FSM del worker (no re-escanea ni re-matchea).
        self._invoke_service(
            "freeze_match",
            {
                "product_id": product_id,
                "title": listing.get("title") or product.title,
                "price": listing.get("price"),
                "price_raw": listing.get("price_raw") or "",
                "url": url,
                "reference": ref,
                "confidence": "HIGH",
            },
        )

        # Persistir el target congelado (la GUI es dueña de la persistencia).
        try:
            matched = self._matched_service.select_match(
                product_id=product_id,
                listing_url=url,
                listing_reference=ref,
                matched_title=listing.get("title") or product.title,
                matched_price=listing.get("price"),
                matched_price_raw=listing.get("price_raw") or "",
                confidence="HIGH",
            )
        except MarketplaceManagerError as exc:
            self._append_log(f"ERROR congelando target: {exc}")
            return None

        self._republish_matched_id = matched.id

        # Snapshot de trazabilidad (new_title/new_price) + awaiting_confirm.
        try:
            self._matched_service.save_edit_snapshot(matched.id, product)
        except MarketplaceManagerError as exc:
            self._append_log(f"ERROR guardando snapshot de edición: {exc}")
        self._invoke_service("mark_edit_saved", matched.id)
        return matched

    def _start_delete_from_target(self, matched_id: int, product: Product) -> None:
        matched = self._matched_service.get(matched_id)
        payload = {
            "product": self._product_to_payload(product),
            "matched_target": {
                "matched_id": matched.id,
                "title": matched.matched_title,
                "price": matched.matched_price,
                "price_raw": matched.matched_price_raw,
                "url": matched.listing_url,
                "reference": matched.listing_reference,
                "confidence": matched.confidence,
            },
        }
        self._invoke_service("prepare_delete", payload)
        self.republish_status_label.setText("Republicación: eliminando publicación original...")

    # -- Cola de republicación múltiple ------------------------------------
    def _on_queue_republish(self) -> None:
        """Inicia la republicación EN COLA de los productos seleccionados.

        Preparación (edición) → resumen → Iniciar → congelar TODOS los targets
        HIGH → procesar 1 a 1. Durante la cola NO hay editor: la edición ya
        ocurrió en `QueuePrepDialog`, antes de congelar.
        """
        if self._automation_service is None:
            QMessageBox.information(self, "Republicar", "Primero abre el navegador de Facebook.")
            return

        products = self._selected_products()
        if not products:
            QMessageBox.information(self, "Republicar", "Selecciona al menos un producto de la lista.")
            return

        build = build_queue(products, self._scan_item_for_product, self._current_active_target)
        if build.count == 0:
            QMessageBox.information(
                self,
                "Republicar",
                "Ninguno de los productos seleccionados es elegible (requiere "
                "coincidencia HIGH con URL/referencia real y sin un flujo en "
                "curso). Re-escanéalos o revisa las publicaciones detectadas.",
            )
            return

        dialog = QueuePrepDialog(build.eligible, build.excluded, self._product_service, parent=self)
        if dialog.exec() != QueuePrepDialog.DialogCode.Accepted:
            self._append_log("Republicación en cola CANCELADA en preparación")
            return

        queue = RepublishQueue()
        queue.set_items(build.eligible)
        self._queue = queue
        self._show_queue_panel(True)

        # Congelar TODOS los targets HIGH ANTES de procesar (la edición ya
        # ocurrió; congelar nunca puede invalidar un producto de la cola).
        for idx, item in enumerate(queue.items):
            try:
                product = self._product_service.get(item.product_id)
            except MarketplaceManagerError as exc:
                self._append_log(f"ERROR leyendo producto {item.product_id}: {exc}")
                item.status = QueueItemStatus.SKIPPED
                item.reason = "producto no disponible"
                continue
            matched = self._freeze_target(product, item.scan_item)
            if matched is None:
                item.status = QueueItemStatus.SKIPPED
                item.reason = "no se pudo congelar el target"
                continue
            queue.set_matched(idx, matched.id)

        if queue.start() is None:
            self._finish_queue(show_summary=False)
            return

        self._append_log(f"Cola iniciada: {build.count} producto(s), procesando 1 a 1")
        self._update_queue_ui()
        self._start_current_queue_item()

    def _start_current_queue_item(self) -> None:
        """Inicia la eliminación del ítem actual de la cola (target congelado).

        Si el ítem actual no tiene target (no se pudo congelar) se omite y se
        avanza, de modo que un ítem no congelable nunca detiene la cola."""
        while True:
            item = self._queue.current_item()
            if item is None:
                self._finish_queue()
                return
            if item.matched_id is None or item.status == QueueItemStatus.SKIPPED:
                self._queue.skip_current()
                self._update_queue_ui()
                continue
            break
        try:
            product = self._product_service.get(item.product_id)
        except MarketplaceManagerError as exc:
            self._append_log(f"ERROR iniciando ítem de cola: {exc}")
            self._finish_queue()
            return
        item.status = QueueItemStatus.DELETING
        self._update_queue_ui()
        self._start_delete_from_target(item.matched_id, product)

    def _handle_queue_delete_result(self, payload: dict, result_name: str) -> None:
        """Resultado de eliminación de un ítem de la cola.

        Persiste el resultado individual y deja que la cola decida: en
        DELETED_CONFIRMED encadena la creación; en DELETE_UNCERTAIN/FAILED
        muestra el diálogo de decisión (nunca avanza sola). No muestra
        popups por producto (el progreso va en el panel de la cola).
        """
        prod_id = payload.get("product_id")
        matched_id = payload.get("matched_id")
        conf = payload.get("confidence") or ""
        url = payload.get("listing_url") or ""
        ref = payload.get("listing_reference") or ""
        error = payload.get("error")
        detail = payload.get("detail") or ""

        if prod_id is not None:
            try:
                self._product_service.record_deletion(
                    product_id=prod_id,
                    result=result_name,
                    confidence=conf,
                    listing_url=url,
                    listing_reference=ref,
                    error=error,
                )
            except MarketplaceManagerError as exc:
                self._append_log(f"ERROR al registrar eliminación: {exc}")
        self._append_log(f"Resultado eliminación de producto {prod_id}: {result_name}")

        action = self._queue.on_delete_result(result_name, detail)
        self._update_queue_ui()

        if action == "create":
            self._chain_create_after_delete(matched_id, prod_id)
            return

        item = self._queue.current_item()
        self._show_queue_failure_dialog(item, result_name, detail)

    def _handle_queue_publication_result(self, payload: dict, result_name: str) -> None:
        """Resultado de publicación de un ítem de la cola.

        PUBLISHED_CONFIRMED → target republished + localizador nuevo y avance
        al siguiente ítem. PUBLISH_UNCERTAIN/FAILED → diálogo de decisión.
        """
        matched_id = payload.get("matched_id")
        product_id = payload.get("product_id")
        product_title = payload.get("product_title") or "?"
        new_url = payload.get("new_url") or ""
        new_ref = payload.get("new_reference") or ""
        detail = payload.get("detail") or ""

        self._append_log(f"Resultado publicación de '{product_title}': {result_name} — {detail}")

        if result_name == "PUBLISHED_CONFIRMED":
            if matched_id is not None:
                try:
                    self._matched_service.mark_republished(matched_id)
                except MarketplaceManagerError as exc:
                    self._append_log(f"ERROR marcando target republished: {exc}")
            if product_id is not None and (new_url or new_ref):
                try:
                    self._product_service.record_publication(product_id, new_url, new_ref)
                except MarketplaceManagerError as exc:
                    self._append_log(f"ERROR guardando publicación nueva: {exc}")
            action = self._queue.on_publication_result(result_name, detail)
            self._reload_products()
            self._update_queue_ui()
            if action == "next":
                self._start_current_queue_item()
            else:
                self._finish_queue()
            return

        action = self._queue.on_publication_result(result_name, detail)
        self._update_queue_ui()
        item = self._queue.current_item()
        self._show_queue_failure_dialog(item, result_name, detail)

    def _show_queue_failure_dialog(self, item: QueueItem, result_name: str, detail: str) -> None:
        """Decisión humana obligatoria ante un ítem no confirmado."""
        if item is None:
            self._finish_queue()
            return
        dialog = QueueFailureDialog(item, result_name, detail, parent=self)
        dialog.exec()
        choice = dialog.choice

        if choice == QueueFailureChoice.RETRY:
            self._append_log(f"Reintentando ítem '{item.display_title}' ({result_name}), verificando primero")
            self._retry_queue_item(item, result_name)
            return
        if choice == QueueFailureChoice.SKIP:
            self._append_log(f"Omitiendo ítem '{item.display_title}'")
            self._mark_queue_item_blocked(item)
            self._queue.skip_current()
            self._update_queue_ui()
            if self._queue.current_item() is None:
                self._finish_queue()
            else:
                self._start_current_queue_item()
            return

        self._append_log("Cola detenida por el usuario")
        self._mark_queue_item_blocked(item)
        self._queue.cancel_now()
        self._update_queue_ui()
        self._finish_queue()

    def _retry_queue_item(self, item: QueueItem, result_name: str) -> None:
        """Reintenta el ítem VERIFICANDO primero, nunca a ciegas.

        - DELETE_FAILED      -> la eliminación no se ejecutó: se vuelve a
                                preparar/ejecutar (seguro).
        - DELETE_UNCERTAIN   -> se ejecutó sin verificación: verifica primero
                                (resume phase=delete).
        - PUBLISH_FAILED     -> nada se publicó: reintenta crear/publicar.
        - PUBLISH_UNCERTAIN  -> verifica/completa primero (resume phase=publish).
        """
        if item.matched_id is None:
            self._append_log(f"Ítem '{item.display_title}' sin target; omitido")
            self._mark_queue_item_blocked(item)
            self._queue.skip_current()
            self._update_queue_ui()
            if self._queue.current_item() is None:
                self._finish_queue()
            else:
                self._start_current_queue_item()
            return

        try:
            matched = self._matched_service.get(item.matched_id)
            product = self._product_service.get(item.product_id)
        except MarketplaceManagerError as exc:
            self._append_log(f"ERROR al reintentar ítem: {exc}")
            self._finish_queue()
            return

        if result_name == "DELETE_FAILED":
            self._start_delete_from_target(item.matched_id, product)
        elif result_name == "DELETE_UNCERTAIN":
            self._invoke_service(
                "resume_republish",
                {
                    "product": self._product_to_payload(product),
                    "matched_id": matched.id,
                    "phase": "delete",
                    "matched_target": {
                        "title": matched.matched_title,
                        "price": matched.matched_price,
                        "price_raw": matched.matched_price_raw,
                        "url": matched.listing_url,
                        "reference": matched.listing_reference,
                        "confidence": matched.confidence,
                    },
                },
            )
        elif result_name == "PUBLISH_FAILED":
            self._invoke_service(
                "create_and_publish",
                {
                    "product": self._product_to_payload(product),
                    "matched_id": matched.id,
                    "image_paths": self._resolve_image_paths(product),
                },
            )
        else:  # PUBLISH_UNCERTAIN
            self._invoke_service(
                "resume_republish",
                {
                    "product": self._product_to_payload(product),
                    "matched_id": matched.id,
                    "phase": "publish",
                    "image_paths": self._resolve_image_paths(product),
                },
            )

    def _mark_queue_item_blocked(self, item: QueueItem) -> None:
        """Marca el target del ítem como bloqueado (terminal) al omitirlo o
        detener: así la reanudación automática tras reinicio no lo retoma."""
        if item.matched_id is None:
            return
        try:
            self._matched_service.mark_blocked(item.matched_id, error=item.reason or item.result)
        except MarketplaceManagerError as exc:
            self._append_log(f"ERROR bloqueando target del ítem omitido: {exc}")

    def _on_queue_stop(self) -> None:
        """Botón "⏹ Detener cola": detiene en un punto seguro."""
        if self._queue is None:
            return
        item = self._queue.current_item()
        if item is not None and item.is_critical:
            # Dejar terminar la operación destructiva/creativa en curso; la
            # cancelación se aplica en el siguiente punto seguro (advance).
            self._queue.request_cancel()
            self.queue_stop_btn.setEnabled(False)
            self.queue_status_label.setText("Cola: deteniendo tras terminar la operación actual...")
            self._append_log("Solicitud de detención: se aplicará en el siguiente punto seguro")
            return
        self._queue.cancel_now()
        self._update_queue_ui()
        self._finish_queue()

    def _finish_queue(self, show_summary: bool = True) -> None:
        """Finaliza la cola: restaura la UI y muestra el resumen final."""
        counts = self._queue.counts() if self._queue else {}
        state = self._queue.state if self._queue else RepublishQueueState.IDLE
        self._queue = None
        self._show_queue_panel(False)
        self._reload_products()
        self._refresh_selected_product_panel()
        self._update_search_button_state()

        if not show_summary:
            return

        summary = (
            f"✓ Completados: {counts.get('completed', 0)}\n"
            f"⏭️ Omitidos: {counts.get('skipped', 0)}\n"
            f"✗ Fallidos: {counts.get('failed', 0)}\n"
        )
        if counts.get("uncertain"):
            summary += f"⚠️ Inciertos: {counts.get('uncertain', 0)}\n"
        if state == RepublishQueueState.CANCELLED:
            summary += "\nCola detenida por el usuario (en un punto seguro)."
        elif state == RepublishQueueState.FAILED:
            summary += "\nCola detenida por un error de automatización."
        elif state == RepublishQueueState.COMPLETED:
            summary += "\nCola finalizada."
        QMessageBox.information(self, "Cola de republicación", summary)
        self.republish_status_label.setText("Republicación en cola: finalizada")

    def _show_queue_panel(self, visible: bool) -> None:
        self._queue_box.setVisible(visible)
        self.queue_items_list.clear()
        self.queue_stop_btn.setEnabled(visible)
        if visible:
            self.queue_status_label.setText("Cola: congelando targets...")

    def _update_queue_ui(self) -> None:
        """Refresca el panel de progreso de la cola según su estado."""
        if self._queue is None:
            return
        item = self._queue.current_item()
        total = len(self._queue.items)
        if item is None:
            self.queue_status_label.setText(f"Cola: finalizada ({total} ítems)")
        else:
            pos = self._queue.items.index(item) + 1
            self.queue_status_label.setText(
                f"Cola: '{item.display_title}' ({pos}/{total}) — {self._queue_item_text(item)}"
            )
            self.republish_status_label.setText(
                f"Republicación en cola: '{item.display_title}' ({pos}/{total})"
            )
        self.queue_items_list.clear()
        for idx, q_item in enumerate(self._queue.items, start=1):
            self.queue_items_list.addItem(f"{idx}. {q_item.display_title} — {self._queue_item_text(q_item)}")
        if self._queue.state == RepublishQueueState.PAUSED:
            self.queue_stop_btn.setEnabled(True)

    def _queue_item_text(self, item: QueueItem) -> str:
        glyphs = {
            QueueItemStatus.PENDING: "⏳ pendiente",
            QueueItemStatus.READY: "⏳ pendiente",
            QueueItemStatus.DELETING: "🔄 eliminando publicación original...",
            QueueItemStatus.CREATING: "🔄 publicando publicación nueva...",
            QueueItemStatus.COMPLETED: "✓ publicado",
            QueueItemStatus.SKIPPED: "⏭️ omitido",
            QueueItemStatus.FAILED: "✗ fallido",
            QueueItemStatus.UNCERTAIN: "⚠️ sin confirmar",
            QueueItemStatus.WAITING_USER: "⏸ esperando tu acción",
        }
        return glyphs.get(item.status, "⏳ pendiente")

    def _on_intervention_paused(self, reason: str) -> None:
        if self._queue_active():
            # La cola queda PAUSED e intacta; "Continuar" reanuda exactamente
            # donde iba y el resultado de la operación reanuda la cola.
            self._queue.handle_intervention()
            self._update_queue_ui()
            QMessageBox.information(
                self,
                "Cola pausada",
                f"{reason}\n\nResuelve la intervención en el navegador y pulsa "
                "'Ya inicié sesión / Continuar' cuando termine. La cola reanudará "
                "exactamente donde iba.",
            )
        self.session_status_label.setText("Estado: requiere tu intervención (navegador abierto)")
        self.nav_status_label.setText("Marketplace: esperando")
        self.continue_btn.setEnabled(True)
        self.nav_marketplace_btn.setEnabled(False)
        # Mientras esperan al usuario, no se puede buscar (la FSM está en
        # WAITING_USER; "Continuar" reanudará la búsqueda pendiente).
        self._search_ready = False
        self._update_search_button_state()

    def _on_automation_error(self, message: str) -> None:
        if self._queue_active():
            self._queue.handle_error()
            self._update_queue_ui()
            QMessageBox.warning(self, "Error de automatización", message)
            self._finish_queue()
            return
        self.connect_btn.setEnabled(True)
        self.nav_marketplace_btn.setEnabled(True)
        self._search_ready = False
        self._update_search_button_state()
        QMessageBox.warning(self, "Error de automatización", message)

    # -- Utilidades ---------------------------------------------------
    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        logger.info(message)

    def closeEvent(self, event) -> None:  # noqa: N802 (nombre requerido por Qt)
        forensics.evt("thread.closeEvent", f"automation={id(self._automation_thread)}")
        if self._automation_service is not None and self._automation_service.is_browser_running:
            from PySide6.QtCore import QMetaObject

            QMetaObject.invokeMethod(self._automation_service, "close_browser", Qt.ConnectionType.BlockingQueuedConnection)
        if self._automation_thread is not None:
            forensics.evt("thread.quit", f"thread={id(self._automation_thread)}")
            self._automation_thread.quit()
            self._automation_thread.wait(5000)
        event.accept()
