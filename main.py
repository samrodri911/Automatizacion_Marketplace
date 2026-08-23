"""Punto de entrada de Marketplace Manager.

Ejecutar con:

    python main.py

(o `MarketplaceManager.exe` una vez empaquetado con PyInstaller).
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.core.config import app_config, ensure_directories
from app.core.logging_config import configure_logging, get_logger
from app.database.database import Database
from app.database.repositories import (
    AutomationRunRepository,
    MatchedListingsRepository,
    ProductRepository,
)
from app.gui.main_window import MainWindow
from app.services.matched_listing_service import MatchedListingService
from app.services.product_service import ProductService


def main() -> int:
    import app.core.veh_dump as veh_dump

    veh_dump.install()

    ensure_directories()
    log_path = configure_logging()
    logger = get_logger(__name__)
    logger.info("=== Iniciando %s ===", app_config.app_name)
    logger.info("Archivo de log: %s", log_path)

    database = Database()
    database.initialize()

    product_repository = ProductRepository(database)
    run_repository = AutomationRunRepository(database)
    product_service = ProductService(product_repository, run_repository=run_repository)
    matched_repository = MatchedListingsRepository(database)
    matched_service = MatchedListingService(matched_repository, product_service)

    app = QApplication(sys.argv)
    app.setApplicationName(app_config.app_name)
    app.setOrganizationName(app_config.organization_name)

    window = MainWindow(product_service, matched_service)
    window.show()

    exit_code = app.exec()
    logger.info("=== %s cerrado (código %s) ===", app_config.app_name, exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
