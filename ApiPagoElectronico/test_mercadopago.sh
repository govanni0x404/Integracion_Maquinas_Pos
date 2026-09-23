#!/bin/bash
# =============================================================
#  test_mercadopago.sh — Prueba de pago Mercado Pago Point
#  Envía una petición real a /pago con type:mercadopago
#
#  Dispositivos MP registrados en esta cuenta:
#    NEWLAND_N950__N950NCC503616517  (terminal activa)
# =============================================================

set -euo pipefail

# ── Colores ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

echo -e "${CYAN}${BOLD}"
echo "=============================================="
echo "  Test Pago — Mercado Pago Point"
echo "=============================================="
echo -e "${NC}"

# ── 1. Leer .env ──────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
    echo -e "${RED}✘ No se encontró .env en: ${ENV_FILE}${NC}"
    exit 1
fi

HTTP_PORT="$(grep -E '^HTTP_PORT='    "$ENV_FILE" | cut -d'=' -f2 | tr -d ' \r' || echo '5005')"
ID_SUCURSAL="$(grep -E '^ID_SUCURSAL=' "$ENV_FILE" | cut -d'=' -f2 | tr -d ' \r' || echo '1')"
NOMBRE_CAJA="$(grep -E '^NOMBRE_CAJA=' "$ENV_FILE" | cut -d'=' -f2 | tr -d ' \r' || echo 'caja_1')"
# Las credenciales Basic Auth no están en el .env (viven en el código del
# servicio): se toman de POS_API_USER / POS_API_PASS o se piden por consola.
AUTH_USER="${POS_API_USER:-}"
AUTH_PASS="${POS_API_PASS:-}"
TIMEOUT_SERVER="$(grep -E '^TIMEOUT_SERVER=' "$ENV_FILE" | cut -d'=' -f2 | tr -d ' \r' || echo '120')"
MP_ACCESS_TOKEN_ENV="$(grep -E '^MP_ACCESS_TOKEN=' "$ENV_FILE" | cut -d'=' -f2- | tr -d ' \r' || echo '')"

# Dispositivos MP registrados (formato: MODELO__SERIAL)
# Estos vienen del campo 'identificacion' en la tabla pago_electronico
MP_DEVICES=(
    "NEWLAND_N950__N950NCC503616517"
)

BASE_URL="http://localhost:${HTTP_PORT}"

echo -e "  Servicio: ${BASE_URL}"
echo -e "  Sucursal: ${ID_SUCURSAL}"
echo -e "  Caja:     ${NOMBRE_CAJA}"
echo ""
echo -e "  ${BOLD}Dispositivos MP Point disponibles:${NC}"
for i in "${!MP_DEVICES[@]}"; do
    echo -e "   [$((i+1))] ${GREEN}${MP_DEVICES[$i]}${NC}"
done
echo ""

# ── 2. Verificar que el servicio esté corriendo ───────────────
echo -e "${CYAN}▶ Verificando servicio...${NC}"
STATUS_CODE="$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 "${BASE_URL}/status" 2>/dev/null || echo '000')"

if [[ "$STATUS_CODE" == "000" ]]; then
    echo -e "${RED}✘ El servicio no responde en ${BASE_URL}${NC}"
    echo -e "   Inicia el servicio primero (python app.py o ApiPagoElectronico.exe)"
    exit 1
fi
echo -e "${GREEN}✔ Servicio activo (HTTP ${STATUS_CODE})${NC}"
echo ""

# ── 3. Solicitar datos al usuario ─────────────────────────────

if [[ -z "${MP_ACCESS_TOKEN_ENV}" ]]; then
    echo -e "${RED}✘ Falta MP_ACCESS_TOKEN en .env${NC}"
    echo -e "   Agrega: ${YELLOW}MP_ACCESS_TOKEN=APP_USR-...${NC}"
    exit 1
fi

# Credenciales Basic Auth del servicio
if [[ -z "$AUTH_USER" ]]; then
    echo -n "  Usuario API: "
    read -r AUTH_USER
fi
if [[ -z "$AUTH_PASS" ]]; then
    echo -n "  Clave API: "
    read -rs AUTH_PASS
    echo ""
fi

# Terminal ID del dispositivo MP Point
echo ""
echo -e "${YELLOW}Terminal ID del dispositivo Mercado Pago Point:${NC}"
echo -e "  (Debe ser el ID del dispositivo físico — ej: NEWLAND_N950__N950NCC503616517)"
echo -e "  (Presiona Enter para usar: ${GREEN}${MP_DEVICES[0]}${NC})"
echo -n "  > "
read -r MP_TERMINAL_ID
MP_TERMINAL_ID="${MP_TERMINAL_ID:-${MP_DEVICES[0]}}"

# Validar formato terminal_id (debe contener __ y no ser POS_XX)
if [[ ! "$MP_TERMINAL_ID" =~ __.+ ]]; then
    echo -e ""
    echo -e "${RED}✘ Terminal ID inválido: '${MP_TERMINAL_ID}'${NC}"
    echo -e "   Los IDs de MP Point tienen formato: MODELO__SERIAL"
    echo -e "   Ejemplo: ${YELLOW}NEWLAND_N950__N950NCC503616517${NC}"
    echo -e "   Busca el ID en: https://www.mercadopago.cl/point/devices"
    exit 1
fi

# Monto
echo ""
echo -e "${YELLOW}Monto a cobrar (en pesos, sin decimales):${NC}"
echo -n "  > "
read -r MONTO

if ! [[ "$MONTO" =~ ^[0-9]+$ ]] || [[ "$MONTO" -le 0 ]]; then
    echo -e "${RED}✘ Monto inválido: ${MONTO}${NC}"
    exit 1
fi

# Timeout
echo ""
echo -e "${YELLOW}Timeout en segundos (Enter = ${TIMEOUT_SERVER}s):${NC}"
echo -n "  > "
read -r TIMEOUT_INPUT
TIMEOUT="${TIMEOUT_INPUT:-$TIMEOUT_SERVER}"

# ── 4. Confirmar ──────────────────────────────────────────────
echo ""
echo -e "${CYAN}─────────────────────────────────────────────${NC}"
echo -e "  ${BOLD}Resumen de la prueba:${NC}"
echo -e "  Tipo:       mercadopago"
echo -e "  Monto:      \$${MONTO}"
echo -e "  Terminal:   ${MP_TERMINAL_ID}"
echo -e "  Token:      configurado en .env"
echo -e "  Timeout:    ${TIMEOUT}s"
echo -e "${CYAN}─────────────────────────────────────────────${NC}"
echo ""
echo -e "${YELLOW}¿Continuar? [S/n]:${NC} "
read -r CONFIRMAR
CONFIRMAR="${CONFIRMAR:-s}"
if [[ "$(echo "$CONFIRMAR" | tr '[:upper:]' '[:lower:]')" == "n" ]]; then
    echo "Cancelado."
    exit 0
fi

# ── 5. Construir payload JSON ─────────────────────────────────
PAYLOAD=$(cat <<EOF
{
  "type": "mercadopago",
  "id_sucursal": "${ID_SUCURSAL}",
  "nombre_caja": "${NOMBRE_CAJA}",
  "terminal_id": "${MP_TERMINAL_ID}",
  "amount": ${MONTO},
  "timeout": ${TIMEOUT}
}
EOF
)

# ── 6. Enviar petición ────────────────────────────────────────
echo ""
echo -e "${CYAN}▶ Enviando petición a ${BASE_URL}/pago ...${NC}"
echo -e "${YELLOW}   (Esperando hasta ${TIMEOUT}s — el usuario debe aprobar en el dispositivo MP)${NC}"
echo ""

CURL_TIMEOUT=$(( TIMEOUT + 15 ))
START_TIME="$(date +%s)"

RESPONSE="$(curl -s \
    --max-time "${CURL_TIMEOUT}" \
    -X POST "${BASE_URL}/pago" \
    -u "${AUTH_USER}:${AUTH_PASS}" \
    -H "Content-Type: application/json" \
    -d "${PAYLOAD}" \
    -w "\n__HTTP_CODE__:%{http_code}" \
    2>/dev/null || echo '__HTTP_CODE__:000')"

END_TIME="$(date +%s)"
ELAPSED=$(( END_TIME - START_TIME ))

# Separar body del código HTTP
HTTP_CODE="$(echo "$RESPONSE" | grep -o '__HTTP_CODE__:[0-9]*' | cut -d':' -f2)"
BODY="$(echo "$RESPONSE" | sed 's/__HTTP_CODE__:[0-9]*//')"

# ── 7. Mostrar resultado ──────────────────────────────────────
echo -e "${CYAN}─────────────────────────────────────────────${NC}"
echo -e "  ${BOLD}Resultado (${ELAPSED}s)${NC}"
echo -e "  HTTP Code: ${HTTP_CODE}"
echo -e "${CYAN}─────────────────────────────────────────────${NC}"
echo ""

# Formatear JSON si está disponible jq o python3
if command -v jq &>/dev/null; then
    echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
elif command -v python3 &>/dev/null; then
    echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY"
else
    echo "$BODY"
fi

echo ""

# ── 8. Interpretar resultado ──────────────────────────────────
if [[ "$HTTP_CODE" == "000" ]]; then
    echo -e "${RED}✘ Timeout o sin conexión con el servicio.${NC}"
    exit 1
fi

# Extraer status del JSON
if command -v python3 &>/dev/null; then
    MP_STATUS="$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo '?')"
else
    MP_STATUS="$(echo "$BODY" | grep -o '"status":"[^"]*"' | head -1 | cut -d'"' -f4 || echo '?')"
fi

case "$MP_STATUS" in
    approved)
        echo -e "${GREEN}${BOLD}✔ PAGO APROBADO por Mercado Pago${NC}"
        echo -e "   Terminal: ${MP_TERMINAL_ID}" ;;
    rejected)
        echo -e "${RED}✘ PAGO RECHAZADO por Mercado Pago${NC}"
        echo -e "   Motivo: verifica saldo/límites en la app MP" ;;
    timeout)
        echo -e "${YELLOW}⏱ TIMEOUT — El usuario no aprobó en el dispositivo a tiempo${NC}"
        echo -e "   → Asegúrate de que el dispositivo ${YELLOW}${MP_TERMINAL_ID}${NC} esté encendido y vinculado" ;;
    error)
        echo -e "${RED}✘ ERROR — Revisa MP_ACCESS_TOKEN y terminal_id${NC}"
        echo -e "   → terminal_id: ${MP_TERMINAL_ID}"
        echo -e "   → Dispositivos válidos: https://www.mercadopago.cl/point/devices" ;;
    failed)
        # Error de API (400) — mostrar detalle del error
        HTTP_ERR="$(echo "$BODY" | python3 -c "
import sys, json
d = json.load(sys.stdin)
resp = d.get('response', {})
errors = resp.get('errors', [])
for e in errors:
    for det in e.get('details', []):
        print('  →', det)
" 2>/dev/null || echo '')"
        echo -e "${RED}✘ PAGO FALLIDO — Error de la API de Mercado Pago${NC}"
        [[ -n "$HTTP_ERR" ]] && echo -e "$HTTP_ERR"
        echo -e "   • Verifica que el terminal ID sea correcto"
        echo -e "   • Terminal usado: ${YELLOW}${MP_TERMINAL_ID}${NC}"
        echo -e "   • Dispositivos registrados en MP: https://www.mercadopago.cl/point/devices"
        echo -e "   • El dispositivo debe estar encendido y en modo de cobro" ;;
    *)
        echo -e "${YELLOW}Estado: ${MP_STATUS}${NC}" ;;
esac

echo ""
