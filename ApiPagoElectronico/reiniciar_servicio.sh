#!/bin/bash
# =============================================================
#  reiniciar_servicio.sh — Reinicia ApiPagoElectronico en macOS
# =============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Reiniciando ApiPagoElectronico..."

"${SCRIPT_DIR}/detener_servicio.sh"
sleep 1
"${SCRIPT_DIR}/iniciar_servicio.sh"
