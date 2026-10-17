# ApiPagoElectronico

Servicio local para Windows que conecta el sistema web de la caja con las máquinas de pago:

| Máquina | Conexión | `type` en la petición |
|---|---|---|
| Transbank (POS integrado) | Cable, puerto COM (SDK `transbank-pos-sdk`) | `TRANSBANK` |
| Getnet (A920 Pro) | Cable USB, puerto COM | `GETNET` |
| Mercado Pago (Point) | API de Orders en la nube | `MERCADOPAGO` |
| TUU / Haulmer (PRO2/Kozen, Pago remoto) | API en la nube | `HAULMER` |

Corre como un `.exe` con un ícono en la bandeja del sistema. Escucha en `http://127.0.0.1:5005` y tiene un panel de control web en `http://localhost:5005/panel`.

## Cómo funciona

- El sistema web de la caja llama a `POST /pago` con Basic Auth. La petición queda abierta hasta que la venta termina (aprobada, rechazada o sin confirmar).
- Cada venta se registra en una base SQLite (`data/pos_gateway.db`) **antes** de enviarse a la máquina. Si una venta queda sin confirmar (`INDETERMINADA`), la caja queda bloqueada hasta resolverla, para evitar cobros dobles.
- Un monitor revisa cada 30 s las ventas sin confirmar y las resuelve consultando a la máquina o a la API (Getnet, Transbank, Mercado Pago y TUU).
- **Cable desconectado (Getnet y Transbank):** si se corta el cable con una venta en curso, la petición sigue abierta. Al reconectar se consulta el resultado real, hasta `GETNET_MAX_ESPERA` / `TRANSBANK_MAX_ESPERA` segundos.
- Getnet y Transbank comparten puertos COM: se detectan de a una (primero Getnet) y ninguna sondea el puerto de la otra.
- `nombre_caja` y `type` se aceptan en mayúsculas o minúsculas y se guardan siempre en MAYÚSCULAS.

## Estructura

```
app.py                 Punto de entrada: instancia única, detección de puertos, servidor y bandeja
config/settings.py     Configuración (lee el .env) y credenciales fijas (solo hashes)
server/api_server.py   API Flask: /pago, /pago/estado, /pago/cancelar, monitor, panel
server/auth.py         Basic Auth, clave del panel y freno a la fuerza bruta
server/panel_html.py   HTML del panel de control y de la pantalla de ingreso
pos/pos_module.py      Transbank (SDK, puerto COM)
pos/getnet_module.py   Getnet (protocolo serie propio)
pos/tuu_module.py      TUU / Haulmer Pago remoto
core/                  Base de transacciones, logs, puertos COM, instancia única, firewall, HTTP
ui/tray_icon.py        Ícono de la bandeja del sistema
tests/                 Pruebas automatizadas (pytest)
docs/                  Documentación adicional (integración Mercado Pago en la web)
```

## Desarrollo

Requiere **Python 3.12 x64**.

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt -r requirements-dev.txt   # Windows: venv\Scripts\pip
venv/bin/python app.py
venv/bin/python -m pytest -q
```

Las pruebas usan un `.env` y una base de datos temporales: nunca tocan la configuración real ni las máquinas.

## Compilar e instalar en Windows

1. `instalar_dependencias_windows.bat`: crea el `venv` con Python 3.12 e instala las dependencias.
2. `compilar_windows.bat`: genera `dist\ApiPagoElectronico.exe` con PyInstaller (versión fija) y el ícono `assets\AppIcon.ico`.
3. Abrir `instaladorInno_windows.iss` con Inno Setup 6 para generar `Instalador_ApiPagoElectronico.exe`. Instala por usuario, en `%LOCALAPPDATA%\Programs`.

Junto al `.exe` se crean el `.env`, `logs\` y `data\`. Si el `.exe` se abre con el servicio ya corriendo, no lo cierra: abre el panel y termina.

## Configuración

Se edita desde el panel (`localhost:5005/panel`, que pide la clave del panel). Los valores se guardan en el `.env` y, para aplicarlos, se usa **Guardar y reiniciar**. Con una venta en curso no se reinicia.

| Clave | Para qué sirve |
|---|---|
| `ID_SUCURSAL`, `NOMBRE_CAJA` | Identifican esta caja (`NOMBRE_CAJA` se guarda en mayúsculas) |
| `USAR_POS_FISICO`, `TRANSBANK_TERMINAL_ID`, `PUERTOS_COM`, `TRANSBANK_MAX_ESPERA` | Transbank |
| `USAR_GETNET`, `GETNET_TERMINAL_ID`, `GETNET_PORT`, `GETNET_MAX_ESPERA` | Getnet |
| `USAR_MP`, `MP_TERMINAL_ID`, `MP_ACCESS_TOKEN`, `ALLOW_MP_TOKEN_IN_REQUEST`, `ALLOWED_MP`, `MP_API_URL` | Mercado Pago |
| `USAR_TUU`, `TUU_DEVICES`, `TUU_API_KEY`, `ALLOW_TUU_KEY_IN_REQUEST`, `TUU_API_URL` | TUU / Haulmer |
| `MAX_TRANSACTION_TIME`, `TIMEOUT_SERVER` | Tiempos máximos por venta |
| `BIND_HOST`, `ALLOWED_ORIGINS`, `AGENTES_REMOTOS`, `LOG_RETENCION_DIAS` | Red, CORS, agentes remotos y retención de logs |

Los identificadores y las credenciales de cada máquina deben ser distintos entre sí; el panel lo valida. El token de Mercado Pago y la API key de TUU no se muestran: quedan bloqueados hasta presionar **Cambiar**.

> El `.env` contiene credenciales: nunca debe subirse al repositorio (está en `.gitignore`).

## API

Todas las rutas requieren **Basic Auth**. El usuario y el hash de la clave están en `config/settings.py`; para cambiarlos hay que recompilar.

| Método y ruta | Descripción |
|---|---|
| `POST /pago` | Cobra en la máquina indicada en `type` |
| `GET /pago/estado/<tx_id>` | Estado de una venta (`PENDIENTE`, `INDETERMINADA`, `APROBADO`, `RECHAZADO`, `ERROR`) |
| `POST /pago/cancelar/<tx_id>` | Cancela una venta en cola o en curso, o deja de esperar el resultado |
| `POST /pago/resolver/<tx_id>` | Resolución manual: `{"estado": "APROBADO"\|"RECHAZADO", "resuelto_por": "...", "nota": "..."}` |
| `GET\|POST /online?type=...` | Indica si la máquina está conectada o configurada |
| `GET /status` | El servicio está vivo |
| `POST /pago/refund`, `POST /pago/detalle` | Anulación y detalle de ventas (Transbank) |

### Ejemplos de `POST /pago`

```json
{"type": "GETNET", "id_sucursal": "1", "nombre_caja": "CAJA1", "terminal_id": "<GETNET_TERMINAL_ID>", "amount": 1500, "timeout": 120}
{"type": "TRANSBANK", "id_sucursal": "1", "nombre_caja": "CAJA1", "terminal_id": "<TRANSBANK_TERMINAL_ID>", "amount": 1500, "timeout": 120}
{"type": "MERCADOPAGO", "id_sucursal": "1", "nombre_caja": "CAJA1", "mp_terminal_id": "NEWLAND_N950__XXXX", "amount": 1500}
{"type": "HAULMER", "id_sucursal": "1", "nombre_caja": "CAJA1", "terminal_id": "<número de serie>", "access_token": "<API key>", "amount": 1500, "timeout": 120}
```

- Campo opcional `tx_id`: permite conocer el id de antemano para consultar o cancelar mientras la petición sigue abierta.
- `access_token` solo se usa si `ALLOW_MP_TOKEN_IN_REQUEST` / `ALLOW_TUU_KEY_IN_REQUEST` está en `true`. Si no, se usa la credencial guardada en el panel.
- TUU no permite cancelar por API: si se agota el tiempo o se cancela la espera, la venta queda `INDETERMINADA` hasta que el monitor confirme el resultado.

## Panel de control

`http://localhost:5005/panel`, accesible solo desde la misma PC. Pide la clave del panel, que está fija en el código como hash, y la sesión dura 12 horas.

Desde el panel se puede:
- Editar la configuración y detectar los puertos COM.
- Ver los logs.
- Resolver ventas sin confirmar.
- Activar el inicio automático con Windows.

## Logs

En `logs/` junto al `.exe`, una carpeta por máquina (`general`, `getnet`, `transbank`, `mercadopago`, `tuu`, `diagnostico`) y un archivo por día. Los logs de cada máquina registran cada venta de principio a fin, sin credenciales.
