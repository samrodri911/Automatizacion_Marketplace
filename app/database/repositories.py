"""Repositorios de acceso a datos.

Cada repositorio encapsula el SQL necesario para un agregado (Product,
AutomationRun) y devuelve/recibe siempre objetos tipados, nunca filas
crudas de sqlite3. Así la capa de servicios y la GUI nunca escriben SQL.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from app.core.exceptions import ProductNotFoundError, RepublishError, RepositoryError
from app.core.logging_config import get_logger
from app.database.database import Database
from app.models.matched_listing import (
    ACTIVE_STATUSES,
    ALLOWED_CONFIDENCE,
    MatchedListing,
)
from app.models.product import Product

logger = get_logger(__name__)


def _row_to_product(row: sqlite3.Row) -> Product:
    def _parse_dt(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    return Product(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        price=row["price"],
        category=row["category"],
        condition=row["condition"],
        location=row["location"],
        tags=Product.parse_tags(row["tags"]),
        images=Product.parse_images(row["images"]),
        enabled=bool(row["enabled"]),
        marketplace_url=row["marketplace_url"],
        marketplace_reference=row["marketplace_reference"],
        last_published_at=_parse_dt(row["last_published_at"]),
        last_deleted_at=_parse_dt(row["last_deleted_at"]),
        last_attempt_at=_parse_dt(row["last_attempt_at"]),
        last_success_at=_parse_dt(row["last_success_at"]),
        last_error=row["last_error"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


class ProductRepository:
    """Operaciones CRUD para productos."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def create(self, product: Product) -> Product:
        now = datetime.now().isoformat(timespec="seconds")
        try:
            with self._db.connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO products (
                        title, description, price, category, condition, location,
                        tags, images, enabled,
                        marketplace_url, marketplace_reference,
                        last_published_at, last_deleted_at, last_attempt_at,
                        last_success_at, last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        product.title,
                        product.description,
                        product.price,
                        product.category,
                        product.condition,
                        product.location,
                        product.tags_as_json(),
                        product.images_as_json(),
                        int(product.enabled),
                        product.marketplace_url,
                        product.marketplace_reference,
                        None,
                        None,
                        None,
                        None,
                        None,
                        now,
                        now,
                    ),
                )
                conn.commit()
                product.id = cursor.lastrowid
                product.created_at = datetime.fromisoformat(now)
                product.updated_at = datetime.fromisoformat(now)
            logger.info("Producto creado: id=%s title=%r", product.id, product.title)
            return product
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo crear el producto: {exc}") from exc

    def update(self, product: Product) -> Product:
        if product.id is None:
            raise RepositoryError("No se puede actualizar un producto sin id")

        now = datetime.now().isoformat(timespec="seconds")
        try:
            with self._db.connect() as conn:
                cursor = conn.execute(
                    """
                    UPDATE products SET
                        title = ?, description = ?, price = ?, category = ?,
                        condition = ?, location = ?, tags = ?, images = ?,
                        enabled = ?, marketplace_url = ?, marketplace_reference = ?,
                        last_published_at = ?, last_deleted_at = ?, last_attempt_at = ?,
                        last_success_at = ?, last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        product.title,
                        product.description,
                        product.price,
                        product.category,
                        product.condition,
                        product.location,
                        product.tags_as_json(),
                        product.images_as_json(),
                        int(product.enabled),
                        product.marketplace_url,
                        product.marketplace_reference,
                        product.last_published_at.isoformat() if product.last_published_at else None,
                        product.last_deleted_at.isoformat() if product.last_deleted_at else None,
                        product.last_attempt_at.isoformat() if product.last_attempt_at else None,
                        product.last_success_at.isoformat() if product.last_success_at else None,
                        product.last_error,
                        now,
                        product.id,
                    ),
                )
                conn.commit()
                if cursor.rowcount == 0:
                    raise ProductNotFoundError(f"No existe el producto id={product.id}")
                product.updated_at = datetime.fromisoformat(now)
            logger.info("Producto actualizado: id=%s title=%r", product.id, product.title)
            return product
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo actualizar el producto: {exc}") from exc

    def delete(self, product_id: int) -> None:
        try:
            with self._db.connect() as conn:
                cursor = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
                conn.commit()
                if cursor.rowcount == 0:
                    raise ProductNotFoundError(f"No existe el producto id={product_id}")
            logger.info("Producto eliminado: id=%s", product_id)
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo eliminar el producto: {exc}") from exc

    def get(self, product_id: int) -> Product:
        try:
            with self._db.connect() as conn:
                row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo leer el producto: {exc}") from exc

        if row is None:
            raise ProductNotFoundError(f"No existe el producto id={product_id}")
        return _row_to_product(row)

    def list_all(self) -> list[Product]:
        try:
            with self._db.connect() as conn:
                rows = conn.execute("SELECT * FROM products ORDER BY title COLLATE NOCASE").fetchall()
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo listar los productos: {exc}") from exc
        return [_row_to_product(row) for row in rows]

    def list_enabled(self) -> list[Product]:
        try:
            with self._db.connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM products WHERE enabled = 1 ORDER BY title COLLATE NOCASE"
                ).fetchall()
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo listar los productos activos: {exc}") from exc
        return [_row_to_product(row) for row in rows]


@dataclass
class AutomationRun:
    """Registro de una ejecución de automatización sobre un producto."""

    product_id: int
    operation: str  # "republish", "delete", "publish", ...
    status: str  # "running", "success", "error"
    started_at: datetime
    id: int | None = None
    finished_at: datetime | None = None
    error: str | None = None
    screenshot_path: str | None = None
    # Trazabilidad de la operación de eliminación (None en otros tipos de run)
    listing_reference: str | None = None
    listing_url: str | None = None
    confidence: str | None = None  # "HIGH" | "MEDIUM" | ...
    # Trazabilidad del flujo de republicación (sección 15 del spec)
    matched_title: str | None = None
    matched_price: int | None = None
    matched_at: datetime | None = None
    new_title: str | None = None
    new_price: float | None = None


class AutomationRunRepository:
    """Historial de ejecuciones de automatización, para depuración y auditoría."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def start_run(
        self,
        product_id: int,
        operation: str,
        listing_reference: str | None = None,
        listing_url: str | None = None,
        confidence: str | None = None,
        matched_title: str | None = None,
        matched_price: int | None = None,
        matched_at: datetime | None = None,
        new_title: str | None = None,
        new_price: float | None = None,
    ) -> AutomationRun:
        run = AutomationRun(
            product_id=product_id,
            operation=operation,
            status="running",
            started_at=datetime.now(),
            listing_reference=listing_reference,
            listing_url=listing_url,
            confidence=confidence,
            matched_title=matched_title,
            matched_price=matched_price,
            matched_at=matched_at,
            new_title=new_title,
            new_price=new_price,
        )
        try:
            with self._db.connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO automation_runs
                        (product_id, started_at, operation, status,
                         listing_reference, listing_url, confidence,
                         matched_title, matched_price, matched_at,
                         new_title, new_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.product_id,
                        run.started_at.isoformat(),
                        run.operation,
                        run.status,
                        run.listing_reference,
                        run.listing_url,
                        run.confidence,
                        run.matched_title,
                        run.matched_price,
                        run.matched_at.isoformat() if run.matched_at else None,
                        run.new_title,
                        run.new_price,
                    ),
                )
                conn.commit()
                run.id = cursor.lastrowid
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo registrar la ejecución: {exc}") from exc
        return run

    def finish_run(
        self,
        run_id: int,
        status: str,
        error: str | None = None,
        screenshot_path: str | None = None,
    ) -> None:
        try:
            with self._db.connect() as conn:
                conn.execute(
                    """
                    UPDATE automation_runs
                    SET status = ?, finished_at = ?, error = ?, screenshot_path = ?
                    WHERE id = ?
                    """,
                    (status, datetime.now().isoformat(), error, screenshot_path, run_id),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo actualizar la ejecución: {exc}") from exc

    def list_for_product(self, product_id: int) -> list[AutomationRun]:
        try:
            with self._db.connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM automation_runs WHERE product_id = ? ORDER BY started_at DESC",
                    (product_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo leer el historial: {exc}") from exc

        runs = []
        for row in rows:
            # Las columnas nuevas pueden ser None en runs anteriores.
            cols = row.keys()
            runs.append(
                AutomationRun(
                    id=row["id"],
                    product_id=row["product_id"],
                    operation=row["operation"],
                    status=row["status"],
                    started_at=datetime.fromisoformat(row["started_at"]),
                    finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
                    error=row["error"],
                    screenshot_path=row["screenshot_path"],
                    listing_reference=row["listing_reference"] if "listing_reference" in cols else None,
                    listing_url=row["listing_url"] if "listing_url" in cols else None,
                    confidence=row["confidence"] if "confidence" in cols else None,
                    matched_title=row["matched_title"] if "matched_title" in cols else None,
                    matched_price=row["matched_price"] if "matched_price" in cols else None,
                    matched_at=datetime.fromisoformat(row["matched_at"]) if row["matched_at"] else None,
                    new_title=row["new_title"] if "new_title" in cols else None,
                    new_price=row["new_price"] if "new_price" in cols else None,
                )
            )
        return runs


def _row_to_matched_listing(row: sqlite3.Row) -> MatchedListing:
    def _parse_dt(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    return MatchedListing(
        id=row["id"],
        product_id=row["product_id"],
        listing_url=row["listing_url"] or "",
        listing_reference=row["listing_reference"] or "",
        matched_title=row["matched_title"],
        matched_price=row["matched_price"],
        matched_price_raw=row["matched_price_raw"] or "",
        confidence=row["confidence"],
        status=row["status"],
        matched_at=_parse_dt(row["matched_at"]),
        new_title=row["new_title"],
        new_price=row["new_price"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


class MatchedListingsRepository:
    """Persistencia de los TARGETS congelados del flujo de republicación.

    Un producto puede tener como máximo UN target ACTIVO (lo garantiza el
    índice único parcial de la tabla). Los targets terminales permanecen
    como historial y permiten una nueva selección.
    """

    def __init__(self, database: Database) -> None:
        self._db = database

    def create(self, matched: MatchedListing) -> MatchedListing:
        if matched.confidence not in ALLOWED_CONFIDENCE:
            raise RepublishError(
                f"No se puede congelar un target con confianza {matched.confidence!r}; "
                f"solo se permite {sorted(ALLOWED_CONFIDENCE)}"
            )
        now = datetime.now().isoformat(timespec="seconds")
        matched_at = (matched.matched_at or datetime.now()).isoformat(timespec="seconds")
        try:
            with self._db.connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO matched_listings (
                        product_id, listing_url, listing_reference,
                        matched_title, matched_price, matched_price_raw,
                        confidence, status, matched_at, new_title, new_price,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        matched.product_id,
                        matched.listing_url,
                        matched.listing_reference,
                        matched.matched_title,
                        matched.matched_price,
                        matched.matched_price_raw,
                        matched.confidence,
                        matched.status,
                        matched_at,
                        matched.new_title,
                        matched.new_price,
                        now,
                        now,
                    ),
                )
                conn.commit()
                matched.id = cursor.lastrowid
                matched.matched_at = datetime.fromisoformat(matched_at)
                matched.created_at = datetime.fromisoformat(now)
                matched.updated_at = datetime.fromisoformat(now)
            logger.info("MatchedListing creado: id=%s product=%s status=%s", matched.id, matched.product_id, matched.status)
            return matched
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc) and "matched_listings" in str(exc):
                raise RepublishError(
                    f"El producto {matched.product_id} ya tiene un target de republicación activo; "
                    "cancela o completa el flujo en curso antes de seleccionar otro."
                ) from exc
            raise RepositoryError(f"No se pudo crear el target: {exc}") from exc
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo crear el target: {exc}") from exc

    def update(self, matched: MatchedListing) -> MatchedListing:
        if matched.id is None:
            raise RepositoryError("No se puede actualizar un target sin id")
        now = datetime.now().isoformat(timespec="seconds")
        try:
            with self._db.connect() as conn:
                cursor = conn.execute(
                    """
                    UPDATE matched_listings SET
                        listing_url = ?, listing_reference = ?,
                        matched_title = ?, matched_price = ?, matched_price_raw = ?,
                        confidence = ?, status = ?, matched_at = ?,
                        new_title = ?, new_price = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        matched.listing_url,
                        matched.listing_reference,
                        matched.matched_title,
                        matched.matched_price,
                        matched.matched_price_raw,
                        matched.confidence,
                        matched.status,
                        matched.matched_at.isoformat(timespec="seconds") if matched.matched_at else None,
                        matched.new_title,
                        matched.new_price,
                        now,
                        matched.id,
                    ),
                )
                conn.commit()
                if cursor.rowcount == 0:
                    raise RepublishError(f"No existe el target id={matched.id}")
                matched.updated_at = datetime.fromisoformat(now)
            logger.info("MatchedListing actualizado: id=%s status=%s", matched.id, matched.status)
            return matched
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo actualizar el target: {exc}") from exc

    def get(self, matched_id: int) -> MatchedListing:
        try:
            with self._db.connect() as conn:
                row = conn.execute("SELECT * FROM matched_listings WHERE id = ?", (matched_id,)).fetchone()
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo leer el target: {exc}") from exc
        if row is None:
            raise RepublishError(f"No existe el target id={matched_id}")
        return _row_to_matched_listing(row)

    def get_active_by_product(self, product_id: int) -> MatchedListing | None:
        active_placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        try:
            with self._db.connect() as conn:
                row = conn.execute(
                    f"""
                    SELECT * FROM matched_listings
                    WHERE product_id = ? AND status IN ({active_placeholders})
                    ORDER BY id DESC LIMIT 1
                    """,
                    (product_id, *sorted(ACTIVE_STATUSES)),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo consultar el target activo: {exc}") from exc
        return _row_to_matched_listing(row) if row else None

    def list_active(self) -> list[MatchedListing]:
        active_placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        try:
            with self._db.connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT * FROM matched_listings
                    WHERE status IN ({active_placeholders})
                    ORDER BY id DESC
                    """,
                    tuple(sorted(ACTIVE_STATUSES)),
                ).fetchall()
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo listar targets activos: {exc}") from exc
        return [_row_to_matched_listing(row) for row in rows]

    def list_historical(self, product_id: int) -> list[MatchedListing]:
        try:
            with self._db.connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM matched_listings WHERE product_id = ? ORDER BY id DESC",
                    (product_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo leer el historial de targets: {exc}") from exc
        return [_row_to_matched_listing(row) for row in rows]

    def transition(self, matched_id: int, new_status: str) -> MatchedListing:
        matched = self.get(matched_id)
        matched.status = new_status
        return self.update(matched)
