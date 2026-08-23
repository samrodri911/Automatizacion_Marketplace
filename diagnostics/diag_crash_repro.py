"""Harness de reproducción local del crash nativo de python.exe (0xC0000409).

Usa la arquitectura REAL (QThread + BrowserManager) sobre páginas locales
sintéticas que imitan Marketplace, y estresa EXACTAMENTE el patrón de
operaciones que coincidió con los crashes reales (logs 2026-08-19):

  - goto /marketplace/you/selling  (navegación pesada)
  - evaluate grande (extracción de items, como ListingExtractor)
  - scroll + evaluate (como el scanner/buscador)
  - goto /item/{id}  (como ListingDeleter -> page.goto al item)
  - goto about:blank + wait 200ms (como BrowserManager._detach_page_websockets)
  - ciclos de stop()/start() del browser (reproducen el crash del cierre)

Objetivo: capturar un minidump de python.exe (WER LocalDumps) que permita
identificar la función corrupta del canary de stack. NO toca Facebook ni el
perfil real (usa un perfil temporal en %TEMP%).

USO:
    .venv/Scripts/python.exe diagnostics/diag_crash_repro.py [ciclos_por_round] [rounds] [segundos_max]
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["MM_FORENSICS"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QObject, QThread, Slot  # noqa: E402

from app.automation.browser import BrowserManager  # noqa: E402
from app.core import forensics  # noqa: E402

N_ITEMS = 2000


def _item_html(i: int) -> str:
    return (
        f'<div class="x1i10hfl"><a href="/marketplace/item/{i}">'
        f'<img src="/img/{i}.jpg" alt="Foto {i}">'
        f'<span class="x1heor9g">Laptop HP Pavilion {i} - $1.850.000</span></a></div>'
    )


LISTINGS_HTML = (
    "<!DOCTYPE html><html><head><title>Tus publicaciones</title></head><body>"
    + "".join(_item_html(i) for i in range(N_ITEMS))
    + "</body></html>"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path.startswith("/marketplace/item/"):
            i = path.rsplit("/", 1)[-1]
            body = (
                "<!DOCTYPE html><html><head><title>Item "
                + i
                + '</title></head><body>'
                f'<div data-testid="listing-title">Laptop HP Pavilion {i} - $1.850.000</div>'
                "<div>Descripcion larga para forzar payload IPC de verdad.</div>"
                "</body></html>"
            )
            content = body.encode()
        elif path == "/marketplace/you/selling" or path == "/marketplace/":
            content = LISTINGS_HTML.encode()
        elif path.startswith("/img/"):
            content = b"\x00"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args) -> None:  # type: ignore[no-untyped-def]
        return


class StressWorker(QObject):
    """Ejecuta el patrón de operaciones de la app sobre el sitio sintético."""

    def __init__(self, base: str, cycles: int, rounds: int) -> None:
        super().__init__()
        self.base = base
        self.cycles = cycles
        self.rounds = rounds
        self.bm = BrowserManager(profile_dir=Path(tempfile.mkdtemp()))

    @Slot()
    def run(self) -> None:
        try:
            for r in range(self.rounds):
                try:
                    page = self.bm.start()
                except Exception as exc:  # noqa: BLE001
                    print(f"[repro] round {r}: fallo start: {type(exc).__name__}: {exc}", flush=True)
                    continue
                for c in range(self.cycles):
                    marker = f"round={r} cycle={c}"
                    try:
                        forensics.evt("repro.goto.selling", marker)
                        page.goto(self.base + "/marketplace/you/selling", wait_until="domcontentloaded")

                        forensics.evt("repro.eval.extract", marker)
                        raw = page.evaluate(
                            "() => Array.from(document.querySelectorAll(\"a[href*='/marketplace/item/']\")).map(a => ({ text: a.innerText, url: a.href, src: a.querySelector('img') ? a.querySelector('img').src : '' }))"
                        )

                        forensics.evt("repro.scroll", marker)
                        for _ in range(10):
                            page.evaluate("() => { window.scrollBy(0, 400); return document.body.scrollHeight; }")

                        item_id = (c * 7) % N_ITEMS
                        forensics.evt("repro.goto.item", f"{marker} id={item_id}")
                        page.goto(self.base + f"/marketplace/item/{item_id}", wait_until="domcontentloaded")
                        title = page.evaluate("() => (document.querySelector('[data-testid=listing-title]')||{}).innerText || ''")

                        if c % 5 == 4:
                            forensics.evt("repro.goto.blank", marker)
                            page.goto("about:blank", wait_until="domcontentloaded", timeout=3000)
                            page.wait_for_timeout(200)

                        if c % 10 == 0:
                            print(f"[repro] {marker} title={title!r} items={len(raw)}", flush=True)
                    except Exception as exc:  # noqa: BLE001
                        forensics.evt("repro.cycle.fail", f"{marker} {type(exc).__name__}: {exc}")
                        print(f"[repro] ciclo {marker} fallo (continua): {type(exc).__name__}: {str(exc)[:120]}", flush=True)
                        try:
                            page.goto("about:blank", wait_until="domcontentloaded", timeout=3000)
                        except Exception:  # noqa: BLE001
                            pass
                try:
                    self.bm.stop()
                except Exception as exc:  # noqa: BLE001
                    print(f"[repro] round {r}: fallo stop: {type(exc).__name__}: {exc}", flush=True)
                print(f"[repro] round {r} completado", flush=True)
            print("[repro] TODOS los rounds completados SIN crash", flush=True)
        except Exception as exc:  # noqa: BLE001
            forensics.evt("repro.fail", f"{type(exc).__name__}: {exc}")
            print(f"[repro] EXCEPCION FATAL: {type(exc).__name__}: {exc}", flush=True)
        finally:
            try:
                self.bm.stop()
            except Exception:  # noqa: BLE001
                pass
            from PySide6.QtCore import QCoreApplication

            QCoreApplication.instance().quit()


def main() -> int:
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    max_secs = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    print(f"[repro] Servidor local en {base}; cycles={cycles} rounds={rounds}", flush=True)

    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    thread = QThread()
    worker = StressWorker(base, cycles, rounds)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    thread.start()

    deadline = time.monotonic() + max_secs if max_secs else None
    while thread.isRunning():
        if deadline and time.monotonic() > deadline:
            print("[repro] TIEMPO MAXIMO alcanzado; terminando", flush=True)
            try:
                QCoreAppExit = app.quit
                app.quit()
            except Exception:  # noqa: BLE001
                pass
            thread.wait(10_000)
            break
        time.sleep(0.5)

    server.shutdown()
    print("[repro] fin del harness", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())