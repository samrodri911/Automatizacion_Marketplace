"""Creador y publicador de publicaciones nuevas de Facebook Marketplace.

Responsabilidad ÚNICA de este módulo:
    Product (datos actuales de la NUEVA publicación) → navegar al formulario
    → subir fotos → rellenar campos → publicar → verificar → PublishResult.

Este módulo se usa SOLO después de que la eliminación del target fue
confirmada (DELETED_CONFIRMED). Nunca se usa para localizar la publicación
a eliminar: eso es responsabilidad de `ListingDeleter` + `MatchedListing`.

Principios invariantes (mismos que ListingDeleter):

- Solo toca la `Page` provista por BrowserManager (nunca crea su propio
  navegador). No es thread-safe: debe correr en el QThread de automatización.
- Nunca usa coordenadas: selectores semánticos (labels, roles, texto) de `selectors.py`.
- Nunca resuelve CAPTCHA/login: si Facebook pide una acción manual, se
  reporta INTERVENTION_REQUIRED para que la capa de servicios pause en
  WAITING_USER (esta capa le está permitido NO tener cuadro de error).
- La verificación posterior es OBLIGATORIA: no se reporta éxito por haber
  pulsado "Publicar", solo si hay evidencia de que el anuncio quedó activo.
- Las señales ambiguas (timeout/red/ausencia de contenido) NO cuentan como
  confirmación -> PUBLISH_UNCERTAIN (nunca se crea un segundo anuncio a ciegas).
- Si ocurre EPIPE/TargetClosed, se diagnostica y se levanta AutomationError
  (no se oculta con try/except genérico).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from app.automation import selectors
from app.automation import category_matcher
from app.automation.brand_extractor import extract_brand
from app.automation.group_matcher import product_keywords, select_audience_groups
from app.automation.listing_extractor import ListingExtractor
from app.automation.listing_matcher import analyze_title
from app.core import forensics
from app.core.config import facebook_config
from app.core.exceptions import AutomationError, InterventionRequiredError
from app.core.logging_config import get_logger
from app.models.product import Product

logger = get_logger(__name__)

_NAV_TIMEOUT_MS = 20_000
_ACTION_TIMEOUT_MS = 15_000
_VERIFY_TIMEOUT_MS = 15_000
_SETTLE_S = 1.5

# Patrones de error de transporte que indican que el motor del navegador se
# desconectó (EPIPE / target closed). Se diagnostican, no se ocultan.
_EPIPE_PATTERNS = (
    "broken pipe",
    "target closed",
    "connection closed",
    "connection reset",
    "protocol error",
)

# Extrae las opciones de categoría visibles del selector de Facebook. El picker
# es un diálogo ("Menú desplegable") cuyas opciones son <div role="button">.
# Se toma la primera línea del texto (la etiqueta; "Envío disponible" es un
# sufijo informativo) y se ignora el cierre del diálogo.
CATEGORY_OPTIONS_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"]')).filter(visible);
  const roots = dialogs.length ? dialogs : [document.body];
  const seen = new Set();
  const out = [];
  const DENY = new Set(['cerrar', 'cancelar']);
  for (const root of roots) {
    for (const el of root.querySelectorAll('[role="button"]')) {
      if (!visible(el)) continue;
      const raw = (el.innerText || '').trim();
      const t = raw.split('\\n')[0].trim();
      if (!t) continue;
      const low = t.toLowerCase();
      if (DENY.has(low)) continue;
      if (seen.has(low)) continue;
      seen.add(low);
      out.push(t);
    }
  }
  return out;
}
"""

# Localiza el control (input/textarea) asociado a una etiqueta visual exacta
# ("Marca", "Etiquetas de productos"...). Facebook no asocia estas etiquetas
# con aria-labelledby. Se revisan TODOS los elementos cuyo PRIMER renglón
# coincide con la etiqueta (el contenedor suele incluir el texto de ayuda en
# la segunda línea), primero dentro del propio elemento y luego subiendo hasta
# 8 ancestros. Devuelve un selector CSS del control o null.
FIELD_BY_LABEL_JS = """
(label) => {
  const labelLower = String(label).toLowerCase();
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const labelOk = (t) => {
    if (!t) return false;
    const first = t.split('\\n')[0].trim().toLowerCase();
    return first === labelLower;
  };
  const findInput = (root) => {
    const c = root.querySelector('input[type="text"], textarea');
    if (!c) return null;
    const cr = c.getBoundingClientRect();
    if (cr.width <= 0 || cr.height <= 0) return null;
    return c;
  };
  const toSelector = (c) => {
    if (c.id) return '#' + CSS.escape(c.id);
    if (c.name) return 'input[name="' + c.name + '"], textarea[name="' + c.name + '"]';
    return c.tagName.toLowerCase();
  };
  const matches = [];
  for (const el of document.querySelectorAll('label, div, span, h1, h2, h3')) {
    if (!visible(el)) continue;
    if (!labelOk(el.innerText)) continue;
    const own = findInput(el);
    if (own) return toSelector(own);
    let node = el;
    for (let d = 0; d < 8 && node; d++) {
      node = node.parentElement;
      if (!node) break;
      const c = findInput(node);
      if (c) return toSelector(c);
    }
  }
  return null;
}
"""

# Grupos del paso final (step=audience): el Marketplace se publica por defecto
# y cada grupo sugerido es un checkbox cuyo texto es "Nombre del grupo\n
# N miembros\n• Público". Se toma la primera línea como nombre.
AUDIENCE_GROUPS_JS = """
() => {
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const out = [];
  for (const el of document.querySelectorAll('[role="checkbox"]')) {
    if (!visible(el)) continue;
    const raw = (el.innerText || '').trim();
    if (!raw) continue;
    const lower = raw.toLowerCase();
    if (!lower.includes('miembro') && !lower.includes('publico')) continue;
    const name = raw.split('\\n')[0].trim();
    if (!name) continue;
    out.push({
      checked: (el.getAttribute('aria-checked') || '').toLowerCase() === 'true',
      name,
    });
  }
  return out;
}
"""


def _is_epipe_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(p in text for p in _EPIPE_PATTERNS)


class PublishStatus(Enum):
    PUBLISHED_CONFIRMED = auto()    # Verificación positiva de que el anuncio existe
    PUBLISH_UNCERTAIN = auto()      # Se pulsó Publicar pero no se pudo verificar
    PUBLISH_FAILED = auto()         # Error durante la operación, no se llegó a publicar
    INTERVENTION_REQUIRED = auto()  # CAPTCHA/login/diálogo inesperado
    CANCELLED = auto()              # Cancelado antes de la acción de publicación


@dataclass
class PublishResult:
    """Resultado completo de una operación de creación/publicación."""

    status: PublishStatus
    title: str
    new_url: str = ""
    new_reference: str = ""
    error: str | None = None
    detail: str = ""
    verification: selectors.PublicationVerificationResult | None = None
    verification_signals: list[str] = field(default_factory=list)

    @property
    def is_confirmed(self) -> bool:
        return self.status == PublishStatus.PUBLISHED_CONFIRMED

    def to_dict(self) -> dict:
        return {
            "status": self.status.name,
            "title": self.title,
            "new_url": self.new_url,
            "new_reference": self.new_reference,
            "error": self.error,
            "detail": self.detail,
            "verification_signals": list(self.verification_signals),
        }


class ListingCreator:
    """Ejecuta la creación + publicación de una publicación nueva."""

    def __init__(self, action_timeout_ms: int | None = None) -> None:
        self._action_timeout_ms = action_timeout_ms or _ACTION_TIMEOUT_MS

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def create(
        self,
        product: Product,
        page: Page,
        navigator=None,
        image_paths: list[str] | None = None,
    ) -> PublishResult:
        """Flujo completo: navegar → subir fotos → rellenar → publicar → verificar.

        No lanza excepciones de automatización: devuelve siempre PublishResult.
        InterventionRequiredError se convierte en INTERVENTION_REQUIRED.
        EPIPE/TargetClosed se diagnostica y se eleva como AutomationError.
        """
        try:
            return self._create_inner(product, page, navigator, image_paths)
        except (AutomationError, InterventionRequiredError):
            raise
        except Exception as exc:
            if _is_epipe_error(exc):
                logger.error("Se detectó desconexión del navegador (EPIPE/TargetClosed) durante la creación: %s", exc)
                raise AutomationError("El motor del navegador se desconectó durante la creación de la publicación. Revisa el navegador e inténtalo de nuevo.") from exc
            raise

    def verify_only(self, product: Product, page: Page, navigator=None) -> PublishResult:
        """Verifica si el producto ya tiene una publicación activa SIN publicar nada.

        Uso: reanudación tras interrupción en CREATING/PUBLISHING. Nunca
        vuelve a crear: solo comprueba si la publicación ya existe en
        "Tus publicaciones".
        """
        logger.info("verify_only() → comprobando si '%s' ya está publicado", product.title)
        try:
            return self._verify_publication_only(product, page, navigator)
        except Exception as exc:
            if _is_epipe_error(exc):
                raise AutomationError(
                    "El motor del navegador se desconectó durante la verificación de publicación."
                ) from exc
            raise

    # ------------------------------------------------------------------
    # Flujo interno
    # ------------------------------------------------------------------
    def _create_inner(self, product, page, navigator, image_paths) -> PublishResult:
        fail = lambda status, detail, error=None: PublishResult(
            status=status, title=product.title, detail=detail, error=error
        )

        # Paso 1: navegar al formulario de nueva publicación.
        nav_ok, nav_detail = self._navigate_to_creator(page)
        if not nav_ok:
            return fail(PublishStatus.PUBLISH_FAILED, f"No se pudo abrir el formulario de publicación: {nav_detail}")

        # Paso 2: detectar intervención antes de tocar el formulario.
        try:
            self._check_intervention(page, navigator)
        except InterventionRequiredError as exc:
            return PublishResult(
                status=PublishStatus.INTERVENTION_REQUIRED,
                title=product.title,
                error=str(exc),
                detail="Facebook requiere intervención manual antes de crear la publicación",
            )

        # Paso 3: asegurar que el lugar de venta sea Marketplace (si FB lo pregunta).
        sale_ok, sale_detail = self._ensure_marketplace_sale(page)
        if not sale_ok:
            return fail(PublishStatus.PUBLISH_FAILED, f"No se pudo seleccionar Marketplace como lugar de venta: {sale_detail}")

        # Paso 4: subir fotos (nuevas o las que tenga el producto).
        if image_paths:
            upload_ok, upload_detail = self._upload_images(page, image_paths)
            if not upload_ok:
                return fail(PublishStatus.PUBLISH_FAILED, f"No se pudieron subir las fotografías: {upload_detail}")
        else:
            logger.warning("No hay rutas de imagen para subir; el formulario puede rechazar la publicación")

        # Paso 5: rellenar el formulario con los datos ACTUALES del producto.
        fill_ok, fill_detail = self._fill_form(product, page)
        if not fill_ok:
            return fail(PublishStatus.PUBLISH_FAILED, f"No se pudo rellenar el formulario: {fill_detail}")

        # Paso 6: volver a comprobar intervención justo antes de publicar
        # (CAPTCHA/2FA suele aparecer al intentar publicar).
        try:
            self._check_intervention(page, navigator)
        except InterventionRequiredError as exc:
            return PublishResult(
                status=PublishStatus.INTERVENTION_REQUIRED,
                title=product.title,
                error=str(exc),
                detail="Facebook requiere intervención manual antes de publicar el anuncio",
            )

        # Paso 7: pulsar Publicar.
        pub_ok, pub_detail = self._click_publish(page, product)
        if not pub_ok:
            return fail(PublishStatus.PUBLISH_FAILED, f"No se pudo pulsar Publicar: {pub_detail}")

        # Paso 8: verificación OBLIGATORIA.
        logger.info("Botón Publicar pulsado. Verificando resultado...")
        return self._verify_after_publish(page, product)

    # ------------------------------------------------------------------
    # Pasos individuales
    # ------------------------------------------------------------------
    def _navigate_to_creator(self, page: Page) -> tuple[bool, str]:
        url = facebook_config.create_listing_url
        try:
            forensics.evt("goto", url)
            page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
            self._settle(page)
            return True, f"Navegado a {url}"
        except PlaywrightTimeout:
            return False, f"Timeout al abrir {url}"
        except Exception as exc:
            # EPIPE/TargetClosed NO se oculta como un simple fallo de navegación:
            # se diagnostica y se eleva (mismo criterio que _run_search).
            if _is_epipe_error(exc):
                raise
            return False, f"Error al navegar a {url}: {exc}"

    def _ensure_marketplace_sale(self, page: Page) -> tuple[bool, str]:
        """Si Facebook pregunta dónde se realizará la venta, elige Marketplace."""
        # Si ya hay un campo de título visible, el formulario está directo.
        if self._field_visible(page, selectors.CREATE_TITLE_FIELD_TOKENS):
            return True, "Formulario directo (sin paso de lugar de venta)"
        for token in selectors.CREATE_MARKETPLACE_SALE_TOKENS:
            try:
                btn = page.get_by_text(token, exact=True).first
                if btn.is_visible(timeout=1_000):
                    btn.click()
                    self._settle(page)
                    return True, f"Seleccionado lugar de venta: {token}"
            except Exception:
                continue
        # No hay pregunta de lugar de venta: seguimos.
        return True, "No se detectó paso de lugar de venta"

    def _field_visible(self, page: Page, tokens: tuple[str, ...]) -> bool:
        for token in tokens:
            try:
                if page.get_by_label(token).first.is_visible(timeout=800):
                    return True
            except Exception:
                continue
            try:
                if page.get_by_role("textbox", name=token).first.is_visible(timeout=800):
                    return True
            except Exception:
                continue
        return False

    def _upload_images(self, page: Page, image_paths: list[str]) -> tuple[bool, str]:
        try:
            file_input = page.locator(selectors.CREATE_PHOTO_INPUT_SELECTOR).first
            file_input.set_input_files(list(image_paths))
            self._settle(page, seconds=2.0)
            logger.info("Fotografías subidas: %d", len(image_paths))
            return True, f"{len(image_paths)} fotografía(s) enviadas al formulario"
        except Exception as exc:
            logger.warning("No se pudo subir fotografías: %s", exc)
            return False, f"Error al subir fotografías: {exc}"

    def _fill_form(self, product: Product, page: Page) -> tuple[bool, str]:
        # Facebook actual oculta la descripción/ubicación en la sección
        # colapsada "Más detalles": expandirla antes de rellenar.
        self._expand_more_details(page)

        # Preferencias de entrega: viven dentro de "Más detalles". Facebook
        # exige al menos una; la operación habitual activa el encuentro en un
        # lugar público y el retiro en la puerta.
        self._set_delivery_preferences(page)

        steps = [
            ("título", selectors.CREATE_TITLE_FIELD_TOKENS, product.title, "text"),
            ("precio", selectors.CREATE_PRICE_FIELD_TOKENS, str(int(product.price)), "text"),
            ("descripción", selectors.CREATE_DESCRIPTION_FIELD_TOKENS, product.description, "textarea"),
            ("categoría", selectors.CREATE_CATEGORY_FIELD_TOKENS, product.category, "category"),
            ("condición", selectors.CREATE_CONDITION_FIELD_TOKENS, product.condition, "combo"),
            ("ubicación", selectors.CREATE_LOCATION_FIELD_TOKENS, product.location, "text"),
        ]
        for name, tokens, value, kind in steps:
            if not value:
                continue
            try:
                filled = self._fill_one(page, tokens, value, kind)
            except InterventionRequiredError:
                raise
            except Exception as exc:
                logger.warning("Error rellenando %s: %s", name, exc)
                filled = False
            if not filled:
                return False, f"no pude rellenar el campo '{name}'"

        # Campos opcionales (best-effort, no bloquean el formulario): la marca
        # se deriva del título y las etiquetas vienen del producto. Se llenan
        # DESPUÉS de los campos principales porque Facebook renderiza la
        # sección 'Más detalles' de forma perezosa: sus controles (p. ej.
        # 'Marca') solo existen en el DOM una vez que el formulario se recorrió
        # (los pasos anteriores hacen scroll).
        self._fill_label_field(page, selectors.CREATE_BRAND_LABEL_TOKENS, extract_brand(product.title) or "")
        self._fill_label_field(
            page,
            selectors.CREATE_TAGS_LABEL_TOKENS,
            ", ".join(product.tags),
        )

        # Toggles opcionales que la operación habitual NO debe activar.
        self._ensure_optional_toggles_off(page)

        return True, "Formulario rellenado"

    def _expand_more_details(self, page: Page) -> bool:
        """Expande la sección 'Más detalles' (donde vive la descripción).

        Idempotente: si ya hay un <textarea> visible (la descripción) no
        vuelve a hacer clic (que colapsaría la sección). Inofensivo si la
        versión de FB no tiene esta sección.
        """
        try:
            if page.locator("textarea:visible").first.is_visible(timeout=500):
                return True
        except Exception:
            pass
        for token in selectors.CREATE_MORE_DETAILS_TOKENS:
            try:
                el = page.get_by_text(token, exact=False).first
                if el.is_visible(timeout=1_000):
                    el.click()
                    self._settle(page, seconds=1.0)
                    logger.info("Sección 'Más detalles' expandida (%r)", token)
                    return True
            except Exception:
                continue
        return False

    def _set_delivery_preferences(self, page: Page) -> bool:
        """Activa las preferencias de entrega del vendedor (checkbox).

        Idempotente: no toca las que ya estén activas. Se activan
        'Encuentro en un lugar público' y 'Retiro en la puerta'; la opción
        'Entrega en la puerta' no se incluye y queda desactivada. Es
        best-effort: si no se encuentran, se registra y se sigue (el botón
        Siguiente/Publicar de Facebook marcará el error si realmente faltan).

        Nota: Facebook puede exponer el MISMO checkbox con nombre accesible en
        dos idiomas (p. ej. 'Encuentro en un lugar público' y
        'Meet in a public place'). Tras cada clic se re-lee aria-checked (con
        un pequeño asentamiento) para no volver a marcar/desmarcar el mismo.
        """
        activated = False
        for token in selectors.CREATE_DELIVERY_ENABLE_TOKENS:
            try:
                box = page.get_by_role("checkbox", name=token, exact=False).first
                if not box.is_visible(timeout=1_000):
                    continue
                checked = box.get_attribute("aria-checked")
                if checked == "true":
                    continue
                box.click()
                self._settle(page, seconds=0.4)
                if box.get_attribute("aria-checked") != "true":
                    # El clic no se aplicó (o el aria-checked tardó): reintentar.
                    box.click()
                    self._settle(page, seconds=0.4)
                activated = True
                logger.info("Preferencia de entrega activada: %r", token)
            except Exception as exc:
                logger.warning("No se pudo activar preferencia de entrega %r: %s", token, exc)
        if not activated:
            logger.warning("No se activó ninguna preferencia de entrega (sección no encontrada o ya configurada)")
        return activated

    def _read_audience_groups(self, page: Page) -> list:
        """Lee los grupos sugeridos del paso final con reintento: Facebook
        tarda en renderizarlos tras pulsar 'Siguiente'."""
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            try:
                data = page.evaluate(AUDIENCE_GROUPS_JS)
                if isinstance(data, list) and data:
                    return data
            except Exception:
                pass
            self._settle(page, seconds=1.2)
        return []

    def _select_audience_groups(self, page: Page, product: Product) -> None:
        """Selecciona en el paso final (step=audience) los grupos donde publicar.

        El Marketplace se publica por defecto y NO aparece como checkbox. De
        los grupos sugeridos (checkbox) se marcan: los GENERALES (aptos para
        cualquier producto, p. ej. 'compra y venta', 'de todo') siempre, y los
        ESPECÍFICOS solo si su nombre tiene relación por palabras clave con el
        producto. Best-effort: si no hay grupos o no se pueden leer, se sigue.
        """
        try:
            data = self._read_audience_groups(page)
        except Exception as exc:
            logger.warning("No se pudieron leer los grupos del paso final: %s", exc)
            return
        if not isinstance(data, list) or not data:
            logger.info("Sin grupos sugeridos en el paso final")
            return

        groups = []
        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                groups.append(name)

        keywords = product_keywords(product)
        to_select = select_audience_groups(groups, keywords)
        logger.info(
            "Grupos disponibles (%d): %s",
            len(groups),
            ", ".join(repr(g) for g in groups),
        )
        logger.info(
            "Grupos a publicar: %s",
            ", ".join(repr(g) for g in to_select) if to_select else "(ninguno adicional)",
        )

        for item in data:
            name = str(item.get("name") or "").strip()
            if not name or item.get("checked"):
                continue
            if name not in to_select:
                continue
            try:
                box = page.get_by_role("checkbox", name=name, exact=False).first
                if box.is_visible(timeout=1_500):
                    box.click()
                    logger.info("Grupo marcado para publicar: %r", name)
            except Exception as exc:
                logger.warning("No se pudo marcar el grupo %r: %s", name, exc)

    def _fill_one(self, page: Page, tokens: tuple[str, ...], value: str, kind: str) -> bool:
        # Combo y categoría NO deben pasar por el llenado genérico por label:
        # un get_by_label sobre un combobox buscable teclea el valor en su
        # campo de búsqueda en lugar de SELECCIONAR la opción, dejando el
        # combo sin valor (placeholder). Estos campos usan su propia lógica.
        if kind == "combo":
            return self._fill_combo(page, tokens, value)
        if kind == "category":
            return self._fill_category(page, value)
        for token in tokens:
            try:
                loc = page.get_by_label(token).first
                if loc.is_visible(timeout=1_000):
                    loc.fill(value)
                    logger.info("Campo %r rellenado por label: %r", kind, value[:40])
                    return True
            except Exception:
                pass
        for token in tokens:
            try:
                loc = page.get_by_role("textbox", name=token).first
                if loc.is_visible(timeout=800):
                    loc.fill(value)
                    logger.info("Campo %r rellenado por role textbox: %r", kind, value[:40])
                    return True
            except Exception:
                pass
            try:
                loc = page.get_by_role("textbox", name=token, exact=False).first
                if loc.is_visible(timeout=800):
                    loc.fill(value)
                    logger.info("Campo %r rellenado por role textbox (parcial): %r", kind, value[:40])
                    return True
            except Exception:
                pass
        # Fallback textarea: en el formulario actual de Facebook la descripción
        # es un <textarea> SIN nombre accesible (sin label/aria-label). Tras
        # expandir 'Más detalles' es el primer textarea visible de la página.
        if kind == "textarea":
            try:
                loc = page.locator("textarea").first
                if loc.is_visible(timeout=1_000):
                    loc.fill(value)
                    logger.info("Campo %r rellenado por fallback textarea: %r", kind, value[:40])
                    return True
            except Exception:
                pass
        logger.info("Campo %r NO rellenado (valor %r)", kind, value[:40])
        return False

    def _fill_combo(self, page: Page, tokens: tuple[str, ...], value: str) -> bool:
        # Abrir el combo (role=combobox o botón con el token). Con
        # coincidencia parcial (exact=False) porque el nombre accesible de
        # Facebook puede incluir el valor ya elegido.
        for attempt in range(2):
            opened = False
            for token in tokens:
                for exact in (True, False):
                    try:
                        combo = page.get_by_role("combobox", name=token, exact=exact).first
                        if combo.is_visible(timeout=1_000):
                            combo.click()
                            opened = True
                            break
                    except Exception:
                        continue
                    if opened:
                        break
                if opened:
                    break
            if not opened:
                for token in tokens:
                    for exact in (True, False):
                        try:
                            btn = page.get_by_role("button", name=token, exact=exact).first
                            if btn.is_visible(timeout=1_000):
                                btn.click()
                                opened = True
                                break
                        except Exception:
                            continue
                        if opened:
                            break
                    if opened:
                        break
            if not opened:
                return False
            self._settle(page, seconds=1.0)

            picked = False
            picked_desc = ""
            # Preferir las opciones reales del desplegable (roles semánticos).
            for role in ("option", "menuitem"):
                try:
                    opt = page.get_by_role(role, name=value, exact=False).first
                    if opt.is_visible(timeout=1_000):
                        opt.click()
                        picked = True
                        picked_desc = f"option/{role}"
                        break
                except Exception:
                    continue
                if picked:
                    break
            if not picked:
                # Opciones dentro de un diálogo/listbox/menú (evita que un
                # get_by_text global golpee el propio combo o su valor).
                for scope in ('[role="dialog"]', '[role="listbox"]', '[role="menu"]'):
                    try:
                        opt = page.locator(scope).get_by_text(value, exact=False).first
                        if opt.is_visible(timeout=1_000):
                            opt.click()
                            picked = True
                            picked_desc = f"scoped {scope}"
                            break
                    except Exception:
                        continue
                    if picked:
                        break
            if not picked:
                try:
                    opt = page.get_by_text(value, exact=False).first
                    if opt.is_visible(timeout=1_000):
                        opt.click()
                        picked = True
                        picked_desc = "get_by_text global"
                except Exception:
                    pass
            self._settle(page, seconds=1.0)

            if picked and self._combo_has_value(page, tokens, value):
                logger.info("Combo %r seleccionado (%s): %r", tokens[0], picked_desc, value[:40])
                return True
            logger.warning("Combo %r no confirmado (picked=%s via %s): %r",
                           tokens[0], picked, picked_desc, value[:40])
            # No se aplicó (re-render de Facebook): cerrar y reintentar.
            try:
                combo = page.get_by_role("combobox", name=tokens[0], exact=False).first
                if combo.is_visible(timeout=800):
                    combo.click()
            except Exception:
                pass
            self._settle(page, seconds=0.8)
        return False

    def _combo_has_value(self, page: Page, tokens: tuple[str, ...], value: str) -> bool:
        """¿El combo muestra ya el valor seleccionado?"""
        for token in tokens:
            try:
                combo = page.get_by_role("combobox", name=token, exact=False).first
                text = combo.inner_text(timeout=1_000)
                if value in (text or ""):
                    return True
            except Exception:
                continue
        return False

    def _fill_category(self, page: Page, value: str) -> bool:
        """Abre el picker de categoría, lee las opciones reales de Facebook y
        elige la más parecida a la categoría del producto.

        HIGH -> se selecciona automáticamente.
        MEDIUM / LOW / NO_MATCH -> se pide intervención manual (navegador
        abierto) con las candidatas y sus scores registrados en el log.
        """
        opened = False
        for token in selectors.CREATE_CATEGORY_FIELD_TOKENS:
            try:
                combo = page.get_by_role("combobox", name=token).first
                if combo.is_visible(timeout=1_000):
                    combo.click()
                    opened = True
                    break
            except Exception:
                pass
        if not opened:
            for token in selectors.CREATE_CATEGORY_FIELD_TOKENS:
                try:
                    btn = page.get_by_role("button", name=token, exact=False).first
                    if btn.is_visible(timeout=1_000):
                        btn.click()
                        opened = True
                        break
                except Exception:
                    continue
        if not opened:
            logger.warning("No se pudo abrir el selector de categoría")
            return False
        self._settle(page, seconds=1.0)

        options = self._extract_category_options(page)
        if not options:
            raise InterventionRequiredError(
                "No se pudieron leer las categorías disponibles de Facebook "
                "para la categoría del producto."
            )

        match = category_matcher.match_category(value, options)
        logger.info("Categoría BD: %r", value)
        logger.info("Categorías Facebook encontradas (%d): %s", len(options), ", ".join(options))
        for candidate, score in match.candidates:
            logger.info("  - %r (score=%.2f)", candidate, score)
        best_candidate = match.candidates[0][0] if match.candidates else None
        logger.info(
            "Mejor coincidencia: %r | score=%.2f | confidence=%s",
            best_candidate,
            match.score,
            match.confidence.value,
        )

        if match.confidence == category_matcher.CategoryConfidence.HIGH and match.selected:
            logger.info("Seleccionando categoría: %r", match.selected)
            try:
                option = page.get_by_role("button", name=match.selected, exact=False).first
                if not option.is_visible(timeout=1_000):
                    option = page.get_by_text(match.selected, exact=False).first
                option.click()
                self._settle(page, seconds=1.0)
                logger.info("Categoría seleccionada: %r", match.selected)
                return True
            except Exception as exc:
                logger.warning("No se pudo hacer clic en la categoría %r: %s", match.selected, exc)
                return False

        candidates_text = ", ".join(
            f"{c} ({s:.2f})" for c, s in match.candidates[:5]
        )
        if match.confidence == category_matcher.CategoryConfidence.MEDIUM:
            raise InterventionRequiredError(
                f"Categoría ambigua. BD='{value}'; candidatas de Facebook: {candidates_text}. "
                "Selecciónala manualmente en el navegador."
            )
        raise InterventionRequiredError(
            f"No se encontró una categoría de Facebook similar a '{value}'. "
            "Opciones vistas: " + (candidates_text or "(ninguna)") + ". "
            "Selecciónala manualmente en el navegador."
        )

    def _extract_category_options(self, page: Page) -> list[str]:
        """Lee las opciones visibles del picker de categoría abierto."""
        try:
            data = page.evaluate(CATEGORY_OPTIONS_JS)
            if isinstance(data, list):
                options = [str(item) for item in data if item]
                if options:
                    return options
        except Exception:
            pass
        return []

    def _fill_label_field(
        self, page: Page, label_tokens: tuple[str, ...], value: str
    ) -> bool:
        """Rellena un campo opcional SIN nombre accesible localizándolo por su
        etiqueta visual (p. ej. 'Marca', 'Etiquetas de productos'). Best-effort:
        si no se encuentra tras reintentos, registra y devuelve False. Algunos
        campos ('Marca') son obligatorios en el formulario actual: se reintenta
        durante unos segundos hasta que el control se renderice."""
        if not value:
            return False
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            for label in label_tokens:
                try:
                    selector = page.evaluate(FIELD_BY_LABEL_JS, label)
                    if not selector:
                        continue
                    # fill() espera visibilidad/estabilidad por sí mismo; el JS
                    # ya confirmó que el control es visible.
                    page.locator(selector).first.fill(value)
                    logger.info("Campo '%s' rellenado (%r)", label, value[:40])
                    return True
                except Exception as exc:
                    logger.warning("No se pudo rellenar el campo '%s': %s", label, exc)
            self._settle(page, seconds=0.7)
        logger.info("Campo opcional no encontrado: %s (valor %r)", label_tokens[0], value[:30])
        return False

    def _ensure_optional_toggles_off(self, page: Page) -> None:
        """Garantiza que 'Promocionar tras publicar' y 'Ocultar a amigos'
        queden DESACTIVADOS (la operación habitual no los activa). Si por
        cualquier razón estuvieran activos, los desmarca."""
        for token in selectors.CREATE_DISABLE_SWITCH_TOKENS:
            try:
                switch = page.get_by_role("switch", name=token, exact=False).first
                if not switch.is_visible(timeout=1_000):
                    continue
                if switch.is_checked():
                    switch.click()
                    logger.info("Toggle desactivado: %s", token)
                else:
                    logger.info("Toggle ya desactivado: %s", token)
            except Exception as exc:
                logger.warning("No se pudo revisar el toggle %s: %s", token, exc)

    def _click_publish(self, page: Page, product: Product) -> tuple[bool, str]:
        # El formulario tiene dos pasos: la pantalla de detalles termina con
        # "Siguiente" y la pantalla final (step=audience) confirma con
        # "Publicar". El paso "Siguiente" es best-effort (algunas versiones de
        # Facebook muestran "Publicar" directamente).
        next_clicked = False
        for token in selectors.CREATE_NEXT_BUTTON_TOKENS:
            try:
                el = page.get_by_role("button", name=token, exact=False).first
                if not el.is_visible(timeout=2_000):
                    continue
                disabled = el.get_attribute("aria-disabled")
                if disabled == "true":
                    # Formulario incompleto: NO avanzar ni intentar otro botón.
                    return False, (
                        "El formulario quedó incompleto: el botón 'Siguiente' "
                        "está deshabilitado (falta un campo obligatorio)."
                    )
                el.click()
                next_clicked = True
                self._settle(page, seconds=_SETTLE_S)
                logger.info("Pulsado 'Siguiente' (%s)", token)
                break
            except Exception:
                continue

        # En la pantalla final (step=audience) se eligen los grupos donde
        # publicar: los generales siempre y los relacionados por palabras
        # clave. El Marketplace se publica por defecto (no es un checkbox).
        if next_clicked:
            self._select_audience_groups(page, product)

        # Botón final de publicación. Se intenta primero con coincidencia
        # EXACTA para no confundir "Publicar" con "Promocionar tras publicar"
        # (toggle) o "Publicar como disponible" (etiqueta de disponibilidad).
        deny = selectors.CREATE_DISABLE_SWITCH_TOKENS + ("publicar como disponible",)
        for token in selectors.CREATE_PUBLISH_BUTTON_TOKENS:
            for exact in (True, False):
                try:
                    btn = page.get_by_role("button", name=token, exact=exact).first
                    if not btn.is_visible(timeout=2_000):
                        continue
                    name = (btn.get_attribute("aria-label") or "").lower()
                    if not exact and any(d in name for d in deny):
                        continue
                    btn.click()
                    self._settle(page, seconds=_SETTLE_S)
                    return True, f"Publicar pulsado ({token})"
                except Exception:
                    continue
            try:
                el = page.get_by_text(token, exact=True).first
                if el.is_visible(timeout=1_000):
                    el.click()
                    self._settle(page, seconds=_SETTLE_S)
                    return True, f"Publicar pulsado por texto ({token})"
            except Exception:
                continue
        return False, (
            "No se encontró el botón de Publicar con ningún token conocido "
            f"(Siguiente pulsado: {next_clicked}). "
            "Tokens: " + ", ".join(selectors.CREATE_PUBLISH_BUTTON_TOKENS)
        )

    def _verify_after_publish(self, page: Page, product: Product) -> PublishResult:
        # Tras pulsar Publicar, Facebook navega a 'Tus publicaciones'
        # (/marketplace/you/selling). El anuncio nuevo tarda en renderizarse,
        # así que se sondea hasta ~12s en busca de señales positivas: URL de
        # item, token de éxito o el título del producto en la página.
        deadline = time.monotonic() + 12.0
        verification = selectors.PublicationVerificationResult(
            confirmed=False, signals_found=[], detail="No se encontraron señales"
        )
        while time.monotonic() < deadline:
            url = page.url or ""
            page_text = ""
            try:
                page_text = page.locator("body").inner_text(timeout=5_000) or ""
            except Exception:
                page_text = ""
            try:
                verification = selectors.verify_publication_from_page(
                    url, page_text, product_title=product.title
                )
            except Exception as exc:
                logger.warning("Error analizando publicación: %s", exc)
                return PublishResult(
                    status=PublishStatus.PUBLISH_UNCERTAIN,
                    title=product.title,
                    error=str(exc),
                    detail="Se pulsó Publicar pero no se pudo analizar el resultado.",
                )
            if verification.confirmed:
                break
            self._settle(page, seconds=1.0)

        logger.info(
            "Verificación de publicación: confirmed=%s, detalle=%s",
            verification.confirmed,
            verification.detail,
        )

        if verification.confirmed:
            return PublishResult(
                status=PublishStatus.PUBLISHED_CONFIRMED,
                title=product.title,
                new_url=verification.extracted_url,
                new_reference=verification.extracted_reference,
                detail=f"Publicación creada y verificada. {verification.detail}",
                verification=verification,
                verification_signals=list(verification.signals_found),
            )

        return PublishResult(
            status=PublishStatus.PUBLISH_UNCERTAIN,
            title=product.title,
            error="No se encontraron señales positivas de publicación",
            detail=(
                f"Se pulsó Publicar pero no se pudo confirmar la creación. "
                f"Detalle: {verification.detail}"
            ),
            verification=verification,
            verification_signals=[],
        )

    def _verify_publication_only(self, product: Product, page: Page, navigator) -> PublishResult:
        """Verificación de reanudación: busca el título del producto en
        'Tus publicaciones' (sin crear nada)."""
        from app.core.config import search_limits

        try:
            if navigator is not None:
                navigator.ensure_listings_section()
            self._check_intervention(page, navigator)
        except InterventionRequiredError as exc:
            return PublishResult(
                status=PublishStatus.INTERVENTION_REQUIRED,
                title=product.title,
                error=str(exc),
                detail="Facebook requiere intervención manual para verificar la publicación",
            )

        extractor = ListingExtractor()
        seen: set[str] = set()
        found: selectors.PublicationVerificationResult | None = None
        deadline = time.monotonic() + search_limits.search_timeout_ms / 1000.0

        while time.monotonic() < deadline:
            try:
                batch = extractor.extract_listings(page)
            except Exception as exc:
                return PublishResult(
                    status=PublishStatus.PUBLISH_UNCERTAIN,
                    title=product.title,
                    error=str(exc),
                    detail=f"Error leyendo las publicaciones: {exc}",
                )
            new_items = [l for l in batch if l.key not in seen]
            seen.update(l.key for l in new_items)
            for listing in new_items:
                analysis = analyze_title(product.title, listing.title)
                if analysis.perfect or analysis.score >= 0.8:
                    url = listing.url or ""
                    ref = listing.reference or ""
                    found = selectors.PublicationVerificationResult(
                        confirmed=True,
                        signals_found=[f"Publicación encontrada: {listing.title}"],
                        detail=f"Publicación encontrada en Tus publicaciones: {listing.title}",
                        extracted_reference=ref,
                        extracted_url=url,
                    )
                    break
            if found:
                break
            if navigator is not None:
                moved = navigator.scroll_feed() or False
            else:
                moved = False
            if not moved:
                break

        if found is not None:
            return PublishResult(
                status=PublishStatus.PUBLISHED_CONFIRMED,
                title=product.title,
                new_url=found.extracted_url,
                new_reference=found.extracted_reference,
                detail=found.detail,
                verification=found,
                verification_signals=list(found.signals_found),
            )

        return PublishResult(
            status=PublishStatus.PUBLISH_UNCERTAIN,
            title=product.title,
            error="No se encontró la publicación pendiente",
            detail=(
                "No se pudo confirmar si la publicación ya fue creada. "
                "Ninguna publicación en 'Tus publicaciones' coincide con el producto."
            ),
        )

    # ------------------------------------------------------------------
    # Seguridad
    # ------------------------------------------------------------------
    def _check_intervention(self, page: Page, navigator=None) -> None:
        """Lanza InterventionRequiredError si Facebook pide acción manual."""
        if navigator is not None and callable(getattr(navigator, "requires_intervention", None)):
            if navigator.requires_intervention():
                raise InterventionRequiredError("Facebook requiere intervención manual durante la creación/publicación")

    def _settle(self, page: Page, seconds: float = _SETTLE_S) -> None:
        """Espera breve y acotada para que la UI de Facebook procese."""
        try:
            page.wait_for_load_state("networkidle", timeout=int(seconds * 1000))
        except Exception:
            time.sleep(min(seconds, 2.0))