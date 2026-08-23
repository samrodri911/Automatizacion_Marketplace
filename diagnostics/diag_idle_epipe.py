"""PRUEBA DECISIVA: EPIPE en IDLE — sin Qt, sin clicks, sin app.

El usuario reporta que el EPIPE aparece tras "Tus publicaciones" al
interactuar. Este script aísla la pregunta: ¿muere el driver Node CERRADO
(ej: EPIPE en Node) SIN QT y SIN ninguna llamada posterior, solo estando
la página abierta en idle?

Hace:
  1) abrir perfil persistente real
  2) sesión detectada?
  3) marketplace + tus publicaciones
  4) IDLE: no se llama más Playwright. Solo se verifica cada 1s (sin NO
     hacer play ANY IPC) si el proceso driver sigue vivo, durante 45s.

Si el proceso Node muere solo en el idle intermedio (pid deja de existir/
exit_code distinto de None) -> el EPIPE NO es causado por la Qt ni por
nuestro patrón de uso: se dispara cabe_driver sola / Facebook.

Uso:
    $env:MM_FORENSICS="1"
    python diagnostics/diag_idle_epipe.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import forensics


def _pid_alive(pid: int) -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong(0)
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def main() -> int:
    from app.automation.browser import BrowserManager

    manager = BrowserManager()
    page = manager.start()

    print("Sesión:", manager.check_facebook_session(page).detail, flush=True)

    from app.automation.marketplace import MarketplaceAdapter

    adapter = MarketplaceAdapter(page)
    print("Marketplace:", adapter.open_marketplace().ok, flush=True)
    st = adapter.open_your_listings()
    print("Tus publicaciones:", st.found, flush=True)

    proc = forensics.driver_proc_info(manager._playwright)
    pid = int(proc.get("pid", 0))
    print(f"Driver PID={pid} alive={proc.get('alive')} — AHORA EN IDLE 45s (sin más llamadas Playwright)", flush=True)

    t0 = time.monotonic()
    while time.monotonic() - t0 < 45:
        alive = _pid_alive(pid)
        info = forensics.driver_proc_info(manager._playwright)
        rc = info.get("exit_code", None)
        print(f"  t={time.monotonic()-t0:5.1f}s alive={alive} exit_code={rc} info={info}", flush=True)
        if not alive or rc is not None:
            print(">>> EL DRIVER MURIÓ SOLO EN IDLE (sin llamar a Playwright) — cauce fuera de la app", flush=True)
            try:
                page.goto("about:blank", wait_until="domcontentloaded")
                print("  despues, goto about:blank OK → el pipe vivo?", flush=True)
                return 3
            except Exception as exc:
                print(f"  despues, goto about:blank FALLÓ: {exc!r}", flush=True)
                return 1 if rc is not None else 2
        time.sleep(1.0)

    print(">>> 45s de IDLE SIN muertes casuales del driver", flush=True)
    print(">> irgo a cerrar ordenadamente")
    manager.stop()
    print(">>> stop OK; driver:", forensics.driver_proc_info(manager._playwright), flush=True)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("MM_FORENSICS", "1")
    sys.exit(main())