"""PASO 6: reproducción del EPIPE con Playwright + QThread.

Ejecuta el mismo flujo que la app real (sesión -> Marketplace ->
Tus publicaciones) pero DENTRO de un QThread, replicando la arquitectura
de `AutomationService` (QObject movido a un QThread) SIN la GUI.

Uso (Windows, con sesión real de Facebook):
    $env:MM_FORENSICS="1"
    python diagnostics/test_qplaywright.py

El worker ejecuta el flujo y, en lugar de cerrar el driver, se queda en
un segundo slot `cerrar_browser` que debe invocarse desde el hilo principal
con BlockingQueuedConnection (igual que `closeEvent` de la GUI).

Comparación:
    - sin QThread (`test_plain.py`, pasos A-D): debe funcionar.
    - con QThread (este): ¿produce EPIPE?
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QMetaObject, QObject, QThread, Qt, Signal, Slot

from app.automation.browser import BrowserManager
from app.core import forensics


class Worker(QObject):
    log = Signal(str)
    done = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._manager = BrowserManager()
        self._page = None

    @Slot()
    def run(self) -> None:
        try:
            self.log.emit("worker: arrancando browser")
            self._page = self._manager.start()

            from app.automation.marketplace import MarketplaceAdapter

            self.log.emit("worker: abriendo Marketplace")
            adapter = MarketplaceAdapter(self._page)
            result = adapter.open_marketplace()
            self.log.emit(f"worker: marketplace ok={result.ok}")
            state = adapter.open_your_listings()
            self.log.emit(f"worker: tus publicaciones found={state.found}")
            self.log.emit("worker: flujo terminado; hilo sigue vivo")
            self.log.emit(f"driver alive={forensics.driver_alive(self._manager._playwright)}")
        except Exception as exc:
            self.log.emit(f"worker: EXCEPCIÓN: {exc!r}")
            forensics.evt("worker.exception", repr(exc))
        finally:
            self.done.emit()

    @Slot()
    def cerrar(self) -> None:
        self.log.emit(f"worker: cerrando driver (alive hoy={forensics.driver_alive(self._manager._playwright)})")
        self._manager.stop()
        self.log.emit("worker: cerrado")


def main() -> int:
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)  # necesario: BlockingQueuedConnection exige event loop

    thread = QThread()
    worker = Worker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    def show(m: str) -> None:
        print("  [QTHREAD]", m, flush=True)

    worker.log.connect(show)

    pending = QEventLoop()
    worker.done.connect(pending.quit)

    thread.start()
    # El flujo termina cuando done.emit() ocurre; no hay sleeps ciegos.
    pending.exec()  # Lanza el event loop del hilo principal; el worker corre en paralelo.

    print("--- Flujo terminado; invocando cierre desde el hilo principal (como closeEvent) ---")
    if worker._page is not None:
        QMetaObject.invokeMethod(worker, "cerrar", Qt.ConnectionType.BlockingQueuedConnection)
    thread.quit()
    thread.wait(10_000)
    print("FIN")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("MM_FORENSICS", "1")
    sys.exit(main())