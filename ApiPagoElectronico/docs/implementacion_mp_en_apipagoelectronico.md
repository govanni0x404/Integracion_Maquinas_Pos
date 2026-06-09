# Implementación Mercado Pago en ApiPagoElectronico (Flask local) para consumo directo desde el frontend

## Contexto y objetivo

Hoy el frontend del PDV (en el PC de la caja) puede llamar a `http://localhost:5005` (servicio local). Eso resuelve el problema de despliegue centralizado: el backend en el VPS no puede llamar a `localhost` del cliente.

Para Transbank/Getnet esto es natural (hardware local). Para Mercado Pago, además se necesita un `access_token` para consumir la API de MP. El punto crítico es:

- El `access_token` **es secreto** y no debe exponerse al navegador ni quedar en JS.
- El servicio Flask local **sí** debe tener acceso al `access_token` para poder ejecutar el pago.

Este documento define cómo implementar Mercado Pago en ApiPagoElectronico (Flask) manteniendo:

- Llamada desde frontend → servicio local.
- `access_token` guardado en el servicio local (env/.env), no enviado por el browser.
- Respuesta estable y compatible con el parser actual del frontend (`voucher = reference_id || payment_id || order_id`).

---

## Diseño propuesto (seguro)

### 1) Frontend → ApiPagoElectronico (local)

El frontend llama:

- `POST http://localhost:5005/online` con `{ "type": "mercadopago" }`
- `POST http://localhost:5005/pago` con:

```json
{
  "type": "mercadopago",
  "amount": 19990,
  "id_sucursal": "70",
  "nombre_caja": "caja_01",
  "terminal_id": "TERM_001",
  "timeout": 130
}
```

Regla: el frontend **NO** envía `access_token`.

### 2) ApiPagoElectronico (local) → Mercado Pago (internet)

El servicio local toma el token desde su configuración:

- `MP_ACCESS_TOKEN` (recomendado), o
- un storage local seguro (si más adelante se implementa UI para setearlo).

---

## Variables de entorno recomendadas (ApiPagoElectronico)

En la caja (PC donde corre el servicio):

- `POS_API_AUTH_USER` / `POS_API_AUTH_PASS` (ya existe auth Basic).
- `ALLOWED_ORIGINS` (CORS) con el dominio del PDV (VPS) y opcional `http://localhost`.
- Mercado Pago:
  - `MP_ACCESS_TOKEN`
  - `MP_API_URL` (si ya existe, mantener)
  - `MP_TERMINAL_ID` (si aplica en tu integración)

Ejemplo:

```ini
POS_API_AUTH_USER=CAJA01
POS_API_AUTH_PASS=CAMBIAR_ESTA_CLAVE
ALLOWED_ORIGINS=https://tu-dominio.cl,http://localhost

MP_ACCESS_TOKEN=APP_USR-XXXXX
MP_API_URL=https://api.mercadopago.com/v1/orders
MP_TERMINAL_ID=TERM_001
```

---

## Cambios requeridos en ApiPagoElectronico (Flask)

El análisis del proyecto Flask indica que hoy para `type=mercadopago` el endpoint `/pago` “requiere `access_token`” en la request.

Eso es lo que hay que cambiar para el modo “frontend directo”.

### 1) Cambiar contrato de `/pago` para Mercado Pago

Antes (actual):

- El cliente envía `access_token` en el body.

Después (propuesto):

- El body **no** requiere `access_token`.
- El servidor obtiene `access_token` desde `MP_ACCESS_TOKEN`.
- Para mantener compatibilidad y facilitar migración, se puede permitir `access_token` en request solo si existe un flag explícito (ej. `ALLOW_MP_TOKEN_IN_REQUEST=true`) y se recomienda dejarlo en `false` en producción.

### 2) Normalizar respuesta para compatibilidad con el frontend

El frontend actual construye `voucher` desde:

- `response.transactions.payments[0].reference_id`, o
- `response.transactions.payments[0].id`, o
- `order_id` / `response.id`

Por lo tanto, ApiPagoElectronico debe devolver (en `data`) algo que contenga al menos:

- `order_id` (string)
- `response` con estructura que incluya `transactions.payments[0]...` (si tu integración no lo trae igual, mapearlo).

Recomendación: devolver el JSON original de MP bajo `response`, y además “promover” campos útiles al nivel de `data`:

- `status`, `status_detail`, `order_id`

### 3) CORS: habilitar headers necesarios

Como el browser llamará a `http://localhost:5005`, el servicio debe permitir el origen del sitio (VPS):

- `Access-Control-Allow-Origin: https://tu-dominio.cl`
- `Access-Control-Allow-Headers: Content-Type, Authorization`
- `Access-Control-Allow-Methods: POST, GET, OPTIONS`

Si usas Basic Auth, el browser enviará `Authorization`. Ese header debe estar permitido.

### 4) Autenticación para el modo browser

Opciones:

- **A (simple)**: mantener Basic Auth (ya existe). El browser cachea la sesión y funciona, pero es fricción UX.
- **B (mejor UX)**: agregar un token local (`POS_LOCAL_TOKEN`) que el frontend envía como header (ej. `X-Pos-Token`). La validación se hace en Flask y no aparece el popup del navegador.

Este doc asume A para mantener mínimo cambio, pero es recomendable B.

### 5) Logging seguro

- No loguear `MP_ACCESS_TOKEN` ni `Authorization`.
- Si se loguea request/response, redactar tokens.

---

## Esqueleto de implementación (pseudocódigo)

### `settings.py` / config

- Agregar lectura de `MP_ACCESS_TOKEN` (si no existe).
- Mantener `MP_API_URL`.

### `process_mercadopago(payload)`

1. `access_token = payload.get("access_token")` solo si `ALLOW_MP_TOKEN_IN_REQUEST=true`.
2. Si no, `access_token = MP_ACCESS_TOKEN` desde settings/env.
3. Si no hay token: error 500 `Falta MP_ACCESS_TOKEN`.
4. Ejecutar flujo:
   - crear orden con `X-Idempotency-Key` (por `tx_id`).
   - poll a `GET /v1/orders/{id}` hasta aprobado/rechazado/timeout.
5. Responder:
   - `success=true|false`
   - `data` con `status`, `status_detail`, `order_id`, `response` (obj original MP).

---

## Contrato de respuesta recomendado

### Aprobado

```json
{
  "success": true,
  "data": {
    "status": "approved",
    "status_detail": "accredited",
    "order_id": "1234567890",
    "response": {
      "id": "1234567890",
      "status": "approved",
      "status_detail": "accredited",
      "transactions": {
        "payments": [
          { "id": "99887766", "reference_id": "ABC-123", "amount": 19990 }
        ]
      }
    }
  }
}
```

### Rechazado / error MP

```json
{
  "success": false,
  "message": "Pago no aprobado",
  "data": {
    "status": "rejected",
    "status_detail": "cc_rejected_other_reason",
    "response": { }
  }
}
```

### Timeout (no llegó aprobación dentro de `timeout`)

```json
{
  "success": false,
  "message": "timeout",
  "data": {
    "status": "timeout",
    "response": { }
  }
}
```

---

## Recomendación operativa

- Mantener Mercado Pago ejecutándose desde el **servicio local** (Flask) por seguridad del token.
- Evitar que el frontend transporte `access_token`. Si se necesita rotación:
  - usar el panel local (`/panel`) para configurar el `.env` en la caja, o
  - implementar “token exchange” con el servidor central (entrega un token efímero al servicio local), pero eso ya reintroduce dependencia del backend.

---

## Checklist de implementación

- ApiPagoElectronico:
  - `MP_ACCESS_TOKEN` existe y se lee desde env.
  - `/pago` para `mercadopago` no exige token en request.
  - CORS permite el origen del PDV y el header `Authorization` (o `X-Pos-Token` si se implementa).
  - Response tiene `data.order_id` y/o `data.response...` según contrato.
- Frontend:
  - Llama a `http://localhost:5005/pago` con `type=mercadopago` y sin token.
  - Construye `voucher` desde `reference_id || payment_id || order_id`.

