# Plan de corrección — Integración Mercado Pago (ApiPagoElectronico)

Este documento es el checklist operativo para corregir SOLO la integración con Mercado Pago, manteniendo Transbank y Getnet sin cambios funcionales.

Fuente de requisitos: [implementacion_mp_en_apipagoelectronico.md](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/docs/implementacion_mp_en_apipagoelectronico.md).

## Objetivo

- Permitir que el frontend (sitio del PDV) invoque el servicio local Flask en `http://localhost:<HTTP_PORT>` para cobrar con Mercado Pago.
- El `access_token` NO viaja desde el navegador; se lee desde configuración local (`MP_ACCESS_TOKEN` en `.env`).
- CORS y preflight funcionan desde navegador.
- Respuesta estable y compatible para construir voucher: `reference_id || payment_id || order_id`.

## No objetivo (garantía)

- No cambiar contratos/comportamiento de Transbank ni Getnet.
- No modificar flujos de cola/agentes fuera de lo estrictamente necesario para Mercado Pago.

## Estado actual (resumen)

- Existe flujo MP en [api_server.py](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/server/api_server.py) vía `process_mercadopago()` (crear orden + poll).
- `/pago` para `type=mercadopago` exige `access_token` en request (incompatible con el modo “frontend directo”).
- CORS está habilitado en forma genérica, pero `OPTIONS` puede fallar por Basic Auth (preflight).

## Checklist de implementación (etapas)

### Etapa 0 — Análisis y preparación

- [x] Revisar implementación actual Mercado Pago en backend (código + scripts).
- [x] Revisar requerimientos del documento de implementación.
- [x] Definir contrato de respuesta final (wrapper `success/data` + compatibilidad con respuesta actual).
- [x] Definir estrategia de idempotencia (`tx_id` opcional desde request; si no viene, el server lo genera; se usa como `external_reference` y base del `X-Idempotency-Key`).

### Etapa 1 — Configuración (settings/.env)

- [x] Agregar lectura de `MP_ACCESS_TOKEN` desde env.
- [x] Agregar `ALLOWED_ORIGINS` (CSV) para restringir CORS; si está vacío, mantener comportamiento “allow all” por compatibilidad.
- [x] Agregar flag `ALLOW_MP_TOKEN_IN_REQUEST` (default false) para compatibilidad temporal (solo si se requiere).
- [x] (Opcional) Agregar `MP_TERMINAL_ID` para default de terminal.

### Etapa 2 — CORS y preflight (navegador)

- [x] Configurar Flask-CORS usando `ALLOWED_ORIGINS` y habilitar headers necesarios (al menos `Content-Type` y `Authorization`).
- [x] Permitir `OPTIONS` sin exigir Basic Auth (preflight) para endpoints protegidos.

### Etapa 3 — Endpoint `/online` (mercadopago)

- [x] Implementar rama `type=mercadopago` en `/online` que valide configuración local (token presente).

### Etapa 4 — Endpoint `/pago` (mercadopago)

- [x] Cambiar contrato: no exigir `access_token` en request; leer de `MP_ACCESS_TOKEN`.
- [x] Mantener compatibilidad: si `ALLOW_MP_TOKEN_IN_REQUEST=true`, aceptar `access_token` en request.
- [x] Normalizar respuesta a contrato recomendado:
  - `success: true|false`
  - `data: { status, status_detail, order_id, response }`
  - Mantener también llaves legacy (`status`, `order_id`, `response`, `http_status`, `message`) si ya existían.
- [x] Idempotencia: usar `X-Idempotency-Key` estable (por `tx_id` opcional).

### Etapa 5 — Tests y herramientas

- [x] Ajustar [test_mercadopago.sh](file:///Users/carloscerda/Sites/localhost/apiPagoElectronico/ApiPagoElectronico/test_mercadopago.sh) para que NO envíe `access_token` (token viene desde `.env`).
- [x] Validación rápida: `python -m compileall .` (sintaxis).
- [ ] Prueba funcional con servicio corriendo: `/online` (mercadopago) y `/pago` (mercadopago) desde el frontend o curl.

### Etapa 6 — Cierre

- [ ] Registrar cambios finales (resumen) y checklist completado.

## Registro de avances

### 2026-04-23

- Completado: análisis inicial de Mercado Pago y definición de brechas.
- Completado: cambios de configuración, CORS/preflight, `/online` MP y `/pago` MP (token desde env + respuesta normalizada).
