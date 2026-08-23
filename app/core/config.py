"""Configuración centralizada de Marketplace Manager.

Todas las rutas de la aplicación se derivan de BASE_DIR para que la app
funcione igual tanto ejecutada como script (`python main.py`) como
empaquetada con PyInstaller (`MarketplaceManager.exe`).

No debe haber rutas absolutas hardcodeadas en ningún otro módulo: todo
el resto del código debe importar las rutas desde aquí.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _resolve_base_dir() -> Path:
    """Determina el directorio base de la app.

    - En ejecución normal (python main.py): la carpeta del proyecto.
    - Empaquetado con PyInstaller (--onefile): la carpeta donde está el
      .exe, NO la carpeta temporal de extracción (sys._MEIPASS), porque
      necesitamos que data/ logs/ screenshots/ persistan entre ejecuciones.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # app/core/config.py -> app/core -> app -> raíz del proyecto
    return Path(__file__).resolve().parent.parent.parent


BASE_DIR: Path = _resolve_base_dir()

# Configurar la ruta de los navegadores de Playwright.
# En modo empaquetado, apuntamos al directorio temporal de extracción (sys._MEIPASS)
# que contiene la carpeta 'playwright_browsers'.
# En desarrollo, apuntamos a la carpeta 'playwright_browsers' de la raíz del proyecto.
if getattr(sys, "frozen", False):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(sys._MEIPASS) / "playwright_browsers")
else:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BASE_DIR / "playwright_browsers")

DATA_DIR: Path = BASE_DIR / "data"
PRODUCTS_DIR: Path = DATA_DIR / "products"
DB_PATH: Path = DATA_DIR / "marketplace.db"

LOGS_DIR: Path = BASE_DIR / "logs"
SCREENSHOTS_DIR: Path = BASE_DIR / "screenshots"

# Perfil persistente de Chromium controlado por Playwright. Aquí vive la
# sesión de Facebook (cookies, localStorage, etc). Nunca se sube a git.
BROWSER_PROFILE_DIR: Path = DATA_DIR / "browser_profile"


@dataclass(frozen=True)
class FacebookConfig:
    """Constantes relativas a Facebook / Marketplace."""

    base_url: str = "https://www.facebook.com/"
    marketplace_url: str = "https://www.facebook.com/marketplace/"
    your_listings_url: str = "https://www.facebook.com/marketplace/you/selling"
    create_listing_url: str = "https://www.facebook.com/marketplace/create/item"

    # Tiempos de espera "razonables" para condiciones de red/UI de Facebook.
    # Se usan como timeout MÁXIMO de las esperas explícitas de Playwright,
    # nunca como sleep() incondicional.
    navigation_timeout_ms: int = 30_000
    action_timeout_ms: int = 15_000
    session_check_timeout_ms: int = 10_000


@dataclass(frozen=True)
class AppConfig:
    """Configuración general de la aplicación."""

    app_name: str = "Marketplace Manager"
    organization_name: str = "MarketplaceManager"

    # Modo simulación: si está activo, la automatización debe navegar y
    # rellenar formularios pero NUNCA ejecutar la acción irreversible
    # final (eliminar / publicar). Ver services/automation_service.py.
    simulation_mode_default: bool = False

    # Procesamiento secuencial, nunca en paralelo (ver sección 13 del spec).
    sequential_processing: bool = True


@dataclass(frozen=True)
class SearchLimits:
    """Límites estrictos de la búsqueda de publicaciones (ver Iteración 3).

    El buscador NUNCA ejecuta un scroll infinito: se detiene por
    `max_scrolls`, por `search_timeout_ms` o cuando deja de aparecer
    contenido nuevo. La ausencia de resultados nuevos también detiene el
    barrido.
    """

    # Número máximo de operaciones de scroll en la sección "Tus publicaciones".
    max_scrolls: int = 30
    # Píxeles que avanza cada operación de scroll (ventana del navegador).
    scroll_step_px: int = 900
    # Espera máxima (ms) después de cada scroll esperando que asiente la página.
    scroll_wait_ms: int = 2_000
    # Límite global de tiempo de una búsqueda (ms). Ajustado a consumidores
    # domésticos de Marketplace (el feed de publicaciones propias es corto).
    search_timeout_ms: int = 120_000


facebook_config = FacebookConfig()
app_config = AppConfig()
search_limits = SearchLimits()


def ensure_directories() -> None:
    """Crea todos los directorios necesarios si no existen.

    Debe llamarse una única vez al arrancar la aplicación (main.py),
    antes de inicializar la base de datos o el logging.
    """
    for directory in (DATA_DIR, PRODUCTS_DIR, LOGS_DIR, SCREENSHOTS_DIR, BROWSER_PROFILE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
