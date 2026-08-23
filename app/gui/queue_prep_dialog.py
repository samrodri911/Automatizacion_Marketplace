"""Diálogo de PREPARACIÓN de la cola de republicación múltiple.

Fusiona el resumen de la cola con la edición de cada producto ANTES de
pulsar "Iniciar republicación". Cumple el requisito del plan aprobado:

    NO hay editor durante la cola: toda la edición ocurre AQUÍ, en la
    preparación. Al iniciar, los targets HIGH se congelan desde el escaneo y
    la edición ya no puede invalidarlos.

Es una vista "tonta": recibe los ítems elegibles (con su ítem HIGH del
escaneo) y un `ProductService` para leer/actualizar productos vía
`ProductEditorDialog`. Al aceptar, la GUI congelará los targets.
"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.exceptions import MarketplaceManagerError
from app.gui.product_editor import ProductEditorDialog
from app.models.republish_queue import QueueItem
from app.services.product_service import ProductService


class QueuePrepDialog(QDialog):
    """Resumen + edición por fila antes de iniciar la cola."""

    def __init__(
        self,
        items: list[QueueItem],
        excluded: list[QueueItem],
        product_service: ProductService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._items = items
        self._excluded = excluded
        self._product_service = product_service
        self._edited: set[int] = set()
        self._row_labels: list[QLabel] = []

        self.setWindowTitle("Republicación en cola")
        self.setMinimumSize(680, 460)
        self.setModal(True)
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        title = QLabel("Republicación en cola")
        font_title = QFont()
        font_title.setBold(True)
        font_title.setPointSize(13)
        title.setFont(font_title)
        layout.addWidget(title)

        sub = QLabel(
            f"Se procesarán {len(self._items)} producto(s), uno a la vez. "
            "Edita aquí los datos de cada NUEVA publicación ANTES de iniciar: "
            "durante la cola ya no se podrá editar."
        )
        sub.setWordWrap(True)
        layout.addWidget(sub)

        layout.addSpacing(8)

        self.items_list = QListWidget()
        layout.addWidget(self.items_list, stretch=1)

        if self._excluded:
            labels = "; ".join(f"'{i.display_title}' ({i.reason})" for i in self._excluded)
            excluded_label = QLabel("Excluidos (no se procesarán): " + labels)
            excluded_label.setWordWrap(True)
            excluded_label.setStyleSheet("color: #8a8a8a;")
            layout.addWidget(excluded_label)

        layout.addSpacing(10)

        hint = QLabel(
            "Al iniciar se CONGELA la publicación original encontrada en Facebook "
            "(URL/referencia/título/precio que mostró) y se eliminará solo tras tu "
            "confirmación en el flujo. Los targets se congelan ANTES de procesar."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6c757d;")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        start_btn = QPushButton("🔄 Iniciar republicación")
        start_btn.setStyleSheet(
            "QPushButton { background-color: #5cb85c; color: white; font-weight: bold; padding: 6px 14px; } "
            "QPushButton:hover { background-color: #449d44; }"
        )
        start_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(start_btn)
        layout.addLayout(buttons)

    def _populate(self) -> None:
        self._row_labels = []
        for i, item in enumerate(self._items, start=1):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 8, 4)

            label = QLabel(self._row_text(item))
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

            edit_btn = QPushButton("✏️ Editar datos")
            edit_btn.clicked.connect(lambda checked=False, idx=i - 1: self._on_edit(idx))

            row_layout.addWidget(label, stretch=1)
            row_layout.addWidget(edit_btn)

            list_item = QListWidgetItem()
            list_item.setSizeHint(row.sizeHint())
            self.items_list.addItem(list_item)
            self.items_list.setItemWidget(list_item, row)
            self._row_labels.append(label)

    def _row_text(self, item: QueueItem) -> str:
        listing = item.scan_item.get("listing") or {}
        fb_title = listing.get("title") or item.product_title or "(sin título)"
        price = listing.get("price_raw") or listing.get("price") or "—"
        edited = "  ✓ EDITADO" if item.product_id in self._edited else ""
        return (
            f"{item.product_title}\n"
            f"   Coincidencia HIGH → '{fb_title}' — {price}{edited}"
        )

    def _on_edit(self, index: int) -> None:
        item = self._items[index]
        try:
            product = self._product_service.get(item.product_id)
        except MarketplaceManagerError as exc:
            QMessageBox.critical(self, "Error", str(exc))
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

        # La edición queda en `Product` (BD). El target se congelará después,
        # desde el escaneo, con el snapshot de los datos editados.
        self._edited.add(item.product_id)
        item.product_title = updated.title
        self._row_labels[index].setText(self._row_text(item))

    def edited_product_ids(self) -> set[int]:
        return set(self._edited)