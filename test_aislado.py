"""Prueba A: Playwright puro, sin PySide6 ni QThread, mismo perfil persistente."""
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(__file__).parent / "data" / "browser_profile"

print("Iniciando Playwright...")
with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        viewport={"width": 1280, "height": 900},
        args=["--start-maximized"],
    )
    page = context.pages[0] if context.pages else context.new_page()
    print("Navegando a Facebook...")
    page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
    time.sleep(3)

    print("Navegando a Marketplace...")
    page.goto("https://www.facebook.com/marketplace/", wait_until="domcontentloaded")
    time.sleep(3)

    print("Navegando a 'Tus publicaciones'...")
    page.goto("https://www.facebook.com/marketplace/you/selling", wait_until="domcontentloaded")
    print("Listo. Ahora esperando 3 minutos SIN hacer nada (simula el punto donde crashea la app)...")

    for i in range(18):
        time.sleep(10)
        print(f"  ... {((i + 1) * 10)}s de espera, todavía viva")

    print("Sobrevivió 3 minutos sin crashear. Cerrando limpiamente.")
    context.close()
print("FIN del script.")