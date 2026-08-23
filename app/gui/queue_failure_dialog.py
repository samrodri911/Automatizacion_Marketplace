"""Diálogo de fallo/incertidumbre de un ítem de la cola (spec 10/11).

Opciones:
  - "🔄 Reintentar (verificar primero)" -> RETRY: reanuda la operación
    VERIFICANDO primero, nunca re-elimina ni re-publica a ciegas.
  - "Omitir y continuar"               -> SKIP: el target queda bloqueado y
    la cola avanza al siguiente ítem.
  - "⏹ Detener cola"                   -> STOP: cancela la cola en un punto
    seguro.

Nunca hay avance automático ante un resultado no confirmado: la cola se
pausa y el usuario decide.
"""

from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.republish_queue import QueueItem


class QueueFailureChoice(Enum):
    RETRY = auto()
    SKIP = auto()
    STOP = auto()


class QueueFailureDialog(QDialog):
    """Decisión humana obligatoria cuando un ítem no se pudo confirmar."""

    def __init__(
        self,
        item: QueueItem,
        result_name: str,
        detail: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._item = item
        self._result_name = result_name
        self._detail = detail
        self._choice = QueueFailureChoice.STOP

        self.setWindowTitle("Cola de republicación: pausada")
        self.setMinimumWidth(540)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        title = QLabel("⚠️ LA COLA SE HA PAUSADO")
        font_title = QFont()
        font_title.setBold(True)
        font_title.setPointSize(12)
        title.setFont(font_title)
        title.setStyleSheet("color: #d9534f;")
        layout.addWidget(title)

        body = QLabel(
            f"'{self._item.display_title}' no se pudo confirmar ({self._result_name})."
        )
        body.setWordWrap(True)
        layout.addWidget(body)

        detail_label = QLabel(self._detail or "Sin detalle adicional.")
        detail_label.setWordWrap(True)
        detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_label.setStyleSheet("color: #6c757d;")
        layout.addWidget(detail_label)

        layout.addSpacing(6)

        info = QLabel(
            "Elige qué hacer con este producto. La cola NO avanza sola hasta que decidas."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addSpacing(12)

        buttons = QHBoxLayout()
        buttons.addStretch()

        retry_btn = QPushButton("🔄 Reintentar (verificar primero)")
        retry_btn.setToolTip("Verifica el estado real antes de actuar; nunca re-elimina/re-publica a ciegas.")
        retry_btn.clicked.connect(lambda: self._choose(QueueFailureChoice.RETRY))

        skip_btn = QPushButton("Omitir y continuar")
        skip_btn.setToolTip("El target queda bloqueado y la cola avanza al siguiente producto.")
        skip_btn.clicked.connect(lambda: self._choose(QueueFailureChoice.SKIP))

        stop_btn = QPushButton("⏹ Detener cola")
        stop_btn.setToolTip("Detiene la cola completa en un punto seguro.")
        stop_btn.clicked.connect(lambda: self._choose(QueueFailureChoice.STOP))

        buttons.addWidget(retry_btn)
        buttons.addWidget(skip_btn)
        buttons.addWidget(stop_btn)
        layout.addLayout(buttons)

    def _choose(self, choice: QueueFailureChoice) -> None:
        self._choice = choice
        self.accept()

    @property
    def choice(self) -> QueueFailureChoice:
        return self._choice