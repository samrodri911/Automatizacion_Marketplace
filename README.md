# Marketplace Manager 🚀

Aplicación de escritorio para Windows diseñada para gestionar y **republicar automáticamente publicaciones en Facebook Marketplace**, eliminando de forma segura la publicación existente y creando una nueva con datos idénticos o actualizados, optimizando el alcance orgánico sin depender de la función limitada de "renovar" de Facebook.

---

## 📌 Características Principales

- **Gestión Local de Productos (CRUD):** Creación, edición, activación/desactivación y organización de productos con fotos y descripciones locales mediante SQLite.
- **Detección y Matching Inteligente:** Escaneo automatizado de tu sección *"Tus publicaciones"* (`/marketplace/you/selling`) con algoritmos de coincidencia conservadores (`HIGH`, `MEDIUM`, `LOW`, `AMBIGUOUS`).
- **Eliminación Segura y Verificable:** Verificación pre y post-eliminación semántica para garantizar que nunca se elimine la publicación equivocada.
- **Creación y Publicación Automática:** Subida de fotografías, rellenado de títulos, descripciones, categorías y verificación de éxito en Facebook.
- **Cola de Republicación Múltiple:** Procesamiento secuencial de múltiples productos seleccionados con reportes de estado, pausas automáticas y opción de detención segura.
- **Intervención Humana Guiada:** Detección de estados que requieran atención (login manual, CAPTCHAs, verificaciones 2FA) pausando la ejecución de forma transparente y reanudable.
- **Navegador Chromium Visible y Persistente:** Mantiene tu sesión de Facebook activa entre ejecuciones mediante un perfil local dedicado (`data/browser_profile/`).
- **Empaquetado Portátil para Windows:** Ejecutable standalone (`MarketplaceManager.exe`) autocontenido que no requiere tener Python ni Node.js instalados en la máquina final.

---

## 🏗️ Arquitectura del Sistema

El proyecto sigue una estricta arquitectura por capas desacoplada:

```text
GUI (PySide6)
  │
  ▼
Services (ProductService, MatchedListingService, AutomationService, RepublishQueue)
  │
  ▼
Automation (MarketplaceAdapter, ListingScanner, ListingDeleter, ListingCreator)
  │
  ▼
Playwright Async / AsyncBridge (Control del Navegador Chromium)
  │
  ▼
Facebook Marketplace
```

### Principios Fundamentales:
1. **La base de datos local (SQLite) es la única fuente de verdad:** Facebook nunca sobreescribe tus datos locales.
2. **Aislamiento de Hilos (Threading):** La GUI corre en el hilo principal de Qt, `AutomationService` en un `QThread` dedicado, y Playwright en su propio `AsyncBridge` con bucle `asyncio` independiente, garantizando una UI fluida y sin bloqueos.
3. **Selectores Semánticos:** Interacción basada en roles de accesibilidad, etiquetas y texto visible (evitando selectores CSS dinámicos o coordenadas fijas).
4. **Seguridad y Privacidad:** La aplicación **nunca** almacena ni solicita contraseñas de Facebook; utiliza la sesión local persistente del navegador Chromium.

---

## 📁 Estructura del Repositorio

```text
marketplace-manager/
├── app/
│   ├── automation/          # Adaptadores de Facebook, Scanner, Deleter, Creator y Selectores
│   ├── core/                # Configuración global, logging, async_bridge y forensics
│   ├── database/            # Conexión SQLite, esquemas y repositorios
│   ├── gui/                 # Ventana principal, paneles de productos y diálogos
│   ├── models/              # Modelos tipados (Product, MatchedListing, QueueItem)
│   └── services/            # Lógica de negocio (ProductService, AutomationService, RepublishQueue)
├── data/                    # Datos persistentes del usuario (ignorado en git)
│   ├── marketplace.db       # Base de datos SQLite
│   ├── browser_profile/     # Perfil y cookies de Chromium (Sesión Facebook)
│   └── products/            # Imágenes de los productos
├── logs/                    # Archivos de registro diario
├── playwright_browsers/     # Binarios de Chromium locales para empaquetado
├── tests/                   # Suite de pruebas automatizadas (unitarias e integración)
├── MarketplaceManager.spec  # Configuración reproducible de PyInstaller
├── main.py                  # Punto de entrada de la aplicación
├── requirements.txt         # Dependencias del proyecto
└── pytest.ini               # Configuración de pruebas
```

---

## 🛠️ Instalación y Uso en Desarrollo

### Requisitos Previos:
- Python 3.11+
- Windows 10 u 11

### Configuración del Entorno:

1. **Clonar el repositorio:**
   ```powershell
   git clone https://github.com/TU_USUARIO/marketplace-manager.git
   cd marketplace-manager
   ```

2. **Crear y activar el entorno virtual:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\activate
   ```

3. **Instalar dependencias:**
   ```powershell
   pip install -r requirements.txt
   ```

4. **Instalar navegadores de Playwright:**
   ```powershell
   playwright install chromium
   ```

5. **Ejecutar la aplicación:**
   ```powershell
   python main.py
   ```

---

## 📦 Empaquetado como Aplicación Windows (`.exe`)

El proyecto está preparado para compilarse en una distribución autocontenida usando **PyInstaller** en modo carpeta (`--onedir`).

### Pasos para compilar:

1. **Descargar Chromium en la carpeta local del proyecto:**
   ```powershell
   $env:PLAYWRIGHT_BROWSERS_PATH="c:\Users\User\Documents\Proyectos Personales\marketplace-manager\playwright_browsers"
   .venv\Scripts\playwright install chromium
   ```

2. **Compilar el ejecutable:**
   - **Versión de Producción (Sin consola visible):**
     ```powershell
     .venv\Scripts\pyinstaller --noconfirm MarketplaceManager.spec
     ```
   - **Versión de Diagnóstico / Debug (Con consola negra para depurar):**
     ```powershell
     $env:MARKETPLACE_DEBUG_BUILD="1"; .venv\Scripts\pyinstaller --noconfirm MarketplaceManager.spec
     ```

3. **Distribución:**
   El resultado estará en `dist/MarketplaceManager/`. El usuario final simplemente debe hacer doble clic en `MarketplaceManager.exe` para abrir la aplicación.

---

## 🐳 Ejecución con Docker y Docker Compose

El proyecto incluye soporte completo para ejecutarse en contenedores Docker mediante un display virtual (Xvfb) y acceso visual vía navegador web (**noVNC**).

### 1. Iniciar la aplicación en Docker:
```bash
docker compose up app
```
Una vez iniciado, abre tu navegador web en:
👉 **[http://localhost:6080/vnc.html](http://localhost:6080/vnc.html)** (haz clic en *Connect*) para interactuar directamente con la interfaz gráfica de Marketplace Manager y el navegador Chromium.

### 2. Ejecutar la suite de pruebas en Docker:
```bash
docker compose run --rm tests
```

*Los volúmenes `data/`, `logs/` y `screenshots/` están montados automáticamente para conservar la base de datos y la sesión de Facebook entre reinicios del contenedor.*

---

## 🧪 Pruebas Automatizadas

La aplicación cuenta con una suite completa de más de 240 pruebas unitarias y de integración que cubren la lógica de negocio, parsing, algoritmos de matching, FSM y rutas de empaquetado (sin depender de conexión real a Facebook):

```powershell
.venv\Scripts\python -m pytest -q
```

Para validar la sintaxis y compilación de todo el proyecto:
```powershell
python -m compileall app tests
```

---

## 🔒 Seguridad y Buenas Prácticas

- **Cero evasión maliciosa:** La aplicación no oculta la automatización ni manipula fingerprints para evadir bloqueos; respeta los límites humanos y se detiene ante requerimientos de seguridad.
- **Navegador Visible:** El navegador siempre se ejecuta en modo visible (`headless=False`) permitiendo que el usuario supervise cada paso.
- **Sin exposición de datos:** Las credenciales, perfiles de navegación (`data/browser_profile/`) y bases de datos personales (`data/marketplace.db`) están estrictamente excluidas del control de versiones mediante `.gitignore`.
