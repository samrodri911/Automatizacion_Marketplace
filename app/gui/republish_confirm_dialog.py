"""Diálogo de confirmación del flujo "Editar y republicar" (spec §8).

Muestra lado a lado:
- la PUBLICACIÓN ENCONTRADA (el target congelado: título/precio que mostró
  Facebook, URL/referencia y confianza HIGH) que se va a ELIMINAR; y
- la NUEVA PUBLICACIÓN (los datos editados del producto local) que se va a
  CREAR.

REGLAS DE SEGURIDAD:
- No hay acción destructiva sin esta confirmación explícita.
- El título/precio mostrados como "original" provienen del `MatchedListing`
  CONGELADO (nunca del producto editado).
- La confianza mostrada debe ser HIGH; si no lo fuera, no se habilitó el
  flujo y este diálogo no debería llegar a abrirse.
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

from app.models.matched_listing import MatchedListing
from app.models.product import Product


class RepublishConfirmDialog(QDialog):
    """Confirmación humana obligatoria antes de eliminar y republicar."""

    def __init__(
        self,
        matched: MatchedListing,
        product: Product,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._matched = matched
        self._product = product

        self.setWindowTitle("🔁 ELIMINAR Y REPUBLICAR EN FACEBOOK")
        self.setMinimumWidth(600)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        warning = QLabel("⚠️ ACCIÓN DESTRUCTIVA + CREACIÓN NUEVA")
        font_title = QFont()
        font_title.setBold(True)
        font_title.setPointSize(12)
        warning.setFont(font_title)
        warning.setStyleSheet("color: #d9534f;")
        layout.addWidget(warning)

        sub = QLabel(
            "Se eliminará de forma permanente la publicación encontrada en Facebook "
            "y, SOLO tras confirmar la eliminación, se creará una publicación nueva "
            "con los datos editados."
        )
        sub.setWordWrap(True)
        layout.addWidget(sub)

        layout.addSpacing(12)

        # -- Sección: publicación encontrada (original congelada) --
        original_title = QLabel("PUBLICACIÓN ENCONTRADA  (se eliminará)")
        self._style_section(original_title, "#5bc0de")
        layout.addWidget(original_title)

        original_form = QFormLayout()
        original_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        original_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        fb_title = self._matched.matched_title or self._matched.listing_reference or "(sin título)"
        fb_price = self._matched.matched_price_raw or (
            str(self._matched.matched_price) if self._matched.matched_price is not None else "—"
        )
        locator = self._matched.listing_url or self._matched.listing_reference or "—"

        original_form.addRow("Título que mostró FB:", self._make_label(fb_title))
        original_form.addRow("Precio mostrado:", self._make_label(fb_price))
        original_form.addRow("URL / referencia:", self._make_label(locator))

        conf_label = self._make_label("HIGH  (coincidencia inequívoca)")
        conf_font = QFont()
        conf_font.setBold(True)
        conf_label.setFont(conf_font)
        conf_label.setStyleSheet("color: #5cb85c;")
        original_form.addRow("Nivel de confianza:", conf_label)

        layout.addLayout(original_form)

        layout.addSpacing(12)

        # -- Sección: nueva publicación (datos editados del producto) --
        new_title = QLabel("NUEVA PUBLICACIÓN  (se creará tras eliminar)")
        self._style_section(new_title, "#5cb85c")
        layout.addWidget(new_title)

        new_form = QFormLayout()
        new_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        new_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        new_form.addRow("Título editado:", self._make_label(self._product.title or "—"))
        new_form.addRow("Precio editado:", self._make_label(str(self._product.price) if self._product.price else "—"))
        layout.addLayout(new_form)

        layout.addSpacing(15)

        # -- Botones --
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)

        action_btn = QPushButton("🗑️ Eliminar y republicar")
        action_btn.setStyleSheet(
            "QPushButton { background-color: #d9534f; color: white; font-weight: bold; padding: 6px 12px; } "
            "QPushButton:hover { background-color: #c9302c; }"
        )
        action_btn.clicked.connect(self.accept)

        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(action_btn)
        layout.addLayout(btn_layout)

    def _style_section(self, label: QLabel, color: str) -> None:
        font = QFont()
        font.setBold(True)
        label.setFont(font)
        label.setStyleSheet(f"color: {color};")

    def _make_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label