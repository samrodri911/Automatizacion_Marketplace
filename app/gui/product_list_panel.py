"""Panel de lista de productos con dos conceptos separados.

REQUISITOS DE DISEÑO (ver tarea de UI):
- Una fila por producto, con un QCheckBox EXPLICITO a la izquierda y el
  título/precio a la derecha. Sin barra/indicador de Qt que parezca un
  segundo checkbox.
- Marcar el checkbox añade el producto al CONJUNTO DE COLA. Desmarcarlo lo
  quita. Cada checkbox es independiente de los demás.
- Hacer clic en la fila (sin checkbox) marca el producto como ENFOCADO para
  mostrar su detalle. Esto es independiente de los checkboxes: enfocar una
  fila NO desmarca otras filas, y desmarcar checkboxes NO cambia el
  enfoque.
- Un único botón "🔄 Republicar seleccionados (N)": N refleja los productos
  marcados (no los enfocados); deshabilitado cuando N==0.

Este widget NO conoce la cola de republicación ni Playwright: solo expone
el conjunto de IDs marcados y emite una señal `queue_requested` cuando el
usuario pulsa el botón. La ventana principal es responsable de pasarlos al
servicio.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.models.product import Product


@dataclass
class _RowEntry:
    product: Product
    row_widget: "ProductListRow"


class ProductListRow(QFrame):
    """Una fila visual: [☑] Título del producto — Precio.

    El checkbox controla la membresía de la cola. El resto de la fila
    controla el producto enfocado. Son independientes.
    """

    def __init__(self, product: Product, match_status: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._product = product
        self.setObjectName("ProductListRow")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        self.checkbox = QCheckBox()
        self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.checkbox.setToolTip("Incluir este producto en la cola de republicación")
        layout.addWidget(self.checkbox, stretch=0)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(2)

        self.title_label = QLabel(self._title_text())
        self.title_label.setObjectName("ProductListRowTitle")
        text_box.addWidget(self.title_label)

        price_str = f"${product.price:,.0f}".replace(",", ".")
        subtitle_parts = [price_str]
        if product.location:
            subtitle_parts.append(product.location)
        self.subtitle_label = QLabel("  ·  ".join(subtitle_parts))
        self.subtitle_label.setObjectName("ProductListRowSubtitle")
        text_box.addWidget(self.subtitle_label)

        # Status label para la publicación encontrada
        if match_status:
            self.status_label = QLabel(match_status)
        else:
            self.status_label = QLabel("○ No se ha buscado publicación")
            
        self.status_label.setObjectName("ProductListRowStatus")
        text_box.addWidget(self.status_label)

        layout.addLayout(text_box, stretch=1)

        self._apply_style(focused=False, checked=False)

    @property
    def product(self) -> Product:
        return self._product

    @property
    def product_id(self) -> int | None:
        return self._product.id

    def _title_text(self) -> str:
        icon = "🟢" if self._product.enabled else "⚪"
        title = self._product.title or "(sin título)"
        return f"{icon}  {title}"

    def set_checked(self, checked: bool) -> None:
        if self.checkbox.isChecked() != checked:
            self.checkbox.setChecked(checked)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def set_focused(self, focused: bool) -> None:
        self._apply_style(focused=focused, checked=self.is_checked())

    def _apply_style(self, focused: bool, checked: bool) -> None:
        # Estilo explícito por fila. NO usamos la selección nativa de Qt
        # para evitar el indicador azul que confundía con un segundo checkbox.
        if focused:
            bg = "#dbe9ff"
            border = "#5a8ed8"
            self.title_label.setStyleSheet("font-weight: 600; color: #15366b;")
        else:
            bg = "#f7f7f7" if not checked else "#eef7ee"
            border = "#dcdcdc" if not checked else "#88c488"
            self.title_label.setStyleSheet("font-weight: 500; color: #222;")
        self.subtitle_label.setStyleSheet("color: #666;")
        
        # Color del status (depende de si es HIGH, MEDIUM, LOW, etc)
        # Esto es rudimentario, pero da contexto visual rápido.
        status_text = self.status_label.text()
        if "✓" in status_text or "ALTA" in status_text:
            self.status_label.setStyleSheet("color: #2e7d32; font-weight: bold;")  # Verde oscuro
        elif "⚠" in status_text or "Posible" in status_text:
            self.status_label.setStyleSheet("color: #e65100; font-weight: bold;")  # Naranja oscuro
        else:
            self.status_label.setStyleSheet("color: #777;")
            
        self.setStyleSheet(
            f"QFrame#ProductListRow {{ background: {bg}; border: 1px solid {border}; "
            f"border-radius: 4px; }}"
        )


class ProductListPanel(QWidget):
    """Panel completo: lista de filas + contador + botón de cola."""

    # Producto que el usuario ha enfocado para ver detalles (None si ninguno).
    selection_changed = Signal(object)  # int | None
    # Conjunto actual de IDs marcados para la cola.
    queue_changed = Signal(object)  # set[int]
    # El usuario ha pulsado el botón de cola con los IDs actuales.
    queue_requested = Signal(object)  # set[int]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: dict[int, _RowEntry] = {}
        self._focused_product_id: int | None = None
        self._queue_ids: set[int] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setObjectName("ProductListScroll")
        self._scroll.setStyleSheet("background-color: #fafafa; border: 1px solid #dcdcdc; border-radius: 4px;")

        self._rows_host = QWidget()
        self._rows_host.setObjectName("ProductListRowsHost")
        self._rows_host.setStyleSheet("background: transparent;")
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(4, 4, 4, 4)
        self._rows_layout.setSpacing(6)
        self._rows_layout.addStretch(1)

        self._scroll.setWidget(self._rows_host)
        root.addWidget(self._scroll, stretch=1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self._count_label = QLabel("0 productos seleccionados")
        self._count_label.setStyleSheet("color: #444;")
        footer.addWidget(self._count_label, stretch=1)

        self.queue_btn = QPushButton("🔄 Republicar seleccionados (0)")
        self.queue_btn.setEnabled(False)
        self.queue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.queue_btn.setStyleSheet(
            "QPushButton:enabled { background-color: #5cb85c; color: white; font-weight: bold; }"
        )
        self.queue_btn.setToolTip(
            "Republica EN COLA los productos marcados. Requiere coincidencia HIGH."
        )
        footer.addWidget(self.queue_btn, stretch=0)
        root.addLayout(footer)

        self.queue_btn.clicked.connect(self._emit_queue_requested)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def set_products(self, products: list[Product], matches_info: dict[int, str] | None = None) -> None:
        """Reconstruye la lista con los productos dados y sus estados de Facebook.

        IMPORTANTE: se PRESERVA la pertenencia a la cola para los IDs que
        sigan existiendo (marcados en la sesión anterior a un reload). El
        enfoque se mantiene sobre el producto que ya estaba enfocado si
        sigue presente; en caso contrario, se pierde (ningún producto
        enfocado por defecto).
        """
        if matches_info is None:
            matches_info = {}
            
        previous_queue = set(self._queue_ids)
        previous_focus = self._focused_product_id

        # Vaciar el layout
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows.clear()

        new_ids: set[int] = set()
        first_focus_candidate: int | None = None

        for product in products:
            pid = product.id
            if pid is None:
                continue
            new_ids.add(pid)
            
            # Obtener el status formattado para este producto
            status_str = matches_info.get(pid, "○ Sin buscar")
            
            row = ProductListRow(product, match_status=status_str)
            row.checkbox.toggled.connect(lambda checked, pid=pid: self._on_row_toggled(pid, checked))
            row.mousePressEvent = self._make_row_click_handler(row)  # type: ignore[assignment]
            # Aseguramos que el click en el título/subtítulo/status también enfoque.
            row.title_label.mousePressEvent = self._make_label_click_handler(row)  # type: ignore[assignment]
            row.subtitle_label.mousePressEvent = self._make_label_click_handler(row)  # type: ignore[assignment]
            row.status_label.mousePressEvent = self._make_label_click_handler(row)  # type: ignore[assignment]

            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            self._rows[pid] = _RowEntry(product=product, row_widget=row)

            if first_focus_candidate is None and pid == previous_focus:
                first_focus_candidate = pid

        # Preservar marcas que sigan presentes.
        preserved_queue = previous_queue & new_ids
        self._queue_ids = set(preserved_queue)

        # Decidir el nuevo enfoque:
        # 1) si el foco anterior sigue presente, se mantiene.
        # 2) si no, NO seleccionamos nada (dejamos el panel "limpio"), así
        #    el usuario decide qué ver sin que un reload le cambie el foco.
        new_focus = first_focus_candidate if first_focus_candidate in new_ids else None
        self._set_focus_internal(new_focus, emit=False)

        # Reaplicar marcas y refrescar estilos.
        for pid in self._queue_ids:
            entry = self._rows.get(pid)
            if entry is not None:
                entry.row_widget.set_checked(True)
        self._refresh_styles()
        self._emit_queue_changed()

    def focused_product_id(self) -> int | None:
        return self._focused_product_id

    def set_focused_product_id(self, product_id: int | None) -> None:
        """Fuerza el enfoque (p. ej. tras editar un producto o seleccionar
        un escaneo). No altera los checkboxes."""
        if product_id is not None and product_id not in self._rows:
            return
        self._set_focus_internal(product_id, emit=True)

    def queue_product_ids(self) -> set[int]:
        return set(self._queue_ids)

    def checked_count(self) -> int:
        return len(self._queue_ids)

    def is_product_checked(self, product_id: int) -> bool:
        return product_id in self._queue_ids

    def clear_queue_marks(self) -> None:
        for entry in self._rows.values():
            entry.row_widget.set_checked(False)
        self._queue_ids.clear()
        self._refresh_styles()
        self._emit_queue_changed()

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _on_row_toggled(self, product_id: int, checked: bool) -> None:
        if checked:
            self._queue_ids.add(product_id)
        else:
            self._queue_ids.discard(product_id)
        self._refresh_styles()
        self._emit_queue_changed()

    def _make_row_click_handler(self, row: ProductListRow):
        def handler(event):
            # Si el usuario hizo clic sobre el checkbox, dejamos que el
            # checkbox gestione el toggle. Qt ya habrá emitido `toggled`.
            if isinstance(event, (object,)) and getattr(event, "pos", None) is not None:
                # Detectamos si el click cae sobre el checkbox comprobando
                # el rect del checkbox en coordenadas de la fila.
                cb = row.checkbox
                if cb.geometry().contains(event.position().toPoint() if hasattr(event, "position") else event.pos()):
                    # Devolvemos el control al comportamiento por defecto
                    # del checkbox (que ya disparó `toggled`).
                    QFrame.mousePressEvent(row, event)
                    return
            self._set_focus_internal(row.product_id, emit=True)
        return handler

    def _make_label_click_handler(self, row: ProductListRow):
        def handler(event):
            self._set_focus_internal(row.product_id, emit=True)
        return handler

    def _set_focus_internal(self, product_id: int | None, *, emit: bool) -> None:
        if product_id == self._focused_product_id:
            if emit:
                self.selection_changed.emit(self._focused_product_id)
            return
        old = self._focused_product_id
        self._focused_product_id = product_id
        if old is not None and old in self._rows:
            self._rows[old].row_widget.set_focused(False)
        if product_id is not None and product_id in self._rows:
            self._rows[product_id].row_widget.set_focused(True)
        if emit:
            self.selection_changed.emit(product_id)

    def _refresh_styles(self) -> None:
        for pid, entry in self._rows.items():
            focused = pid == self._focused_product_id
            checked = pid in self._queue_ids
            entry.row_widget._apply_style(focused=focused, checked=checked)

    def _emit_queue_changed(self) -> None:
        self.queue_changed.emit(set(self._queue_ids))
        n = len(self._queue_ids)
        if n == 1:
            self._count_label.setText("1 producto seleccionado")
        else:
            self._count_label.setText(f"{n} productos seleccionados")
        self.queue_btn.setText(f"🔄 Republicar seleccionados ({n})")
        self.queue_btn.setEnabled(n > 0)

    def _emit_queue_requested(self) -> None:
        if not self._queue_ids:
            return
        self.queue_requested.emit(set(self._queue_ids))

    # -- Métodos de compatibilidad para tests antiguos que esperaban un QListWidget --
    def count(self) -> int:
        return len(self._rows)

    def item(self, index: int) -> "_FakeItem":
        # Devolver un objeto simulado que tenga data() y setSelected()
        pid = list(self._rows.keys())[index]
        return _FakeItem(self, pid)


    def setCurrentRow(self, row: int) -> None:
        pids = list(self._rows.keys())
        if 0 <= row < len(pids):
            self.set_focused_product_id(pids[row])

    def clearSelection(self) -> None:
        self.set_focused_product_id(None)

class _FakeItem:
    def __init__(self, panel, pid: int):
        self.panel = panel
        self.pid = pid
        
    def data(self, role: int) -> int:
        return self.pid
        
    def setSelected(self, selected: bool) -> None:
        self.panel._on_row_toggled(self.pid, selected)
        # También actualizamos el checkbox para que el UI cambie visualmente
        entry = self.panel._rows.get(self.pid)
        if entry:
            entry.row_widget.set_checked(selected)
