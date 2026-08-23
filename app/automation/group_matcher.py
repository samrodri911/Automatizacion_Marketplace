"""Clasificación determinista de los grupos del paso final de publicación.

Regla de selección (pura, sin Playwright ni IA):

1. Un grupo es "general" si su nombre indica un mercado de compra/venta para
   cualquier tipo de producto (p. ej. "compra y venta", "de todo",
   "todo tipo"). Los grupos generales se publican SIEMPRE.
2. Un grupo NO es general si un término de especialización ("deportivos",
   "inmuebles", "entretenimiento"...) aparece ANTES de la señal general en el
   nombre (p. ej. "Implementos deportivos compra - venta" es deportivo).
3. Un grupo específico solo se publica si su nombre tiene relación de
   palabras clave con el producto (título + categoría + familia de categoría).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.automation import category_matcher
from app.models.product import Product

# Señales de que el grupo es un mercado general de compra/venta.
_GENERAL_SIGNALS = (
    "compra y venta",
    "compra venta",
    "compra-venta",
    "compraventa",
    "compro y vendo",
    "compro vendo",
    "compro-vendo",
    "comprar y vender",
    "venta de todo",
    "vendo de todo",
    "de todo",
    "todo tipo",
    "venta libre",
    "clasificados",
    "marketplace",
    "mercado",
    "trueque",
)

# Términos que especializan un grupo (NO apto para cualquier producto).
_SPECIALIZATION_SIGNALS = (
    "deport",
    "inmueble",
    "inmobiliaria",
    "entretenimiento",
    "automotriz",
    "autos",
    "carros",
    "carros",
    "motos",
    "mascota",
    "perros",
    "gatos",
    "ropa",
    "calzado",
    "zapatos",
    "mueble",
    "bebe",
    "bebes",
    "juguete",
    "libros",
    "celular",
    "telefono",
    "computador",
    "laptop",
    "portatil",
    "hogar",
    "cocina",
    "jardin",
    "herramienta",
    "artesania",
    "belleza",
    "salud",
    "alimento",
    "comida",
    "moda",
    "joya",
    "bicicleta",
    "pesca",
    "futbol",
    "gimnasio",
    "musica",
    "instrumento",
    "arte",
    "pintura",
    "videojuego",
    "camara",
    "camaras",
    "electrodomestico",
    "tecnolog",
    "electronica",
    "informatica",
    "computacion",
)

# Familias de categoría: al detectar estos tokens en título/categoría se
# amplían las palabras clave con las del resto de la familia (matching amplio).
_CATEGORY_FAMILIES = {
    "electronica": {
        "electronica",
        "informatica",
        "computacion",
        "computadora",
        "portatil",
        "laptop",
        "notebook",
        "tecnologia",
        "tecnologicos",
        "celular",
        "telefono",
        "accesorios",
        "computo",
        "tablet",
        "videojuegos",
    },
    "informatica": {
        "informatica",
        "electronica",
        "computacion",
        "computadora",
        "portatil",
        "laptop",
        "notebook",
        "tecnologia",
        "computo",
    },
    "computacion": {
        "computacion",
        "informatica",
        "electronica",
        "computadora",
        "portatil",
        "laptop",
        "notebook",
        "tecnologia",
    },
    "hogar": {"hogar", "mueble", "decoracion", "cocina", "electrodomestico"},
    "ropa": {"ropa", "moda", "calzado", "zapato", "vestido"},
    "mascota": {"mascota", "perro", "gato"},
    "deporte": {"deporte", "futbol", "bicicleta", "gimnasio", "pesca"},
    "musica": {"musica", "instrumento", "concierto"},
    "juguete": {"juguete", "nino", "bebe"},
    "libro": {"libro", "pelicula"},
    "herramienta": {"herramienta", "jardineria", "bricolaje"},
    "belleza": {"belleza", "salud", "cosmetico"},
    "vehiculo": {"vehiculo", "autoparte", "automotriz", "moto"},
    "artesania": {"artesania", "manualidad", "arte"},
    "salud": {"salud", "belleza"},
    "bebe": {"bebe", "nino", "juguete"},
    "autoparte": {"autoparte", "vehiculo", "automotriz"},
    "clasificados": {"clasificados", "varios", "compraventa", "mercado"},
}

_SCORE_THRESHOLD = 0.5


@dataclass
class GroupProfile:
    name: str
    is_general: bool
    score: float
    selected: bool
    reason: str


def _norm(text: str) -> str:
    return category_matcher.normalize_category(text)


def _first_signal_index(norm_name: str, signals: tuple[str, ...]) -> int:
    idx = -1
    for sig in signals:
        i = norm_name.find(sig)
        if i >= 0 and (idx < 0 or i < idx):
            idx = i
    return idx


def is_general_group(name: str) -> bool:
    """True si el grupo es un mercado general apto para cualquier producto."""
    n = _norm(name)
    if not n:
        return False
    gen_idx = _first_signal_index(n, _GENERAL_SIGNALS)
    if gen_idx < 0:
        return False
    spec_idx = _first_signal_index(n, _SPECIALIZATION_SIGNALS)
    if 0 <= spec_idx < gen_idx:
        return False
    return True


def product_keywords(product: Product) -> set[str]:
    """Palabras clave del producto: tokens canónicos de título + categoría,
    ampliados con la familia de la categoría."""
    kw = category_matcher._canonical_tokens(_norm(product.title))
    kw |= category_matcher._canonical_tokens(_norm(product.category))
    expanded = set(kw)
    for token in kw:
        family = _CATEGORY_FAMILIES.get(token)
        if family:
            expanded |= family
    return expanded


def group_keyword_score(name: str, keywords: set[str]) -> float:
    """Fracción de tokens del nombre del grupo que son palabras clave del
    producto (0..1)."""
    g_tokens = category_matcher._canonical_tokens(_norm(name))
    if not g_tokens or not keywords:
        return 0.0
    inter = len(g_tokens & keywords)
    return inter / len(g_tokens)


def classify_group(name: str, keywords: set[str]) -> GroupProfile:
    """Clasifica un grupo (general siempre; específico solo por keywords)."""
    general = is_general_group(name)
    score = group_keyword_score(name, keywords)
    if general:
        return GroupProfile(name, True, score, True, "grupo general de compra/venta")
    if score >= _SCORE_THRESHOLD:
        return GroupProfile(
            name, False, score, True,
            f"relación con el producto por palabras clave (score={score:.2f})",
        )
    return GroupProfile(
        name, False, score, False,
        "grupo específico sin relación con el producto",
    )


def select_audience_groups(
    group_names: list[str], keywords: set[str]
) -> list[str]:
    """Devuelve los nombres de grupo que deben marcarse para publicar."""
    selected = []
    for name in group_names:
        profile = classify_group(name, keywords)
        if profile.selected:
            selected.append(name)
    return selected