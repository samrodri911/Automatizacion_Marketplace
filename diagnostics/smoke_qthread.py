import os
import sys
import tempfile
import time

os.environ["MM_FORENSICS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QMetaObject, QObject, QThread, Qt, Slot, Signal

from pathlib import Path

from app.automation.browser import BrowserManager
from app.core import forensics


class Worker(QObject):
    log = Signal(str)

    def __init__(self):
        super().__init__()
        self.manager = BrowserManager(profile_dir=Path(tempfile.mkdtemp()))

    @Slot()
    def run(self):
        page = self.manager.start()
        page.goto("data:text/html,<h1>ok</h1>")
        print("eval:", page.evaluate("document.title"))
        print("alive mid:", forensics.driver_alive(self.manager._playwright))
        self.log.emit("run done")

    @Slot()
    def cerrar(self):
        self.manager.stop()
        print("cerrado; proc info:", forensics.driver_proc_info(self.manager._playwright))
        self.log.emit("cerrado")


worker = Worker()
worker.log.connect(print)
thread = QThread()
worker.moveToThread(thread)
thread.started.connect(worker.run)
thread.start()
time.sleep(6)
QMetaObject.invokeMethod(worker, "cerrar", Qt.ConnectionType.BlockingQueuedConnection)
thread.quit()
thread.wait(5000)
print("FIN sin EPIPE")
