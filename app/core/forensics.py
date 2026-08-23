"""Instrumentación forense temporal para el diagnóstico EPIPE (Iteración 4.2).

SOLO se activa si la variable de entorno `MM_FORENSICS` está establecida
(en cualquier valor no vacío). Con ella desactivada (lo normal), este
módulo no añade overhead ni cambia el comportamiento de la aplicación.

Propósito:
- Registrar con timestamp, thread id y operación cada evento relevante del
  ciclo de vida de Playwright/browser/context/page y del QThread.
- Recolectar el PID del driver Node embebido (Playwright) y comprobar si el
  proceso sigue vivo justo antes/después de cada operación, para detectar la
  muerte del driver que antecede al EPIPE.

NUNCA accede ni imprime cookies, tokens, credenciales ni contenido privado
de Facebook: solo identidades de recursos (ids de objeto, PIDs, hilos).
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from typing import Any

from app.core.logging_config import get_logger

logger = get_logger(__name__)

_started_ns = time.perf_counter_ns()


def _active() -> bool:
    """Re-evalúa la variable por cada llamada: permite activarla/desactivarla
    dinámicamente y evita que el orden de import rompa los diagnósticos."""
    return bool(os.environ.get("MM_FORENSICS", ""))


def is_enabled() -> bool:
    return _active()


def _now_ms() -> int:
    return round((time.perf_counter_ns() - _started_ns) / 1_000_000)


def evt(kind: str, *parts: object) -> None:
    """Registra un evento forense si la instrumentación está activa."""
    if not _active():
        return
    tid = threading.get_ident()
    payload = " | ".join(str(p) for p in parts) if parts else ""
    logger.info(
        "[FORENS %.6d ms] [thread=%s] %-22s %s",
        _now_ms(),
        tid,
        kind,
        payload,
    )


def _win_pid_alive(pid: int) -> bool:
    """Comprueba si el proceso con `pid` sigue vivo (solo Windows).

    Usa la API de Windows (OpenProcess + GetExitCodeProcess con STILL_ACTIVE).
    Devuelve False ante cualquier error (proceso no existe / sin permisos).
    """
    try:
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong(0)
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return bool(ok) and exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def driver_proc_info(playwright: Any) -> dict[str, object]:
    """Devuelve info del proceso Node del driver (pid, alive, exit_code).

    IMPORTANTE: esta función accede a internos asyncio de Playwright
    (`_connection._transport._proc`). Solo debe llamarse desde el mismo
    hilo que posee el objeto `playwright` (el worker de automatización),
    y ÚNICAMENTE en momentos seguros: arranque o parada del navegador,
    NUNCA desde un QTimer ni desde callbacks de Qt mientras Playwright
    está procesando activamente una respuesta.

    Para monitoreo periódico (heartbeat) usar `driver_alive_by_pid(pid)`
    con el PID cacheado al arrancar.
    """
    if not _active():
        return {}
    try:
        impl = getattr(playwright, "_impl_obj", None) or playwright
        transport = impl._connection._transport  # type: ignore[attr-defined]
        proc = transport._proc  # type: ignore[attr-defined]
        pid = int(proc.pid)
        returncode = getattr(proc, "returncode", None)
        alive = returncode is None and _win_pid_alive(pid)
        return {
            "pid": pid,
            "alive": bool(alive),
            "exit_code": returncode,
        }
    except Exception:
        return {}


def extract_node_pid(playwright: Any) -> int | None:
    """Extrae el PID del proceso Node del driver UNA SOLA VEZ al arrancar.

    Devuelve un entero inmutable (el PID) que puede usarse de forma
    completamente thread-safe sin acceder a ningún objeto asyncio interno.
    Llamar solo desde el hilo worker, durante `BrowserManager.start()`.
    """
    try:
        impl = getattr(playwright, "_impl_obj", None) or playwright
        transport = impl._connection._transport  # type: ignore[attr-defined]
        proc = transport._proc  # type: ignore[attr-defined]
        return int(proc.pid)
    except Exception:
        return None


def driver_alive_by_pid(pid: int | None) -> bool:
    """Comprueba si el proceso Node del driver sigue vivo usando solo el PID.

    Thread-safe: solo usa la Windows API con un entero PID, sin tocar ningún
    objeto de Playwright ni de asyncio. Diseñado para el heartbeat del QTimer.
    Devuelve True si `pid` es None (no hay info, asumimos vivo).
    """
    if not _active():
        return True
    if pid is None:
        return True
    return _win_pid_alive(pid)


def driver_alive(playwright: Any) -> bool | None:
    """True si el driver Node sigue vivo, False si murió, None si no hay info.

    IMPORTANTE: mismas restricciones de thread-safety que `driver_proc_info`.
    No llamar desde QTimer. Usar `driver_alive_by_pid` para el heartbeat.
    """
    if not _active():
        return None
    info = driver_proc_info(playwright)
    if not info:
        return None
    return bool(info.get("alive"))


def mark_page(page: Any, label: str) -> None:
    """Registra la operación que se va a ejecutar sobre una `Page`."""
    if not _active():
        return
    try:
        evt("page", f"{label} url={page.url!r} closed={getattr(page, 'is_closed', lambda: False)}")
    except Exception:
        evt("page", label, "error leyendo url")