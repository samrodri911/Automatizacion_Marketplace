"""Modelo de dominio: Product.

Este es el modelo tipado que usa el resto de la aplicación (GUI,
servicios, automatización). La base de datos SQLite es el mecanismo de
persistencia, pero el código de negocio siempre trabaja con instancias
de `Product`, nunca con filas/diccionarios sueltos.

Facebook NO es la fuente de verdad: `marketplace_url` /
`marketplace_reference` son simplemente el último resultado conocido de
haber publicado este producto, guardado para referencia y para ayudar
a la localización en la siguiente republicación.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ProductCondition(str, Enum):
    """Condiciones soportadas. Se mapean al selector de condición de
    Facebook Marketplace en la capa de automatización, nunca al revés."""

    NEW = "Nuevo"
    USED_LIKE_NEW = "Usado - Como nuevo"
    USED_GOOD = "Usado - Buen estado"
    USED_FAIR = "Usado - Aceptable"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


@dataclass
class Product:
    """Representa un producto gestionado por la aplicación.

    `id` es None para productos que aún no se han guardado en la base
    de datos (el repositorio le asigna un id al insertarlo).
    """

    title: str
    description: str
    price: float
    category: str
    condition: str
    location: str
    tags: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)  # rutas absolutas o relativas a PRODUCTS_DIR
    enabled: bool = True

    id: int | None = None

    # Estado frente a Facebook (informativo, nunca autoritativo)
    marketplace_url: str | None = None
    marketplace_reference: str | None = None

    # Trazabilidad
    last_published_at: datetime | None = None
    last_deleted_at: datetime | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None

    # -- Validación de dominio -------------------------------------------------
    def validate(self) -> list[str]:
        """Devuelve la lista de errores de validación (vacía si es válido).

        No lanza excepción aquí a propósito: la capa de servicios decide
        si convierte estos errores en `ProductValidationError`, y la GUI
        puede usarlos directamente para resaltar campos en el formulario.
        """
        errors: list[str] = []

        if not self.title or not self.title.strip():
            errors.append("El título es obligatorio")

        if not self.description or not self.description.strip():
            errors.append("La descripción es obligatoria")

        if self.price is None or self.price < 0:
            errors.append("El precio debe ser un número igual o mayor que 0")

        if not self.category or not self.category.strip():
            errors.append("La categoría es obligatoria")

        if not self.condition or not self.condition.strip():
            errors.append("La condición es obligatoria")

        if not self.location or not self.location.strip():
            errors.append("La ubicación es obligatoria")

        if not self.images:
            errors.append("Debe haber al menos una fotografía")

        return errors

    @property
    def is_valid(self) -> bool:
        return len(self.validate()) == 0

    # -- (De)serialización auxiliar --------------------------------------------
    def tags_as_json(self) -> str:
        return json.dumps(self.tags, ensure_ascii=False)

    def images_as_json(self) -> str:
        return json.dumps(self.images, ensure_ascii=False)

    @staticmethod
    def parse_tags(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return [str(t) for t in data]
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def parse_images(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return [str(i) for i in data]
        except (json.JSONDecodeError, TypeError):
            return []
