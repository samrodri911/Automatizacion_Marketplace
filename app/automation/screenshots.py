"""Capturas de pantalla de diagnóstico.

Solo se guardan capturas cuando aportan valor para depurar (Facebook
cambió la interfaz, error, ambigüedad, límite alcanzado...), nunca de
forma indiscriminada en operaciones exitosas.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.core.config import SCREENSHOTS_DIR
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def save_screenshot(page, tag: str, kind: str = "listing_search") -> str | None:
    """Guarda una captura en `screenshots/<kind>_<tag>_<ts>.png`.

    `page` puede ser una Page de Playwright o un objeto con `.screenshot()`.
    Devuelve la ruta absoluta o None si falló.
    """
    try:
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        safe_tag = re.sub(r"[^a-zA-Z0-9_-]", "_", tag)[:40] or "untagged"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{kind}_{safe_tag}_{timestamp}.png"
        path = SCREENSHOTS_DIR / filename
        page.screenshot(path=str(path))
        logger.info("Screenshot guardado: %s", path)
        return str(path)
    except Exception as exc:
        logger.warning("No se pudo guardar el screenshot: %s", exc)
        return None