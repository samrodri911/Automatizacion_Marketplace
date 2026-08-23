"""Gestión del navegador controlado por Playwright (API async).

Responsabilidades de este módulo (y solo estas):

- Lanzar Chromium de forma VISIBLE (headless=False, nunca cambiar esto).
- Usar un perfil persistente (`launch_persistent_context`) para que la
  sesión de Facebook sobreviva entre ejecuciones de la aplicación.
- Detectar si existe una sesión de Facebook iniciada.
- Esperar (sin bloquear indefinidamente el hilo de la GUI, ver
  `automation_service.py`) a que el usuario complete el login manual.

Este módulo NO sabe nada sobre productos, publicaciones ni la base de
datos: es una capa puramente de "control del navegador". La lógica
específica de Marketplace (buscar, eliminar, crear, publicar) vive en
otros módulos de `app/automation/`, que reciben la `Page` que este módulo
entrega (envuelta en `AsyncProxy` por `AutomationService`).

IMPORTANTE (sección 29 del spec): esta clase nunca debe:
- pedir o almacenar la contraseña de Facebook;
- exportar/leer cookies para robarlas;
- modificar fingerprint/señales del navegador para evadir detección;
- intentar resolver CAPTCHA de forma automática.

THREADING: todos los métodos son `async` y se ejecutan SIEMPRE en el hilo
del loop asyncio dedicado (`app.core.async_bridge.AsyncBridge`), NUNCA en
el hilo Qt del worker ni en el de la GUI.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from app.core import forensics
from app.core.config import BROWSER_PROFILE_DIR, facebook_config
from app.core.exceptions import BrowserLaunchError
from app.core.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class SessionStatus:
    """Resultado de comprobar si hay sesión de Facebook activa."""

    logged_in: bool
    detail: str


class BrowserManager:
    """Encapsula el ciclo de vida de Chromium + el contexto persistente.

    Uso típico (desde `AutomationService`, vía `AsyncBridge.submit`):

        page = await manager.start()
        status = await manager.check_facebook_session(page)
        ...
        await manager.stop()

    Todos los métodos deben ejecutarse en el hilo del loop asyncio dedicado.
    """

    def __init__(self, profile_dir: Path | None = None, headless: bool = False) -> None:
        if headless:
            # Está prohibido explícitamente por el diseño del proyecto
            # (sección 5 del spec): el usuario debe poder observar y, si
            # hace falta, intervenir en el navegador.
            raise BrowserLaunchError("El navegador debe ejecutarse siempre en modo visible (headless=False)")

        self.profile_dir = profile_dir or BROWSER_PROFILE_DIR
        self.headless = False

        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._node_pid: int | None = None  # PID cacheado del proceso Node (inmutable, thread-safe)
        self._running = False

    @property
    def node_pid(self) -> int | None:
        """PID del proceso Node de Playwright. Inmutable tras el arranque: thread-safe."""
        return self._node_pid

    @property
    def is_running(self) -> bool:
        """Flag simple de ciclo de vida (no toca objetos asyncio): thread-safe."""
        return self._running

    async def start(self) -> Page:
        """Lanza Chromium con perfil persistente y devuelve una página lista
        para navegar. Reutiliza una página existente del contexto si
        Chromium ya abrió una en blanco."""
        thread_id = threading.get_ident()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[Thread %s] Iniciando Playwright y contexto persistente en: %s", thread_id, self.profile_dir)

        try:
            forensics.evt("playwright.start", f"thread={thread_id}")
            self._playwright = await async_playwright().start()
            # Cachear el PID UNA SOLA VEZ al arrancar (momento seguro: el loop
            # no está procesando ninguna respuesta todavía). Es un int inmutable
            # que el heartbeat puede leer sin tocar ningún objeto asyncio.
            self._node_pid = forensics.extract_node_pid(self._playwright)
            forensics.evt("playwright.started", f"driver=pid:{self._node_pid}")
            forensics.evt("browser.launch", "launch_persistent_context")
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=False,
                viewport={"width": 1280, "height": 900},
                args=["--start-maximized"],
            )
            forensics.evt("browser.launched", f"ctx={id(self._context)} driver={forensics.driver_proc_info(self._playwright)}")
        except Exception as exc:  # Playwright lanza varias excepciones distintas al fallar el arranque
            logger.error("[Thread %s] No se pudo iniciar Chromium: %s", thread_id, exc)
            raise BrowserLaunchError(f"No se pudo iniciar Chromium: {exc}") from exc

        self._running = True
        page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        forensics.evt("page.new", f"page={id(page)} context={id(self._context)}")
        page.set_default_timeout(facebook_config.action_timeout_ms)
        page.set_default_navigation_timeout(facebook_config.navigation_timeout_ms)
        logger.info("[Thread %s] Chromium e interfaz iniciada (context_id=%s, page_id=%s)", thread_id, id(self._context), id(page))
        return page

    async def stop(self) -> None:
        """Cierra el contexto y Playwright de forma limpia y tolerante a EPIPE.

        Fix EPIPE (Iteración 4.2): el EPIPE aparece en el teardown, cuando el
        driver Node aún mantiene los WebSockets de la página de Facebook en
        vuelo y Python cierra el pipe. Para no dejar eventos pendientes que el
        driver intente escribir en un pipe ya cerrado, antes de cerrar cada
        página la navegamos a `about:blank` (desconecta el WebSocket) y damos
        un tick al bucle para que el driver evacúe los últimos mensajes.
        """
        thread_id = threading.get_ident()
        self._running = False
        logger.info("[Thread %s] Solicitada detención de Chromium (context_id=%s)", thread_id, id(self._context) if self._context else None)
        forensics.evt("browser.stop", f"thread={thread_id} driver=pid:{self._node_pid}")

        if self._context is not None:
            try:
                pages = list(self._context.pages)
                logger.info("[Thread %s] Cerrando %d página(s) individualmente para cleanup de IPC", thread_id, len(pages))
                for page in pages:
                    forensics.evt("page.close", f"page={id(page)} driver=pid:{self._node_pid}")
                    try:
                        await self._detach_page_websockets(page)
                        await page.close()
                    except Exception as page_exc:
                        logger.debug("[Thread %s] Ignorando error al cerrar página: %s", thread_id, page_exc)
            except Exception as exc:
                logger.debug("[Thread %s] No se pudieron enumerar las páginas al cerrar: %s", thread_id, exc)

        try:
            if self._context is not None:
                forensics.evt("context.close", f"driver=pid:{self._node_pid}")
                await self._context.close()
                logger.info("[Thread %s] Contexto de Chromium cerrado", thread_id)
        except Exception as exc:
            logger.warning("[Thread %s] Excepción durante cierre de contexto: %s", thread_id, exc)
        finally:
            self._context = None

        try:
            if self._playwright is not None:
                forensics.evt("playwright.stop", f"driver=pid:{self._node_pid}")
                await self._playwright.stop()
                logger.info("[Thread %s] Motor Playwright detenido", thread_id)
                forensics.evt("playwright.stopped", f"driver=pid:{self._node_pid}")
        except Exception as exc:
            logger.warning("[Thread %s] Excepción al detener Playwright: %s", thread_id, exc)
        finally:
            self._playwright = None
            self._node_pid = None

    async def _detach_page_websockets(self, page: Page) -> None:
        """Desconecta los WebSockets de una página antes de cerrarla.

        Navegar a `about:blank` hace que el navegador cierre limpio las
        conexiones de red pendientes (incluidos los WebSockets de Facebook).
        Esto evita que el driver Node envíe eventos de transporte cuando el
        pipe ya no es legible por Python (la causa del EPIPE en el teardown).
        """
        try:
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=3000)
            # Deja al driver drenar los últimos mensajes que generó la navegación.
            await page.wait_for_timeout(200)
        except Exception as exc:
            logger.debug("[Thread %s] No se pudo desconectar WebSockets de la página: %s", threading.get_ident(), exc)

    # -- Sesión de Facebook ---------------------------------------------------
    async def check_facebook_session(self, page: Page) -> SessionStatus:
        """Navega a Facebook y determina si hay una sesión iniciada.

        Estrategia: se apoya en indicadores semánticos (no en coordenadas
        ni en clases CSS generadas dinámicamente):
        - Si la URL redirige a una pantalla de login o aparecen campos de
          "Correo electrónico o teléfono" / "Contraseña", NO hay sesión.
        - Si aparece un elemento de navegación propio de la app logueada
          (rol "banner"/"navigation", o el buscador de Facebook), SÍ hay
          sesión.
        """
        await page.goto(facebook_config.base_url, wait_until="domcontentloaded")

        try:
            await page.wait_for_load_state("networkidle", timeout=facebook_config.session_check_timeout_ms)
        except Exception:
            # networkidle puede no llegar nunca en Facebook (websockets,
            # polling, etc). No es un error: seguimos con la comprobación
            # basada en elementos visibles.
            pass

        login_email_field = page.get_by_label("Correo electrónico o número de teléfono móvil").or_(
            page.get_by_placeholder("Correo electrónico o teléfono")
        )
        login_password_field = page.get_by_label("Contraseña")

        try:
            has_login_form = await login_email_field.first.is_visible(timeout=2000) or await login_password_field.first.is_visible(
                timeout=1000
            )
        except Exception:
            has_login_form = False

        if has_login_form:
            logger.info("Sesión de Facebook NO detectada (formulario de login visible)")
            return SessionStatus(logged_in=False, detail="Formulario de inicio de sesión visible")

        # Indicadores de sesión iniciada: navegación principal de Facebook.
        main_nav = page.get_by_role("navigation").first
        try:
            logged_in = await main_nav.is_visible(timeout=3000)
        except Exception:
            logged_in = False

        if logged_in:
            logger.info("Sesión de Facebook detectada")
            return SessionStatus(logged_in=True, detail="Navegación principal visible")

        logger.info("No se pudo determinar el estado de sesión con certeza")
        return SessionStatus(logged_in=False, detail="No se detectaron indicadores claros de sesión")

    async def wait_for_login_indicator(self, page: Page, poll_interval_ms: int = 2000) -> bool:
        """Comprueba una vez (no bloqueante en bucle infinito) si el login
        manual ya se completó. Pensado para ser llamado repetidamente desde
        un QThread con pausas cortas y cancelables, no con un sleep largo.
        """
        status = await self.check_facebook_session(page)
        return status.logged_in