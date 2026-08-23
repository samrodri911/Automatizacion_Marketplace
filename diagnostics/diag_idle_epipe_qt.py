"""PASO decisivo: EPIPE en idle dentro de UN QThread (sin GUI).

Completa la matriz del diagnóstico 4.2:

    | entorno              | result (dr ver) |
    |----------------------|-----------------|
    | playwright sixto (sin Qt)   | 45s idle OK   (diag_idle_epipe) |
    | QThread + Qt (sin GUI)      | ??   <- ESTE  |
    | GUI real                    | EPIPE (usuario) |

Sigue el flujo real de `run_qplaywright` (worker en QThread + QApplication),
pero en lugar de cerrar al terminar, se queda IDLE 45s comprobando cada
segundo (sin llamadas Playwright) si el driver sigue vivo.

Uso:
    python diagnostics/diag_idle_epipe_qt.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QMetaObject, QObject, QThread, Qt, Signal, Slot

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
            self.log.emit("worker: abriendo browser")
            self._page = self._manager.start()
            self.log.emit(f"  driver={forensics.driver_proc_info(self._manager._playwright)}")

            from app.automation.marketplace import MarketplaceAdapter

            adapter = MarketplaceAdapter(self._page)
            m = adapter.open_marketplace()
            self.log.emit(f"  marketplace ok={m.ok}")
            s = adapter.open_your_listings()
            self.log.emit(f"  tus publicaciones found={s.found}")
            self.log.emit("worker: PERMANEZO EN IDLE 45s sin llamadas (el heartbeat vigilará)")
        except Exception as exc:
            self.log.emit(f"worker: EXCEPCIÓN: {exc!r}")
            forensics.evt("worker.exception", repr(exc))
        finally:
            self.done.emit()

    @Slot()
    def cerrar(self) -> None:
        self.log.emit("worker: cerrando browser")
        self._manager.stop()
        self.log.emit("worker: cerrado; driver:")
        self.log.emit(str(forensics.driver_proc_info(self._manager._playwright)))


def main() -> int:
    import ctypes

    def pid_alive(pid: int) -> bool:
        try:
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, int(pid))
            if not h:
                return False
            try:
                code = ctypes.c_ulong(0)
                k.GetExitCodeProcess(h, ctypes.byref(code))
                return code.value == 259
            finally:
                k.CloseHandle(h)
        except Exception:
            return False

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)  # QApplication sí (como la GUI real)

    thread = QThread()
    worker = Worker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.log.connect(lambda m: print("  [QTHREAD]", m, flush=True))

    pending = QEventLoop()
    worker.done.connect(pending.quit)

    thread.start()
    pending.exec()

    proc = forensics.driver_proc_info(worker._manager._playwright)
    pid = int(proc.get("pid", 0))
    if pid:
        print(f"--- EN IDLE 30s (driver pid={pid}) ---", flush=True)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 30:
            alive = pid_alive(pid)
            info = forensics.driver_proc_info(worker._manager._playwright)
            print(f"    t={time.monotonic()-t0:4.1f}s alive={alive} rc={info.get('exit_code')}", flush=True)
            if not alive:
                print(">>> EL DRIVER MURIÓ DENTRO DEL QTHREAD EN IDLE", flush=True)
                # no llamar Playwright si murió; intentamos reintento? no -> salir
                return 2
            time.sleep(1.0)
        print(">>> 30s IDLE en QThread sin muerte del driver", flush=True)
    else:
        print(">>> NO se obtuvo pid de driver (¿se abre bien?)", flush=True)
        return 3

    QMetaObject.invokeMethod(worker, "cerrar", Qt.ConnectionType.BlockingQueuedConnection)
    thread.quit()
    thread.wait(10_000)
    print("FIN", flush=True)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("MM_FORENSICS", "1")
    sys.exit(main())