# Análisis del proyecto ApiPagoElectronico

## 1) Propósito y alcance

Este repositorio implementa un servicio local (desktop) que expone una API HTTP para ejecutar operaciones de pago en distintos medios:

- Transbank POS (POS físico vía SDK y puerto serie).
- Getnet A920 Pro (POS físico vía puerto serie y protocolo JSON firmado).
- Mercado Pago Point (vía API HTTP de Mercado Pago).

El servicio está pensado para correr como proceso “gateway” en cada máquina/caja (o como “agente” local) y permitir que un cliente (por ejemplo un sistema de caja) dispare ventas, consulte estado, vea panel de control, y ejecute utilidades (detalle/anulación en Transbank).

## 2) Estructura del repositorio

- `app.py`: punto de entrada; inicializa módulos, firewall, servidor Flask y UI de bandeja/barra.
- `config/settings.py`: configuración por variables de entorno y carga de `.env` (con compatibilidad PyInstaller).
- `server/`
  - `api_server.py`: servidor Flask, rutas HTTP, colas por caja, worker local y limpieza.
  - `auth.py`: autenticación Basic para endpoints protegidos.
  - `panel_html.py`: HTML embebido del panel web.
- `pos/`
  - `pos_module.py`: integración Transbank POS (detección puerto, ventas, anulación, detalle, monitor).
  - `getnet_module.py`: integración Getnet (detección puerto y venta).
- `core/`
  - `logging_config.py`: configuración de logging y path del log (desarrollo y PyInstaller).
  - `singleton.py`: lock para instancia única y limpieza al salir.
  - `firewall.py`: regla de firewall (Windows) para abrir el puerto HTTP.
- `ui/tray_icon.py`: icono de bandeja/barra (macOS con `rumps`, otros con `pystray`).
- Scripts: `iniciar_*.sh/.bat`, `reiniciar_*.sh/.bat`, `detener_*.sh/.bat`, `compilar_*.sh/.bat`.
- `requirements.txt`: dependencias Python.

## 3) Flujo de arranque (runtime)

Implementado en [app.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/app.py).

1. Inicializa logging y ejecuta diagnóstico de credenciales (`_log_auth_diagnostics()`).
2. Asegura instancia única mediante lock file (ver [singleton.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/core/singleton.py)).
3. Intenta abrir reglas de firewall para el puerto HTTP configurado (especialmente relevante en Windows).
4. Inicializa módulos:
   - Getnet: detección de puerto en segundo plano si `USAR_GETNET=true`.
   - Transbank POS: detección/monitor en segundo plano si `USAR_POS_FISICO=true`.
5. Crea `APIServer` (Flask) y lo ejecuta en un hilo de fondo.
6. Mantiene la app viva con UI de bandeja/barra (requerido en macOS: el UI corre en main thread).

## 4) Configuración (.env / variables de entorno)

La carga de `.env` se resuelve en [settings.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/config/settings.py) buscando en ubicaciones compatibles con PyInstaller y modo desarrollo. Si `python-dotenv` no está disponible, usa un parser propio.

Claves principales (con defaults):

- `HTTP_PORT` (default `5005`): puerto del servidor HTTP.
- `ID_SUCURSAL` (default `1`): sucursal atendida por este servicio.
- `NOMBRE_CAJA` (default `hostname`): caja local; se usa para “agente local”.
- `ID_TERMINAL` (default `POS_<NOMBRE_CAJA>`): identificador de terminal.
- `USAR_POS_FISICO` (default `true`): habilita Transbank POS físico.
- `PUERTOS_COM` (default `COM5,COM6,COM7,COM8`): preferencia de puertos (Transbank).
- `USAR_GETNET` (default `false`): habilita Getnet en la máquina.
- `MAX_TRANSACTION_TIME` (default `90`): timeout de venta POS (worker) y operaciones POS.
- `TIMEOUT_SERVER` (default `120`): timeout por defecto para espera HTTP.
- Mercado Pago:
  - `MP_API_URL` (default `https://api.mercadopago.com/v1/orders`)
  - `ALLOWED_MP` (default vacío): lista CSV de cajas habilitadas (expuesto en el panel; el enforcement depende del cliente/panel).

Notas importantes:

- En `settings.py` existen credenciales de autenticación API definidas en el código y explícitamente protegidas para que no sean sobrescritas por `.env`.
- El panel (`/panel`) expone la lectura/edición del `.env` encontrado por el servicio (ver sección de endpoints).

## 5) Arquitectura del servidor (colas, tareas, agentes)

Implementado en [api_server.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/server/api_server.py).

### 5.1 Colas por sucursal/caja

El servidor mantiene una estructura:

- `queues[id_sucursal][nombre_caja] -> queue.Queue`

Cada caja tiene su propia cola de tareas, lo que permite:

- Encolar una venta para una caja específica.
- Que un “agente” remoto (otra máquina/caja) haga polling para llevarse la tarea.

### 5.2 Registro y “agentes”

- `register_agent(id_sucursal, nombre_caja, info)` registra heartbeats y metadatos de agentes.
- `register_local_agent()` registra el agente local usando `ID_SUCURSAL` + `NOMBRE_CAJA`.
- `/poll` permite que un agente consulte si hay una tarea para su caja (long-poll corto).
- `/result` permite que el agente devuelva el resultado.

### 5.3 Concurrencia y control de “caja ocupada”

Para evitar ejecución concurrente por caja, se usa:

- `busy_boxes: set[(id_sucursal, nombre_caja)]`

Flujo típico:

1. Llega `/pago` para `(id_sucursal, nombre_caja)`.
2. Si la caja está en `busy_boxes`, responde `429 busy`.
3. Si no, marca la caja como ocupada, crea una tarea `tx_id` y la encola.
4. Espera un `threading.Event()` asociado al `tx_id` hasta `timeout`.
5. Cuando llega el resultado (worker local o `/result`), libera la caja.

### 5.4 Worker local

En `APIServer.__init__()` se arranca un thread “local worker” que consume la cola de la caja local y ejecuta:

- Transbank: `POSModule.do_sale_with_timeout(...)`
- Getnet: `GetnetModule.do_sale_with_timeout(...)`
- Mercado Pago: `process_mercadopago(...)`

Además existe un thread de limpieza que elimina transacciones viejas del diccionario `tasks`.

## 6) Integraciones de pago

### 6.1 Transbank POS (SDK + serial)

Implementado en [pos_module.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/pos/pos_module.py).

Puntos clave:

- Detección automática del puerto con `serial.tools.list_ports` y lista preferida `PUERTOS_COM`.
- La detección intenta `open_port` y `poll` con timeouts para evitar bloqueo.
- Ventas con timeout:
  - Abre puerto, ejecuta `sale(amount, ticket)` y cierra puerto.
  - Clasifica respuesta “aprobado” por `response_code` (`"0"` o `"00"`).
- Monitor de estado:
  - Si hay puerto, valida que siga respondiendo.
  - Si se pierde, reintenta detección con cooldown.
- Operaciones adicionales expuestas por API:
  - Anulación (`refund`) por `operation_id`.
  - Detalle (`details`) opcionalmente imprimiendo en POS.

### 6.2 Getnet A920 Pro (serial + JSON firmado)

Implementado en [getnet_module.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/pos/getnet_module.py).

Puntos clave:

- Detección automática de puerto por heurística de descripción (prioriza “getnet”, luego USB serial).
- Handshake “POLL” con comando `106`, mensaje JSON y firma SHA256.
- Venta con comando `100` con campos como `Amount`, `TicketNumber`, `PrintOnPos`, etc.
- Parseo de respuesta desde un “outer JSON” con `JsonSerialized` + `Sign` (con tolerancia a buffers concatenados).

### 6.3 Mercado Pago (API HTTP)

Implementado en [process_mercadopago](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/server/api_server.py#L21-L85).

Puntos clave:

- Crea una orden (`POST /v1/orders`) con `X-Idempotency-Key`.
- Luego consulta el estado (`GET /v1/orders/{id}`) en bucle hasta `timeout`.
- Devuelve estados según `order_info["status"]` o `timeout/error`.

## 7) Endpoints HTTP (principales)

Servidor Flask arrancado en `0.0.0.0:<HTTP_PORT>` (ver [api_server.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/server/api_server.py#L1191-L1193)).

### 7.1 Panel y utilidades (sin Basic Auth)

- `GET /` → redirige a `/panel`.
- `GET /panel` → panel HTML con:
  - valores relevantes (puerto, sucursal, caja, terminal, flags, etc.)
  - lectura del `.env` detectado
  - acciones: ver estado, ver log, reiniciar, etc.
- `POST /panel/config` → sobrescribe/añade variables en el `.env` encontrado.
- `GET /panel/log` → últimas 100 líneas del log.
- `POST /panel/restart` → relanza el servicio (por script o por relaunch del proceso).
- `GET /auth/test` → diagnóstico de credenciales (devuelve información comparativa del servidor vs lo enviado en `Authorization`).

### 7.2 API de agentes (sin Basic Auth)

- `POST /register_agent` → registra un agente (id_sucursal + nombre_caja + meta).
- `GET /poll?id_sucursal=...&nombre_caja=...` → obtiene 1 tarea o heartbeat.
- `POST /result` → publica resultado para `tx_id`.

### 7.3 API de pagos (protegida con Basic Auth)

La protección aplica vía `@require_basic_auth` (ver [auth.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/server/auth.py)).

- `POST|GET /pago` → inicia y espera resultado (bloqueante hasta `timeout`).
  - Campos comunes: `id_sucursal`, `nombre_caja`, `type`.
  - `type=transbank`:
    - requiere `terminal_id`, `amount`, `timeout`
    - valida `terminal_id == ID_TERMINAL`
  - `type=getnet`:
    - requiere `terminal_id`, `amount`, `timeout`
    - valida `terminal_id == ID_TERMINAL`
    - requiere `USAR_GETNET=true`
  - `type=mercadopago`:
    - requiere `access_token`, `amount` (y opcional `terminal_id`)
- `POST|GET /online` → ping/estado (opcional `type=transbank|getnet`).
- `POST|GET /pago/iniciar` → inicia pago asíncrono (202) y entrega `transaction_id` (actualmente implementado para `transbank`).
- `GET /pago/estado/<tx_id>` → consulta estado de una transacción.
- `POST /pago/cancelar/<tx_id>` → cancela una transacción en memoria (marca `cancelled`).
- `POST /pago/limpiar` → limpia transacciones antiguas.

### 7.4 Operaciones Transbank adicionales (sin Basic Auth)

- `POST /pago/refund` → anulación por `operation_id` (usa `POSModule.do_refund_with_timeout`).
- `POST|GET /pago/detalle` → detalle de última transacción; acepta `print_on_pos` (bool).

## 8) Logging y observabilidad

El logger principal se inicializa en [logging_config.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/core/logging_config.py). Determina el path del log según:

- `LOG_FILE` absoluto (si está definido).
- Si está empaquetado como `.app` (PyInstaller en macOS), coloca el log fuera del bundle.
- En desarrollo, usa la carpeta del script principal.
- Fallback: `CWD` o `~/pos_gateway.log`.

El panel expone un preview del log vía `/panel/log`.

## 9) Empaquetado y ejecución

- Desarrollo:
  - ejecutar `python app.py` (con dependencias instaladas).
  - definir `.env` en la raíz del proyecto o en el directorio de ejecución.
- Empaquetado:
  - hay scripts por plataforma; en macOS destaca [compilar_mac.sh](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/compilar_mac.sh) (PyInstaller `--onedir`, assets incluidos, soporte arm64).
  - el `.env` se copia a `dist/.env` para acompañar el bundle.

## 10) Hallazgos y puntos a revisar

### 10.1 Seguridad

- Existen credenciales de Basic Auth definidas en el código (`settings.py`) y no provenientes de `.env`.
- El endpoint `/auth/test` entrega información de diagnóstico útil para troubleshooting, pero también incrementa superficie de exposición si el puerto es accesible en red.
- Algunos endpoints no requieren Basic Auth (`/pago/refund`, `/pago/detalle`, `/poll`, `/result`, `/register_agent`), lo que puede ser correcto si la red está aislada, pero es un punto crítico a validar.

### 10.2 Consistencia de API

- `/pago` está protegido por Basic Auth, mientras `/pago/refund` y `/pago/detalle` no lo están.
- `type=transbank` usa campos `terminal_id` / `amount` / `timeout`, pero en el payload interno del worker se mezcla `id_terminal` y `terminal_id` según flujo; hoy funciona porque el worker usa `amount` y `timeout`, pero conviene estandarizar nomenclatura si se amplía.

### 10.3 Robustez operativa

- El server usa hilos (Flask + workers + detecciones + monitor). Está bien para un gateway local, pero conviene monitorear bloqueos y asegurar que las operaciones serial siempre cierren puertos incluso ante excepciones.
- La limpieza automática de transacciones elimina entradas por “antigüedad”, lo que impacta endpoints de consulta si el cliente guarda `tx_id` más allá de ese tiempo.

## 11) Mapa rápido de archivos clave

- Inicio y wiring: [app.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/app.py)
- Configuración: [settings.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/config/settings.py)
- API HTTP y colas: [api_server.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/server/api_server.py)
- Auth: [auth.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/server/auth.py)
- Transbank: [pos_module.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/pos/pos_module.py)
- Getnet: [getnet_module.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/pos/getnet_module.py)
- UI: [tray_icon.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/ui/tray_icon.py)

