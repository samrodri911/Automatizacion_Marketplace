"""Selectores semánticos y lógica de clasificación para Facebook Marketplace.

Este módulo es intencionalmente PURO: no importa Playwright ni toca el
navegador. Centraliza dos cosas:

1. Los tokens de texto (en español e inglés, según el idioma de la cuenta
   de Facebook) que se usan para reconocer la UI de Marketplace usando
   selectores SEMÁNTICOS de Playwright (roles, labels, texto visible) en
   vez de clases CSS frágiles.

2. La lógica de clasificación que, dado la URL de la página y un conjunto
   de fragmentos de texto visible, decide si la sección "Tus publicaciones"
   (Your listings / selling) está cargada.

Tener estos tokens separados hace que, si Facebook cambia un texto o
añade otro idioma, solo haya que tocarlos aquí. Y tener la clasificación
como función pura permite probarla sin abrir un navegador real.

Señales que se distinguen en "Tus publicaciones":

- URL: la página debe estar en /marketplace/you/selling (señal débil).
- Cabecera: un heading con el texto "Tus publicaciones" (o inglés).
- Tabs/sección: botones/tabs tipo "Activos", "Vendidos", "Borradores"...
  (indican que el listado de publicaciones renderizó de verdad).
- Estado vacío: textos de "no hay publicaciones" (la sección también
  cargó correctamente aunque no haya nada publicado).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# URL raíz de Marketplace como prefijo fuerte. Usamos re (no str.startswith)
# porque a Facebook le gusta añadir parámetros de tráfico.
MARKETPLACE_URL_PATTERN = re.compile(r"/marketplace/?(\?.*)?$", re.IGNORECASE)
YOUR_LISTINGS_URL_PATTERN = re.compile(r"/marketplace/you/selling", re.IGNORECASE)

# URL de un item/publicación individual de Marketplace. El grupo captura el
# id numérico que luego se guarda como `reference` del Listing.
LISTING_ITEM_URL_PATTERN = re.compile(r"/marketplace/item/(\d+)", re.IGNORECASE)


def extract_item_reference(url: str) -> str:
    """Extrae la referencia (id de Marketplace) de una URL de item.

    'https://www.facebook.com/marketplace/item/123456789012345/' -> '123456789012345'
    Devuelve '' si la URL no parece un item de Marketplace.
    """
    match = LISTING_ITEM_URL_PATTERN.search(url or "")
    return match.group(1) if match else ""

# Tokens para reconocer la sección Marketplace de la barra de navegación
# superior / lateral de Facebook.
MARKETPLACE_NAV_TOKENS: tuple[str, ...] = (
    "Marketplace",
)

# Encabezados de la sección "Tus publicaciones".
YOUR_LISTINGS_HEADING_TOKENS: tuple[str, ...] = (
    "Tus publicaciones",
    "Your listings",
    "Your Marketplace listings",
    "Tus listados",
)

# Tabs/etiquetas de la sección de listados. La presencia de cualquiera de
# ellos significa que la sección de publicaciones ha renderizado.
YOUR_LISTINGS_TAB_TOKENS: tuple[str, ...] = (
    "Activos",
    "Activas",
    "Vendidos",
    "Vendidas",
    "Borradores",
    "Borrador",
    "Descartados",
    "Active",
    "Sold",
    "Drafts",
    "Discarded",
)

# Textos de "estado vacío" (listados sin publicaciones). La sección sigue
# contando como "cargada" porque es el estado correcto de la página.
YOUR_LISTINGS_EMPTY_TOKENS: tuple[str, ...] = (
    "No tienes publicaciones",
    "No tienes ninguna publicación",
    "Nothing posted yet",
    "You haven't",
    "No hay publicaciones",
)


@dataclass
class SnippetMatch:
    """Resultado de intentar emparejar un fragmento de texto visible de la
    página contra un conjunto de tokens.

    Atributos:
        text: el fragmento de texto exacto de la página.
        token: el token (en el idioma de la cuenta) que emparejó.
        kind: a qué conjunto de señales pertenece el token (heading, tab,
            empty, nav, url).
    """

    text: str
    token: str
    kind: str

    def to_dict(self) -> dict[str, str]:
        return {"text": self.text, "token": self.token, "kind": self.kind}


def find_snippet_matches(
    snippets: Iterable[str],
    tokens: Iterable[str],
    *,
    kind: str,
) -> list[SnippetMatch]:
    """Empareja fragmentos de texto visibles de la página contra tokens.

    La comparación es de subcadena case-insensitive: Facebook a veces
    añade espacios o el token aparece dentro de un texto más largo
    (p.ej. "Tus publicaciones de compra y venta").
    """
    matches: list[SnippetMatch] = []
    for snippet in snippets:
        if not snippet or not snippet.strip():
            continue
        lowered = snippet.casefold()
        for token in tokens:
            if token.casefold() and token.casefold() in lowered:
                matches.append(SnippetMatch(text=snippet, token=token, kind=kind))
                break  # una sola coincidencia por fragmento es suficiente
    return matches


@dataclass
class ListingsSectionState:
    """Diagnóstico de por qué / cómo se reconoció la sección "Tus publicaciones".

    Atributos:
        found: True si la sección parece cargada (una o más de las señales).
        url_matches: `True` si la URL apunta a /marketplace/you/selling.
        signals: los SnippetMatch que confirmaron la carga.
        reason: texto corto legible para la UI / logs.
    """

    found: bool
    url_matches: bool
    signals: list[SnippetMatch] = field(default_factory=list)

    @property
    def reason(self) -> str:
        if not self.found:
            return "No se detectaron señales de 'Tus publicaciones' en la página"
        if self.url_matches and not self.signals:
            return "URL de 'Tus publicaciones' detectada (señales de UI aún por confirmar manualmente)"
        parts = [m.kind for m in self.signals]
        return ", ".join(parts) or "señal de sección de publicaciones detectada"


def classify_listings_section(url: str, snippets: list[str]) -> ListingsSectionState:
    """Clasifica la página actual de Marketplace.

    Entradas:
      url: la URL actual de la página.
      snippets: fragmentos de texto visible de la página (headings, tabs,
          texto de estado vacío, etc.) ya recogidos por el adaptador.

    Salida: un `ListingsSectionState` listo para ser consumido por la
    capa de servicios y la GUI.

    Regla de decisión: la sección está cargada si aparece CUALQUIERA de
    estas señales semánticas:
      - hay un encabezado que contenga un token de "Tus publicaciones";
      - hay un tab/botón de la sección (Activos/Vendidos/...) , que es la
        señal más fuerte de que el render de publicaciones ocurrió;
      - hay un texto de estado vacío.
    La URL sola NO marca `found`: si coincide pero no hay señal textual,
    `found` es `False` y queda reflejado en `url_matches=True` para que la
    UI lo muestre con honestidad.
    """
    signals: list[SnippetMatch] = []

    url_match = bool(YOUR_LISTINGS_URL_PATTERN.search(url))

    heading_matches = find_snippet_matches(snippets, YOUR_LISTINGS_HEADING_TOKENS, kind="heading")
    tab_matches = find_snippet_matches(snippets, YOUR_LISTINGS_TAB_TOKENS, kind="tabs")
    empty_matches = find_snippet_matches(snippets, YOUR_LISTINGS_EMPTY_TOKENS, kind="empty")

    signals.extend(heading_matches)
    signals.extend(tab_matches)
    signals.extend(empty_matches)

    found = bool(heading_matches or tab_matches or empty_matches)

    return ListingsSectionState(
        found=found,
        url_matches=url_match,
        signals=signals,
    )


def find_marketplace_signal(url: str, snippets: list[str]) -> bool:
    """Señal débil de que estamos dentro de Marketplace (para la navegación
    inicial). Devuelve True si la URL apunta a /marketplace o si aparece un
    token de navegación de Marketplace en el texto visible."""
    if MARKETPLACE_URL_PATTERN.search(url):
        return True
    nav_matches = find_snippet_matches(snippets, MARKETPLACE_NAV_TOKENS, kind="nav")
    return bool(nav_matches)

# ===========================================================================
# Selectores para el flujo de ELIMINACIÓN (Iteración 4)
# ===========================================================================

# Textos del menú de opciones de una publicación individual.
# Facebook muestra típicamente un botón "..." o "⋯" (con aria-label) sobre
# la tarjeta de cada publicación en "Tus publicaciones".
LISTING_MENU_ARIA_LABELS: tuple[str, ...] = (
    "Más opciones",
    "More options",
    "Más opciones para la publicación",
    "Opciones de la publicación",
    "Item options",
    "Abrir menú",
    "Open menu",
    "Opciones",
    "Options",
    "Acciones",
    "Actions",
)

# Texto visible en el ítem de menú que lanza la eliminación.
# Facebook puede usar distintos textos según el idioma e historial del ítem.
LISTING_DELETE_ACTION_TOKENS: tuple[str, ...] = (
    "Eliminar publicación de Marketplace",
    "Eliminar publicación",
    "Eliminar anuncio",
    "Eliminar",
    "Delete listing",
    "Delete post",
    "Delete",
    "Borrar publicación",
    "Borrar",
)

# Texto del diálogo de confirmación que muestra Facebook antes de eliminar.
FACEBOOK_DELETE_CONFIRM_DIALOG_TOKENS: tuple[str, ...] = (
    "¿Confirmas que quieres eliminar",
    "¿Eliminar publicación?",
    "¿Eliminar anuncio?",
    "¿Borrar publicación?",
    "Delete listing?",
    "Delete post?",
    "Are you sure",
    "¿Estás seguro",
)

# Texto del botón de confirmación DENTRO del diálogo de Facebook.
FACEBOOK_DELETE_CONFIRM_BUTTON_TOKENS: tuple[str, ...] = (
    "Eliminar",
    "Delete",
    "Confirmar",
    "Confirm",
    "Borrar",
)

# Señales de que la publicación ya NO existe (verificación post-eliminación).
LISTING_GONE_URL_PATTERNS: tuple[str, ...] = (
    "/marketplace/you/selling",  # Redirigió a la lista: ítem ya no existe
)

LISTING_GONE_PAGE_TOKENS: tuple[str, ...] = (
    "Esta publicación ya no está disponible",
    "This listing is no longer available",
    "La publicación fue eliminada",
    "Listing deleted",
    "Esta página no está disponible",
    "This content isn't available",
    "No se puede encontrar esta página",
    "Page Not Found",
    "Publicación eliminada",
    "Listing removed",
)

# Mensaje de éxito mostrado por Facebook justo después de eliminar.
FACEBOOK_DELETE_SUCCESS_TOKENS: tuple[str, ...] = (
    "Publicación eliminada",
    "Listing deleted",
    "Se eliminó la publicación",
    "Your listing has been deleted",
    "Anuncio eliminado",
)

# Marcadores del feed general de Marketplace. Facebook sirve el feed en la
# URL de un item YA ELIMINADO (la URL no redirige y no muestra "no
# disponible"); la ausencia del título del listing + estos marcadores son la
# evidencia de que la publicación ya no existe.
FACEBOOK_FEED_MARKERS: tuple[str, ...] = (
    "sugerencias de hoy",
    "recién publicado",
    "en un radio de",
    "gratis",
)


@dataclass
class DeletionVerificationResult:
    """Resultado del proceso de verificación post-eliminación.

    Atributos:
        confirmed: True SOLO si hay evidencia POSITIVA de eliminación.
            (modificación 3: señales ambiguas/timeout/red → confirmed=False)
        signals_found: lista de señales semánticas que confirmaron la
            eliminación (vacía si no hay ninguna).
        detail: descripción legible del resultado.
    """

    confirmed: bool
    signals_found: list[str]
    detail: str


def verify_deletion_from_page(
    url: str,
    page_text: str,
    listing_title: str = "",
) -> DeletionVerificationResult:
    """Analiza si la página actual indica que la publicación ya no existe.

    Política conservadora (modificación 3 del spec):
    - Devuelve confirmed=True SOLO si hay una señal POSITIVA (token de
      "publicación eliminada" / "ya no disponible" / redirección a lista /
      item URL sirviendo el feed general sin el título del listing).
    - Un error de red, timeout o ausencia de contenido NO se interpreta
      como confirmación: devuelve confirmed=False con el detalle.
    - La redirección a /marketplace/you/selling ES señal positiva porque
      Facebook solo redirige ahí si el ítem ya no existe.

    No lanza excepciones: siempre devuelve un DeletionVerificationResult.
    """
    signals: list[str] = []

    # Señal 1: redirigió a "Tus publicaciones" → el ítem ya no existe.
    for pattern in LISTING_GONE_URL_PATTERNS:
        if pattern in (url or ""):
            signals.append(f"URL indica redirección a lista: {pattern}")
            break

    # Señal 2: texto de "ya no disponible" o similar en el cuerpo de la página.
    lowered = (page_text or "").casefold()
    for token in LISTING_GONE_PAGE_TOKENS:
        if token.casefold() in lowered:
            signals.append(f"Texto de página: {token!r}")
            break

    # Señal 3: mensaje de éxito de Facebook (toast / banner).
    for token in FACEBOOK_DELETE_SUCCESS_TOKENS:
        if token.casefold() in lowered:
            signals.append(f"Mensaje de éxito: {token!r}")
            break

    # Señal 4: el item URL se mantiene pero muestra el feed general de
    # Marketplace en lugar del item (Facebook lo sirve cuando el ítem ya no
    # existe). Requiere el título del listing y que esté AUSENTE de la
    # página, además de marcadores inequívocos del feed.
    if listing_title:
        title_absent = listing_title.casefold() not in lowered
        feed_present = any(
            marker.casefold() in lowered for marker in FACEBOOK_FEED_MARKERS
        )
        if "/marketplace/item/" in (url or "") and title_absent and feed_present:
            signals.append(
                "El item URL muestra el feed general de Marketplace (publicación ya no existe)"
            )

    if signals:
        return DeletionVerificationResult(
            confirmed=True,
            signals_found=signals,
            detail=f"Eliminación confirmada ({len(signals)} señal(es)): {'; '.join(signals)}",
        )

    return DeletionVerificationResult(
        confirmed=False,
        signals_found=[],
        detail="No se encontraron señales positivas de eliminación",
    )


# ===========================================================================
# Selectores para el flujo de CREACIÓN / PUBLICACIÓN (Iteración 5)
# ===========================================================================

# Selector del input de fotos del formulario de nueva publicación. Facebook
# usa un único <input type="file"> (oculto) para subir las imágenes.
CREATE_PHOTO_INPUT_SELECTOR = "input[type=file]"

# Nombres de los campos del formulario de creación de una publicación
# (nuevo anuncio de Marketplace). Se buscan semánticamente con get_by_label /
# get_by_role; se listan las variantes habituales en español e inglés.
CREATE_TITLE_FIELD_TOKENS: tuple[str, ...] = (
    "Título",
    "Title",
)
CREATE_PRICE_FIELD_TOKENS: tuple[str, ...] = (
    "Precio",
    "Price",
)
CREATE_DESCRIPTION_FIELD_TOKENS: tuple[str, ...] = (
    "Descripción",
    "Description",
)
CREATE_CATEGORY_FIELD_TOKENS: tuple[str, ...] = (
    "Categoría",
    "Category",
)
CREATE_CONDITION_FIELD_TOKENS: tuple[str, ...] = (
    "Condición",
    "Estado",
    "Condition",
)
CREATE_LOCATION_FIELD_TOKENS: tuple[str, ...] = (
    "Ubicación",
    "Location",
)
CREATE_TAGS_FIELD_TOKENS: tuple[str, ...] = (
    "Etiquetas",
    "Tags",
)

# Sección colapsada "Más detalles": la descripción (y otros campos) solo
# aparecen tras expandirla. Facebook actual oculta la descripción detrás de
# este toggle hasta que el usuario hace clic.
CREATE_MORE_DETAILS_TOKENS: tuple[str, ...] = (
    "Más detalles",
    "More details",
)

# Preferencias de entrega (sección dentro de "Más detalles"): checkboxes que
# el vendedor debe activar. La operación habitual es marcar el encuentro en un
# lugar público y el retiro en la puerta; "Entrega en la puerta" se deja
# desactivada (no se incluye aquí).
CREATE_DELIVERY_ENABLE_TOKENS: tuple[str, ...] = (
    "Encuentro en un lugar público",
    "Meet in a public place",
    "Retiro en la puerta",
    "Pickup at door",
)

# El formulario de creación tiene dos pasos: los detalles terminan con
# "Siguiente" y la pantalla final (step=audience) confirma con "Publicar".
CREATE_NEXT_BUTTON_TOKENS: tuple[str, ...] = (
    "Siguiente",
    "Next",
)

# Campos opcionales SIN nombre accesible en Facebook actual: sus etiquetas
# visuales no están asociadas al control (input/textarea "desnudo"). Se
# localizan por su etiqueta de texto exacta en el DOM.
CREATE_BRAND_LABEL_TOKENS: tuple[str, ...] = (
    "Marca",
    "Brand",
)
CREATE_TAGS_LABEL_TOKENS: tuple[str, ...] = (
    "Etiquetas de productos",
    "Etiquetas",
    "Tags",
)

# Toggles opcionales del formulario que NO deben activarse en la operación
# habitual: "Promocionar tras publicar" y "Ocultar a amigos". Si por cualquier
# motivo quedaron activados, se desactivan.
CREATE_DISABLE_SWITCH_TOKENS: tuple[str, ...] = (
    "Promocionar tras publicar",
    "Ocultar a amigos",
)

# Texto del botón final que publica el anuncio.
CREATE_PUBLISH_BUTTON_TOKENS: tuple[str, ...] = (
    "Publicar",
    "Publish",
    "Publicar en Marketplace",
)

# Selección del lugar de venta: a veces Facebook pregunta "¿Dónde se
# realizará la venta?" antes de mostrar el formulario o de publicar.
CREATE_MARKETPLACE_SALE_TOKENS: tuple[str, ...] = (
    "En Marketplace",
    "Marketplace",
)

# Señales de que una publicación YA EXISTE en "Tus publicaciones" (usadas
# para VERIFICAR una publicación sin volver a crearla; ver sección 14).
PUBLISHED_POSITIVE_PAGE_TOKENS: tuple[str, ...] = (
    "Publicación creada con éxito",
    "Tu publicación se creó",
    "Tu anuncio se publicó",
    "se publicó correctamente",
    "listing created successfully",
    "Your listing was created",
    "Your ad is now live",
    "Your listing is now live",
    "Publicaste",
)


@dataclass
class PublicationVerificationResult:
    """Resultado de la verificación de una publicación.

    Atributos:
        confirmed: True SOLO si hay evidencia POSITIVA de que el anuncio
            existe en Marketplace (URL de item real o señal textual).
        signals_found: señales que confirmaron la publicación.
        detail: descripción legible.
        extracted_reference / extracted_url: localizador de la publicación
            nueva (si se pudo extraer), para persistir en el producto.
    """

    confirmed: bool
    signals_found: list[str]
    detail: str
    extracted_reference: str = ""
    extracted_url: str = ""


def verify_publication_from_page(
    url: str, page_text: str, product_title: str = ""
) -> PublicationVerificationResult:
    """Analiza si la página actual confirma la existencia de una publicación.

    Política conservadora (espejo de `verify_deletion_from_page`):
    - confirmed=True SOLO con señal positiva:
        * la URL apunta a un item real de Marketplace (`/marketplace/item/<id>`),
          que Facebook solo ofrece para anuncios publicados; o
        * el texto visible contiene un token de éxito de publicación; o
        * el texto visible contiene el título del producto (p. ej. tras
          publicar, Facebook navega a 'Tus publicaciones', donde el anuncio
          nuevo aparece listado).
    - Timeout, red o ausencia de señales -> confirmed=False (nunca se da
      por hecho que la publicación existe sin evidencia).

    Nunca decide por el usuario: si no hay evidencia, devuelve False.
    """
    signals: list[str] = []

    # Señal 1 (fuerte): la URL es la página de un item publicado.
    ref = ""
    item_match = LISTING_ITEM_URL_PATTERN.search(url or "")
    if item_match:
        ref = item_match.group(1)
        signals.append(f"URL de item publicado: {url}")

    # Señal 2: texto de éxito en el cuerpo de la página.
    lowered = (page_text or "").casefold()
    for token in PUBLISHED_POSITIVE_PAGE_TOKENS:
        if token.casefold() in lowered:
            signals.append(f"Texto de éxito: {token!r}")
            break

    # Señal 3: el título del producto aparece listado (p. ej. en
    # 'Tus publicaciones', a donde Facebook navega tras publicar).
    if product_title:
        title_norm = " ".join(product_title.casefold().split())
        if title_norm and title_norm in " ".join(lowered.split()):
            signals.append(f"Título del producto visible: {product_title[:60]!r}")

    if signals:
        return PublicationVerificationResult(
            confirmed=True,
            signals_found=signals,
            detail=f"Publicación confirmada ({len(signals)} señal(es)): {'; '.join(signals)}",
            extracted_reference=ref,
            extracted_url=url or "",
        )

    return PublicationVerificationResult(
        confirmed=False,
        signals_found=[],
        detail="No se encontraron señales positivas de que la publicación exista",
    )
