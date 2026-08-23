"""Modelo de dominio: Listing.

Representa una publicación (listing) encontrada en Facebook Marketplace.
Es la contrapartida remota de `Product`:

    Product  = nuestra fuente de verdad local (SQLite).
    Listing  = lo que Facebook muestra en "Tus publicaciones".

Nunca se deben asumir iguales: la coincidencia la decide la capa de
matching (listing_matcher.py), nunca la mera estructura de datos.

`price` se representa como `int | None` porque trabajamos principalmente
con COP (sin decimales). `price_raw` conserva el texto tal como lo
mostró Facebook, útil para diagnóstico y para mostrar en la GUI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Listing:
    """Una publicación encontrada en la sección de listados de Facebook."""

    title: str
    price: int | None
    price_raw: str = ""
    url: str = ""
    reference: str = ""
    image_refs: list[str] = field(default_factory=list)
    raw_text: str = ""
    extracted_at: datetime | None = None

    @property
    def key(self) -> str:
        """Clave estable de deduplicación.

        Dos tarjetas con la misma `reference` (id de Marketplace) o la
        misma URL son la misma publicación aunque el texto varíe entre
        renders. Si no hay referencia ni URL, se cae a una clave basada en
        texto (título + precio).
        """
        if self.reference:
            return f"ref:{self.reference}"
        if self.url:
            return f"url:{self.url}"
        return f"text:{self.title}|{self.price}"