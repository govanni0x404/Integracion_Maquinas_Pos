#!/bin/bash
# =============================================================
#  reiniciar_linux.sh — Reinicia ApiPagoElectronico en Linux
# =============================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Reiniciando ApiPagoElectronico (Linux)..."
"${SCRIPT_DIR}/detener_linux.sh"
sleep 2
exec "${SCRIPT_DIR}/iniciar_linux.sh"
