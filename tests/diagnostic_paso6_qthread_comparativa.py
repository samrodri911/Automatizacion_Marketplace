"""Paso 6 — Comparativa con QThread vs sin QThread.

Ejecuta el MISMO flujo (Facebook → Marketplace → Tus publicaciones →
varias evaluations) DOS VECES:

  Run 1: SIN QThread (todo en el hilo principal)
  Run 2: CON QThread (AutomationService en un hilo dedicado)

Si SOLO el Run 2 reproduce el EPIPE: el lifecycle de Qt/QThread es
responsable.
Si AMBOS fallan: el problema está debajo de Qt.

USO (en Windows real):
    MM_FORENSICS=1 .venv/Scripts/python.exe tests/diagnostic_paso6_qthread_comparativa.py
"""

from __future__ import annotations

import os
import sys
import time

os.environ["MM_FORENSICS"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core import forensics  # noqa: E402

PROFILE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "browser_profile")


class _Worker(QObject):
    """Mismo flujo que AutomationService: lanza browser, navega, ejecuta
    evaluate, todo desde un slot."""

    finished = Signal()

    @Slot()
    def run(self) -> None:
        from app.automation.browser import BrowserManager
        bm = BrowserManager()
        try:
            forensics.evt("paso6.worker.start", f"tid={threading_get_ident()}")
            page = bm.start()
            page.goto("https://www.facebook.com/marketplace/you/selling", wait_until="domcontentloaded")
            for i in range(5):
                page.evaluate("() => document.querySelectorAll('a').length")
            forensics.evt("paso6.worker.ok")
        except Exception as exc:
            forensics.evt("paso6.worker.fail", f"{type(exc).__name__}: {exc}")
        finally:
            try:
                bm.stop()
            except Exception:
                pass
            self.finished.emit()


def threading_get_ident() -> int:
    import threading
    return threading.get_ident()


def run_without_qthread() -> int:
    forensics.evt("paso6.run1.sinthread", "inicio")
    w = _Worker()
    w.run()
    return 0


def run_with_qthread(app: QApplication) -> int:
    forensics.evt("paso6.run2.conthread", "inicio")
    thread = QThread()
    worker = _Worker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    thread.start()
    thread.wait(60_000)
    return 0


def main() -> int:
    print(f"[PASO 6] MM_FORENSICS = {os.environ.get('MM_FORENSICS')}")
    print("[PASO 6] Run 1: sin QThread")
    run_without_qthread()
    print("[PASO 6] Run 1 OK. Run 2: con QThread")
    app = QApplication.instance() or QApplication([])
    run_with_qthread(app)
    print("[PASO 6] Ambos runs terminaron.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
