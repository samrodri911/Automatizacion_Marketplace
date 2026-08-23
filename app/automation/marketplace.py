"""Adaptador de la UI de Facebook Marketplace para Playwright.

Este módulo es la capa que conoce los detalles de cómo se ve Facebook
Marketplace y solo esto. Recibe la `Page` que entrega `BrowserManager`
(no crea su propio navegador) y expone operaciones de granularidad media:

    open_marketplace(page)       -> navega a la home de Marketplace.
    open_your_listings(page)     -> navega a "Tus publicaciones".
    wait_for_listings(page)      -> espera (con esperas explícitas de
                                    Playwright, nunca sleep()) a que la
                                    sección de publicaciones cargue.

La detección de "qué hay en la página" se delega en `selectors.py`, que
es lógica pura y testeable sin navegador real.

IMPORTANTE (reglas invariantes del proyecto):
- Nunca resuelve/evade CAPTCHA ni verificaciones de seguridad: si aparece
  una pantalla de login o "challenge", expone `requires_intervention()`
  para que la capa de servicios pause en WAITING_USER.
- Nunca intenta ocultar la automatización (el navegador sigue visible).
- Usa esperas explícitas (get_by_role / get_by_text / wait_for con
  timeout) y nunca coordenadas de mouse ni sleep() convencional.
- No toca la base de datos ni los productos: es solo navegación.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from playwright.sync_api import Page

from app.automation import selectors
from app.core import forensics
from app.core.config import facebook_config, search_limits
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Marcas de una página de login/verificación que NO debemos resolver de
# forma automática: si aparecen durante la navegación, la capa de servicios
# debe pausar en WAITING_USER y esperar al usuario.
_INTERVENTION_TOKENS: tuple[str, ...] = (
    "Inicia sesión",
    "Log in",
    "Introduce el código",
    "Enter the code",
    "reCAPTCHA",
    "Verifica que eres humano",
    "Verify your identity",
)


@dataclass
class MarketplaceResult:
    """Resultado de una operación de navegación dentro de Marketplace."""

    ok: bool
    detail: str


class MarketplaceAdapter:
    """Operaciones de navegación sobre la UI de Facebook Marketplace.

    No es thread-safe: debe usarse desde el mismo hilo (el QThread de
    automatización) que creó la `Page`.
    """

    def __init__(self, page: Page) -> None:
        self._page = page
        self._config = facebook_config

    # ------------------------------------------------------------------ #
    # Marketplace (entrada)
    # ------------------------------------------------------------------ #
    def open_marketplace(self) -> MarketplaceResult:
        """Navega a la entrada de Marketplace (marketplace_url).

        No se hace clic en la barra de navegación: la URL es estable para
        esta estructura y evita depender de dónde coloque Facebook el
        enlace. Tras `goto` se espera la carga y se valida la señal débil
        de "estamos en Marketplace".
        """
        logger.info("Navegando a Marketplace")
        forensics.evt("goto", self._config.marketplace_url)
        self._page.goto(
            self._config.marketplace_url,
            wait_until="domcontentloaded",
            timeout=self._config.navigation_timeout_ms,
        )
        self._settle()

        ok = self.is_marketplace_ready()
        detail = "Marketplace cargó" if ok else "No se detectó Marketplace en la página"
        logger.info("Resultado entrada Marketplace: ok=%s", ok)
        return MarketplaceResult(ok=ok, detail=detail)

    def is_marketplace_ready(self) -> bool:
        url = self._page.url
        snippets = self._collect_snippets()
        return selectors.find_marketplace_signal(url, snippets)

    # ------------------------------------------------------------------ #
    # Tus publicaciones
    # ------------------------------------------------------------------ #
    def ensure_listings_section(self) -> None:
        """Garantiza que la página está en la sección "Tus publicaciones".

        Si la URL actual ya apunta a `/marketplace/you/selling`, no se
        re-navega (evita recargar la página innecesariamente). En caso
        contrario llama a `open_your_listings()`.
        """
        current_url = self._page.url or ""
        if selectors.YOUR_LISTINGS_URL_PATTERN.search(current_url):
            return
        self.open_your_listings()

    def scroll_feed(self, step_px: int | None = None) -> bool:
        """Hace avanzar el scroll del feed de publicaciones una vez.

        Devuelve True si la posición de scroll cambió (hay más contenido
        potencial) o False si no se movió (fin del feed o página no
        scrolleable). Es una barra de límite para `ListingFinder`:
        nunca permite scroll infinito porque el propio finder controla
        `max_scrolls`, el timeout y la ausencia de contenido nuevo.

        Implementación: `window.scrollBy` del navegador (scroll de
        página, NO clics en coordenadas de ratón), seguida de una espera
        explícita acotada.
        """
        step_px = step_px or search_limits.scroll_step_px
        try:
            forensics.evt("evaluate", "scrollBy")
            before = self._page.evaluate("() => window.scrollY")
            self._page.evaluate("(step) => window.scrollBy(0, step)", step_px)
            self._settle(timeout_ms=facebook_config.action_timeout_ms)
            after = self._page.evaluate("() => window.scrollY")
            moved = after > before
            if not moved:
                logger.info("El feed no avanzó al hacer scroll (fin de contenido)")
            return moved
        except Exception as exc:
            logger.warning("No se pudo hacer scroll del feed: %s", exc)
            return False

    def open_your_listings(self) -> selectors.ListingsSectionState:
        """Navega a "Tus publicaciones" (/marketplace/you/selling) y espera
        a que la sección cargue. Devuelve el estado de la sección."""
        logger.info("Navegando a 'Tus publicaciones'")
        forensics.evt("goto", self._config.your_listings_url)
        self._page.goto(
            self._config.your_listings_url,
            wait_until="domcontentloaded",
            timeout=self._config.navigation_timeout_ms,
        )
        self._settle()
        self.wait_for_listings()
        return self.listings_section_state()

    def wait_for_listings(self, timeout_ms: int | None = None) -> bool:
        """Espera, con esperas explícitas de Playwright (nunca sleep()
        arbitrario), a que la sección de publicaciones aparezca.

        Se recoge el estado a intervalos; entre cada intento se espera a
        `domcontentloaded` con el tiempo restante (espera real del
        navegador, no un `time.sleep`).
        """
        timeout_ms = timeout_ms or self._config.action_timeout_ms
        deadline = time.monotonic() + timeout_ms / 1000.0

        state = self.listings_section_state()
        while not state.found:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                self._page.wait_for_load_state("domcontentloaded", timeout=min(2000, remaining * 1000))
            except Exception:
                pass
            state = self.listings_section_state()

        if state.found:
            logger.info("Sección 'Tus publicaciones' cargada (reason: %s)", state.reason)
        else:
            logger.warning("No se detectó la sección 'Tus publicaciones' (reason: %s)", state.reason)
        return state.found

    def listings_section_state(self) -> selectors.ListingsSectionState:
        """Recoge la URL y los fragmentos de texto visibles y los clasifica
        con la lógica pura de `selectors.classify_listings_section`."""
        url = self._page.url
        snippets = self._collect_snippets()
        state = selectors.classify_listings_section(url, snippets)
        logger.info(
            "Detector de 'Tus publicaciones': found=%s url_matches=%s reason=%r",
            state.found,
            state.url_matches,
            state.reason,
        )
        return state

    # ------------------------------------------------------------------ #
    # Intervención manual
    # ------------------------------------------------------------------ #
    def requires_intervention(self) -> bool:
        """Devuelve True si la página actual parece pedir una acción manual
        (login, CAPTCHA, verificación de seguridad).

        Es una señal para la capa de servicios: en ese caso se debe pausar
        en WAITING_USER y NUNCA intentar resolverla automáticamente.
        """
        try:
            forensics.evt("locator", "body.inner_text")
            page_text = self._page.locator("body").inner_text(timeout=1_500) or ""
        except Exception:
            return False

        lowered = page_text.casefold()
        return any(token.casefold() in lowered for token in _INTERVENTION_TOKENS)

    # ------------------------------------------------------------------ #
    # Internos
    # ------------------------------------------------------------------ #
    def _collect_snippets(self) -> list[str]:
        """Recoge fragmentos de texto visibles que permiten clasificar la
        sección: headings, tabs y barra de navegación. Se limita a lo
        estrictamente necesario para no recorrer todo el DOM.

        Usa page.evaluate() atómico en 1 sola llamada IPC para evitar saturar el pipe.
        """
        snippets: list[str] = []

        try:
            js_script = """
            () => {
                const textList = [];
                const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, [role="heading"]'));
                for (const h of headings) {
                    const t = (h.innerText || h.textContent || '').trim();
                    if (t) textList.push(t);
                }
                const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
                for (const tab of tabs) {
                    const t = (tab.innerText || tab.textContent || '').trim();
                    if (t) textList.push(t);
                }
                const nav = document.querySelector('[role="navigation"]');
                if (nav) {
                    const t = (nav.innerText || nav.textContent || '').trim();
                    if (t) textList.push(t);
                }
                return textList;
            }
            """
            raw_list = self._page.evaluate(js_script)
            if isinstance(raw_list, list) and raw_list:
                return [str(s).strip() for s in raw_list if s and str(s).strip()]
        except Exception as exc:
            logger.debug("Evaluación JS atómica de snippets devolvió excepción (usando fallback): %s", exc)

        # Fallback a locators para Mocks de tests
        try:
            forensics.evt("locator", "headings.all")
            headings = self._page.get_by_role("heading")
            for heading in headings.all():
                text = heading.text_content()
                if text:
                    snippets.append(str(text).strip())
        except Exception:
            pass

        try:
            tabs = self._page.get_by_role("tab")
            for tab in tabs.all():
                text = tab.text_content()
                if text:
                    snippets.append(str(text).strip())
        except Exception:
            pass

        try:
            nav = self._page.get_by_role("navigation").first
            if nav and nav.is_visible():
                text = nav.text_content()
                if text:
                    snippets.append(str(text).strip())
        except Exception:
            pass

        return [s for s in snippets if s]

    def _settle(self, timeout_ms: int | None = None) -> None:
        """Espera a que la página asiente de forma acotada y sin sleep.
        Usa esperas de load_state de Playwright; si no llega nunca (websockets
        y polling de Facebook), se continúa sin error."""
        try:
            self._page.wait_for_load_state("networkidle", timeout=timeout_ms or 3_000)
        except Exception:
            pass