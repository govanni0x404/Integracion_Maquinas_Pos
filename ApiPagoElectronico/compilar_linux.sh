#!/bin/bash
# =============================================================
#  compilar_linux.sh — Compila ApiPagoElectronico para Linux
#  Genera un ejecutable standalone en dist/ApiPagoElectronico
#
#  REQUISITOS:
#    sudo apt install python3 python3-venv python3-pip   (Debian/Ubuntu)
#    sudo dnf install python3 python3-pip                (Fedora/CentOS)
# =============================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

APP_NAME="ApiPagoElectronico"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${CYAN}"
echo "=============================================="
echo "  Compilando ${APP_NAME}"
echo "  Plataforma: Linux"
echo "=============================================="
echo -e "${NC}"

# ── 1. Detectar distro y arquitectura ───────────────────────
ARCH="$(uname -m)"
echo -e "${YELLOW}Arquitectura: ${ARCH}${NC}"
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    echo -e "${YELLOW}Distro: ${NAME} ${VERSION_ID:-}${NC}"
fi
echo ""

# ── 2. Verificar Python ──────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}✘ python3 no encontrado.${NC}"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-venv python3-pip"
    echo "  Fedora/CentOS: sudo dnf install python3 python3-pip"
    exit 1
fi
echo "Python: $(command -v python3) [$(python3 --version)]"

# ── 3. Entrar al proyecto ────────────────────────────────────
cd "$SCRIPT_DIR"

# ── 4. Virtualenv ─────────────────────────────────────────────
if [[ -d "venv" ]]; then
    echo -e "\n${CYAN}▶ Activando virtualenv existente...${NC}"
    source venv/bin/activate
else
    echo -e "\n${CYAN}▶ Creando virtualenv...${NC}"
    if ! python3 -m venv venv >/dev/null 2>&1; then
        echo -e "${RED}✘ No se pudo crear el virtualenv.${NC}"
        echo "  Ubuntu/Debian: sudo apt install python3-venv"
        echo "  Fedora/CentOS: sudo dnf install python3-virtualenv"
        exit 1
    fi
    source venv/bin/activate
fi

echo "Python en venv: $(which python3) [$(python3 --version)]"

# ── 5. Dependencias ─────────────────────────────────────────
echo -e "\n${CYAN}▶ Instalando dependencias...${NC}"
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

# rumps es solo macOS — en Linux se puede usar pystray o sin systray
pip install pyinstaller --quiet

# ── 6. Limpiar builds anteriores ─────────────────────────────
echo -e "\n${CYAN}▶ Limpiando builds anteriores...${NC}"
rm -rf build dist

# ── 7. Compilar ──────────────────────────────────────────────
echo -e "\n${CYAN}▶ Compilando con PyInstaller...${NC}"

# Assets (íconos para systray si se usa pystray)
ASSETS_FLAG=""
if [[ -d "${SCRIPT_DIR}/assets" ]]; then
    ASSETS_FLAG="--add-data=${SCRIPT_DIR}/assets:assets"
    echo -e "   ${GREEN}✔${NC} Empaquetando assets/"
fi

# Ícono
ICON_FLAG=""
if [[ -f "${SCRIPT_DIR}/assets/icon.png" ]]; then
    ICON_FLAG="--icon=${SCRIPT_DIR}/assets/icon.png"
    echo -e "   ${GREEN}✔${NC} Usando ícono: assets/icon.png"
fi

python3 -m PyInstaller \
    --onedir \
    --name "${APP_NAME}" \
    ${ICON_FLAG} \
    ${ASSETS_FLAG} \
    --exclude-module tkinter \
    --exclude-module matplotlib \
    --exclude-module numpy \
    --exclude-module pandas \
    --exclude-module scipy \
    --exclude-module IPython \
    --exclude-module PyQt5 \
    --exclude-module PyQt6 \
    --exclude-module rumps \
    --hidden-import serial \
    --hidden-import serial.tools \
    --hidden-import serial.tools.list_ports \
    --collect-all transbank \
    app.py

# ── 8. Verificar resultado ───────────────────────────────────
DIST_BIN="dist/${APP_NAME}/${APP_NAME}"
if [[ -f "$DIST_BIN" ]]; then
    chmod +x "$DIST_BIN"
    SIZE="$(du -sh "dist/${APP_NAME}" | cut -f1)"
    echo ""
    echo -e "${GREEN}✔ Compilación exitosa${NC}"
    echo -e "   Ejecutable: ${DIST_BIN}"
    echo -e "   Tamaño:     ${SIZE}"

    # Copiar .env
    echo ""
    echo -e "${CYAN}▶ Preparando paquete...${NC}"
    if [[ -f "${SCRIPT_DIR}/.env" ]]; then
        cp "${SCRIPT_DIR}/.env" "dist/${APP_NAME}/.env"
        echo -e "   ${GREEN}✔${NC} .env copiado"
    fi

    # Crear script de inicio rápido en dist/
    cat > "dist/iniciar.sh" << 'QUICK_EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
[[ ! -f "$SCRIPT_DIR/ApiPagoElectronico/ApiPagoElectronico" ]] && echo "✘ Ejecutable no encontrado" && exit 1
nohup "$SCRIPT_DIR/ApiPagoElectronico/ApiPagoElectronico" >> "$SCRIPT_DIR/pos_gateway.log" 2>&1 &
echo "✔ Iniciado (PID $!). Log: $SCRIPT_DIR/pos_gateway.log"
QUICK_EOF
    chmod +x "dist/iniciar.sh"
    echo -e "   ${GREEN}✔${NC} Lanzador rápido: dist/iniciar.sh"

else
    echo -e "${RED}✘ No se encontró el ejecutable en dist/. Revisa los errores.${NC}"
    exit 1
fi

echo ""
echo -e "${CYAN}==============================================${NC}"
echo -e "${CYAN}  Listo. Distribuye la carpeta: dist/${APP_NAME}/${NC}"
echo -e "${CYAN}  Inicia con: ./dist/${APP_NAME}/${APP_NAME}${NC}"
echo -e "${CYAN}==============================================${NC}"
