#!/bin/bash
# =============================================================
#  iniciar_servicio.sh — Inicia ApiPagoElectronico en macOS
#  Compatible con Apple Silicon y Intel
#
#  MODOS DE INICIO:
#   1. APP BUNDLE (producción): usa `open dist/ApiPagoElectronico.app`
#      → El sistema operativo gestiona la sesión GUI correctamente
#      → Ícono aparece en la barra de menú
#
#   2. PYTHON DIRECTO (desarrollo): corre en una nueva ventana Terminal
#      → Útil para ver errores en consola
#      → También muestra el ícono en la barra de menú
#
#  CROSS-PLATFORM NOTE:
#   - macOS:   usa `open .app` o Terminal con AppleScript
#   - Windows: usa `start ApiPagoElectronico.exe` o pythonw.exe
#   - Linux:   usa `nohup python3 app.py &` (sin display issues en systemd)
# =============================================================

set -uo pipefail

# ── Colores ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

APP_NAME="ApiPagoElectronico"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_APP="${SCRIPT_DIR}/dist/${APP_NAME}.app"
DIST_BIN="${SCRIPT_DIR}/dist/${APP_NAME}"
LOG_FILE="${SCRIPT_DIR}/dist/pos_gateway.log"
PID_FILE="${SCRIPT_DIR}/dist/pos_gateway.pid"

echo -e "${CYAN}${BOLD}"
echo "=============================================="
echo "  ${APP_NAME}"
echo "  Iniciando servicio..."
echo "=============================================="
echo -e "${NC}"

# ── 0. Leer .env ──────────────────────────────────────────────
ENV_FILE="${SCRIPT_DIR}/dist/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    # Fallback: buscar en directorio raíz del proyecto
    ENV_FILE="${SCRIPT_DIR}/.env"
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo -e "${RED}✘ No se encontró el archivo .env${NC}"
    echo "  Esperado en: ${SCRIPT_DIR}/dist/.env"
    exit 1
fi

HTTP_PORT="$(grep -E '^HTTP_PORT='    "$ENV_FILE" | cut -d'=' -f2 | tr -d ' \r' || echo '5005')"
NOMBRE_CAJA="$(grep -E '^NOMBRE_CAJA=' "$ENV_FILE" | cut -d'=' -f2 | tr -d ' \r' || echo 'N/A')"
ID_SUCURSAL="$(grep -E '^ID_SUCURSAL=' "$ENV_FILE" | cut -d'=' -f2 | tr -d ' \r' || echo 'N/A')"

echo "  Sucursal:  ${ID_SUCURSAL}"
echo "  Caja:      ${NOMBRE_CAJA}"
echo "  Puerto:    ${HTTP_PORT}"
echo ""

# ── 1. Verificar que no esté ya corriendo ────────────────────
echo -e "${CYAN}▶ Verificando si ya está corriendo...${NC}"
if curl -s --connect-timeout 2 "http://localhost:${HTTP_PORT}/status" &>/dev/null; then
    echo -e "${YELLOW}⚠  El servicio ya está activo en el puerto ${HTTP_PORT}${NC}"
    ESTADO="$(curl -s "http://localhost:${HTTP_PORT}/status" 2>/dev/null)"
    echo -e "   Estado: ${ESTADO}"
    echo ""
    echo -e "   Para detenerlo:   ${CYAN}./detener_servicio.sh${NC}"
    echo -e "   Para reiniciarlo: ${CYAN}./reiniciar_servicio.sh${NC}"
    exit 0
fi

# ── 2. Preparar modo de inicio ───────────────────────────────
echo ""

# MODO A: .app bundle compilado (producción — con GUI completa)
if [[ -d "$DIST_APP" ]]; then
    echo -e "${GREEN}▶ Modo: APP BUNDLE (producción con GUI)${NC}"
    echo -e "   Bundle: ${DIST_APP}"
    echo ""

    # Quitar cuarentena de Gatekeeper si aplica
    if xattr "$DIST_APP" 2>/dev/null | grep -q "com.apple.quarantine"; then
        echo -e "${YELLOW}  Removiendo cuarentena de Gatekeeper...${NC}"
        xattr -cr "$DIST_APP" 2>/dev/null || true
    fi

    # Usar `open` — el único método correcto para GUI en macOS
    # `open` conecta el proceso a la sesión del window server del usuario
    open "$DIST_APP"
    LAUNCH_METHOD="app_bundle"

# MODO B: Ejecutable binario compilado (sin .app)
elif [[ -f "$DIST_BIN" ]]; then
    echo -e "${YELLOW}▶ Modo: EJECUTABLE BINARIO${NC}"
    echo -e "   Binario: ${DIST_BIN}"
    echo ""
    xattr -cr "$DIST_BIN" 2>/dev/null || true
    open "$DIST_BIN"  # El binario onefile generado por PyInstaller también admite open
    LAUNCH_METHOD="binary"

# MODO C: Python directo (desarrollo — abre nueva ventana Terminal)
elif [[ -f "${SCRIPT_DIR}/app.py" ]]; then
    echo -e "${CYAN}▶ Modo: PYTHON DIRECTO (desarrollo)${NC}"
    echo ""

    # Buscar Python correcto
    if [[ -f "${SCRIPT_DIR}/venv/bin/python3" ]]; then
        PYTHON_BIN="${SCRIPT_DIR}/venv/bin/python3"
        echo -e "   Python: ${PYTHON_BIN} (venv)"
    elif command -v python3 &>/dev/null; then
        PYTHON_BIN="$(command -v python3)"
        echo -e "${YELLOW}   Python: ${PYTHON_BIN} (sistema — sin venv)${NC}"
    else
        echo -e "${RED}✘ python3 no encontrado. Instálalo con: brew install python${NC}"
        exit 1
    fi

    # En macOS, para que la GUI funcione, el proceso DEBE estar conectado
    # al window server. `nohup &` lo desconecta. Usamos AppleScript para
    # abrir una nueva ventana de Terminal que sí tiene acceso a GUI.
    echo -e "   Abriendo nueva ventana Terminal con entorno gráfico..."
    echo ""

    # Construir comando Python con venv activado
    CMD="cd '${SCRIPT_DIR}' && source venv/bin/activate 2>/dev/null || true && '${PYTHON_BIN}' '${SCRIPT_DIR}/app.py'"

    # AppleScript abre Terminal con el proceso conectado a la sesión GUI
    osascript <<APPLESCRIPT
tell application "Terminal"
    activate
    do script "${CMD}"
end tell
APPLESCRIPT

    LAUNCH_METHOD="python_terminal"

else
    echo -e "${RED}✘ No se encontró forma de iniciar el servicio.${NC}"
    echo "  Compila primero con: ${CYAN}./compilar_mac.sh${NC}"
    echo "  O asegúrate de tener app.py en: ${SCRIPT_DIR}"
    exit 1
fi

# ── 3. Esperar que levante el servidor HTTP ──────────────────
echo -e "${YELLOW}  Esperando que el servidor HTTP levante...${NC}"
MAX_WAIT=20
WAITED=0
while [[ $WAITED -lt $MAX_WAIT ]]; do
    if curl -s --connect-timeout 1 "http://localhost:${HTTP_PORT}/status" &>/dev/null; then
        break
    fi
    sleep 1
    WAITED=$((WAITED + 1))
    echo -n "."
done
echo ""

# ── 4. Verificar resultado ───────────────────────────────────
if curl -s --connect-timeout 2 "http://localhost:${HTTP_PORT}/status" &>/dev/null; then
    ESTADO="$(curl -s "http://localhost:${HTTP_PORT}/status" 2>/dev/null)"
    echo ""
    echo -e "${GREEN}${BOLD}✔ Servicio iniciado correctamente${NC}"
    echo -e "   URL:    ${CYAN}http://localhost:${HTTP_PORT}/status${NC}"
    echo -e "   Estado: ${ESTADO}"
    echo ""

    case "$LAUNCH_METHOD" in
        app_bundle|binary)
            echo -e "${YELLOW}El ícono 'TM' debería aparecer en la barra de menú superior.${NC}"
            echo -e "   Log: ${CYAN}${LOG_FILE}${NC}"
            ;;
        python_terminal)
            echo -e "${YELLOW}La app está corriendo en la nueva ventana Terminal.${NC}"
            echo -e "   Puedes cerrar esa ventana para detenerla."
            ;;
    esac
    echo ""
    echo -e "${YELLOW}Para detener: ${CYAN}./detener_servicio.sh${NC}"
else
    echo ""
    echo -e "${RED}✘ El servicio no respondió en ${MAX_WAIT}s${NC}"
    echo ""
    # Buscar log en múltiples ubicaciones
    for LOG_CANDIDATE in "${LOG_FILE}" "${SCRIPT_DIR}/pos_gateway.log" "$HOME/pos_gateway.log"; do
        if [[ -f "$LOG_CANDIDATE" ]]; then
            echo -e "${YELLOW}Log (${LOG_CANDIDATE}):${NC}"
            tail -15 "$LOG_CANDIDATE"
            break
        fi
    done
    echo ""
    echo -e "${CYAN}Sugerencias:${NC}"
    echo "  1. Compila de nuevo con: ./compilar_mac.sh"
    echo "  2. Verifica el .env en dist/.env"
    echo "  3. Revisa el log completo"
    exit 1
fi
