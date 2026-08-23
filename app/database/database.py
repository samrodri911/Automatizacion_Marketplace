"""Gestión de la conexión y el esquema de SQLite.

Se usa el módulo estándar `sqlite3` (sin ORM) a propósito: la base de
datos es local, de un único usuario, y el esquema es pequeño. Esto evita
una dependencia adicional para algo que sqlite3 resuelve perfectamente.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.config import DB_PATH
from app.core.exceptions import RepositoryError
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    title                   TEXT NOT NULL,
    description             TEXT NOT NULL DEFAULT '',
    price                   REAL NOT NULL DEFAULT 0,
    category                TEXT NOT NULL DEFAULT '',
    condition               TEXT NOT NULL DEFAULT '',
    location                TEXT NOT NULL DEFAULT '',
    tags                    TEXT NOT NULL DEFAULT '[]',
    images                  TEXT NOT NULL DEFAULT '[]',
    enabled                 INTEGER NOT NULL DEFAULT 1,
    marketplace_url         TEXT,
    marketplace_reference   TEXT,
    last_published_at       TEXT,
    last_deleted_at         TEXT,
    last_attempt_at         TEXT,
    last_success_at         TEXT,
    last_error              TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id          INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    operation           TEXT NOT NULL,
    status              TEXT NOT NULL,
    error               TEXT,
    screenshot_path     TEXT,
    listing_reference   TEXT,
    listing_url         TEXT,
    confidence          TEXT,
    matched_title       TEXT,
    matched_price       INTEGER,
    matched_at          TEXT,
    new_title           TEXT,
    new_price           REAL
);

CREATE INDEX IF NOT EXISTS idx_automation_runs_product_id
    ON automation_runs(product_id);

-- Target congelado de eliminación para el flujo de republicación.
-- Un único target ACTIVO por producto (índice único parcial); los targets
-- terminales (republished/blocked/cancelled) quedan como historial y
-- permiten iniciar una nueva selección.
CREATE TABLE IF NOT EXISTS matched_listings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id          INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    listing_url         TEXT,
    listing_reference   TEXT,
    matched_title       TEXT NOT NULL,
    matched_price       INTEGER,
    matched_price_raw   TEXT NOT NULL DEFAULT '',
    confidence          TEXT NOT NULL,
    status              TEXT NOT NULL,
    matched_at          TEXT NOT NULL,
    new_title           TEXT,
    new_price           REAL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_matched_listings_active_product
    ON matched_listings(product_id)
    WHERE status IN (
        'selected', 'editing', 'awaiting_confirm', 'deleting', 'deleted',
        'creating', 'publishing', 'verifying_publication'
    );

-- Migración segura: añadir columnas si no existen (idempotente).
CREATE TABLE IF NOT EXISTS _schema_migrations (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    applied_at TEXT NOT NULL
);
"""


class Database:
    """Punto único de acceso a la conexión SQLite de la aplicación."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DB_PATH

    # Migraciones aplicadas sobre esquemas anteriores (ALTER TABLE idempotente).
    _MIGRATIONS: list[tuple[str, str]] = [
        (
            "add_deletion_columns_to_automation_runs",
            "ALTER TABLE automation_runs ADD COLUMN listing_reference TEXT",
        ),
        (
            "add_deletion_url_to_automation_runs",
            "ALTER TABLE automation_runs ADD COLUMN listing_url TEXT",
        ),
        (
            "add_deletion_confidence_to_automation_runs",
            "ALTER TABLE automation_runs ADD COLUMN confidence TEXT",
        ),
        (
            "add_republish_matched_title_to_automation_runs",
            "ALTER TABLE automation_runs ADD COLUMN matched_title TEXT",
        ),
        (
            "add_republish_matched_price_to_automation_runs",
            "ALTER TABLE automation_runs ADD COLUMN matched_price INTEGER",
        ),
        (
            "add_republish_matched_at_to_automation_runs",
            "ALTER TABLE automation_runs ADD COLUMN matched_at TEXT",
        ),
        (
            "add_republish_new_title_to_automation_runs",
            "ALTER TABLE automation_runs ADD COLUMN new_title TEXT",
        ),
        (
            "add_republish_new_price_to_automation_runs",
            "ALTER TABLE automation_runs ADD COLUMN new_price REAL",
        ),
    ]

    def initialize(self) -> None:
        """Crea el archivo de base de datos y el esquema si no existen.

        Después del CREATE IF NOT EXISTS inicial aplica las migraciones
        pendientes registradas en `_MIGRATIONS`. Cada migración es
        idempotente: si ya existe la columna, el ALTER TABLE falla y se
        captura silenciosamente; si ya estaba registrada en
        `_schema_migrations`, se omite.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.connect() as conn:
                conn.executescript(_SCHEMA)
                conn.commit()
            logger.info("Base de datos inicializada en %s", self.db_path)
            self._apply_migrations()
        except sqlite3.Error as exc:
            raise RepositoryError(f"No se pudo inicializar la base de datos: {exc}") from exc

    def _apply_migrations(self) -> None:
        """Aplica las migraciones pendientes de forma idempotente."""
        for name, sql in self._MIGRATIONS:
            try:
                with self.connect() as conn:
                    already = conn.execute(
                        "SELECT 1 FROM _schema_migrations WHERE name = ?", (name,)
                    ).fetchone()
                    if already:
                        continue
                    try:
                        conn.execute(sql)
                    except sqlite3.OperationalError as col_exc:
                        # La columna ya existe en BDs creadas con el schema nuevo;
                        # ignoramos el error pero seguimos registrando la migración.
                        logger.debug("Migración '%s' ya aplicada (columna existente): %s", name, col_exc)
                    conn.execute(
                        "INSERT INTO _schema_migrations (name, applied_at) VALUES (?, ?)",
                        (name, __import__("datetime").datetime.now().isoformat()),
                    )
                    conn.commit()
                    logger.info("Migración aplicada: %s", name)
            except sqlite3.Error as exc:
                logger.warning("Error aplicando migración '%s': %s", name, exc)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Context manager que entrega una conexión con row_factory configurado
        y claves foráneas activadas."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()
