"""Normalización y coincidencia de categorías (pura, sin Playwright ni IA)."""

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum


class CategoryConfidence(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NO_MATCH = "NO_MATCH"


@dataclass
class CategoryMatch:
    requested: str
    selected: str | None
    confidence: CategoryConfidence
    score: float
    candidates: list[tuple[str, float]] = field(default_factory=list)


HIGH_THRESHOLD = 0.85
HIGH_GAP = 0.12
MEDIUM_THRESHOLD = 0.55

_STOPWORDS = {
    "a", "al", "con", "de", "del", "e", "el", "en", "la", "las", "los",
    "para", "por", "un", "una", "unos", "unas", "y",
}

_TOKEN_CANONICAL = {
    "computador": "computadora",
    "computadores": "computadora",
    "computadoras": "computadora",
    "laptop": "portatil",
    "laptops": "portatil",
    "notebook": "portatil",
    "notebooks": "portatil",
    "portatil": "portatil",
    "portatiles": "portatil",
    "celular": "telefono",
    "celulares": "telefono",
    "movil": "telefono",
    "moviles": "telefono",
    "telefono": "telefono",
    "telefonos": "telefono",
    "musica": "musica",
    "musical": "musica",
    "musicales": "musica",
    "videojuegos": "videojuego",
    "videojuego": "videojuego",
    "juegos": "juego",
    "juguete": "juguete",
    "juguetes": "juguete",
    "instrumento": "instrumento",
    "instrumentos": "instrumento",
    "herramienta": "herramienta",
    "herramientas": "herramienta",
    "mueble": "mueble",
    "muebles": "mueble",
    "electrodomestico": "electrodomestico",
    "electrodomesticos": "electrodomestico",
    "bicicleta": "bicicleta",
    "bicicletas": "bicicleta",
    "autoparte": "autoparte",
    "autopartes": "autoparte",
    "antiguedad": "antiguedad",
    "antiguedades": "antiguedad",
    "deporte": "deporte",
    "deportes": "deporte",
    "jardineria": "jardineria",
    "hogar": "hogar",
    "electronica": "electronica",
    "informatica": "informatica",
    "accesorio": "accesorio",
    "accesorios": "accesorio",
    "bolso": "bolso",
    "bolsos": "bolso",
    "equipaje": "equipaje",
    "juguetes": "juguete",
    "ropa": "ropa",
    "calzado": "calzado",
    "joyas": "joya",
    "joya": "joya",
    "salud": "salud",
    "belleza": "belleza",
    "mascotas": "mascota",
    "mascota": "mascota",
    "bebes": "bebe",
    "bebe": "bebe",
    "ninos": "nino",
    "nino": "nino",
    "libros": "libro",
    "libro": "libro",
    "peliculas": "pelicula",
    "pelicula": "pelicula",
    "arte": "arte",
    "artes": "arte",
    "manualidades": "manualidad",
    "manualidad": "manualidad",
    "garaje": "garaje",
    "varios": "varios",
    "vehiculos": "vehiculo",
    "vehiculo": "vehiculo",
    "clasificados": "clasificados",
    "familia": "familia",
    "pasatiempos": "pasatiempo",
    "pasatiempo": "pasatiempo",
}


def normalize_category(text: str) -> str:
    """Normaliza una categoría: minúsculas, sin tildes, sin puntuación,
    espacios colapsados."""
    if not text:
        return ""
    s = unicodedata.normalize("NFD", text.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _canonical_token(token: str) -> str:
    if token in _TOKEN_CANONICAL:
        return _TOKEN_CANONICAL[token]
    for suffix in ("es", "s"):
        if token.endswith(suffix) and len(token) > 4:
            base = token[: -len(suffix)]
            if base in _TOKEN_CANONICAL:
                return _TOKEN_CANONICAL[base]
            if len(base) > 2:
                return base
    return token


def _canonical_tokens(norm: str) -> set[str]:
    tokens = set()
    for token in norm.split():
        if token in _STOPWORDS:
            continue
        tokens.add(_canonical_token(token))
    return tokens


def _category_score(
    requested_norm: str,
    requested_tokens: set[str],
    candidate_norm: str,
    candidate_tokens: set[str],
) -> float:
    if requested_norm == candidate_norm:
        return 1.0
    if not requested_tokens or not candidate_tokens:
        return 0.0
    inter = len(requested_tokens & candidate_tokens)
    union = len(requested_tokens | candidate_tokens)
    jaccard = inter / union if union else 0.0
    containment = 0.0
    if requested_tokens <= candidate_tokens:
        containment = 1.0
    elif candidate_tokens <= requested_tokens:
        containment = 0.85
    ratio = SequenceMatcher(None, requested_norm, candidate_norm).ratio()
    return max(jaccard, containment * 0.9, ratio)


def match_category(requested: str, candidates: list[str]) -> CategoryMatch:
    """Encuentra la categoría de Facebook más parecida a la solicitada.

    HIGH: coincidencia claramente superior -> se puede seleccionar sola.
    MEDIUM: razonable pero no suficientemente clara -> no seleccionar sola.
    LOW/NO_MATCH: sin coincidencia razonable -> no continuar.
    """
    requested_norm = normalize_category(requested)
    requested_tokens = _canonical_tokens(requested_norm)
    results: list[tuple[str, float]] = []
    for candidate in candidates:
        candidate_norm = normalize_category(candidate)
        candidate_tokens = _canonical_tokens(candidate_norm)
        score = _category_score(
            requested_norm, requested_tokens, candidate_norm, candidate_tokens
        )
        results.append((candidate, score))
    results.sort(key=lambda item: item[1], reverse=True)
    if not results:
        return CategoryMatch(requested, None, CategoryConfidence.NO_MATCH, 0.0, [])

    best, best_score = results[0]
    second_score = results[1][1] if len(results) > 1 else 0.0
    gap = best_score - second_score

    if best_score >= HIGH_THRESHOLD and gap >= HIGH_GAP:
        confidence = CategoryConfidence.HIGH
        selected = best
    elif best_score >= MEDIUM_THRESHOLD:
        confidence = CategoryConfidence.MEDIUM
        selected = None
    elif best_score > 0.0:
        confidence = CategoryConfidence.LOW
        selected = None
    else:
        confidence = CategoryConfidence.NO_MATCH
        selected = None

    return CategoryMatch(requested, selected, confidence, best_score, results)