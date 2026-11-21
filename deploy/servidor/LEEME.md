# ApiPago en un servidor (modo servidor)

La misma API que el `.exe` de la caja (mismas rutas, mismas peticiones y respuestas, mismo usuario y clave),
pero corriendo siempre en un servidor Linux. **Solo atiende Mercado Pago y TUU**, que se cobran por internet.
Getnet y Transbank van conectados por cable a la caja, así que siguen con el `.exe` de cada caja.
Si al servidor le llega un `type=getnet` o `type=transbank`, responde `503` con `status: "no_disponible_en_servidor"`.

```
Navegador de la caja ──HTTPS──▶ nginx (servidor) ──▶ ApiPago 127.0.0.1:5005 ──▶ Mercado Pago / TUU
Navegador de la caja ──http://localhost:5005──▶ .exe de la caja ──cable──▶ Getnet / Transbank
```

## Qué necesitas
- Un servidor con **Ubuntu 22.04 o 24.04** (o Debian 12) y acceso por SSH con un usuario con `sudo`.
- Un **dominio o subdominio** (ej. `pagos.midominio.cl`) que apunte a la IP del servidor.
- Los puertos **80 y 443** abiertos hacia internet. El 5005 **no** se abre: la API solo escucha dentro del servidor.

## 1. Bajar el código
```bash
sudo apt-get install -y git
sudo git clone -b modo-servidor https://github.com/govanni0x404/Integracion_Maquinas_Pos.git /opt/apipago
```
Si el repositorio es privado, git te pide usuario y un *token* de GitHub (no la contraseña).

## 2. Instalar el servicio
```bash
cd /opt/apipago
sudo bash deploy/servidor/instalar.sh
```
El script instala Python, crea el usuario de sistema `apipago`, el entorno virtual y las dependencias,
copia el `.env` de ejemplo (si no existe) y deja el servicio `apipago` activado: arranca solo al prender
el servidor y se vuelve a levantar si se cae.

## 3. Configurar el `.env`
```bash
sudo nano /opt/apipago/.env
```
Lo mínimo:
- `MODO_SERVIDOR=true` (ya viene en el ejemplo).
- `ID_SUCURSAL`.
- `ALLOWED_ORIGINS` con el dominio del sistema web, ej. `https://misistema.cl`. **No dejarlo vacío en un servidor.**
- Mercado Pago: `MP_ACCESS_TOKEN` y `MP_TERMINAL_ID`.
- TUU: `USAR_TUU=true`, `TUU_API_KEY` y `TUU_DEVICES`.

Guardar (`Ctrl+O`, `Enter`, `Ctrl+X`) y reiniciar:
```bash
sudo systemctl restart apipago
```

## 4. Publicarlo con HTTPS (nginx)
```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
sudo cp /opt/apipago/deploy/servidor/nginx-apipago.conf /etc/nginx/sites-available/apipago
sudo nano /etc/nginx/sites-available/apipago          # cambiar pagos.midominio.cl por el dominio real
sudo ln -s /etc/nginx/sites-available/apipago /etc/nginx/sites-enabled/apipago
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d pagos.midominio.cl            # pone el certificado HTTPS y lo renueva solo
```
Hace falta HTTPS porque el sistema web corre en `https://`, y el navegador bloquea las llamadas
a un `http://` que no sea `localhost`.

## 5. Probar
```bash
curl http://127.0.0.1:5005/status                    # dentro del servidor
curl https://pagos.midominio.cl/status               # desde cualquier lado
```
Debe responder `"status":"ok"`, `"modo_servidor":true` y la `"version"`.

## 6. Cambio en el sistema web
Las ventas `type=mercadopago` y `type=haulmer` (TUU) se envían a `https://pagos.midominio.cl/pago`
en vez de a `http://localhost:5005/pago`. El body, el usuario y la clave (Basic Auth) y la respuesta son
los mismos. Getnet y Transbank siguen yendo a `http://localhost:5005` (el `.exe` de la caja).
Lo mismo con `/pago/estado/<tx_id>` y `/pago/cancelar/<tx_id>`: se envían al mismo lugar donde se creó la venta.

## 7. Manejar el servicio (comandos del día a día)
| Qué quiero | Comando |
|---|---|
| Ver si está corriendo | `sudo systemctl status apipago` |
| Reiniciar | `sudo systemctl restart apipago` |
| Detener | `sudo systemctl stop apipago` |
| Iniciar | `sudo systemctl start apipago` |
| Que no arranque solo al prender | `sudo systemctl disable apipago` |

Con una venta en curso conviene esperar a que termine antes de reiniciar o detener.

## 8. Ver los logs
**En vivo** (lo que va pasando; `Ctrl+C` para salir):
```bash
sudo journalctl -u apipago -f
```
**Solo las ventas de un medio de pago:**
```bash
sudo journalctl -u apipago -f | grep "\[mercadopago\]"
sudo journalctl -u apipago -f | grep "\[tuu\]"
```
**De hoy, o desde una hora:**
```bash
sudo journalctl -u apipago --since today
sudo journalctl -u apipago --since "2026-11-21 10:00"
```
**Los archivos, igual que en la caja** (uno por día, se guardan `LOG_RETENCION_DIAS` días):
```bash
ls /opt/apipago/logs/*/
sudo tail -f /opt/apipago/logs/mercadopago/mercadopago-$(date +%F).log
sudo tail -f /opt/apipago/logs/tuu/tuu-$(date +%F).log
sudo tail -f /opt/apipago/logs/general/pos_gateway-$(date +%F).log
```
La base de ventas queda en `/opt/apipago/data/pos_gateway.db`.

## 9. Entrar al panel (por túnel SSH)
El panel permite cambiar el `.env`, resolver ventas y reiniciar, así que **no se publica a internet**:
nginx lo bloquea y la API lo rechaza si no llega por `localhost`. Para usarlo, desde tu PC:
```bash
ssh -L 5005:127.0.0.1:5005 usuario@ip-del-servidor
```
Con esa ventana abierta, entra en el navegador de tu PC a **http://localhost:5005/panel** (el login es con
la ApiKey, igual que en la caja). Al cerrar el SSH se corta el acceso. "Guardar y reiniciar" funciona:
el servicio termina y systemd lo vuelve a levantar.

## 10. Actualizar a una versión nueva
```bash
sudo bash /opt/apipago/deploy/servidor/actualizar.sh
```
Baja lo último de la rama `modo-servidor`, instala dependencias y reinicia. Al final muestra `/status`
con la versión nueva.

## Problemas comunes
- **`systemctl status` dice `failed`**: mirar `sudo journalctl -u apipago -n 50`. Lo más común es que falte
  `MODO_SERVIDOR=true` o que haya un error en el `.env`.
- **El navegador dice CORS**: el dominio del sistema web no está en `ALLOWED_ORIGINS` (tiene que ser exacto,
  con `https://` y sin `/` al final). Después de cambiarlo hay que reiniciar.
- **`502 Bad Gateway` en nginx**: el servicio está detenido (`sudo systemctl start apipago`).
- **`403` con `id_sucursal`**: el `ID_SUCURSAL` del `.env` no es el que manda el sistema web.
