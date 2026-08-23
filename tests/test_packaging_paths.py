import sys
import importlib
import os
from pathlib import Path
import pytest
import app.core.config

@pytest.fixture(autouse=True)
def restore_config():
    yield
    # Restaurar el estado de desarrollo al finalizar cada test
    if hasattr(sys, "frozen"):
        delattr(sys, "frozen")
    if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
        del os.environ["PLAYWRIGHT_BROWSERS_PATH"]
    importlib.reload(app.core.config)

def test_development_mode_paths(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    importlib.reload(app.core.config)
    
    assert app.core.config.BASE_DIR.exists()
    assert (app.core.config.BASE_DIR / "main.py").exists()
    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == str(app.core.config.BASE_DIR / "playwright_browsers")

def test_frozen_mode_paths(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fake_exe = "C:\\Program Files\\MarketplaceManager\\MarketplaceManager.exe"
    monkeypatch.setattr(sys, "executable", fake_exe)
    
    # Simulamos el directorio temporal de PyInstaller
    fake_meipass = "C:\\Users\\User\\AppData\\Local\\Temp\\_MEI12345"
    monkeypatch.setattr(sys, "_MEIPASS", fake_meipass, raising=False)
    
    importlib.reload(app.core.config)
    
    assert app.core.config.BASE_DIR == Path("C:\\Program Files\\MarketplaceManager")
    assert app.core.config.DATA_DIR == Path("C:\\Program Files\\MarketplaceManager\\data")
    assert app.core.config.DB_PATH == Path("C:\\Program Files\\MarketplaceManager\\data\\marketplace.db")
    assert app.core.config.LOGS_DIR == Path("C:\\Program Files\\MarketplaceManager\\logs")
    assert app.core.config.BROWSER_PROFILE_DIR == Path("C:\\Program Files\\MarketplaceManager\\data\\browser_profile")
    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == str(Path(fake_meipass) / "playwright_browsers")
