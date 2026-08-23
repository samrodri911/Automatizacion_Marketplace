"""Diálogo modal de confirmación explícita para la eliminación de publicaciones.

REGLA DE SEGURIDAD CLAVE (sección 7 del spec):
Muestra de forma destacada e inconfundible los datos de la publicación que
está a punto de eliminarse (Producto, Título en FB, Precio, URL, Confianza)
y exige una confirmación explícita del usuario mediante un botón dedicado.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class DeleteConfirmDialog(QDialog):
    """Diálogo modal de confirmación humana obligatoria."""

    def __init__(self, ready_payload: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._payload = ready_payload

        self.setWindowTitle("⚠️ ELIMINAR PUBLICACIÓN DE FACEBOOK")
        self.setMinimumWidth(560)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Encabezado de advertencia destacado
        warning_title = QLabel("⚠️ CONFIRMACIÓN REQUERIDA")
        font_title = QFont()
        font_title.setBold(True)
        font_title.setPointSize(12)
        warning_title.setFont(font_title)
        warning_title.setStyleSheet("color: #d9534f;")
        layout.addWidget(warning_title)

        warning_sub = QLabel(
            "Esta operación eliminará de forma permanente la publicación seleccionada "
            "en Facebook Marketplace. Esta acción no se puede deshacer."
        )
        warning_sub.setWordWrap(True)
        layout.addWidget(warning_sub)

        layout.addSpacing(10)

        # Formulario con datos claros
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        prod_title = self._payload.get("product_title") or "—"
        list_title = self._payload.get("listing_title") or "—"
        price = self._payload.get("price") or "—"
        url = self._payload.get("url") or "—"
        confidence = self._payload.get("confidence") or "—"

        form.addRow("Producto local:", self._make_label(prod_title))
        form.addRow("Publicación en FB:", self._make_label(list_title))
        form.addRow("Precio mostrado:", self._make_label(str(price)))
        form.addRow("URL:", self._make_label(url))

        conf_label = self._make_label(f"{confidence} (Coincidencia inequívoca)")
        conf_font = QFont()
        conf_font.setBold(True)
        conf_label.setFont(conf_font)
        conf_label.setStyleSheet("color: #5cb85c;")
        form.addRow("Nivel de confianza:", conf_label)

        layout.addLayout(form)
        layout.addSpacing(15)

        # Botones de acción explícitos
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)

        delete_btn = QPushButton("🗑️ Eliminar publicación definitivamente")
        delete_btn.setStyleSheet(
            "QPushButton { background-color: #d9534f; color: white; font-weight: bold; padding: 6px 12px; } "
            "QPushButton:hover { background-color: #c9302c; }"
        )
        delete_btn.clicked.connect(self.accept)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(delete_btn)
        layout.addLayout(btn_layout)

    def _make_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label
