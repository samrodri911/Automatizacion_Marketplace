import os
import sys
import time

os.environ["MM_FORENSICS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QObject, QThread, Qt, Slot
from PySide6.QtCore import QMetaObject

LOG = "diagnostics/slot_marker.txt"


class Worker(QObject):
    @Slot()
    def cerrar(self):
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("SLOT cerrar EJECUTADO\n")
        print("SLOT cerrar EJECUTADO (file written)", flush=True)

    @Slot()
    def run(self):
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("run ej\n")
        print("run en worker thread", flush=True)


if os.path.exists(LOG):
    os.remove(LOG)

w = Worker()
t = QThread()
w.moveToThread(t)
t.started.connect(w.run)
t.start()
time.sleep(2)
print("main: invocando cerrar (BlockingQueued)", flush=True)
ok = QMetaObject.invokeMethod(w, "cerrar", Qt.ConnectionType.BlockingQueuedConnection)
print("invoke result:", ok, flush=True)
t.quit()
t.wait(5000)
print("FIN — contenido del marcador:", flush=True)
with open(LOG, encoding="utf-8") as f:
    print(f.read())