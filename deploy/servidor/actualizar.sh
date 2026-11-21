#!/usr/bin/env bash
# Baja la última versión de la rama modo-servidor y reinicia el servicio.
# Uso: sudo bash /opt/apipago/deploy/servidor/actualizar.sh
set -euo pipefail

DIR=/opt/apipago
if [ "$(id -u)" != "0" ]; then
    echo "Ejecutar con sudo: sudo bash $DIR/deploy/servidor/actualizar.sh"
    exit 1
fi
cd "$DIR"

# Con una venta en curso no se corta: el panel tampoco reinicia en ese caso.
if curl -fs http://127.0.0.1:5005/status >/dev/null 2>&1; then
    echo "Servicio activo. Si hay una venta en curso, espera a que termine antes de seguir."
    read -r -p "¿Continuar con la actualización? [s/N] " r
    [ "$r" = "s" ] || [ "$r" = "S" ] || exit 0
fi

git -c safe.directory="$DIR" pull --ff-only origin modo-servidor
venv/bin/pip install -q -r requirements.txt
cp deploy/servidor/apipago.service /etc/systemd/system/apipago.service
chown -R apipago:apipago "$DIR"
systemctl daemon-reload
systemctl restart apipago
sleep 3
curl -s http://127.0.0.1:5005/status; echo
