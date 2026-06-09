#!/bin/bash
set -euo pipefail

APP_NAME="ApiPagoElectronico"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}==============================================${NC}"
echo -e "${CYAN}  Compilando ${APP_NAME} (macOS arm64)${NC}"
echo -e "${CYAN}==============================================${NC}"

ARCH_NOW="$(/usr/bin/arch)"
if [[ "$ARCH_NOW" != "arm64" ]]; then
  echo -e "${RED}✘ Esta terminal está corriendo como: ${ARCH_NOW}${NC}"
  echo -e "${YELLOW}Abre Terminal sin Rosetta y vuelve a intentar.${NC}"
  exit 1
fi

PYTHON_BIN=""
if [[ -x "/opt/homebrew/bin/python3" ]]; then
  PYTHON_BIN="/opt/homebrew/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo -e "${RED}✘ python3 no encontrado.${NC}"
  echo -e "${YELLOW}Instala Python arm64 con Homebrew:${NC}"
  echo -e "${YELLOW}  arch -arm64 /bin/bash -lc \"brew install python\"${NC}"
  exit 1
fi

PYTHON_ARCH="$(file "$(realpath "$PYTHON_BIN")" 2>/dev/null | grep -o 'arm64\|x86_64' | head -1 || true)"
echo "Python: $PYTHON_BIN"
echo "Python arch: ${PYTHON_ARCH:-desconocida}"

if [[ "$PYTHON_ARCH" != "arm64" ]]; then
  echo -e "${RED}✘ El python seleccionado no es arm64.${NC}"
  echo -e "${YELLOW}Usa el Python arm64 de Homebrew en /opt/homebrew/bin/python3${NC}"
  exit 1
fi

cd "$SCRIPT_DIR"

VENV_DIR="${SCRIPT_DIR}/venv_arm64"
if [[ -d "$VENV_DIR" ]]; then
  echo -e "${CYAN}▶ Activando venv existente: venv_arm64${NC}"
  source "${VENV_DIR}/bin/activate"
else
  echo -e "${CYAN}▶ Creando venv arm64: venv_arm64${NC}"
  /usr/bin/arch -arm64 "$PYTHON_BIN" -m venv "$VENV_DIR"
  source "${VENV_DIR}/bin/activate"
fi

VENV_PY="$(which python3)"
VENV_ARCH="$(file "$(realpath "$VENV_PY")" 2>/dev/null | grep -o 'arm64\|x86_64' | head -1 || true)"
echo "Python en venv: ${VENV_PY} [${VENV_ARCH:-?}]"
if [[ "$VENV_ARCH" != "arm64" ]]; then
  echo -e "${RED}✘ El venv no quedó arm64. Elimina venv_arm64 y reintenta.${NC}"
  exit 1
fi

echo -e "${CYAN}▶ Instalando dependencias...${NC}"
python3 -m pip install --upgrade pip --quiet
python3 -m pip install -r requirements.txt --quiet
python3 -m pip install pyinstaller --quiet

echo -e "${CYAN}▶ Limpiando builds anteriores...${NC}"
rm -rf build dist

ICON_FLAG=""
if [[ -f "${SCRIPT_DIR}/assets/AppIcon.icns" ]]; then
  ICON_FLAG="--icon=${SCRIPT_DIR}/assets/AppIcon.icns"
elif [[ -f "${SCRIPT_DIR}/assets/icon.png" ]]; then
  ICON_FLAG="--icon=${SCRIPT_DIR}/assets/icon.png"
fi

ASSETS_FLAG=""
if [[ -d "${SCRIPT_DIR}/assets" ]]; then
  ASSETS_FLAG="--add-data=${SCRIPT_DIR}/assets:assets"
fi

echo -e "${CYAN}▶ Compilando con PyInstaller (onedir + .app)...${NC}"
/usr/bin/arch -arm64 python3 -m PyInstaller \
  --onedir \
  --windowed \
  --name "${APP_NAME}" \
  --target-arch arm64 \
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
  --collect-all flask_cors \
  --collect-all transbank \
  app.py

APP_BUNDLE="dist/${APP_NAME}.app"
INNER_BIN="dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"

if [[ ! -d "${APP_BUNDLE}" ]]; then
  echo -e "${RED}✘ No se encontró el bundle en dist/. Revisa los errores anteriores.${NC}"
  exit 1
fi

BINARY_ARCH="$(file "${INNER_BIN}" 2>/dev/null | grep -o 'arm64\|x86_64' | head -1 || echo 'desconocida')"
SIZE="$(du -sh "${APP_BUNDLE}" | cut -f1)"
echo -e "${GREEN}✔ Compilación exitosa${NC}"
echo -e "  Bundle:       ${APP_BUNDLE}"
echo -e "  Arquitectura: ${BINARY_ARCH}"
echo -e "  Tamaño:       ${SIZE}"

if [[ -f "${SCRIPT_DIR}/.env" ]]; then
  cp "${SCRIPT_DIR}/.env" "dist/.env"
  echo -e "${GREEN}✔ .env copiado a dist/.env${NC}"
else
  echo -e "${YELLOW}⚠ No se encontró .env en la raíz del proyecto${NC}"
fi

/usr/bin/xattr -cr "${APP_BUNDLE}" 2>/dev/null || true
echo -e "${GREEN}✔ Cuarentena removida del bundle${NC}"

echo -e "${CYAN}==============================================${NC}"
echo -e "${CYAN}Listo. Bundle en: dist/${APP_NAME}.app${NC}"
echo -e "${CYAN}==============================================${NC}"
