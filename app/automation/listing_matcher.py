"""Sistema de coincidencia Listing <-> Product.

Capa PURA: no importa Playwright ni PySide6, no toca el navegador ni la
base de datos. Solo recibe objetos tipados (`Product` y `Listing`) y
produce un `MatchResult` determinista, pensado para ser unit-testable.

Principios de diseño:

- **Nunca substring**: "iPhone 13" NO coincide con "iPhone 13 Pro" por
  contener una dentro de la otra; se compara por NORMALIZACIÓN + TOKENS.
- **Varias señales**: título, precio, nº de fotos y referencia conocida.
- **Conservador**: si no hay un ganador claramente superior entre
  candidatos, `resolve()` devuelve AMBIGUOUS. Nunca se elige en silencio.
- **Referencia conocida != autorización ciega**: una `marketplace_url` o
  `reference` previa es señal prioritaria, pero debe existir coherencia
  con el producto (título compatible y precio no contradictorio).
- **Determinista y testeable**: todos los umbrales están fijos aquí.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto

from app.models.listing import Listing
from app.models.product import Product

# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------

_UNITS = ("gb", "tb", "mb", "ghz", "hz", "w", "v", "mah")


def _attach_unit_spaces(text: str) -> str:
    """'128 GB' -> '128gb' para que sea igual a '128GB'."""
    result = text
    for unit in _UNITS:
        result = re.sub(rf"(\d)\s+{unit}\b", rf"\1{unit}", result, flags=re.IGNORECASE)
    return result


def normalize_title(text: str) -> str:
    """Normaliza un título para comparación de tokens.

    - casefold (mayúsculas/minúsculas);
    - espacios y puntuación unificados;
    - '128 GB' == '128gb' == '128GB'.

    >>> normalize_title("IPHONE 13 128GB")
    'iphone 13 128gb'
    """
    if not text:
        return ""
    low = text.casefold()
    low = _attach_unit_spaces(low)
    low = re.sub(r"[^a-z0-9]+", " ", low)
    return " ".join(low.split())


def tokens_of(title: str) -> frozenset[str]:
    return frozenset(normalize_title(title).split())


_VARIANT_TOKENS: frozenset[str] = frozenset(
    {
        "pro", "slim", "mini", "plus", "max", "lite", "ultra", "air", "se",
        "xl", "xs", "xr", "fe", "cellular", "wifi",
    }
)

# Patrón de precio: "$1.850.000" o "1.850.000 COP" (u otra moneda CNF).
_PRICE_CURRENCY_RE = re.compile(
    r"\$\s*([\d][\d.,]*)|([\d][\d.,]*)\s*(?:COP|USD|EUR|CLP|MXN|PEN|ARS|CRC|GTQ)",
    re.IGNORECASE,
)
_STANDALONE_NUMBER_RE = re.compile(r"^[\d][\d.,]*$")


def _digits_int(amount: str) -> int:
    """'1.850.000' / '1,850,000' / '1850000' -> 1850000 (COP, sin decimales)."""
    return int(re.sub(r"\D", "", amount))


def parse_price_from_text(text: str) -> tuple[int | None, str]:
    """Extrae un precio (int COP) de un texto de tarjeta de Facebook.

    Devuelve (precio, texto_crudo_del_precio). Si no se encuentra un
    precio inequívoco, devuelve (None, ''). No se adivinan números sueltos
    del título (p.ej. "128GB"): solo se aceptan precios con símbolo de
    moneda, con palabra de moneda, o una línea numérica suelta.
    """
    if not text:
        return None, ""

    match = _PRICE_CURRENCY_RE.search(text)
    if match:
        amount = match.group(1) or match.group(2)
        return _digits_int(amount), match.group(0)

    for line in text.splitlines():
        candidate = line.strip()
        if _STANDALONE_NUMBER_RE.match(candidate):
            return _digits_int(candidate), candidate

    return None, ""


def parse_listing_price(listing: Listing) -> int | None:
    """Precio del Listing: usa el campo normalizado si existe; si no,
    lo intenta parsear del texto crudo."""
    if listing.price is not None:
        return listing.price
    parsed, _ = parse_price_from_text(listing.raw_text)
    return parsed


# ---------------------------------------------------------------------------
# Enums / resultados
# ---------------------------------------------------------------------------
class ConfidenceLevel(Enum):
    HIGH = auto()
    MEDIUM = auto()
    LOW = auto()
    NO_MATCH = auto()


class MatchStatus(Enum):
    FOUND = auto()
    MEDIUM_CONFIDENCE = auto()
    LOW_CONFIDENCE = auto()
    AMBIGUOUS = auto()
    NOT_FOUND = auto()
    SEARCH_LIMIT_REACHED = auto()


@dataclass
class TitleAnalysis:
    """Análisis de similitud entre el título del producto y el del listing."""

    score: float  # 0..1
    perfect: bool
    missing: frozenset[str] = field(default_factory=frozenset)
    extra: frozenset[str] = field(default_factory=frozenset)

    @property
    def has_variant_tokens(self) -> bool:
        return bool(self.extra & _VARIANT_TOKENS)


@dataclass
class MatchResult:
    """Resultado de comparar UN producto contra UN listing."""

    matched: bool
    confidence: ConfidenceLevel
    score: float
    listing: Listing
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    title: TitleAnalysis | None = None

    def to_dict(self) -> dict:
        listing = self.listing
        return {
            "matched": self.matched,
            "confidence": self.confidence.name,
            "score": round(self.score, 1),
            "listing": {
                "title": listing.title,
                "price": listing.price,
                "price_raw": listing.price_raw,
                "url": listing.url,
                "reference": listing.reference,
                "image_count": len(listing.image_refs),
            },
            "reasons": self.reasons,
            "warnings": self.warnings,
        }


@dataclass
class MatchOutcome:
    """Resultado de buscar un producto entre MUCHOS listings."""

    status: MatchStatus
    best: MatchResult | None = None
    candidates: list[MatchResult] = field(default_factory=list)
    scanned_count: int = 0

    @property
    def details(self) -> dict:
        return {
            "status": self.status.name,
            "best": self.best.to_dict() if self.best else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "scanned": self.scanned_count,
        }


# Umbrales deterministas de confianza. El delta de ambigüedad decide si los
# dos mejores candidatos están "demasiado cerca" (-> AMBIGUOUS).
_HIGH_MIN = 75
_MEDIUM_MIN = 50
_LOW_MIN = 25
_AMBIGUITY_DELTA = 12.0

_CONFIDENCE_SCORE = {
    ConfidenceLevel.HIGH: 90.0,
    ConfidenceLevel.MEDIUM: 62.0,
    ConfidenceLevel.LOW: 38.0,
    ConfidenceLevel.NO_MATCH: 10.0,
}

_CONFIDENCE_RANK = {
    ConfidenceLevel.HIGH: 3,
    ConfidenceLevel.MEDIUM: 2,
    ConfidenceLevel.LOW: 1,
    ConfidenceLevel.NO_MATCH: 0,
}


# ---------------------------------------------------------------------------
# Señales individuales
# ---------------------------------------------------------------------------
def compare_prices(product_price: float | int | None, listing_price: int | None) -> bool | None:
    """True si coinciden, False si difieren, None si falta información."""
    if product_price is None or listing_price is None:
        return None
    return int(product_price) == int(listing_price)


def compare_image_counts(product_count: int, listing_count: int) -> bool | None:
    if product_count <= 0 or listing_count <= 0:
        return None
    return product_count == listing_count


def known_reference_match(
    product_url: str | None,
    product_reference: str | None,
    listing: Listing,
) -> bool:
    """True si el listing coincide con la referencia/URL previamente
    registrada por nuestra aplicación."""
    if product_reference and listing.reference and listing.reference == product_reference:
        return True
    if product_url and listing.url and listing.url.rstrip("/") == product_url.rstrip("/"):
        return True
    return False


def analyze_title(product_title: str, listing_title: str) -> TitleAnalysis:
    """Análisis determinista de similitud de títulos (por tokens)."""
    product_tokens = tokens_of(product_title)
    listing_tokens = tokens_of(listing_title)
    if not product_tokens:
        return TitleAnalysis(score=0.0, perfect=False)

    common = product_tokens & listing_tokens
    missing = product_tokens - listing_tokens
    extra = listing_tokens - product_tokens

    missing = frozenset(missing)
    extra = frozenset(extra)

    coverage = len(common) / len(product_tokens)
    score = coverage - 0.20 * len(missing) - 0.15 * len(extra)
    score = max(0.0, min(1.0, score))

    perfect = not missing and not extra
    return TitleAnalysis(score=round(score, 3), perfect=perfect, missing=missing, extra=extra)


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------
class ListingMatcher:
    """Comparador determinista `Product` vs `Listing`(s)."""

    def match(self, product: Product, listing: Listing) -> MatchResult:
        """Compara un producto contra un único listing. Conservador: ante
        la duda, baja la confianza y añade una advertencia."""
        title = analyze_title(product.title, listing.title)
        listing_price = parse_listing_price(listing)
        price = compare_prices(product.price, listing_price)
        images = compare_image_counts(len(product.images), len(listing.image_refs))
        known = known_reference_match(product.marketplace_url, product.marketplace_reference, listing)

        reasons: list[str] = []
        warnings: list[str] = []

        if title.perfect:
            reasons.append("título coincide exactamente")
        elif title.score >= 0.8:
            reasons.append("título muy similar")
        elif title.score >= 0.5:
            warnings.append("título solo parcialmente coincidente")
        else:
            warnings.append("título poco relacionado")

        if price is True:
            reasons.append("precio idéntico")
        elif price is False:
            warnings.append("el precio mostrado difiere del producto")

        if images is True:
            reasons.append("misma cantidad de fotografías")
        elif images is False:
            warnings.append("la cantidad de fotografías difiere")

        if known:
            reasons.append("coincide con referencia previa registrada")

        confidence = self._decide(title=title, price=price, images=images, known=known)

        if title.has_variant_tokens:
            warnings.append("el título del listing sugiere una variante distinta (p. ej. 'pro', 'slim')")
            if confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM):
                confidence = ConfidenceLevel.LOW

        matched = confidence != ConfidenceLevel.NO_MATCH
        return MatchResult(
            matched=matched,
            confidence=confidence,
            score=_CONFIDENCE_SCORE[confidence],
            listing=listing,
            reasons=reasons,
            warnings=warnings,
            title=title,
        )

    def resolve(self, product: Product, candidates: list[Listing]) -> MatchOutcome:
        """Resolución CONSERVADORA entre varios candidatos.

        - Si no hay ningún candidato con al menos MEDIUM -> NOT_FOUND.
        - Si los dos mejores están demasiado cerca o hay más de un HIGH
          -> AMBIGUOUS. Nunca se selecciona en silencio.
        - Si hay un ganador claramente superior -> FOUND (con su grado).
        """
        if not candidates:
            return MatchOutcome(status=MatchStatus.NOT_FOUND, scanned_count=0)

        results = [self.match(product, c) for c in candidates]
        results.sort(key=lambda r: (_CONFIDENCE_RANK[r.confidence], r.score), reverse=True)

        best = results[0]
        second = results[1] if len(results) > 1 else None

        ambiguous = False
        if second is not None:
            same_tier = _CONFIDENCE_RANK[second.confidence] == _CONFIDENCE_RANK[best.confidence]
            close_score = abs(best.score - second.score) < _AMBIGUITY_DELTA
            if same_tier or close_score:
                ambiguous = True

        if ambiguous:
            status = MatchStatus.AMBIGUOUS
        elif best.confidence == ConfidenceLevel.HIGH:
            status = MatchStatus.FOUND
        elif best.confidence == ConfidenceLevel.MEDIUM:
            status = MatchStatus.MEDIUM_CONFIDENCE
        elif best.confidence == ConfidenceLevel.LOW:
            status = MatchStatus.LOW_CONFIDENCE
        else:
            status = MatchStatus.NOT_FOUND

        return MatchOutcome(status=status, best=best, candidates=results, scanned_count=len(results))

    # -- regla de decisión de UN listing --------------------------------------
    def _decide(
        self,
        *,
        title: TitleAnalysis,
        price: bool | None,
        images: bool | None,
        known: bool,
    ) -> ConfidenceLevel:
        """Regla determinista (ver tests)."""
        if known:
            # Referencia conocida: prioritaria, pero exige coherencia con el
            # producto (título compatible y precio no contradictorio).
            if title.perfect or title.score >= 0.7:
                if price is False:
                    return ConfidenceLevel.LOW
                return ConfidenceLevel.HIGH
            if title.score >= 0.4:
                return ConfidenceLevel.MEDIUM
            return ConfidenceLevel.LOW

        if title.perfect:
            if price is True:
                return ConfidenceLevel.HIGH
            if price is None and images is not False:
                return ConfidenceLevel.MEDIUM
            return ConfidenceLevel.LOW

        if title.score >= 0.8:
            if price is True:
                return ConfidenceLevel.MEDIUM
            return ConfidenceLevel.LOW

        if title.score >= 0.5:
            return ConfidenceLevel.LOW

        return ConfidenceLevel.NO_MATCH