"""Configuración de logging estructurado.

Formato de salida (ver sección 26 del spec):

    [19:31:02] INFO  Iniciando navegador

Los logs se escriben simultáneamente en consola y en un archivo diario
dentro de LOGS_DIR, para poder depurar ejecuciones pasadas (por ejemplo
cuando Facebook cambió algo y una automatización falló ayer).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.core.config import LOGS_DIR

_CONSOLE_FORMAT = "[%(asctime)s] %(levelname)-5s %(name)-22s %(message)s"
_FILE_FORMAT = "[%(asctime)s] %(levelname)-5s %(name)-22s %(message)s"
_TIME_FORMAT = "%H:%M:%S"

_configured = False


def configure_logging(level: int = logging.INFO) -> Path:
    """Configura el logging raíz de la aplicación.

    Idempotente: llamarla varias veces no duplica handlers.
    Devuelve la ruta del archivo de log de la sesión actual.
    """
    global _configured

    log_filename = f"{datetime.now():%Y-%m-%d}.log"
    log_path = LOGS_DIR / log_filename

    if _configured:
        return log_path

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt=_TIME_FORMAT))
    root_logger.addHandler(console_handler)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FILE_FORMAT, datefmt=_TIME_FORMAT))
    root_logger.addHandler(file_handler)

    _configured = True
    return log_path


def get_logger(name: str) -> logging.Logger:
    """Atajo para obtener un logger con nombre de módulo."""
    return logging.getLogger(name)
