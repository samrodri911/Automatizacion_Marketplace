"""veh_dump.py — Captura minidump en el instante del crash nativo.

Registra un Vectored Exception Handler (VEH) en primer lugar de la cadena de
excepciones de Windows. Cuando el proceso recibe una excepción nativa
(0xC0000005 access violation, 0xC0000409 fail-fast, 0xC00000FD stack overflow,
...), el VEH escribe un minidump full-memory ANTES de que Windows termine el
proceso.

Por qué existe: el crash 0xC0000005/0xC0000409 de este proyecto NO se entrega
al debugger de procdump (ni first-chance ni unhandled): el proceso muere sin
que procdump pueda capturar el estado real (sus dumps solo reflejan el
teardown final, sin python311.dll/PySide6/greenlet). Un VEH corre DENTRO del
proceso, en el mismo hilo que lanza la excepción, y puede volcar el estado
completo (con todos los módulos cargados) en el momento exacto.

El VEH solo escribe el dump y devuelve EXCEPTION_CONTINUE_SEARCH: el proceso
sigue muriendo como antes (el comportamiento de la app no cambia).

Uso: importar este módulo al arrancar la app (antes de Playwright):
    import app.core.veh_dump as veh_dump
    veh_dump.install()

Directorio de salida: variable de entorno MM_DUMP_DIR o el directorio de
dumps de diagnósticos (por defecto %TEMP%\\opencode\\werdumps).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import threading
import time
from ctypes import POINTER, Structure, byref, c_long, c_ulong, c_void_p, cast

# -- Códigos de excepción de interés -------------------------------------------
EXCEPTION_ACCESS_VIOLATION = 0xC0000005
STATUS_STACK_BUFFER_OVERRUN = 0xC0000409  # fail-fast __security_check_cookie
STATUS_STACK_OVERFLOW = 0xC00000FD
STATUS_BREAKPOINT = 0x80000003
STATUS_SINGLE_STEP = 0x80000004

INTERESTING = frozenset(
    {
        EXCEPTION_ACCESS_VIOLATION,
        STATUS_STACK_BUFFER_OVERRUN,
        STATUS_STACK_OVERFLOW,
    }
)

EXCEPTION_CONTINUE_SEARCH = 0

# Flags de minidump (MINIDUMP_TYPE)
# OJO: NO usar MDMP_FULL_MEMORY ni MDMP_DATA_SEG: en un proceso cuya memoria
# está corrupta (el caso de este crash), MiniDumpWriteDump falla con
# ERROR_NOACCESS al recorrer las páginas. MiniDumpNormal + extras basta:
# lista de módulos, pilas de todos los threads y registro de excepción.
MDMP_HANDLE_DATA = 0x00000004
MDMP_WITH_THREAD_INFO = 0x00000008
MDMP_UNLOADED_MODULES = 0x00000010

MINIDUMP_TYPE_FLAGS = MDMP_HANDLE_DATA | MDMP_WITH_THREAD_INFO | MDMP_UNLOADED_MODULES

# -- Estructuras ----------------------------------------------------------------
class EXCEPTION_RECORD(Structure):
    pass


class EXCEPTION_RECORD(Structure):
    _fields_ = [
        ("ExceptionCode", c_ulong),
        ("ExceptionFlags", c_ulong),
        ("ExceptionRecord", POINTER(EXCEPTION_RECORD)),
        ("ExceptionAddress", c_void_p),
        ("NumberParameters", c_ulong),
        ("ExceptionInformation", c_void_p * 15),
    ]


class CONTEXT(Structure):
    _fields_ = []  # no se interpreta; solo se pasa por puntero


class EXCEPTION_POINTERS(Structure):
    _fields_ = [
        ("ExceptionRecord", POINTER(EXCEPTION_RECORD)),
        ("ContextRecord", POINTER(CONTEXT)),
    ]


class MINIDUMP_EXCEPTION_INFORMATION(Structure):
    _fields_ = [
        ("ThreadId", c_ulong),
        ("ExceptionPointers", POINTER(EXCEPTION_POINTERS)),
        ("ClientPointers", wintypes.BOOL),
    ]


# -- API de Windows -------------------------------------------------------------
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_dbghelp = ctypes.WinDLL("dbghelp", use_last_error=True)

_AddVectoredExceptionHandler = _kernel32.AddVectoredExceptionHandler
_AddVectoredExceptionHandler.argtypes = [c_ulong, ctypes.c_void_p]
_AddVectoredExceptionHandler.restype = c_void_p

_RemoveVectoredExceptionHandler = _kernel32.RemoveVectoredExceptionHandler
_RemoveVectoredExceptionHandler.argtypes = [c_void_p]
_RemoveVectoredExceptionHandler.restype = c_ulong

_GetCurrentProcess = _kernel32.GetCurrentProcess
_GetCurrentProcess.restype = c_void_p

_GetCurrentProcessId = _kernel32.GetCurrentProcessId
_GetCurrentProcessId.restype = c_ulong

_GetCurrentThreadId = _kernel32.GetCurrentThreadId
_GetCurrentThreadId.restype = c_ulong

_CreateFileW = _kernel32.CreateFileW
_CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    c_ulong,
    c_ulong,
    c_void_p,
    c_ulong,
    c_ulong,
    c_void_p,
]
_CreateFileW.restype = c_void_p

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [c_void_p]
_CloseHandle.restype = wintypes.BOOL

_MiniDumpWriteDump = _dbghelp.MiniDumpWriteDump
_MiniDumpWriteDump.argtypes = [
    c_void_p,  # hProcess
    c_ulong,  # ProcessId
    c_void_p,  # hFile
    c_ulong,  # DumpType
    POINTER(MINIDUMP_EXCEPTION_INFORMATION),  # ExceptionParam
    c_void_p,  # UserStreamParam
    c_void_p,  # CallbackParam
]
_MiniDumpWriteDump.restype = wintypes.BOOL

GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_ALWAYS = 4
CREATE_ALWAYS = 2

# -- Estado ---------------------------------------------------------------------
_installed = False
_handler_ptr = None
_dump_dir = ""
_lock = threading.Lock()
_last_write = 0.0
_seq = 0


def _handler(exception_pointers_addr: int) -> int:
    """Callback del VEH. Se ejecuta en el hilo que lanzó la excepción."""
    global _last_write, _seq
    try:
        if not exception_pointers_addr:
            return EXCEPTION_CONTINUE_SEARCH

        ep = cast(exception_pointers_addr, POINTER(EXCEPTION_POINTERS)).contents
        rec = ep.ExceptionRecord.contents
        code = rec.ExceptionCode
        addr = rec.ExceptionAddress

        if code not in INTERESTING:
            return EXCEPTION_CONTINUE_SEARCH

        # Gate anti-reentrada: como mucho un dump por segundo.
        now = time.monotonic()
        if now - _last_write < 1.0:
            return EXCEPTION_CONTINUE_SEARCH
        _last_write = now

        _seq += 1
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        dump_path = os.path.join(
            _dump_dir, f"veh_python_{stamp}_{os.getpid()}_{_seq}.dmp"
        )

        # Notificar por stderr para que aparezca en la consola del launcher.
        try:
            import sys

            sys.stderr.write(
                f"[VEH] EXCEPTION 0x{code:08X} en {addr:#x} tid={_GetCurrentThreadId()} -> volcando {dump_path}\n"
            )
            sys.stderr.flush()
        except Exception:
            pass

        # NO pasamos MINIDUMP_EXCEPTION_INFORMATION: probado que con el
        # contexto de la VEH MiniDumpWriteDump falla con ERROR_NOACCESS.
        # Sin ese parámetro funciona (módulos + pilas de todos los threads),
        # y el código/dirección del faulting ya quedaron en el marker.
        minidump_info = None

        handle = _CreateFileW(
            dump_path,
            GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            CREATE_ALWAYS,
            0,
            None,
        )
        if handle and handle != c_void_p(-1).value and handle != 0xFFFFFFFFFFFFFFFF:
            try:
                ok_write = _MiniDumpWriteDump(
                    _GetCurrentProcess(),
                    _GetCurrentProcessId(),
                    handle,
                    MINIDUMP_TYPE_FLAGS,
                    None,
                    None,
                    None,
                )
            finally:
                _CloseHandle(handle)
            try:
                import sys

                le = ctypes.get_last_error()
                if ok_write:
                    sys.stderr.write("[VEH] dump escrito\n")
                    try:
                        sidecar = dump_path + ".txt"
                        with open(sidecar, "w", encoding="utf-8") as fh:
                            fh.write(f"exception_code=0x{code:08X}\n")
                            fh.write(f"exception_address={addr:#x}\n")
                            fh.write(f"thread_id={_GetCurrentThreadId()}\n")
                            fh.write(f"dump={dump_path}\n")
                    except Exception:
                        pass
                else:
                    sys.stderr.write(f"[VEH] MiniDumpWriteDump fallo, last_error={le} (0x{le & 0xFFFFFFFF:08X})\n")
                sys.stderr.flush()
            except Exception:
                pass
    except Exception:
        # Nunca romper la cadena de excepciones por un fallo del propio VEH.
        pass

    return EXCEPTION_CONTINUE_SEARCH


_VEH_CALLBACK = ctypes.CFUNCTYPE(c_long, c_void_p)(_handler)


def install(dump_dir: str | None = None) -> bool:
    """Registra el VEH. Devuelve True si se registró correctamente."""
    global _installed, _handler_ptr, _dump_dir
    if _installed:
        return True

    dump_dir = dump_dir or os.environ.get("MM_DUMP_DIR") or os.path.join(
        os.environ.get("TEMP", "."), "opencode", "werdumps"
    )
    os.makedirs(dump_dir, exist_ok=True)
    _dump_dir = dump_dir

    # Primer manejador (0): primero en ver la excepción. Usamos 1 por
    # compatibilidad (se añade al inicio de la cadena igualmente).
    _handler_ptr = _AddVectoredExceptionHandler(1, cast(_VEH_CALLBACK, c_void_p))
    _installed = bool(_handler_ptr) and _handler_ptr != c_void_p(0).value
    return _installed


def uninstall() -> None:
    global _installed, _handler_ptr
    if _installed and _handler_ptr:
        _RemoveVectoredExceptionHandler(_handler_ptr)
        _handler_ptr = None
        _installed = False