#!/bin/bash
# =============================================================
#  compilar_mac.sh — Compilador ApiPagoElectronico para macOS
#  Solo para macOS Intel (x86_64)
# =============================================================

set -euo pipefail

# ── Colores ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'   # Sin color

APP_NAME="ApiPagoElectronico"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${CYAN}"
echo "=============================================="
echo "  Compilando ${APP_NAME}"
echo "  Plataforma: macOS Intel (x86_64)"
echo "=============================================="
echo -e "${NC}"

# ── 1. Verificar arquitectura ────────────────────────────────
ARCH="$(uname -m)"
echo -e "${YELLOW}Arquitectura detectada: ${ARCH}${NC}"
if [[ "$ARCH" != "x86_64" ]]; then
    echo -e "${RED}✘ Este script es solo para macOS Intel (x86_64).${NC}"
    echo "   En Apple Silicon (arm64), usa el script de compilación arm64."
    exit 1
fi

# ── 2. Verificar Python 3 (x86_64) ───────────────────────────
PYTHON_BIN="$(command -v python3 2>/dev/null || true)"
if [[ -z "$PYTHON_BIN" ]]; then
    echo -e "${RED}✘ python3 no encontrado. Instálalo con: brew install python${NC}"
    exit 1
fi

PYTHON_ARCHS="$(file "$(realpath "$PYTHON_BIN")" 2>/dev/null | grep -o 'arm64\|x86_64' | tr '\n' ' ' | xargs || true)"
echo "Python: $PYTHON_BIN"
echo "Python arch: ${PYTHON_ARCHS:-desconocida}"

if [[ "${PYTHON_ARCHS}" != *"x86_64"* ]]; then
    echo -e "${RED}✘ Tu python3 no es x86_64. Este script compila solo para Intel.${NC}"
    exit 1
fi

# ── 3. Entrar al directorio del proyecto ─────────────────────
cd "$SCRIPT_DIR"

# ── 4. Activar virtualenv si existe, o crear uno ─────────────
if [[ -d "venv" ]]; then
    echo -e "\n${CYAN}▶ Activando virtualenv existente...${NC}"
    source venv/bin/activate
else
    echo -e "\n${CYAN}▶ Creando virtualenv x86_64...${NC}"
    python3 -m venv venv
    source venv/bin/activate
fi

VENV_ARCHS="$(file "$(realpath "$(which python3)")" 2>/dev/null | grep -o 'arm64\|x86_64' | tr '\n' ' ' | xargs || true)"
echo "Python en venv: $(which python3) [${VENV_ARCHS:-?}]"
if [[ "${VENV_ARCHS}" != *"x86_64"* ]]; then
    echo -e "${RED}✘ El venv no es x86_64. Este script compila solo para Intel.${NC}"
    echo "   Solución recomendada (Intel):"
    echo "     rm -rf venv"
    echo "     python3 -m venv venv"
    echo "     source venv/bin/activate"
    echo "     pip install -r requirements.txt"
    exit 1
fi

PIL_BIN="$(python3 -c "import pathlib; import PIL._imaging as m; print(str(pathlib.Path(m.__file__).resolve()))" 2>/dev/null || true)"
if [[ -n "${PIL_BIN}" && -f "${PIL_BIN}" ]]; then
    PIL_ARCHS="$(file "${PIL_BIN}" 2>/dev/null | grep -o 'arm64\|x86_64' | tr '\n' ' ' | xargs || true)"
    if [[ "${PIL_ARCHS}" != *"x86_64"* ]]; then
        echo -e "${RED}✘ Pillow (PIL) no es x86_64, incompatible con build Intel:${NC}"
        echo "   ${PIL_BIN}"
        echo "   Reinstala Pillow con tu Python x86_64:"
        echo "     pip uninstall -y pillow"
        echo "     pip install --no-cache-dir --force-reinstall pillow"
        exit 1
    fi
fi

# ── 5. Instalar/actualizar dependencias ──────────────────────
echo -e "\n${CYAN}▶ Instalando dependencias desde requirements.txt...${NC}"
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

# ── 6. Instalar PyInstaller ──────────────────────────────────
echo -e "\n${CYAN}▶ Verificando PyInstaller...${NC}"
pip install pyinstaller --quiet

# ── 7. Limpiar compilaciones anteriores ──────────────────────
echo -e "\n${CYAN}▶ Limpiando builds anteriores...${NC}"
rm -rf build dist

# ── 8. Compilar ──────────────────────────────────────────────
echo -e "\n${CYAN}▶ Compilando con PyInstaller (onedir para .app funcional en macOS)...${NC}"

# Ícono del .app (Finder/Dock)
ICON_FLAG=""
if [[ -f "${SCRIPT_DIR}/assets/AppIcon.icns" ]]; then
    ICON_FLAG="--icon=${SCRIPT_DIR}/assets/AppIcon.icns"
    echo -e "   ${GREEN}✔${NC} Usando ícono: assets/AppIcon.icns"
elif [[ -f "${SCRIPT_DIR}/assets/icon.png" ]]; then
    ICON_FLAG="--icon=${SCRIPT_DIR}/assets/icon.png"
    echo -e "   ${YELLOW}⚠${NC}  .icns no encontrado, usando icon.png"
fi

# Carpeta assets/ (íconos de barra de menú)
ASSETS_FLAG=""
if [[ -d "${SCRIPT_DIR}/assets" ]]; then
    ASSETS_FLAG="--add-data=${SCRIPT_DIR}/assets:assets"
    echo -e "   ${GREEN}✔${NC} Empaquetando assets/"
fi

python3 -m PyInstaller \
    --onedir \
    --windowed \
    --name "${APP_NAME}" \
    --target-arch x86_64 \
    ${ICON_FLAG} \
    ${ASSETS_FLAG} \
    --exclude-module tkinter \
    --exclude-module matplotlib \
    --exclude-module numpy \
    --exclude-module pandas \
    --exclude-module scipy \
    --exclude-module IPython \
    --exclude-module notebook \
    --exclude-module PyQt5 \
    --exclude-module PyQt6 \
    --hidden-import serial \
    --hidden-import serial.tools \
    --hidden-import serial.tools.list_ports \
    --hidden-import rumps \
    --collect-all transbank \
    app.py

# ── 9. Verificar resultado ───────────────────────────────────
echo ""
# En modo --onedir el resultado es el .app bundle y el ejecutable interno
APP_BUNDLE="dist/${APP_NAME}.app"
INNER_BIN="dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"

if [[ -d "${APP_BUNDLE}" ]]; then
    BINARY_ARCH="$(file "${INNER_BIN}" 2>/dev/null | grep -o 'arm64\|x86_64' | head -1 || echo 'desconocida')"
    SIZE="$(du -sh "${APP_BUNDLE}" | cut -f1)"
    echo -e "${GREEN}✔ Compilación exitosa${NC}"
    echo -e "   Bundle:         ${APP_BUNDLE}"
    echo -e "   Arquitectura:   ${BINARY_ARCH}"
    echo -e "   Tamaño:         ${SIZE}"

    # ── 10. Post-build: copiar .env y lanzador ───────────────
    echo ""
    echo -e "${CYAN}▶ Preparando paquete de distribución...${NC}"

    # Copiar .env
    if [[ -f "${SCRIPT_DIR}/.env" ]]; then
        cp "${SCRIPT_DIR}/.env" "dist/.env"
        echo -e "   ${GREEN}✔${NC} .env copiado a dist/"
    else
        echo -e "   ${YELLOW}⚠${NC}  No se encontró .env en ${SCRIPT_DIR}"
    fi

    # Copiar lanzador .command al dist/
    LAUNCHER_SRC="${SCRIPT_DIR}/dist/Iniciar ${APP_NAME}.command"
    if [[ ! -f "$LAUNCHER_SRC" ]]; then
        # Crearlo si no existe
        cat > "$LAUNCHER_SRC" << 'LAUNCHER_EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
clear
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════╗"
echo "║        ApiPagoElectronico                ║"
echo -e "╚══════════════════════════════════════════╝${NC}"
[[ ! -f "$SCRIPT_DIR/ApiPagoElectronico" ]] && echo -e "${RED}✘ Ejecutable no encontrado${NC}" && sleep 5 && exit 1
[[ ! -f "$SCRIPT_DIR/.env" ]] && echo -e "${RED}✘ .env no encontrado${NC}" && sleep 5 && exit 1
HTTP_PORT="$(grep -E '^HTTP_PORT=' "$SCRIPT_DIR/.env" | cut -d'=' -f2 | tr -d ' \r' || echo '5005')"
xattr -cr "$SCRIPT_DIR/ApiPagoElectronico" 2>/dev/null || true
if curl -s --connect-timeout 2 "http://localhost:${HTTP_PORT}/status" &>/dev/null; then
    echo -e "${YELLOW}⚠ Servicio ya corriendo en puerto ${HTTP_PORT}${NC}"
    echo -e "  Estado: $(curl -s http://localhost:${HTTP_PORT}/status 2>/dev/null)"
    echo -e "\n[Enter] Ver log   [Ctrl+C] Salir"; read -r
    tail -f "$SCRIPT_DIR/pos_gateway.log" 2>/dev/null; exit 0
fi
echo -e "${CYAN}▶ Iniciando...${NC}"
"$SCRIPT_DIR/ApiPagoElectronico" &
APP_PID=$!
MAX_WAIT=15; WAITED=0
while [[ $WAITED -lt $MAX_WAIT ]]; do
    curl -s --connect-timeout 1 "http://localhost:${HTTP_PORT}/status" &>/dev/null && break
    sleep 1; WAITED=$((WAITED+1)); echo -n "."
done; echo ""
if curl -s --connect-timeout 2 "http://localhost:${HTTP_PORT}/status" &>/dev/null; then
    echo -e "\n${GREEN}${BOLD}✔ Servicio activo en http://localhost:${HTTP_PORT}${NC}"
    echo -e "${YELLOW}El servicio corre en segundo plano. Puedes cerrar esta ventana.${NC}"
    echo -e "\n[Enter] Ver log en vivo   [Ctrl+C] Cerrar"; read -r
    tail -f "$SCRIPT_DIR/pos_gateway.log" 2>/dev/null
else
    echo -e "\n${RED}✘ El servicio no respondió${NC}"
    LOG="$SCRIPT_DIR/pos_gateway.log"; [[ ! -f "$LOG" ]] && LOG="$HOME/pos_gateway.log"
    echo -e "${YELLOW}Últimas líneas del log:${NC}"; tail -20 "$LOG" 2>/dev/null || echo "Sin log"
    echo -e "\n[Enter] para cerrar"; read -r
fi
LAUNCHER_EOF
    fi
    chmod +x "$LAUNCHER_SRC"
    xattr -cr "$LAUNCHER_SRC" 2>/dev/null || true
    echo -e "   ${GREEN}✔${NC} Lanzador copiado: 'Iniciar ${APP_NAME}.command'"

    # Quitar cuarentena del .app bundle
    xattr -cr "${APP_BUNDLE}" 2>/dev/null || true
    echo -e "   ${GREEN}✔${NC} Cuarentena removida del bundle"

    echo ""
    echo -e "${YELLOW}Para distribuir, copia la carpeta ${CYAN}dist/${YELLOW} completa.${NC}"
    echo -e "${YELLOW}→ Abre ${CYAN}ApiPagoElectronico.app${YELLOW} con doble clic${NC}"
else
    echo -e "${RED}✘ No se encontró el bundle en dist/. Revisa los errores anteriores.${NC}"
    exit 1
fi

echo -e "\n${CYAN}==============================================${NC}"
echo -e "${CYAN}  Listo. Bundle en: dist/${APP_NAME}.app${NC}"
echo -e "${CYAN}==============================================${NC}"
