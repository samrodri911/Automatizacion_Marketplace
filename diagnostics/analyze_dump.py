"""Analizador de minidumps para el crash 0xC0000005/0xC0000409 de python.exe.

USO:
    .venv/Scripts/python.exe diagnostics/analyze_dump.py <ruta_al_dmp> [--tid <id>] [--all-stack]

El dump puede venir de:
  - procdump:  si el proceso murió con excepción, viene el stream de excepción.
  - VEH (app/core/veh_dump.py): no trae stream de excepción. El VEH deja un
    sidecar "<dmp>.txt" con exception_code/exception_address/thread_id; usa
    --tid <thread_id> para indicar el hilo del crash (y el sidecar se lee solo).

Imprime:
  - información del sistema (arquitectura)
  - excepción (código, dirección, thread) si existe, o la del sidecar
  - módulos cargados (los nativos relevantes)
  - para cada thread: Id, Rip/Rsp y a qué módulo pertenecen
  - para el thread que crasheó: caminata de pila nativa (direcciones de
    retorno candidatas con módulo + RVA) + escaneo completo del stack.
"""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, __file__.rsplit("site-packages", 1)[0] + "site-packages")

from minidump.minidumpfile import MinidumpFile  # noqa: E402

INTERESTING_MODULES = (
    "python311.dll",
    "Qt6Core.dll",
    "Qt6Widgets.dll",
    "Qt6Gui.dll",
    "shiboken6.abi3.dll",
    "PySide6.abi3.dll",
    "_greenlet.pyd",
    "_overlapped.pyd",
    "libffi-8.dll",
    "ucrtbase.dll",
    "msvcrt.dll",
    "KERNELBASE.dll",
    "ntdll.dll",
    "KERNEL32.dll",
    "node.exe",
    "driver.dll",
)


def module_for(addr: int, modules) -> tuple | None:
    for mod in modules:
        if mod.baseaddress <= addr < mod.endaddress:
            return mod
    return None


def fmt_addr(addr: int, modules) -> str:
    mod = module_for(addr, modules)
    if mod is None:
        return f"0x{addr:016x} (sin módulo)"
    rva = addr - mod.baseaddress
    name = mod.name.rsplit("\\", 1)[-1] if mod.name else "?"
    return f"0x{addr:016x}  {name}+0x{rva:X}"


def walk_stack(thread, modules, max_frames: int = 40):
    """Caminata heurística de pila: lee qwords del stack del thread y
    conserva los que apuntan dentro de un módulo cargado (returns)."""
    ctx = getattr(thread, "ContextObject", None)
    rsp = getattr(ctx, "Rsp", 0) if ctx else 0
    rip = getattr(ctx, "Rip", 0) if ctx else 0
    stack = thread.Stack
    start = stack.StartOfMemoryRange
    end = start + stack.DataSize
    fh = None
    try:
        fh = thread._mf_file  # noqa: SLF001  (se asigna en analyze)
    except Exception:
        fh = None
    if fh is None:
        return rip, rsp, []
    fh.seek(stack.Rva)
    raw = fh.read(stack.DataSize)

    frames = []
    # Empezamos por la dirección de retorno de __security_check_cookie (Rsp).
    addr = max(rsp, start) & ~0x7
    while addr + 8 <= end and len(frames) < max_frames:
        idx = addr - start
        if idx + 8 > len(raw):
            break
        qword = struct.unpack_from("<Q", raw, idx)[0]
        if module_for(qword, modules) is not None:
            frames.append((addr, qword))
        addr += 8
    return rip, rsp, frames


def analyze(path: str, all_stack: bool = False, tid: int | None = None) -> int:
    mf = MinidumpFile.parse(path)
    print(f"=== Minidump: {path} ===")
    if mf.sysinfo:
        print(
            "Arquitectura: %s  (OS version info disponible)" % mf.sysinfo.ProcessorArchitecture
        )

    # Sidecar del VEH (si existe junto al dump)
    sidecar_path = path + ".txt"
    if os.path.exists(sidecar_path):
        sidecar = {}
        with open(sidecar_path, encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    k, _, v = line.partition("=")
                    sidecar[k.strip()] = v.strip()
        print("\n=== SIDECAR VEH ===")
        for k, v in sidecar.items():
            print("  %-18s: %s" % (k, v))
        if tid is None and sidecar.get("thread_id"):
            try:
                tid = int(sidecar["thread_id"])
            except ValueError:
                pass

    # Excepción
    crash_tid = None
    if mf.exception and mf.exception.exception_records:
        print("\n=== EXCEPCIÓN ===")
        for rec in mf.exception.exception_records:
            er = rec.ExceptionRecord
            crash_tid = rec.ThreadId
            print(
                "  ThreadId        : 0x%08x (%d)"
                % (rec.ThreadId, rec.ThreadId)
            )
            print("  ExceptionCode   : 0x%08X (%s)" % (er.ExceptionCode_raw, er.ExceptionCode))
            print("  ExceptionFlags  : 0x%08X" % er.ExceptionFlags)
            print("  ExceptionAddress: 0x%016x" % er.ExceptionAddress)
            print("  Params          : %s" % ";".join("0x%x" % p for p in er.ExceptionInformation))

    if tid is not None:
        crash_tid = tid

    # Módulos
    print("\n=== MÓDULOS NATIVOS RELEVANTES ===")
    for mod in mf.modules.modules:
        name = mod.name.rsplit("\\", 1)[-1] if mod.name else "?"
        if any(name.lower().endswith(m.lower()) for m in INTERESTING_MODULES):
            print(
                "  %-28s base=0x%016x size=0x%x"
                % (name, mod.baseaddress, mod.size)
            )

    # Threads
    print("\n=== THREADS ===")
    for th in mf.threads.threads:
        ctx = getattr(th, "ContextObject", None)
        rip = getattr(ctx, "Rip", 0) if ctx else 0
        rsp = getattr(ctx, "Rsp", 0) if ctx else 0
        marker = "  <== CRASH" if th.ThreadId == crash_tid else ""
        print(
            "  tid=0x%08x (%6d)  Rip=%s  Rsp=%s%s"
            % (
                th.ThreadId,
                th.ThreadId,
                fmt_addr(rip, mf.modules.modules).split("  ")[-1],
                fmt_addr(rsp, mf.modules.modules).split("  ")[-1],
                marker,
            )
        )

    # Pila del thread que crasheó
    crash_thread = None
    for th in mf.threads.threads:
        th._mf_file = mf.file_handle  # noqa: SLF001
        if th.ThreadId == crash_tid:
            crash_thread = th
    if crash_thread is None:
        print("\nNo se pudo identificar el thread que crasheó (usa --tid <id> con el sidecar del VEH).")
        return 1

    rip, rsp, frames = walk_stack(crash_thread, mf.modules.modules)
    print("\n=== PILA DEL THREAD CRASH (tid=0x%08x) ===" % crash_thread.ThreadId)
    print("  Rip (IP en fallo): %s" % fmt_addr(rip, mf.modules.modules))
    print("  Rsp:               0x%016x" % rsp)
    print("  Frames candidatos (retorno):")
    if not frames:
        print("    (sin frames detectados en el rango capturado)")
    for addr, qword in frames:
        print("    [0x%016x] %s" % (addr, fmt_addr(qword, mf.modules.modules)))

    # Escaneo completo de qwords del stack dentro de los módulos de interés
    # (por si la caminata desde Rsp se pierde, p. ej. con dumps del VEH donde
    # Rsp es el stack de dispatch de la excepción y los frames reales del
    # crash quedan a direcciones más altas).
    if all_stack:
        print("\n=== SCAN STACK COMPLETO (solo módulos nativos) ===")
        stack = crash_thread.Stack
        start = stack.StartOfMemoryRange
        mf.file_handle.seek(stack.Rva)
        raw = mf.file_handle.read(stack.DataSize)
        seen = set()
        n = 0
        for idx in range(0, len(raw) - 7, 8):
            qword = struct.unpack_from("<Q", raw, idx)[0]
            mod = module_for(qword, mf.modules.modules)
            if mod and mod.name.rsplit("\\", 1)[-1].lower().endswith(("python311.dll", "_greenlet.pyd", "_overlapped.pyd", "Qt6Core.dll", "shiboken6.abi3.dll", "PySide6.abi3.dll", "libffi-8.dll", "ucrtbase.dll")):
                key = (mod.name, qword)
                if key in seen:
                    continue
                seen.add(key)
                rva = qword - mod.baseaddress
                print(
                    "  [0x%016x] 0x%016x  %s+0x%X"
                    % (start + idx, qword, mod.name.rsplit("\\", 1)[-1], rva)
                )
                n += 1
                if n > 120:
                    break
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    args = sys.argv[1:]
    path = args[0]
    tid = None
    if "--tid" in args:
        idx = args.index("--tid")
        try:
            tid = int(args[idx + 1], 0)
        except (ValueError, IndexError):
            tid = None
    sys.exit(analyze(path, "--all-stack" in args, tid))