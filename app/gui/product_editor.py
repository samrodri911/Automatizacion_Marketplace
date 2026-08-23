"""Diálogo de creación / edición de producto.

Es una vista "tonta": no habla con la base de datos directamente.
Recibe un `Product` opcional (None = creación) y, al aceptar, expone el
producto resultante junto con las rutas de imágenes nuevas seleccionadas
por el usuario. Quien la invoca (`main_window.py`) es responsable de
pasar esos datos a `ProductService`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.product import Product, ProductCondition

_IMAGE_FILE_FILTER = "Imágenes (*.jpg *.jpeg *.png *.webp)"

# Roles custom para diferenciar items de imagen ya guardada vs nueva en la QListWidget.
_IMAGE_ROLE_EXISTING = Qt.ItemDataRole.UserRole + 1
_IMAGE_ROLE_NEW = Qt.ItemDataRole.UserRole + 2


class ProductEditorDialog(QDialog):
    """Diálogo modal para crear o editar un producto."""

    def __init__(self, product: Product | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._existing_product = product
        self._new_image_paths: list[str] = []  # rutas absolutas seleccionadas ahora, aún no copiadas
        self._existing_images: list[str] = list(product.images) if product else []

        self.setWindowTitle("Editar producto" if product else "Nuevo producto")
        self.setMinimumWidth(480)
        self._build_ui()
        if product:
            self._load_product(product)

    # -- UI ---------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.title_edit = QLineEdit()
        form.addRow("Título:", self.title_edit)

        self.description_edit = QPlainTextEdit()
        self.description_edit.setFixedHeight(90)
        form.addRow("Descripción:", self.description_edit)

        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0, 999_999_999)
        self.price_spin.setDecimals(0)
        self.price_spin.setGroupSeparatorShown(True)
        form.addRow("Precio:", self.price_spin)

        self.category_edit = QLineEdit()
        self.category_edit.setPlaceholderText("Ej: Electrónica")
        form.addRow("Categoría:", self.category_edit)

        self.condition_combo = QComboBox()
        self.condition_combo.addItems(ProductCondition.values())
        form.addRow("Condición:", self.condition_combo)

        self.location_edit = QLineEdit()
        self.location_edit.setPlaceholderText("Ej: Cali")
        form.addRow("Ubicación:", self.location_edit)

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("iphone, apple, 128gb (separados por coma)")
        form.addRow("Tags:", self.tags_edit)

        self.enabled_checkbox = QCheckBox("Activo (se incluye al republicar seleccionados)")
        self.enabled_checkbox.setChecked(True)
        form.addRow("", self.enabled_checkbox)

        layout.addLayout(form)

        # -- Fotografías --
        layout.addWidget(QLabel("Fotografías:"))
        self.images_list = QListWidget()
        layout.addWidget(self.images_list)

        images_buttons = QHBoxLayout()
        add_images_btn = QPushButton("+ Agregar fotos")
        add_images_btn.clicked.connect(self._on_add_images)
        remove_image_btn = QPushButton("Quitar seleccionada")
        remove_image_btn.clicked.connect(self._on_remove_selected_image)
        images_buttons.addWidget(add_images_btn)
        images_buttons.addWidget(remove_image_btn)
        layout.addLayout(images_buttons)

        # -- Botones OK/Cancelar --
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_product(self, product: Product) -> None:
        self.title_edit.setText(product.title)
        self.description_edit.setPlainText(product.description)
        self.price_spin.setValue(product.price)
        self.category_edit.setText(product.category)
        index = self.condition_combo.findText(product.condition)
        if index >= 0:
            self.condition_combo.setCurrentIndex(index)
        self.location_edit.setText(product.location)
        self.tags_edit.setText(", ".join(product.tags))
        self.enabled_checkbox.setChecked(product.enabled)

        for relative_path in product.images:
            item = QListWidgetItem(f"(guardada) {relative_path}")
            item.setData(_IMAGE_ROLE_EXISTING, relative_path)
            self.images_list.addItem(item)

    # -- Manejo de imágenes -------------------------------------------------
    def _on_add_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Seleccionar fotografías", "", _IMAGE_FILE_FILTER)
        for path in paths:
            self._new_image_paths.append(path)
            item = QListWidgetItem(f"(nueva) {Path(path).name}")
            item.setData(_IMAGE_ROLE_NEW, path)
            self.images_list.addItem(item)

    def _on_remove_selected_image(self) -> None:
        for item in self.images_list.selectedItems():
            new_path = item.data(_IMAGE_ROLE_NEW)
            if new_path and new_path in self._new_image_paths:
                self._new_image_paths.remove(new_path)
            existing_path = item.data(_IMAGE_ROLE_EXISTING)
            if existing_path and existing_path in self._existing_images:
                self._existing_images.remove(existing_path)
            self.images_list.takeItem(self.images_list.row(item))

    # -- Validación y resultado ----------------------------------------------
    def _on_accept(self) -> None:
        product = self.result_product()
        errors = product.validate()
        # Las fotos nuevas todavía no están copiadas/contadas en
        # product.images en este punto (eso lo hace ProductService), así
        # que si hay imágenes nuevas seleccionadas no bloqueamos por
        # "falta fotografía" aunque product.images esté vacío todavía.
        if not product.images and not self._new_image_paths:
            pass  # el error "Debe haber al menos una fotografía" ya queda en `errors`
        elif "Debe haber al menos una fotografía" in errors:
            errors.remove("Debe haber al menos una fotografía")

        if errors:
            QMessageBox.warning(self, "Datos incompletos", "\n".join(f"• {e}" for e in errors))
            return

        self.accept()

    def result_product(self) -> Product:
        """Construye el `Product` con los datos actuales del formulario.

        No incluye las imágenes nuevas todavía copiadas; usar
        `new_image_paths()` para obtenerlas y pasarlas a
        `ProductService.create/update`.
        """
        tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]

        product = Product(
            id=self._existing_product.id if self._existing_product else None,
            title=self.title_edit.text().strip(),
            description=self.description_edit.toPlainText().strip(),
            price=float(self.price_spin.value()),
            category=self.category_edit.text().strip(),
            condition=self.condition_combo.currentText(),
            location=self.location_edit.text().strip(),
            tags=tags,
            images=list(self._existing_images),
            enabled=self.enabled_checkbox.isChecked(),
        )
        if self._existing_product:
            product.marketplace_url = self._existing_product.marketplace_url
            product.marketplace_reference = self._existing_product.marketplace_reference
            product.last_published_at = self._existing_product.last_published_at
            product.last_deleted_at = self._existing_product.last_deleted_at
            product.last_attempt_at = self._existing_product.last_attempt_at
            product.last_success_at = self._existing_product.last_success_at
            product.last_error = self._existing_product.last_error
            product.created_at = self._existing_product.created_at
            product.updated_at = self._existing_product.updated_at
        return product

    def new_image_paths(self) -> list[str]:
        return list(self._new_image_paths)
