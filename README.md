# Marketplace Manager

Aplicación de escritorio para Windows que automatiza la **republicación**
de tus propias publicaciones en Facebook Marketplace: elimina una
publicación existente y crea una nueva con exactamente los mismos datos,
en vez de usar la función "renovar publicación" de Facebook.

> ⚠️ Este proyecto automatiza tu propio navegador con Playwright para
> repetir acciones que tú mismo harías manualmente en tu cuenta. No
> resuelve CAPTCHAs, no evade verificaciones de seguridad ni intenta
> ocultar la automatización: cuando Facebook pide una acción manual, la
> app se detiene y te espera.

---

## Estado actual del proyecto

**Iteración 1 — Fundación: completa.**

**Iteración 2 — Navegación de Marketplace: completa.**

**Iteración 3 — Localización y verificación segura: completa.**

**Iteración 4 — Eliminación segura y verificable: completa.**

**Iteración 5 — Flujo completo por producto (MATCH → EDITAR → ELIMINAR → CREAR → PUBLICAR): completa.**

Lo que funciona hoy:

- Interfaz gráfica (PySide6) para crear, editar, eliminar y listar
  productos, con checkboxes para selección.
- Base de datos SQLite local (`data/marketplace.db`) con las tablas
  `products` y `automation_runs`.
- Gestión de fotografías: al crear/editar un producto, las imágenes
  seleccionadas se copian a `data/products/<slug-del-producto>/`.
- Integración con Playwright: botón "Abrir Chromium y comprobar sesión"
  que lanza Chromium **visible**, con un **perfil persistente**, navega
  a Facebook y detecta si hay sesión iniciada.
- Si no hay sesión, la app espera a que inicies sesión manualmente en la
  propia ventana de Chromium y, cuando pulsas "Ya inicié sesión /
  Continuar", vuelve a comprobar sin reiniciar nada.
- **Navegación de Marketplace**: tras confirmar la sesión (o al pulsar el
  botón de prueba "Marketplace → Tus publicaciones"), la app navega a
  Marketplace y luego a "Tus publicaciones" (`/marketplace/you/selling`),
  y detecta de forma **semántica** (roles de accesibilidad, texto visible;
  nunca coordenadas ni clases CSS) cuándo la sección cargó: cabecera
  "Tus publicaciones", tabs "Activos/Vendidos/...", o el estado vacío.
- Si Facebook pide una acción manual durante la navegación (login,
  CAPTCHA, verificación), la app se **pausa** (`WAITING_USER`), deja el
  navegador abierto y se retoma exactamente donde quedó al pulsar
  "Continuar".
- **Localización automática de la publicación** (Iteración 3, integrada en
  el escaneo): al confirmar la sesión, la app escanea "Tus publicaciones"
  (barrido con scroll acotado: máximo de scrolls, presupuesto de tiempo y
  detención si no hay contenido nuevo). El matcher compara **título
  normalizado por tokens**, **precio**, **número de fotos** y **referencia
  previa registrada**, y devuelve un resultado **conservador**: `FOUND`,
  `MEDIUM_CONFIDENCE`, `LOW_CONFIDENCE`, `AMBIGUOUS`, `NOT_FOUND` o
  `SEARCH_LIMIT_REACHED`. Ante la duda (dos candidatos demasiado parecidos)
  siempre responde `AMBIGUOUS`, nunca decide por ti. El escaneo es **100%
  solo lectura**; la búsqueda/matching vive en el backend y no tiene botón
  dedicado en la GUI.
- El panel "Producto y republicación" muestra la publicación encontrada
  para el producto seleccionado con su coincidencia (🟢 ALTA / MEDIUM /
  LOW / AMBIGUOUS / NO_MATCH).
- **Persistencia segura**: solo se guarda en SQLite
  `marketplace_url`/`marketplace_reference` cuando el resultado fue
  `FOUND` y la publicación encontrada trae una URL o referencia real
  extraída de Facebook. Nunca se guardan candidatos ambiguos ni dudosos.
- **Eliminación segura y verificable** (Iteración 4, integrada en el flujo
  de republicación): no hay botón de eliminar suelto. Solo el flujo
  "🔄 Republicar" elimina, y únicamente con una coincidencia `HIGH` y
  localizador real. Tras la confirmación explícita en un diálogo dedicado,
  `ListingDeleter` navega, abre el menú, elimina y **verifica** que
  Facebook dejó de mostrar la publicación (`DELETED_CONFIRMED` /
  `DELETE_UNCERTAIN` / `DELETE_FAILED`). Si se interrumpe a mitad
  (p. ej. CAPTCHA), al reanudar **verifica, no reintenta** la eliminación a
  ciegas.
- **Flujo "Republicar"** (Iteración 5, un producto): el botón
  **"🔄 Republicar"** es la ÚNICA acción principal; se habilita solo con
  una publicación `HIGH` del escaneo para el producto seleccionado. La
  edición de los datos de la NUEVA publicación se hace ANTES con
  **"✏️ Editar datos"** (editor reutilizado). El flujo completo es
  `MATCH → congelar target → confirmar → eliminar → verificar eliminación
  → crear → publicar → verificar publicación`:
  - El `MatchedListing` congela el target (URL/referencia/título/precio
    que mostró Facebook) y **jamás** se vuelve a derivar del producto
    editado; al editar el producto solo se guarda un snapshot de
    trazabilidad (`new_title`/`new_price`). Editar **no re-matchea ni
    invalida** el target congelado.
  - **Ciclo de vida del target**: queda congelado hasta que el flujo
    termina o se cancela. Se limpia explícitamente solo al cancelar la
    republicación, al cambiar de producto (solo targets pre-confirmación),
    o al borrar el producto (cualquier fase). Un target post-confirmación
    (deleting/deleted/...) bloquea el botón: debe reanudarse.
  - Un diálogo de confirmación muestra la **publicación encontrada**
    (original congelada) frente a la **nueva publicación** (datos
    editados) antes de actuar.
  - Tras `DELETED_CONFIRMED` se crea y publica la publicación nueva con
    `ListingCreator` (subida de fotos, relleno del formulario, verificación
    posterior obligatoria: `PUBLISHED_CONFIRMED` / `PUBLISH_UNCERTAIN` /
    `PUBLISH_FAILED`). Sin `DELETED_CONFIRMED` **no se crea nada**.
  - **Reanudación al arrancar**: si cierras la app a mitad del flujo, al
    volver (con la sesión y "Tus publicaciones" listas) la app continúa el
    target activo **verificando antes de actuar** — nunca re-elimina ni
    crea un segundo anuncio a ciegas.
- 165 tests automatizados (modelo, repositorios, servicios, FSM,
  selectores, `ListingCreator`, flujo de republicación a nivel de servicio,
  despacho GUI ↔ señales sin `QMetaObject.invokeMethod`, y la integración
  GUI en modo `offscreen`) que no dependen de un navegador real.

Lo que **todavía no existe**: la republicación automática **en lote** de
varios productos (botón "Republicar seleccionados"). El flujo actual está
pensado para **un producto a la vez**.

---

## Requisitos

- Windows 10/11 (o cualquier SO para desarrollo; el `.exe` final es para Windows).
- Python 3.11 o superior.
- Conexión a internet (solo para instalar dependencias y los binarios de Chromium de Playwright; la app en sí funciona 100% local, sin servidores externos).

## Instalación

```bash
# 1. Clonar/copiar el proyecto y entrar en la carpeta
cd marketplace-manager

# 2. Crear un entorno virtual (recomendado)
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac (solo para desarrollo)

# 3. Instalar dependencias de Python
pip install -r requirements.txt

# 4. Instalar el navegador Chromium que usará Playwright
playwright install chromium
```

## Cómo ejecutar la aplicación

```bash
python main.py
```

Se abrirá la ventana principal de Marketplace Manager. La base de datos
SQLite y las carpetas de datos se crean automáticamente en el primer
arranque (`data/`, `logs/`, `screenshots/`).

## Cómo crear la base de datos

No hace falta ningún paso manual: `main.py` llama a
`Database.initialize()` al arrancar, que crea `data/marketplace.db` y
las tablas necesarias si no existen todavía. Si quieres empezar de cero,
simplemente borra ese archivo (perderás los productos guardados, pero
nunca tu sesión de Facebook, que vive en `data/browser_profile/`).

## Cómo agregar productos

1. Pulsa **"+ Nuevo producto"**.
2. Completa título, descripción, precio, categoría, condición,
   ubicación y tags (separados por coma).
3. Pulsa **"+ Agregar fotos"** y selecciona una o varias imágenes
   (`.jpg`, `.jpeg`, `.png`, `.webp`). Se copian automáticamente a
   `data/products/<slug-del-producto>/`, así que puedes borrar los
   archivos originales después sin problema.
4. Pulsa **Aceptar**. El producto se guarda en SQLite sin publicarse en
   Facebook todavía; republicar (flujo de un producto) se hace desde la
   sección de publicaciones escaneadas.

Puedes marcar un producto como **inactivo** desde el editor (checkbox
"Activo") para excluirlo de futuras republicaciones masivas sin
borrarlo.

## Cómo funciona el perfil persistente (sesión de Facebook)

La primera vez que pulsas **"Abrir Chromium y comprobar sesión"**:

1. Se lanza una ventana de Chromium controlada por Playwright, siempre
   **visible** (nunca en modo headless).
2. Chromium usa una carpeta de perfil propia
   (`data/browser_profile/`), separada de tu navegador normal.
3. La app navega a Facebook y comprueba si ya hay sesión iniciada.
4. Si **no** la hay, verás el formulario de login normal de Facebook
   dentro de esa misma ventana de Chromium. Inicia sesión tú mismo,
   como lo harías normalmente (usuario/contraseña, verificación en dos
   pasos, etc.). **La aplicación nunca ve ni guarda tu contraseña.**
5. Cuando termines, vuelve a la app y pulsa **"Ya inicié sesión /
   Continuar"**. La app vuelve a comprobar el estado de la página sin
   reiniciar nada.
6. Una vez detectada la sesión, Playwright la guarda en
   `data/browser_profile/` (cookies, almacenamiento local, etc. — igual
   que cualquier perfil de Chrome/Chromium normal). En la siguiente
   ejecución de la app, esa sesión debería seguir activa y el paso de
   login manual no debería ser necesario, salvo que Facebook la cierre
   por su cuenta (por seguridad, inactividad, cambio de contraseña, etc.).

`data/browser_profile/` **nunca se sube a git** (ver `.gitignore`) y
nunca se procesa ni se lee su contenido por la aplicación más allá de lo
que hace el propio Chromium: la app no exporta cookies ni credenciales.

## Cómo probar la localización de una publicación (Iteración 3)

La búsqueda/matching de "Tus publicaciones" es **100% solo lectura** y
ocurre de forma automática en el escaneo: no elimina, no edita, no publica
nada en Facebook. Sirve para que la app localice y **verifique** la
publicación real de un producto y guarde (si es seguro) su localizador
(`marketplace_url`/`marketplace_reference` en SQLite). No hay botón de
"Buscar" en la GUI: el panel "Producto y republicación" muestra el
resultado del escaneo.

Prueba real (manual):

1. Ejecuta la aplicación: `python main.py`.
2. Selecciona un producto existente en "Mis productos".
3. Pulsa **"Abrir Chromium y comprobar sesión"** y completa el login si
   hace falta (ver "Cómo funciona el perfil persistente").
4. Al confirmar la sesión, la app escanea "Tus publicaciones" con scroll
   acotado, comparando título, precio, nº de fotos y referencia previa.
   Las coincidencias seguras se marcan con **🟢 HIGH**.
5. Selecciona el producto y mira el panel "Producto y republicación": verás
   la publicación encontrada, su coincidencia (🟢 ALTA / MEDIUM / LOW /
   AMBIGUOUS / NO_MATCH) y el estado.
6. Si el resultado fue `FOUND` con una URL/referencia real, la app guarda
   solo el localizador en la base local (mira el panel "Registro" para
   confirmar). Si fue ambiguo o dudoso, **no guarda nada**.
7. **No modifica nada**: ni elimina, ni edita, ni publica, ni marca como
   vendido.

Soporte de intervención humana: si durante el escaneo Facebook pide
login/CAPTCHA/verificación, la app se pausa (`WAITING_USER`), deja el
navegador abierto y muestra un aviso. Resuelve la acción en la ventana de
Chromium y pulsa **"Ya inicié sesión / Continuar"**: el escaneo se
reanuda, sin cerrar el navegador ni reiniciar nada.

## Cómo realizar una republicación (flujo de un producto)

*(La republicación automática en lote de varios productos sigue pendiente;
el flujo actual está pensado para UN producto a la vez.)*

Flujo completo de un producto:

1. **Conectar**: pulsa "Abrir Chromium y comprobar sesión" y completa el
   login si hace falta.
2. **Escaneo automático**: al confirmar la sesión, la app navega a
   "Tus publicaciones" y escanea las publicaciones, marcando las
   coincidencias seguras con **🟢 HIGH** (auto-seleccionadas).
3. **Seleccionar**: haz clic en el producto en "Mis productos" (o en una
   publicación `HIGH` del escaneo, que lo selecciona automáticamente). El
   panel "Producto y republicación" muestra la publicación encontrada y se
   habilita el botón **"🔄 Republicar"** (solo con coincidencia `HIGH` y
   localizador real; MEDIUM/LOW/AMBIGUOUS/NO_MATCH lo dejan deshabilitado).
4. **Editar (opcional, antes de republicar)**: pulsa **"✏️ Editar datos"**
   y cambia título, descripción, precio, fotos, etc. Al guardar, el target
   congelado (URL/referencia/título/precio que mostró Facebook) **no se
   modifica**: solo se guarda un snapshot de trazabilidad. Editar no
   re-matchea ni invalida el target.
5. **Confirmar**: al pulsar "🔄 Republicar", el diálogo muestra la
   publicación encontrada (original congelada) frente a la nueva
   publicación (editada) y exige confirmación explícita con el botón
   "🗑️ Eliminar y republicar".
6. **Eliminar**: la app elimina la publicación original y **verifica** su
   ausencia. Si la eliminación no se puede confirmar
   (`DELETE_UNCERTAIN`/`DELETE_FAILED`), el flujo se **bloquea** y NO se
   crea nada.
7. **Crear y publicar**: con la eliminación confirmada, la app navega al
   formulario de nueva publicación, sube las fotos del producto, rellena
   los campos con los datos editados, pulsa Publicar y **verifica** que el
   anuncio quedó activo. El localizador nuevo se guarda en el producto.
8. Si Facebook pide una acción manual (CAPTCHA/login/verificación) en
   cualquier punto, la app se pausa (`WAITING_USER`), deja el navegador
   abierto y espera a que pulses **"Continuar"**.

**Ciclo de vida del target**: la coincidencia `HIGH` queda congelada
como `MatchedListing` hasta que el flujo termina o se cancela. Se limpia
explícitamente solo al cancelar la republicación, al cambiar de producto
(solo si aún no se confirmó), o al borrar el producto. Si cierras la app
a mitad de una eliminación/creación, al volver el flujo **se reanuda**
verificando primero, nunca re-elimina ni duplica.

### Prueba real controlada (Fase A y Fase B)

**Fase A (sin riesgo, en la app, sin tocar Facebook):**
1. Conecta y escanea hasta ver una publicación `HIGH`.
2. Pulsa "✏️ Editar datos", edita título/descripción/precio/fotos y guarda.
3. Verifica en `data/marketplace.db` (o en `logs/`) que
   `matched_listings.listing_url/reference/matched_title/matched_price`
   quedan intactos y que **no** se ejecutó ningún escaneo/matcher nuevo.
4. Cancela la republicación (cancelar el diálogo de confirmación) y
   confirma que el target queda como `cancelled` (terminal). Vuelve a
   seleccionar el producto y comprueba que **no queda un target viejo**
   asociado: el panel vuelve a proponer la coincidencia `HIGH` fresca.

**Fase B (con UNA publicación prescindible):**
1. Repite hasta el paso "Confirmar" de arriba.
2. Acepta "Eliminar y republicar".
3. Comprueba en el panel "Registro" la secuencia:
   `DELETED_CONFIRMED` → creación → `PUBLISHED_CONFIRMED`, y que el
   producto guardó `last_published_at` + `marketplace_url/reference`
   nuevos y el target quedó `republished`.
4. **Corte-control de reanudación**: repite el flujo, pero cierra la app
   justo después de la eliminación (target en `deleted`). Al volver a
   abrirla y conectar la sesión, la app debe **reanudar sola** la creación
   (verificando primero), sin re-eliminar ni duplicar.

## Cómo actuar ante una verificación (CAPTCHA, login, confirmación de identidad...)

Cuando la automatización pide una acción manual en cualquier punto del
proceso (navegación, escaneo, búsqueda, eliminación o publicación), la
aplicación:

1. Deja el navegador **abierto**, tal como está.
2. Pausa la automatización (estado `WAITING_USER`).
3. Muestra un aviso en la interfaz pidiéndote que completes la acción
   en la ventana de Chromium.
4. Cuando pulsas **"Continuar"**, la app comprueba el estado actual de
   la página y **retoma exactamente donde se quedó** — nunca reinicia
   el proceso completo para ese producto. En eliminación/publicación
   interrumpidas, primero **verifica** el estado real antes de actuar.

## Cómo revisar logs

Los logs se guardan en `logs/AAAA-MM-DD.log` (uno por día), con el
mismo formato que se muestra en el panel "Registro" de la ventana
principal. Útiles para depurar qué pasó exactamente en una ejecución
anterior, especialmente si Facebook cambió algo en su interfaz.

## Cómo revisar screenshots

Cuando la automatización de republicación esté implementada, cada error
importante guardará automáticamente una captura de pantalla en
`screenshots/AAAA-MM-DD_<producto>_error.png`, para poder ver
exactamente en qué pantalla de Facebook ocurrió el problema.

---

## Ejecutar los tests

```bash
pip install -r requirements.txt   # incluye pytest
pytest -q
```

Los tests cubren el modelo `Product`, el `MatchedListing` (target
congelado), los repositorios SQLite, el servicio de productos (incluida
la copia de imágenes), el servicio de republicación, la máquina de
estados, la lógica de selectores/clasificación de Marketplace y de
verificación de publicación, el `ListingCreator`, el flujo de
republicación a nivel de servicio (con fakes de Playwright) y la
**integración GUI ↔ señales** (habilitación de botones, payloads,
persistencia segura del localizador, diálogos y recuperación al arrancar,
en modo `offscreen`). **No** dependen de un navegador real ni de
Facebook, así que pueden ejecutarse en cualquier máquina, incluida una
sin interfaz gráfica.

La automatización real contra Facebook (`app/automation/browser.py` y
`app/automation/marketplace.py`) se prueba por separado, de forma
manual/interactiva, porque depende de un navegador real y de tu cuenta
de Facebook. Para probar la Iteración 2: abre el navegador, confirma la
sesión y pulsa "Probar: Marketplace → Tus publicaciones". Para probar la
Iteración 3: sigue los pasos de la sección "Cómo probar la localización
de una publicación (Iteración 3)".

---

## Arquitectura

```text
GUI (PySide6)
   │
   ▼
Services (ProductService, MatchedListingService, AutomationService)
   │
   ▼
Automation (MarketplaceAdapter + selectors.py, listing_scanner.py,
           listing_finder.py, listing_deleter.py, listing_creator.py)
   │
   ▼
Playwright (BrowserManager)
   │
   ▼
Facebook Marketplace
```

Principios:

- La GUI nunca contiene lógica de negocio ni de automatización: solo
  construye la interfaz y delega en la capa de servicios.
- `ProductService` es la única puerta de entrada a la base de datos
  desde el resto de la app; la GUI nunca ejecuta SQL.
- Toda la automatización del navegador vive bajo `app/automation/`,
  aislada del resto. `BrowserManager` solo gestiona el ciclo de vida del
  navegador; `MarketplaceAdapter` conoce la lógica de Marketplace y
  delega la clasificación de páginas en `selectors.py` (lógica pura y
  testeable). Si Facebook cambia su interfaz, el objetivo es tener que
  tocar solo esta capa.
- La automatización corre en un `QThread` dedicado (ver
  `AutomationService`), nunca en el hilo principal de la GUI, para que
  la ventana no se congele mientras Chromium navega.
- Facebook **nunca** es la fuente de verdad de los datos de un
  producto: siempre lo es la base de datos SQLite local.

### Estructura de carpetas

```text
marketplace-manager/
├── app/
│   ├── core/            # configuración, logging, excepciones
│   ├── database/        # conexión SQLite + repositorios
│   ├── models/          # Product, MatchedListing, Listing (dataclasses tipadas)
│   ├── services/        # ProductService, MatchedListingService, AutomationService
│   ├── automation/      # BrowserManager, MarketplaceAdapter, selectors, estados
│   │                    # listing_scanner, listing_finder, listing_deleter, listing_creator
│   └── gui/             # MainWindow, ProductEditorDialog, diálogos de confirmación
├── data/
│   ├── marketplace.db   # SQLite (se crea automáticamente)
│   ├── browser_profile/ # perfil persistente de Chromium (sesión de Facebook)
│   └── products/        # fotos de cada producto, una carpeta por producto
├── logs/                # un archivo .log por día
├── screenshots/         # capturas de errores durante la automatización
├── tests/               # tests que no dependen de un navegador real
├── main.py
├── requirements.txt
├── pytest.ini
└── .gitignore
```

---

## Empaquetado como `.exe`

*(Se documentará con más detalle cuando el flujo de automatización esté
completo, pero el proyecto ya está preparado para esto: `app/core/config.py`
detecta si corre "congelado" con PyInstaller y ajusta las rutas de datos
para que se guarden junto al `.exe`, no en una carpeta temporal.)*

```bash
pip install pyinstaller
pyinstaller --name MarketplaceManager --onefile --noconsole main.py
```

Tras el build, `dist/MarketplaceManager.exe` necesitará que el usuario
final ejecute una vez `playwright install chromium` (o distribuir los
binarios de Chromium junto al `.exe`) para que la automatización del
navegador funcione.

---

## Seguridad y límites deliberados

- La app **nunca** pide ni guarda tu contraseña de Facebook.
- La app **nunca** exporta ni lee cookies para robarlas: solo usa el
  perfil persistente normal de Chromium tal como lo haría cualquier
  navegador.
- La app **nunca** intenta resolver CAPTCHAs, evadir verificaciones de
  seguridad, ocultar que es una automatización, ni saltarse límites
  impuestos por Facebook. Cuando Facebook pide algo así, la app se
  detiene y te espera.
- El navegador siempre corre en modo **visible**, nunca `headless=True`.
- La automatización nunca hace clic en coordenadas fijas: usa selectores
  semánticos de Playwright (roles, labels, texto) para ser más robusta
  ante cambios de Facebook.

---

## Hoja de ruta (próximas iteraciones)

2. **Navegación de Marketplace**: abrir Marketplace → "Tus publicaciones" y confirmar que la sección carga correctamente. ✅ **Completada (Iteración 2).**
3. **Localización segura**: buscar una publicación existente y mostrar la coincidencia encontrada, sin eliminar nada todavía. ✅ **Completada (Iteración 3).**
4. **Eliminación**: eliminar una publicación ya verificada y confirmar que Facebook la eliminó de verdad. ✅ **Completada (Iteración 4).**
5. **Creación + publicación**: crear una publicación nueva, subir fotos, rellenar los campos, publicar y verificar éxito. ✅ **Completada (Iteración 5, flujo de un producto).**
6. **Flujo completo "Republicar"**: MATCH → congelar target → confirmar → eliminar → crear → publicar → verificar, con ciclo de vida del target y reanudación segura al reiniciar. ✅ **Completada (Iteración 5).**
7. **Intervención humana**: integrar `WAITING_USER` en el flujo real de republicación (eliminación y publicación). ✅ **Completada (Iteración 5).**
8. **Procesamiento múltiple**: republicar varios productos seleccionados, secuencialmente, con progreso, pausa, detención y resumen final. ⏳ **Pendiente** (por diseño, el flujo actual es de un producto a la vez).

Cada iteración se implementa, se prueba y se deja la app en un estado
ejecutable antes de avanzar a la siguiente.
