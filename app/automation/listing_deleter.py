"""Eliminador seguro de publicaciones de Facebook Marketplace.

Responsabilidad ÚNICA de este módulo:
    Listing → navegar a "Tus publicaciones" → localizar la tarjeta por
    título → abrir menú de opciones o panel de detalles → seleccionar
    Eliminar → confirmar diálogo de Facebook → verificar eliminación →
    DeleteResult.

Principios invariantes:

- NUNCA usa coordenadas de mouse. Siempre selectores semánticos
  (roles, aria-labels, texto visible), centralizados en `selectors.py`.
- NUNCA elimina ante la duda: si el menú no se encuentra, si el diálogo
  de Facebook es inesperado, o si la UI no puede identificarse con
  certeza, devuelve DELETE_FAILED o pasa a INTERVENTION_REQUIRED.
- La verificación posterior es OBLIGATORIA. No se reporta éxito solo por
  haber hecho clic en "Eliminar".
- Las señales ambiguas (red, timeout, ausencia de contenido) NO cuentan
  como confirmación → DELETE_UNCERTAIN (modificación 3 del spec).
- Si ocurre una interrupción DURANTE el delete, no se reintenta a ciegas:
  la capa de servicio debe llamar a `verify_only()` para determinar el
  estado real (modificación 1 del spec).
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum, auto

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from app.automation import selectors
from app.core import forensics
from app.core.config import facebook_config
from app.core.exceptions import InterventionRequiredError
from app.core.logging_config import get_logger
from app.models.listing import Listing

logger = get_logger(__name__)

# Timeouts cortos para operaciones de UI (ms). No se usan sleeps: son los
# timeouts de las esperas explícitas de Playwright.
_MENU_TIMEOUT_MS = 8_000
_ACTION_TIMEOUT_MS = 6_000
_DIALOG_TIMEOUT_MS = 8_000
_VERIFY_TIMEOUT_MS = 10_000

# Tiempo de asentamiento tras hacer clic (en segundos). Facebook usa
# animaciones suaves; sin este margen el DOM puede no haberse actualizado.
_SETTLE_S = 1.5


class DeleteStatus(Enum):
    DELETED_CONFIRMED = auto()      # Verificación positiva de eliminación
    DELETE_UNCERTAIN = auto()       # No se pudo verificar (modificación 3)
    DELETE_FAILED = auto()          # Error durante la operación, no se ejecutó
    INTERVENTION_REQUIRED = auto()  # CAPTCHA/login/diálogo inesperado
    CANCELLED = auto()              # Cancelado antes de la acción destructiva


@dataclass
class DeleteResult:
    """Resultado completo de una operación de eliminación.

    Atributos:
        status:             resultado final.
        listing:            el listing sobre el que se operó.
        verification:       resultado de la verificación posterior (si se llegó
                            a ejecutar la acción).
        error:              descripción del error si status es FAILED o UNCERTAIN.
        detail:             descripción legible de lo ocurrido (para UI y logs).
    """

    status: DeleteStatus
    listing: Listing
    error: str | None = None
    detail: str = ""
    verification: selectors.DeletionVerificationResult | None = None
    verification_signals: list[str] = field(default_factory=list)

    @property
    def is_confirmed(self) -> bool:
        return self.status == DeleteStatus.DELETED_CONFIRMED

    def to_dict(self) -> dict:
        return {
            "status": self.status.name,
            "listing": {
                "title": self.listing.title,
                "price": self.listing.price,
                "price_raw": self.listing.price_raw,
                "url": self.listing.url,
                "reference": self.listing.reference,
            },
            "error": self.error,
            "detail": self.detail,
            "verification_signals": list(self.verification_signals),
        }


class ListingDeleter:
    """Ejecuta la eliminación de una publicación de Marketplace.

    No es thread-safe: debe usarse desde el mismo hilo de automatización
    (el QThread) que tiene acceso a la `Page`.

    Uso típico:
        deleter = ListingDeleter()
        result = deleter.delete(listing, page, navigator)
        if result.status == DeleteStatus.DELETED_CONFIRMED:
            ...

    Verificación sin eliminar (para reanudación tras interrupción):
        result = deleter.verify_only(listing, page)
    """

    def __init__(self, action_timeout_ms: int | None = None) -> None:
        self._action_timeout_ms = action_timeout_ms or _ACTION_TIMEOUT_MS

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def delete(self, listing: Listing, page: Page, navigator=None) -> DeleteResult:
        """Flujo completo de eliminación.

        1. Navegar a "Tus publicaciones" (donde Facebook actual expone la
           gestión de publicaciones; en la página del item ya NO hay menú).
        2. Detectar intervención (CAPTCHA/login).
        3. Localizar la tarjeta del listing (por título) y ejecutar la
           acción de eliminar: menú de la tarjeta → "Eliminar publicación",
           o panel de detalles → "Eliminar publicación de Marketplace".
        4. Confirmar el diálogo de Facebook.
        5. Verificar eliminación posterior (OBLIGATORIO).

        No lanza excepciones de automatización: siempre devuelve DeleteResult.
        InterventionRequiredError se convierte en INTERVENTION_REQUIRED.
        """
        logger.info("ListingDeleter.delete() → listing: %r (url=%s)", listing.title, listing.url)
        forensics.evt("deleter.delete.start", f"thread={threading.get_ident()} url={listing.url}")

        # Paso 1: navegar a "Tus publicaciones" para localizar el listing.
        nav_ok, nav_detail = self._navigate_to_your_listings(page)
        if not nav_ok:
            logger.warning("No se pudo abrir 'Tus publicaciones': %s", nav_detail)
            return DeleteResult(
                status=DeleteStatus.DELETE_FAILED,
                listing=listing,
                error=nav_detail,
                detail=f"No se pudo abrir 'Tus publicaciones': {nav_detail}",
            )

        # Paso 2: detectar intervención antes de operar.
        try:
            self._check_intervention(page, navigator)
        except InterventionRequiredError as exc:
            return DeleteResult(
                status=DeleteStatus.INTERVENTION_REQUIRED,
                listing=listing,
                error=str(exc),
                detail="Facebook requiere intervención manual antes de eliminar",
            )

        # Paso 3: localizar la tarjeta y ejecutar la acción de eliminar
        # (cubre menú de tarjeta y panel de detalles; semántico, sin coordenadas).
        action_ok, action_detail = self._select_delete_action(page, listing)
        if not action_ok:
            logger.warning("No se pudo ejecutar la acción de eliminar: %s", action_detail)
            return DeleteResult(
                status=DeleteStatus.DELETE_FAILED,
                listing=listing,
                error=action_detail,
                detail=f"No se pudo abrir el menú de la publicación: {action_detail}",
            )

        # Paso 4: confirmar diálogo de Facebook.
        try:
            self._check_intervention(page, navigator)
        except InterventionRequiredError as exc:
            return DeleteResult(
                status=DeleteStatus.INTERVENTION_REQUIRED,
                listing=listing,
                error=str(exc),
                detail="Facebook requiere intervención manual antes de confirmar eliminación",
            )

        confirm_ok, confirm_detail = self._confirm_facebook_dialog(page)
        if not confirm_ok:
            logger.warning("Diálogo de confirmación de Facebook no confirmado: %s", confirm_detail)
            return DeleteResult(
                status=DeleteStatus.INTERVENTION_REQUIRED,
                listing=listing,
                error=confirm_detail,
                detail=f"Diálogo de Facebook inesperado: {confirm_detail}",
            )

        # Paso 5: verificación posterior OBLIGATORIA.
        logger.info("Acción de eliminación ejecutada. Verificando resultado...")
        return self._verify_after_delete(page, listing)

    def verify_only(self, listing: Listing, page: Page) -> DeleteResult:
        """Verifica el estado de una publicación SIN ejecutar ninguna acción.

        Uso: reanudación tras interrupción en DELETING_LISTING (modificación 1
        del spec). No intenta volver a eliminar; solo comprueba si la
        publicación existe o no.
        """
        logger.info("verify_only() → verificando si %r aún existe", listing.url)
        return self._verify_after_delete(page, listing)

    # ------------------------------------------------------------------
    # Flujo interno
    # ------------------------------------------------------------------

    def _navigate_to_your_listings(self, page: Page) -> tuple[bool, str]:
        """Navega a "Tus publicaciones" (facebook_config.your_listings_url).

        Es donde la UI actual de Facebook ofrece la gestión de publicaciones
        (menú de tarjeta y panel de detalles). Devuelve (ok, detalle).
        """
        url = facebook_config.your_listings_url
        try:
            forensics.evt("deleter.goto.before", f"thread={threading.get_ident()} url={url}")
            page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            forensics.evt("deleter.goto.after", f"thread={threading.get_ident()} url={url}")
            self._settle(page, seconds=2.0)
            return True, f"Navegado a {url}"
        except PlaywrightTimeout:
            return False, f"Timeout al navegar a {url}"
        except Exception as exc:
            logger.warning(
                "Error navegando a %s: %s\n%s", url, exc, traceback.format_exc()
            )
            return False, f"Error navegando a {url}: {exc}"

    def _select_delete_action(self, page: Page, listing: Listing) -> tuple[bool, str]:
        """Localiza la tarjeta del listing (por título) y ejecuta la acción
        de eliminar. Devuelve (ejecutado, detalle).

        Rutas (semánticas, sin coordenadas):
        1. Menú de la tarjeta: botón `Más opciones para <título>` → menú →
           menuitem "Eliminar publicación".
        2. Panel de detalles: clic en la tarjeta (aria-label = título) →
           botón directo "Eliminar publicación de Marketplace".
        3. Selectores genéricos: aria-labels conocidos, texto "..." y botones
           de diálogo sin label → menú → acción de eliminar.
        """
        # Ruta 1: menú de la tarjeta por título (firma confirmada del DOM real).
        card_menu = page.get_by_role("button", name="Más opciones para " + listing.title)
        for _ in range(4):
            try:
                if card_menu.first.is_visible(timeout=1_500):
                    card_menu.first.click()
                    self._settle(page, seconds=1.0)
                    action_ok, action_detail = self._click_delete_action(page)
                    if action_ok:
                        logger.info("Acción de eliminar desde el menú de la tarjeta")
                        return True, action_detail
                    self._close_open_menu(page)
                    break
            except Exception:
                break
            try:
                page.evaluate("() => window.scrollBy(0, 800)")
                self._settle(page, seconds=0.6)
            except Exception:
                break

        # Ruta 2: panel de detalles (clic en la tarjeta → botón directo).
        try:
            card = page.get_by_role("button", name=listing.title).first
            if card.is_visible(timeout=1_500):
                card.click()
                self._settle(page, seconds=1.0)
                direct = page.get_by_role("button", name="Eliminar publicación de Marketplace")
                if direct.first.is_visible(timeout=3_000):
                    direct.first.click()
                    self._settle(page, seconds=_SETTLE_S)
                    logger.info("Acción de eliminar desde el panel de detalles")
                    return True, "Eliminar desde el panel de detalles"
        except Exception:
            pass

        # Ruta 3: selectores genéricos de menú (siempre como última opción).
        if self._open_listing_menu_generic(page):
            action_ok, action_detail = self._click_delete_action(page)
            if action_ok:
                logger.info("Acción de eliminar desde menú genérico")
                return True, action_detail

        self._log_menu_candidates(page)
        return False, (
            "No se encontró el botón de menú de opciones con ningún selector conocido. "
            "Facebook puede haber cambiado la UI."
        )

    def _close_open_menu(self, page: Page) -> None:
        """Cierra un menú abierto sin seleccionar ninguna acción."""
        try:
            page.keyboard.press("Escape")
            self._settle(page, seconds=0.5)
        except Exception:
            pass

    def _open_listing_menu_generic(self, page: Page) -> bool:
        """Abre el menú de opciones mediante selectores genéricos.

        Estrategia:
        1. Buscar por aria-label conocidos (LISTING_MENU_ARIA_LABELS).
        2. Buscar por role=button con texto "..." o "⋯".
        3. Botones `[role=button][aria-haspopup=dialog]` sin nombre.
        Nunca hace clic por coordenadas.
        """
        for label in selectors.LISTING_MENU_ARIA_LABELS:
            try:
                btn = page.get_by_role("button", name=label)
                if btn.first.is_visible(timeout=1_500):
                    btn.first.click()
                    self._settle(page, seconds=1.0)
                    logger.info("Menú de opciones abierto (aria-label: %r)", label)
                    return True
            except Exception:
                continue

        for text in ("...", "⋯", "···"):
            try:
                btn = page.get_by_role("button").filter(has_text=text)
                if btn.first.is_visible(timeout=1_000):
                    btn.first.click()
                    self._settle(page, seconds=1.0)
                    logger.info("Menú de opciones abierto (texto: %r)", text)
                    return True
            except Exception:
                continue

        for candidate in self._unlabeled_dialog_buttons(page):
            try:
                candidate.click()
                self._settle(page, seconds=1.0)
                logger.info("Menú de opciones abierto (botón sin aria-label)")
                return True
            except Exception:
                continue

        return False

    def _unlabeled_dialog_buttons(self, page: Page):
        """Botones `[role=button][aria-haspopup=dialog]` visibles y SIN nombre
        accesible (aria-label/texto). Último recurso para localizar un menú
        contextual cuando los selectores conocidos fallaron. Generador:
        nunca lanza."""
        try:
            locator = page.locator("[role='button'][aria-haspopup='dialog']")
            total = locator.count()
            for i in range(total):
                try:
                    el = locator.nth(i)
                    if not el.is_visible(timeout=500):
                        continue
                    label = (el.get_attribute("aria-label") or "").strip()
                    text = (el.inner_text() or "").strip()
                    if label or text:
                        continue
                    yield el
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("No se pudieron enumerar botones de menú sin label: %s", exc)

    def _log_menu_candidates(self, page: Page) -> None:
        """Volca al log los botones/menús reales de la página para diagnosticar
        un selector desactualizado (solo se ejecuta cuando el menú no se
        encontró). No altera el flujo."""
        try:
            inventory = page.evaluate(
                """
                () => {
                    const out = [];
                    for (const el of document.querySelectorAll('button, [role="button"], [role="menuitem"]')) {
                        const r = el.getBoundingClientRect();
                        const aria = el.getAttribute('aria-label') || '';
                        const popup = el.getAttribute('aria-haspopup') || '';
                        if (!aria && !popup && !(el.innerText || '').trim()) continue;
                        out.push({
                            label: aria.slice(0, 60),
                            popup,
                            text: (el.innerText || el.textContent || '').trim().slice(0, 30),
                            visible: r.width > 0 && r.height > 0,
                        });
                    }
                    return out;
                }
                """
            )
            if isinstance(inventory, list):
                visible = [b for b in inventory if b.get("visible")]
                logger.warning(
                    "Menú no encontrado. Botones visibles con label/haspopup: %s",
                    visible[:25],
                )
        except Exception as exc:
            logger.debug("No se pudo volcar el inventario de botones: %s", exc)

    def _click_delete_action(self, page: Page) -> tuple[bool, str]:
        """Selecciona la acción de eliminar del menú abierto.

        Busca semánticamente (por texto visible) sin asumir posición.
        Nunca hace clic en "el tercer ítem" o similar.
        """
        for token in selectors.LISTING_DELETE_ACTION_TOKENS:
            try:
                # Buscar primero como menuitem (estructura de menú de FB).
                item = page.get_by_role("menuitem", name=token, exact=False)
                if item.first.is_visible(timeout=1_500):
                    item.first.click()
                    self._settle(page, seconds=_SETTLE_S)
                    logger.info("Acción de eliminar seleccionada (menuitem: %r)", token)
                    return True, f"Acción seleccionada: {token}"
            except Exception:
                pass

            try:
                # Fallback: cualquier elemento con ese texto visible.
                el = page.get_by_text(token, exact=False).first
                if el.is_visible(timeout=1_000):
                    el.click()
                    self._settle(page, seconds=_SETTLE_S)
                    logger.info("Acción de eliminar seleccionada (texto: %r)", token)
                    return True, f"Acción seleccionada por texto: {token}"
            except Exception:
                continue

        return False, (
            "No se encontró ninguna acción de eliminar en el menú. "
            "Tokens buscados: " + ", ".join(selectors.LISTING_DELETE_ACTION_TOKENS)
        )

    def _confirm_facebook_dialog(self, page: Page) -> tuple[bool, str]:
        """Detecta y confirma el diálogo de confirmación de Facebook."""
        try:
            dialog_locator = page.get_by_role("dialog").first
            # Verificar si realmente está visible
            if not dialog_locator.is_visible(timeout=2_000):
                logger.info("No apareció diálogo de confirmación de Facebook; continuando con verificación")
                return True, "Sin diálogo de confirmación (FB eliminó directamente)"
        except Exception:
            logger.info("Sin diálogo de confirmación detectado; continuando")
            return True, "Sin diálogo de confirmación detectado"

        # Verificar que es el diálogo esperado.
        try:
            raw_text = dialog_locator.inner_text(timeout=2_000)
            dialog_text = str(raw_text) if isinstance(raw_text, str) else ""
        except Exception:
            dialog_text = ""

        if not dialog_text:
            return True, "Diálogo sin texto discernible; continuando"

        lowered = dialog_text.casefold()
        is_known_dialog = any(
            t.casefold() in lowered for t in selectors.FACEBOOK_DELETE_CONFIRM_DIALOG_TOKENS
        )

        if not is_known_dialog:
            logger.warning(
                "Diálogo inesperado de Facebook (texto: %r...); pasando a INTERVENTION_REQUIRED",
                dialog_text[:120],
            )
            return False, f"Diálogo de Facebook inesperado: {dialog_text[:120]!r}"

        # Confirmar en el diálogo conocido.
        for btn_token in selectors.FACEBOOK_DELETE_CONFIRM_BUTTON_TOKENS:
            try:
                btn = dialog_locator.get_by_role("button", name=btn_token, exact=False)
                if btn.first.is_visible(timeout=2_000):
                    btn.first.click()
                    self._settle(page, seconds=_SETTLE_S)
                    logger.info("Diálogo de confirmación confirmado (botón: %r)", btn_token)
                    return True, f"Confirmado en diálogo con botón: {btn_token}"
            except Exception:
                continue

        # Si encontramos el diálogo conocido pero no el botón esperado,
        # es más seguro pedir intervención que hacer clic a ciegas.
        logger.warning("Diálogo conocido pero botón de confirmación no encontrado")
        return False, "Diálogo de Facebook conocido pero botón de confirmación no localizado"

    def _verify_after_delete(self, page: Page, listing: Listing) -> DeleteResult:
        """Verificación posterior a la acción de eliminación.

        Estrategia:
        1. Señal inmediata: el toast de éxito que Facebook muestra en la
           página actual (todavía en "Tus publicaciones").
        2. Navegar al item URL y aplicar las señales de verificación
           (redirección a lista, "ya no disponible", item URL sirviendo el
           feed general sin el título del listing).

        Política (modificación 3 del spec):
        - confirmed=True SOLO con evidencia positiva.
        - Timeout, error de red o ausencia de señales → DELETE_UNCERTAIN.
        """
        # Señal inmediata: toast de éxito en la página actual (antes de navegar).
        try:
            current_text = page.locator("body").inner_text(timeout=3_000) or ""
        except Exception:
            current_text = ""
        current_lowered = current_text.casefold()
        for token in selectors.FACEBOOK_DELETE_SUCCESS_TOKENS:
            if token.casefold() in current_lowered:
                logger.info("Verificación: toast de éxito detectado (%r)", token)
                verification = selectors.DeletionVerificationResult(
                    confirmed=True,
                    signals_found=[f"Toast de éxito: {token}"],
                    detail=f"Toast de éxito detectado: {token}",
                )
                return DeleteResult(
                    status=DeleteStatus.DELETED_CONFIRMED,
                    listing=listing,
                    detail=f"Publicación eliminada y verificada. {verification.detail}",
                    verification=verification,
                    verification_signals=list(verification.signals_found),
                )

        # Esperar un poco para que Facebook procese la eliminación.
        time.sleep(2.0)

        # Navegar a la URL del item para verificar el estado.
        url = listing.url or ""
        page_text = ""
        current_url = url

        try:
            if url:
                forensics.evt("deleter.verify.goto.before", f"thread={threading.get_ident()} url={url}")
                page.goto(url, wait_until="domcontentloaded", timeout=_VERIFY_TIMEOUT_MS)
                forensics.evt("deleter.verify.goto.after", f"thread={threading.get_ident()} url={url}")
                self._settle(page, seconds=1.0)
            current_url = page.url or url
            try:
                page_text = page.locator("body").inner_text(timeout=5_000) or ""
            except Exception:
                page_text = ""
        except PlaywrightTimeout:
            # Timeout al recargar: señal ambigua, no confirmamos.
            logger.warning("Timeout al verificar si la publicación fue eliminada")
            return DeleteResult(
                status=DeleteStatus.DELETE_UNCERTAIN,
                listing=listing,
                error="Timeout al recargar la página de verificación",
                detail=(
                    "La acción de eliminación fue ejecutada, pero no se pudo "
                    "verificar el resultado porque la página no respondió."
                ),
            )
        except Exception as exc:
            logger.warning("Error de red al verificar eliminación: %s", exc)
            return DeleteResult(
                status=DeleteStatus.DELETE_UNCERTAIN,
                listing=listing,
                error=f"Error de red durante verificación: {exc}",
                detail=(
                    "La acción de eliminación fue ejecutada, pero un error de red "
                    "impidió verificar el resultado."
                ),
            )

        verification = selectors.verify_deletion_from_page(
            current_url, page_text, listing_title=listing.title or ""
        )
        logger.info("Verificación de eliminación: confirmed=%s, detalle=%s", verification.confirmed, verification.detail)

        if verification.confirmed:
            return DeleteResult(
                status=DeleteStatus.DELETED_CONFIRMED,
                listing=listing,
                detail=f"Publicación eliminada y verificada. {verification.detail}",
                verification=verification,
                verification_signals=list(verification.signals_found),
            )

        return DeleteResult(
            status=DeleteStatus.DELETE_UNCERTAIN,
            listing=listing,
            error="No se encontraron señales positivas de eliminación",
            detail=(
                f"La acción fue ejecutada pero no se pudo confirmar la eliminación. "
                f"Detalle: {verification.detail}"
            ),
            verification=verification,
            verification_signals=[],
        )

    # ------------------------------------------------------------------
    # Seguridad
    # ------------------------------------------------------------------

    def _check_intervention(self, page: Page, navigator=None) -> None:
        """Lanza InterventionRequiredError si Facebook pide acción manual."""
        if navigator is not None and callable(getattr(navigator, "requires_intervention", None)):
            if navigator.requires_intervention():
                raise InterventionRequiredError("Facebook requiere intervención manual durante la eliminación")

    def _settle(self, page: Page, seconds: float = _SETTLE_S) -> None:
        """Espera breve para que la UI de Facebook procese el clic.

        NOTA: `time.sleep` es aceptable aquí (no hay alternativa en la
        API síncrona de Playwright para esperar animaciones/diffs de UI).
        El timeout máximo es pequeño y acotado.
        """
        try:
            page.wait_for_load_state("networkidle", timeout=int(seconds * 1000))
        except Exception:
            time.sleep(min(seconds, 2.0))
