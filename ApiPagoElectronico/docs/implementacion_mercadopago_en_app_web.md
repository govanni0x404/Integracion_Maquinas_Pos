# Implementación Mercado Pago en la app web (consumiendo ApiPagoElectronico local)

Este documento describe cómo integrar Mercado Pago desde la app web (frontend del PDV) consumiendo el servicio local ApiPagoElectronico (Flask) corriendo en la PC de la caja.

Alcance: SOLO Mercado Pago (`type=mercadopago`). No incluye Transbank ni Getnet.

## 1) Requisitos previos (en la PC/caja)

### 1.1 Servicio local configurado

En el `.env` del servicio ApiPagoElectronico (PC/caja) debe existir:

- `MP_ACCESS_TOKEN=APP_USR-...` (obligatorio)
- `ALLOWED_ORIGINS=https://tu-dominio.cl` (obligatorio para browser; puede incluir varios separados por coma)
- `HTTP_PORT=5005` (si usas otro puerto)
- (Opcional) `MP_TERMINAL_ID=TERM_001` (si quieres un default local)
- (Opcional) `ALLOW_MP_TOKEN_IN_REQUEST=false` (recomendado: mantener en false)

El frontend NO debe transportar `MP_ACCESS_TOKEN`.

### 1.2 Autenticación de la API local

Los endpoints usados por la app web están protegidos con Basic Auth:

- `POST /online`
- `POST /pago`

Por lo tanto, el frontend debe enviar header `Authorization: Basic ...`.

En este proyecto, las credenciales actualmente están hardcodeadas en [settings.py:L7-L8](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/config/settings.py#L7-L8) como `API_AUTH_USER` y `API_AUTH_PASS`.

Implementación práctica en la app web:

- En cada request al servicio local agrega `Authorization: Basic <base64(user:pass)>`
- Donde `user`/`pass` son esos valores de `API_AUTH_USER`/`API_AUTH_PASS`

## 2) Flujo recomendado en el frontend

### 2.1 Verificar que el servicio está disponible

Antes de cobrar, validar:

1) “Servidor vivo”:

- `GET http://localhost:<HTTP_PORT>/status`

2) “Mercado Pago habilitado en el servicio” (token configurado):

- `POST http://localhost:<HTTP_PORT>/online`
- Body: `{ "type": "mercadopago" }`

Respuesta esperada:

- `online: true` si `MP_ACCESS_TOKEN` está configurado.

### 2.2 Iniciar cobro (Mercado Pago)

- Endpoint: `POST http://localhost:<HTTP_PORT>/pago`
- Body mínimo:

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

Campos:

- `type`: siempre `mercadopago`
- `amount`: entero (pesos, sin decimales)
- `id_sucursal`, `nombre_caja`: identificadores de tu sistema (sirven para el routing interno del gateway)
- `terminal_id`: terminal del dispositivo MP Point (si no lo envías, el servicio puede usar defaults locales)
- `timeout`: segundos de espera del polling
- (Opcional) `tx_id`: string único generado por el frontend (para reintentos idempotentes)

## 3) Contrato de respuesta (lo que debe parsear la app web)

El servicio responde un JSON que incluye:

- `success`: boolean
- `data`: objeto normalizado con:
  - `status`: estado principal (ej: `approved`, `rejected`, `timeout`, `error`, etc.)
  - `status_detail`: detalle (si Mercado Pago lo devuelve)
  - `order_id`: id de la orden MP
  - `response`: objeto original (o casi original) de Mercado Pago

Además, por compatibilidad, también se mantienen llaves “legacy” como `status`, `order_id`, `response` al nivel raíz.

### 3.1 Voucher (regla)

Construir voucher con la prioridad:

1) `data.response.transactions.payments[0].reference_id`
2) `data.response.transactions.payments[0].id`
3) `data.order_id` (o `data.response.id`)

## 4) Ejemplo de implementación (JavaScript)

### 4.1 Helpers

```js
function basicAuthHeader(user, pass) {
  const token = btoa(`${user}:${pass}`);
  return `Basic ${token}`;
}

function buildVoucher(mpJson) {
  const data = mpJson?.data ?? {};
  const resp = data?.response ?? {};
  const payment0 = resp?.transactions?.payments?.[0];

  return (
    payment0?.reference_id ||
    payment0?.id ||
    data?.order_id ||
    resp?.id ||
    null
  );
}
```

### 4.2 Verificar online (mercadopago)

```js
async function mpOnline({ port, authUser, authPass }) {
  const r = await fetch(`http://localhost:${port}/online`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": basicAuthHeader(authUser, authPass),
    },
    body: JSON.stringify({ type: "mercadopago" }),
  });

  const json = await r.json();
  return { httpOk: r.ok, ...json };
}
```

### 4.3 Cobrar (mercadopago)

```js
async function mpCobrar({
  port,
  authUser,
  authPass,
  idSucursal,
  nombreCaja,
  terminalId,
  amount,
  timeoutSeconds,
  txId,
}) {
  const payload = {
    type: "mercadopago",
    amount,
    id_sucursal: String(idSucursal),
    nombre_caja: String(nombreCaja),
    terminal_id: String(terminalId),
    timeout: timeoutSeconds,
    ...(txId ? { tx_id: txId } : {}),
  };

  const r = await fetch(`http://localhost:${port}/pago`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": basicAuthHeader(authUser, authPass),
    },
    body: JSON.stringify(payload),
  });

  const json = await r.json();
  const voucher = buildVoucher(json);

  return { httpStatus: r.status, ...json, voucher };
}
```

## 5) Consideraciones de navegador (CORS y preflight)

- Al enviar `Content-Type: application/json` + `Authorization`, el navegador hará preflight `OPTIONS`.
- El servicio local debe permitir:
  - `Origin` de tu sitio (`ALLOWED_ORIGINS`)
  - headers: `Content-Type`, `Authorization`
  - método `OPTIONS`

## 6) Manejo de errores (UX)

Casos típicos y qué mostrar:

- Servicio local no responde (`/status` falla): “No se detecta ApiPagoElectronico en esta caja”.
- `/online` mercadopago devuelve `online:false`: “Mercado Pago no está configurado en esta caja (falta token)”.
- `/pago` devuelve `success:false` con `status=rejected`: “Pago rechazado”.
- `/pago` devuelve `status=timeout`: “Tiempo de espera agotado: el cliente no aprobó en el dispositivo”.
- `/pago` devuelve `status=error`: “Error en integración: revisar conectividad/token/terminal”.

## 7) Checklist de implementación en la app web

- [ ] Configurar URL base del servicio local: `http://localhost:<HTTP_PORT>`
- [ ] Agregar credenciales para Basic Auth (fuente según tu app; no deben ir hardcodeadas en el frontend público)
- [ ] Implementar “health check” (`/status`) y “MP online” (`/online` con `type=mercadopago`)
- [ ] Implementar el request de cobro (`/pago` con `type=mercadopago`)
- [ ] Parsear respuesta y construir voucher con `reference_id || payment_id || order_id`
- [ ] Manejar timeouts/rechazos/errores y mostrar mensajes claros al usuario
