#!/bin/bash
# =============================================================
#  detener_linux.sh — Detiene ApiPagoElectronico en Linux
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
PID_FILE="${SCRIPT_DIR}/dist/pos_gateway.pid"

# Buscar también el .pid en raíz (modo desarrollo)
[[ ! -f "$PID_FILE" ]] && PID_FILE="${SCRIPT_DIR}/pos_gateway.pid"

echo -e "${CYAN}${BOLD}"
echo "=============================================="
echo "  ${APP_NAME} — Deteniendo servicio (Linux)..."
echo "=============================================="
echo -e "${NC}"

_kill_pid() {
    local PID="$1"
    echo -e "   Enviando SIGTERM al proceso PID ${PID}..."
    kill "$PID" 2>/dev/null || true
    for i in {1..5}; do
        sleep 1
        kill -0 "$PID" 2>/dev/null || break
        echo -e "   Esperando... (${i}s)"
    done
    if kill -0 "$PID" 2>/dev/null; then
        echo -e "${YELLOW}   Forzando con SIGKILL...${NC}"
        kill -9 "$PID" 2>/dev/null || true
        sleep 1
    fi
    if kill -0 "$PID" 2>/dev/null; then
        echo -e "${RED}✘ No se pudo terminar el proceso ${PID}.${NC}"
        return 1
    fi
    echo -e "${GREEN}${BOLD}✔ Proceso ${PID} terminado.${NC}"
    return 0
}

# ── Por PID file ─────────────────────────────────────────────
if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || echo '')"
    if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
        _kill_pid "$PID"
        rm -f "$PID_FILE"
        exit 0
    else
        echo -e "${YELLOW}⚠  PID ${PID} ya no está activo. Limpiando PID file...${NC}"
        rm -f "$PID_FILE"
    fi
else
    echo -e "${YELLOW}⚠  No se encontró PID file. Buscando proceso por nombre...${NC}"
fi

# ── Fallback: buscar por nombre de proceso ───────────────────
FOUND_PIDS="$(pgrep -f "${APP_NAME}" 2>/dev/null || pgrep -f "python.*app.py" 2>/dev/null || true)"

if [[ -n "$FOUND_PIDS" ]]; then
    for PID in $FOUND_PIDS; do
        echo -e "   Encontrado proceso PID ${PID}. Terminando..."
        _kill_pid "$PID" || true
    done
    rm -f "$PID_FILE"
    echo -e "${GREEN}✔ Servicio detenido.${NC}"
else
    echo -e "${YELLOW}⚠  Ningún proceso activo encontrado.${NC}"
    echo -e "   El servicio puede no estar corriendo."
fi
