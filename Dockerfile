# syntax=docker/dockerfile:1
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

# Evitar prompts interactivos durante la instalación de paquetes
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Instalar dependencias del sistema para Qt6 (PySide6) y herramientas de visualización X11 / noVNC
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    libgl1-mesa-glx \
    libgl1-mesa-dri \
    libegl1 \
    libxkbcommon-x11-0 \
    libxcb-cursor0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-shape0 \
    libxcb-xfixes0 \
    libxcb-xinerama0 \
    libxcb-sync1 \
    libfontconfig1 \
    libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias de Python
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código fuente del proyecto
COPY . /app/

# Dar permisos de ejecución al script de entrada
RUN chmod +x /app/entrypoint.sh

# Exponer puertos: 6080 para acceso web (noVNC) y 5900 para cliente VNC
EXPOSE 6080 5900

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "main.py"]
