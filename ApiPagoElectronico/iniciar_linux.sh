#!/bin/bash
# =============================================================
#  iniciar_linux.sh — Inicia ApiPagoElectronico en Linux
#  Compatible con Ubuntu, Debian, CentOS, Fedora, Arch
#
#  MODOS DE INICIO:
#   1. EJECUTABLE compilado (producción): dist/ApiPagoElectronico
#   2. PYTHON DIRECTO (desarrollo): python3 app.py
#
#  Sin dependencias de GUI: corre en modo servidor HTTP puro.
#  Para GUI de bandeja de sistema instala: python3-gi, libappindicator3
# =============================================================

set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

APP_NAME="ApiPagoElectronico"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${SCRIPT_DIR}/dist/${APP_NAME}"
DIST_BIN="${DIST_DIR}/${APP_NAME}"
LOG_FILE="${SCRIPT_DIR}/dist/pos_gateway.log"
PID_FILE="${SCRIPT_DIR}/dist/pos_gateway.pid"

echo -e "${CYAN}${BOLD}"
echo "=============================================="
echo "  ${APP_NAME}"
echo "  Iniciando en Linux..."
echo "=============================================="
echo -e "${NC}"

# ── 0. Leer .env ──────────────────────────────────────────────
ENV_FILE="${DIST_DIR}/.env"
[[ ! -f "$ENV_FILE" ]] && ENV_FILE="${SCRIPT_DIR}/dist/.env"
[[ ! -f "$ENV_FILE" ]] && ENV_FILE="${SCRIPT_DIR}/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo -e "${RED}✘ No se encontró el archivo .env${NC}"
    echo "  Esperado en: ${SCRIPT_DIR}/dist/.env  o  ${SCRIPT_DIR}/.env"
    exit 1
fi

HTTP_PORT="$(grep -E '^HTTP_PORT='    "$ENV_FILE" | cut -d'=' -f2 | tr -d ' \r' || echo '5005')"
NOMBRE_CAJA="$(grep -E '^NOMBRE_CAJA=' "$ENV_FILE" | cut -d'=' -f2 | tr -d ' \r' || echo 'N/A')"
ID_SUCURSAL="$(grep -E '^ID_SUCURSAL=' "$ENV_FILE" | cut -d'=' -f2 | tr -d ' \r' || echo 'N/A')"

echo "  Sucursal:  ${ID_SUCURSAL}"
echo "  Caja:      ${NOMBRE_CAJA}"
echo "  Puerto:    ${HTTP_PORT}"
echo ""

# ── 1. Verificar si ya corre ─────────────────────────────────
echo -e "${CYAN}▶ Verificando si ya está corriendo...${NC}"
if curl -s --connect-timeout 2 "http://localhost:${HTTP_PORT}/status" &>/dev/null; then
    echo -e "${YELLOW}⚠  El servicio ya está activo en el puerto ${HTTP_PORT}${NC}"
    echo -e "   Estado: $(curl -s "http://localhost:${HTTP_PORT}/status" 2>/dev/null)"
    echo ""
    echo -e "   Para detenerlo:   ${CYAN}./detener_linux.sh${NC}"
    echo -e "   Para reiniciarlo: ${CYAN}./reiniciar_linux.sh${NC}"
    exit 0
fi

echo ""

# ── 2. Preparar modo de inicio ───────────────────────────────
if [[ -f "$DIST_BIN" ]]; then
    echo -e "${GREEN}▶ Modo: EJECUTABLE COMPILADO (producción)${NC}"
    echo -e "   Binario: ${DIST_BIN}"
    chmod +x "$DIST_BIN"

    # Lanzar en background, redirigir salida al log
    nohup "$DIST_BIN" >> "$LOG_FILE" 2>&1 &
    APP_PID=$!
    echo "$APP_PID" > "$PID_FILE"
    echo -e "   PID: ${APP_PID}"
    LAUNCH_METHOD="binary"

elif [[ -f "${SCRIPT_DIR}/app.py" ]]; then
    echo -e "${CYAN}▶ Modo: PYTHON DIRECTO (desarrollo)${NC}"

    # Buscar Python
    if [[ -f "${SCRIPT_DIR}/venv/bin/python3" ]]; then
        PYTHON_BIN="${SCRIPT_DIR}/venv/bin/python3"
        echo -e "   Python: ${PYTHON_BIN} (venv)"
    elif command -v python3 &>/dev/null; then
        PYTHON_BIN="$(command -v python3)"
        echo -e "${YELLOW}   Python: ${PYTHON_BIN} (sistema — sin venv)${NC}"
    else
        echo -e "${RED}✘ python3 no encontrado.${NC}"
        echo "   Instálalo con: sudo apt install python3  (Debian/Ubuntu)"
        echo "              o: sudo dnf install python3   (Fedora/CentOS)"
        exit 1
    fi

    cd "${SCRIPT_DIR}"
    nohup "$PYTHON_BIN" "${SCRIPT_DIR}/app.py" >> "$LOG_FILE" 2>&1 &
    APP_PID=$!
    echo "$APP_PID" > "$PID_FILE"
    echo -e "   PID: ${APP_PID}"
    LAUNCH_METHOD="python"

else
    echo -e "${RED}✘ No se encontró ejecutable ni app.py${NC}"
    echo "  Compila primero con: ${CYAN}./compilar_linux.sh${NC}"
    exit 1
fi

# ── 3. Esperar que levante ───────────────────────────────────
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
    echo -e "   Panel:  ${CYAN}http://localhost:${HTTP_PORT}/panel${NC}"
    echo -e "   Estado: ${ESTADO}"
    echo -e "   Log:    ${CYAN}${LOG_FILE}${NC}"
    echo ""
    echo -e "${YELLOW}Para detener: ${CYAN}./detener_linux.sh${NC}"
else
    echo ""
    echo -e "${RED}✘ El servicio no respondió en ${MAX_WAIT}s${NC}"
    for LOG_CANDIDATE in "${LOG_FILE}" "${SCRIPT_DIR}/pos_gateway.log" "$HOME/pos_gateway.log"; do
        if [[ -f "$LOG_CANDIDATE" ]]; then
            echo -e "${YELLOW}Últimas líneas del log (${LOG_CANDIDATE}):${NC}"
            tail -15 "$LOG_CANDIDATE"
            break
        fi
    done
    rm -f "$PID_FILE"
    exit 1
fi
