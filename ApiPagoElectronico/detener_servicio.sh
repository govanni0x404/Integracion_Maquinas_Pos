#!/bin/bash
# =============================================================
#  detener_servicio.sh — Detiene ApiPagoElectronico en macOS
# =============================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

APP_NAME="ApiPagoElectronico"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${SCRIPT_DIR}/pos_gateway.pid"

echo -e "${CYAN}${BOLD}"
echo "=============================================="
echo "  ${APP_NAME} — Deteniendo servicio..."
echo "=============================================="
echo -e "${NC}"

# ── Función para matar un PID ────────────────────────────────
_kill_pid() {
    local pid="$1"
    kill "$pid" 2>/dev/null || true
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
        sleep 1
    fi
    if kill -0 "$pid" 2>/dev/null; then
        echo -e "${RED}✘ No se pudo terminar el proceso ${pid}.${NC}"
        return 1
    fi
    echo -e "${GREEN}${BOLD}✔ Proceso ${pid} terminado correctamente.${NC}"
}

if [[ ! -f "$PID_FILE" ]]; then
    echo -e "${YELLOW}⚠  No se encontró PID file. Buscando proceso activo...${NC}"

    # Leer puerto del .env
    HTTP_PORT="5005"
    if [[ -f "${SCRIPT_DIR}/.env" ]]; then
        _PORT="$(grep -E '^HTTP_PORT=' "${SCRIPT_DIR}/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
        [[ -n "$_PORT" ]] && HTTP_PORT="$_PORT"
    fi
    # También buscar en dist/.env
    if [[ -f "${SCRIPT_DIR}/dist/.env" ]]; then
        _PORT="$(grep -E '^HTTP_PORT=' "${SCRIPT_DIR}/dist/.env" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
        [[ -n "$_PORT" ]] && HTTP_PORT="$_PORT"
    fi

    FOUND_PID=""

    # 1. Buscar por puerto HTTP (funciona con .app y con python)
    FOUND_PID="$(lsof -ti tcp:"${HTTP_PORT}" 2>/dev/null | head -1 || true)"

    # 2. Buscar por nombre del ejecutable (.app bundle o binario compilado)
    if [[ -z "$FOUND_PID" ]]; then
        FOUND_PID="$(pgrep -f "ApiPagoElectronico" 2>/dev/null | grep -v "$$" | head -1 || true)"
    fi

    # 3. Buscar por python app.py (modo desarrollo)
    if [[ -z "$FOUND_PID" ]]; then
        FOUND_PID="$(pgrep -f "python.*app\.py" 2>/dev/null | head -1 || true)"
    fi

    if [[ -n "$FOUND_PID" ]]; then
        echo -e "   Proceso encontrado: PID ${FOUND_PID} (puerto ${HTTP_PORT})"
        _kill_pid "$FOUND_PID"
    else
        echo -e "   Ningún proceso encontrado en puerto ${HTTP_PORT}."
    fi
    exit 0
fi

# ── Usar PID file ────────────────────────────────────────────
PID="$(cat "$PID_FILE" 2>/dev/null || echo '')"

if [[ -z "$PID" ]]; then
    echo -e "${YELLOW}⚠  PID file vacío. Limpiando...${NC}"
    rm -f "$PID_FILE"
    exit 0
fi

if ! kill -0 "$PID" 2>/dev/null; then
    echo -e "${YELLOW}⚠  El proceso (PID ${PID}) ya no está activo.${NC}"
    rm -f "$PID_FILE"
    echo -e "${GREEN}✔ PID file eliminado.${NC}"
    exit 0
fi

echo -e "   Enviando SIGTERM al proceso PID ${PID}..."
_kill_pid "$PID"
rm -f "$PID_FILE"
