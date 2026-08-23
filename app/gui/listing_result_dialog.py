"""Diálogo informativo con el resultado de buscar una publicación.

Es EXCLUSIVAMENTE una vista de solo lectura del resultado de la
`Iteración 3` (localización y verificación segura): muestra qué encontró el
buscador y por qué, pero NO contiene ningún botón de acción (no eliminar,
no editar, no publicar, no modificar). Si la persona decide actuar sobre la
publicación, lo hará en futuras iteraciones, nunca desde aquí.

Los datos llegan ya serializados (dicts) desde `AutomationService`; este
diálogo no sabe nada de la base de datos ni del navegador.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Texto legible por usuario para cada resultado.
_STATUS_LABELS = {
    "FOUND": "Coincidencia confirmada",
    "MEDIUM_CONFIDENCE": "Coincidencia con confianza media",
    "LOW_CONFIDENCE": "Coincidencia con baja confianza",
    "AMBIGUOUS": "Resultado ambiguo (no se decide por ti)",
    "NOT_FOUND": "No se encontró",
    "SEARCH_LIMIT_REACHED": "Límite de búsqueda alcanzado",
}

_CONFIDENCE_LABELS = {
    "HIGH": "Alta",
    "MEDIUM": "Media",
    "LOW": "Baja",
    "NO_MATCH": "Sin coincidencia",
}


class ListingResultDialog(QDialog):
    """Muestra el resultado de la búsqueda de una publicación (solo lectura)."""

    def __init__(self, result_payload: dict, product_title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._payload = result_payload

        self.setWindowTitle("Resultado de la búsqueda")
        self.setMinimumWidth(560)
        self.setModal(True)
        self._build_ui(product_title)

    # -- UI ---------------------------------------------------------------
    def _build_ui(self, product_title: str) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        status = self._payload.get("status", "")
        status_label = _STATUS_LABELS.get(status, status)

        best = self._payload.get("best")
        signs: list[str] = list(best.get("reasons") or []) if best else []
        warnings: list[str] = list(best.get("warnings") or []) if best else []

        confidence = ""
        title_found = None
        price = "—"
        url = "—"
        if best:
            confidence = _CONFIDENCE_LABELS.get(best.get("confidence", ""), best.get("confidence", ""))
            title_found = best.get("listing", {}).get("title")
            raw_price = best.get("listing", {}).get("price_raw")
            price = raw_price if raw_price else best.get("listing", {}).get("price")
            if price is None or price == "":
                price = "—"
            url = best.get("listing", {}).get("url") or "—"

        # -- Producto buscado --
        form.addRow("Producto buscado:", self._word_label(product_title or "—"))

        # -- Resultado --
        result_widget = self._word_label(status_label)
        font = QFont()
        font.setBold(True)
        result_widget.setFont(font)
        form.addRow("Resultado:", result_widget)

        if confidence:
            form.addRow("Confianza:", self._word_label(confidence))

        form.addRow("Título encontrado:", self._word_label(title_found))
        form.addRow("Precio:", self._word_label(str(price)))
        form.addRow("URL:", self._word_label(url))

        # -- Señales utilizadas --
        if signs:
            form.addRow("Señales utilizadas:", self._word_label("\n".join(f"• {s}" for s in signs)))

        if warnings:
            form.addRow("Advertencias:", self._word_label("\n".join(f"• {w}" for w in warnings)))

        scanned = self._payload.get("scanned")
        if scanned is not None:
            form.addRow("Anuncios revisados:", self._word_label(str(scanned)))

        # Siempre se muestra, aunque no haya intervención (por claridad).
        intervention = "Sí (Facebook pidió confirmación)" if self._payload.get("had_intervention") else "No"
        form.addRow("Intervención manual:", self._word_label(intervention))

        layout.addLayout(form)

        # -- Botón de cierre (acciones prohibidas: nada de eliminar/editar/publicar) --
        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _word_label(self, text: str | None) -> QLabel:
        label = QLabel(text or "—")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label