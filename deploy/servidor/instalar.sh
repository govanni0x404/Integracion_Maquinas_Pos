#!/usr/bin/env bash
# Instala ApiPago (modo servidor) como servicio de systemd.
# Uso (en el servidor, con el código ya descargado en /opt/apipago):
#   cd /opt/apipago && sudo bash deploy/servidor/instalar.sh
set -euo pipefail

DIR=/opt/apipago
USUARIO=apipago

if [ "$(id -u)" != "0" ]; then
    echo "Ejecutar con sudo: sudo bash deploy/servidor/instalar.sh"
    exit 1
fi
if [ ! -f "$DIR/servidor.py" ]; then
    echo "No encuentro $DIR/servidor.py: el código tiene que estar en $DIR"
    exit 1
fi
cd "$DIR"

echo "1/6 Instalando Python (venv)..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv

echo "2/6 Creando el usuario de sistema '$USUARIO' (sin login)..."
id "$USUARIO" >/dev/null 2>&1 || useradd --system --home-dir "$DIR" --shell /usr/sbin/nologin "$USUARIO"

echo "3/6 Creando el entorno virtual e instalando dependencias..."
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt

echo "4/6 Preparando el .env..."
if [ ! -f .env ]; then
    cp deploy/servidor/env.servidor.ejemplo .env
    echo "    Se creó $DIR/.env desde el ejemplo: COMPLÉTALO (sudo nano $DIR/.env)"
fi
mkdir -p data logs
chown -R "$USUARIO:$USUARIO" "$DIR"
chmod 600 .env

echo "5/6 Instalando el servicio de systemd..."
cp deploy/servidor/apipago.service /etc/systemd/system/apipago.service
systemctl daemon-reload
systemctl enable apipago >/dev/null

echo "6/6 Arrancando..."
systemctl restart apipago
sleep 3
systemctl --no-pager --lines=15 status apipago || true

echo
echo "Listo. Probar:   curl http://127.0.0.1:5005/status"
echo "Ver el log:      sudo journalctl -u apipago -f"
