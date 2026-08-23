#!/bin/bash
set -e

# Establecer DISPLAY por defecto si no se pasó uno externo
export DISPLAY=${DISPLAY:-:99}

# Si usamos la pantalla virtual por defecto (:99), iniciar Xvfb y servidor VNC/noVNC
if [ "$DISPLAY" = ":99" ]; then
    echo "============================================================"
    echo "Iniciando servidor de display virtual Xvfb en $DISPLAY..."
    Xvfb :99 -screen 0 1366x768x24 -ac +extension GLX +render -noreset &
    XVFB_PID=$!
    sleep 1

    if [ "${ENABLE_VNC:-1}" = "1" ]; then
        echo "Iniciando x11vnc en el puerto 5900..."
        x11vnc -display :99 -forever -shared -rfbport 5900 -nopw -quiet &

        echo "Iniciando noVNC (interfaz web) en http://0.0.0.0:6080/vnc.html ..."
        /usr/share/novnc/utils/novnc_proxy --vnc localhost:5900 --listen 6080 &
        echo "============================================================"
        echo " -> Accede a la interfaz gráfica desde tu navegador en:"
        echo "    http://localhost:6080/vnc.html"
        echo "============================================================"
    fi
fi

# Asegurar directorios de persistencia
mkdir -p data/products data/browser_profile logs screenshots

exec "$@"
