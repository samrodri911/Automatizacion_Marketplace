"""Capa de servicio para productos.

La GUI nunca habla directamente con `ProductRepository`: siempre pasa
por `ProductService`, que además de delegar en el repositorio se encarga
de:

- validar el producto antes de guardar;
- copiar las imágenes seleccionadas por el usuario a la carpeta propia
  del producto dentro de data/products/<slug>/, para que la app no
  dependa de que el archivo original siga existiendo en su ubicación
  original.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.core.config import PRODUCTS_DIR
from app.core.exceptions import ProductValidationError
from app.core.logging_config import get_logger
from app.database.repositories import AutomationRun, AutomationRunRepository, ProductRepository
from app.models.product import Product

logger = get_logger(__name__)

_SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def slugify(title: str) -> str:
    """Convierte un título en un nombre de carpeta seguro.

    'iPhone 13 128GB' -> 'iphone-13-128gb'
    """
    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "producto"


class ProductService:
    def __init__(
        self,
        repository: ProductRepository,
        products_dir: Path | None = None,
        run_repository: AutomationRunRepository | None = None,
    ) -> None:
        self._repository = repository
        self._products_dir = products_dir or PRODUCTS_DIR
        self._run_repository = run_repository

    # -- Lectura -----------------------------------------------------------
    def list_all(self) -> list[Product]:
        return self._repository.list_all()

    def list_enabled(self) -> list[Product]:
        return self._repository.list_enabled()

    def get(self, product_id: int) -> Product:
        return self._repository.get(product_id)

    # -- Escritura -----------------------------------------------------------
    def create(self, product: Product, source_image_paths: list[str] | None = None) -> Product:
        """Crea un producto nuevo.

        `source_image_paths`, si se indica, son rutas de archivos de imagen
        seleccionados por el usuario (p.ej. desde un QFileDialog). Se copian
        a la carpeta propia del producto y se guardan como rutas relativas
        en `product.images`.
        """
        if source_image_paths:
            # El slug se calcula antes de tener id porque el id todavía no
            # existe; usamos el título. Si luego el usuario cambia el
            # título, las imágenes ya copiadas se conservan en su carpeta.
            folder_name = slugify(product.title)
            product.images = self._copy_images(folder_name, source_image_paths)

        self._validate_or_raise(product)
        return self._repository.create(product)

    def update(self, product: Product, source_image_paths: list[str] | None = None) -> Product:
        if source_image_paths:
            folder_name = slugify(product.title)
            new_images = self._copy_images(folder_name, source_image_paths)
            product.images = [*product.images, *new_images]

        self._validate_or_raise(product)
        return self._repository.update(product)

    def delete(self, product_id: int) -> None:
        self._repository.delete(product_id)

    def set_enabled(self, product: Product, enabled: bool) -> Product:
        product.enabled = enabled
        return self._repository.update(product)

    def record_found_listing(self, product_id: int, url: str, reference: str) -> Product:
        """Registra la publicación localizada y verificada de un producto.

        Se llama SOLO después de que el buscador haya confirmado una
        coincidencia (FOUND) y con valores reales extraídos de Facebook
        (no temporales ni dudosos). Actualiza únicamente
        `marketplace_url` / `marketplace_reference` en SQLite local;
        nunca toca Facebook.
        """
        product = self.get(product_id)
        if url:
            product.marketplace_url = url
        if reference:
            product.marketplace_reference = reference
        return self._repository.update(product)

    def record_publication(self, product_id: int, url: str, reference: str) -> Product:
        """Registra la publicación confirmada de un producto en Facebook.

        Se llama SOLO cuando la creación fue verificada
        (PUBLISHED_CONFIRMED), con la URL/referencia real de la
        publicación nueva extraída de Facebook. Actualiza
        `last_published_at` y el localizador `marketplace_url` /
        `marketplace_reference`; nunca toca el navegador.
        """
        from datetime import datetime

        product = self.get(product_id)
        product.last_published_at = datetime.now()
        if url:
            product.marketplace_url = url
        if reference:
            product.marketplace_reference = reference
        return self._repository.update(product)

    def record_phase_run(
        self,
        product_id: int,
        operation: str,
        status: str,
        error: str | None = None,
        *,
        listing_url: str | None = None,
        listing_reference: str | None = None,
        confidence: str | None = None,
        matched_title: str | None = None,
        matched_price: int | None = None,
        matched_at=None,
        new_title: str | None = None,
        new_price: float | None = None,
    ) -> AutomationRun | None:
        """Registra una fase del flujo de republicación en `automation_runs`
        (trazabilidad de la sección 15 del spec).

        Una fase es un evento puntual ya terminado, por lo que el run se
        abre y se cierra inmediatamente con el `status` indicado
        ("success" / "error").
        """
        if self._run_repository is None:
            logger.debug("No hay run_repository; omitiendo registro de fase")
            return None

        run = self._run_repository.start_run(
            product_id=product_id,
            operation=operation,
            listing_reference=listing_reference or None,
            listing_url=listing_url or None,
            confidence=confidence or None,
            matched_title=matched_title,
            matched_price=matched_price,
            matched_at=matched_at,
            new_title=new_title,
            new_price=new_price,
        )
        self._run_repository.finish_run(
            run_id=run.id,  # type: ignore[arg-type]
            status=status,
            error=error,
        )
        logger.info(
            "Fase registrada: id=%s product=%s op=%s status=%s",
            run.id,
            product_id,
            operation,
            status,
        )
        return run

    def record_deletion(
        self,
        product_id: int,
        result: str,
        confidence: str,
        listing_url: str,
        listing_reference: str,
        error: str | None = None,
    ) -> AutomationRun | None:
        """Persiste el resultado de una operación de eliminación.

        REGLA CRÍTICA (modificación 2 del spec):
        `last_deleted_at` SOLO se actualiza cuando `result` es exactamente
        'DELETED_CONFIRMED'. Para DELETE_UNCERTAIN, DELETE_FAILED o
        CANCELLED el campo permanece sin modificar: no podemos afirmar que
        la publicación fue eliminada.

        Devuelve el `AutomationRun` creado, o None si no hay repositorio
        de runs disponible.
        """
        from datetime import datetime

        # Solo actualizar last_deleted_at con confirmación real.
        if result == "DELETED_CONFIRMED":
            product = self.get(product_id)
            product.last_deleted_at = datetime.now()
            self._repository.update(product)
            logger.info(
                "last_deleted_at actualizado para producto %s (DELETED_CONFIRMED)",
                product_id,
            )
        else:
            logger.info(
                "last_deleted_at NO actualizado para producto %s (result=%s, no es DELETED_CONFIRMED)",
                product_id,
                result,
            )

        if self._run_repository is None:
            logger.debug("No hay run_repository; omitiendo registro de AutomationRun")
            return None

        status = "success" if result == "DELETED_CONFIRMED" else "error"
        run = self._run_repository.start_run(
            product_id=product_id,
            operation="delete",
            listing_reference=listing_reference or None,
            listing_url=listing_url or None,
            confidence=confidence or None,
        )
        self._run_repository.finish_run(
            run_id=run.id,  # type: ignore[arg-type]
            status=status,
            error=error,
        )
        logger.info(
            "AutomationRun registrado: id=%s product=%s op=delete status=%s",
            run.id,
            product_id,
            status,
        )
        return run

    # -- Internos -----------------------------------------------------------
    def _validate_or_raise(self, product: Product) -> None:
        errors = product.validate()
        if errors:
            raise ProductValidationError(field="(múltiples)", message="; ".join(errors))

    def _copy_images(self, folder_name: str, source_paths: list[str]) -> list[str]:
        """Copia imágenes a data/products/<folder_name>/NN.jpg y devuelve
        las rutas relativas a PRODUCTS_DIR (portables entre ejecuciones)."""
        target_dir = self._products_dir / folder_name
        target_dir.mkdir(parents=True, exist_ok=True)

        # Continuar la numeración si ya hay imágenes previas en la carpeta.
        existing = sorted(target_dir.glob("[0-9][0-9].*"))
        next_index = len(existing) + 1

        copied_relative_paths: list[str] = []
        for source in source_paths:
            source_path = Path(source)
            if source_path.suffix.lower() not in _SUPPORTED_IMAGE_EXTENSIONS:
                logger.warning("Imagen ignorada (extensión no soportada): %s", source_path)
                continue
            if not source_path.exists():
                logger.warning("Imagen ignorada (no existe): %s", source_path)
                continue

            try:
                # Validar que sea una imagen real, no solo la extensión.
                with Image.open(source_path) as img:
                    img.verify()
            except (UnidentifiedImageError, OSError) as exc:
                logger.warning("Imagen ignorada (no es una imagen válida): %s (%s)", source_path, exc)
                continue

            dest_name = f"{next_index:02d}{source_path.suffix.lower()}"
            dest_path = target_dir / dest_name
            shutil.copyfile(source_path, dest_path)

            relative = dest_path.relative_to(self._products_dir)
            copied_relative_paths.append(str(relative))
            next_index += 1

        logger.info("Copiadas %d imágenes a %s", len(copied_relative_paths), target_dir)
        return copied_relative_paths

    def resolve_image_path(self, relative_path: str) -> Path:
        """Convierte una ruta relativa guardada en el producto a una ruta
        absoluta real en disco (usada por la GUI y por la automatización
        para adjuntar archivos)."""
        return self._products_dir / relative_path
