"""HTML del panel de control — servido en /panel por Flask."""

PANEL_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ app_name }} — Panel de Control</title>
<style>
  /* Sin fuentes externas: el panel se abre en la caja aunque no haya internet. */
  :root {
    --bg:        #0b1020;
    --surface:   #10172a;
    --card:      #141c31;
    --card-2:    #18213a;
    --border:    #243050;
    --border-2:  #2f3c60;
    --text:      #e6eaf3;
    --text-2:    #b4bdd3;
    --muted:     #8590ab;
    --accent:    #4f7cff;
    --accent-2:  #3b63e0;
    --green:     #22c55e;
    --yellow:    #f5a524;
    --red:       #ef4444;
    --c-transbank: #e5484d;
    --c-getnet:    #ff7a1a;
    --c-mp:        #00a8e8;
    --c-tuu:       #9b6dff;
    --radius:    12px;
    --mono: ui-monospace, "SF Mono", "Cascadia Code", Consolas, "Liberation Mono", monospace;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { scroll-behavior: smooth; scroll-padding-top: 84px; }

  body {
    font-family: "Inter", "Segoe UI", system-ui, -apple-system, Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    font-size: 14px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  code { font-family: var(--mono); }

  /* ── Barra superior ── */
  .topbar {
    position: sticky; top: 0; z-index: 50;
    background: rgba(11,16,32,0.92);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--border);
  }
  .topbar-inner {
    max-width: 1240px; margin: 0 auto;
    padding: 14px 24px;
    display: flex; align-items: center; gap: 14px;
  }
  .brand-mark {
    width: 36px; height: 36px; border-radius: 9px;
    background: var(--accent);
    display: grid; place-items: center;
    color: #fff; font-weight: 700; font-size: 15px; letter-spacing: -0.02em;
  }
  .brand h1 { font-size: 16px; font-weight: 650; letter-spacing: -0.01em; }
  .brand p  { font-size: 12px; color: var(--muted); }
  .topbar-right { margin-left: auto; display: flex; align-items: center; gap: 14px; }
  .clock { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }

  /* ── Layout ── */
  .layout {
    max-width: 1240px; margin: 0 auto;
    padding: 24px;
    display: grid; grid-template-columns: 210px minmax(0, 1fr); gap: 28px;
    align-items: start;
  }
  .sidenav { position: sticky; top: 88px; }
  .sidenav-group { margin-bottom: 18px; }
  .sidenav-title {
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .08em;
    color: var(--muted); padding: 0 10px; margin-bottom: 6px;
  }
  .sidenav a {
    display: flex; align-items: center; gap: 10px;
    padding: 7px 10px; border-radius: 8px;
    color: var(--text-2); font-size: 13px;
  }
  .sidenav a:hover { background: var(--card); color: var(--text); text-decoration: none; }
  .nav-count {
    margin-left: auto; min-width: 20px; padding: 0 6px; border-radius: 999px;
    background: rgba(245,165,36,0.18); color: var(--yellow);
    font-size: 11px; font-weight: 700; text-align: center;
  }
  main { min-width: 0; display: flex; flex-direction: column; gap: 20px; }

  /* ── Indicadores ── */
  .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
  .dot-live { animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: .35 } }
  .brand-dot { width: 9px; height: 9px; border-radius: 3px; flex: none; }
  .bd-transbank { background: var(--c-transbank); }
  .bd-getnet    { background: var(--c-getnet); }
  .bd-mp        { background: var(--c-mp); }
  .bd-tuu       { background: var(--c-tuu); }

  .badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 3px 10px; border-radius: 999px;
    font-size: 12px; font-weight: 600; white-space: nowrap;
    background: rgba(133,144,171,0.14); color: var(--text-2);
    border: 1px solid rgba(133,144,171,0.25);
  }
  .badge-green  { background: rgba(34,197,94,0.12);  color: var(--green);  border-color: rgba(34,197,94,0.3); }
  .badge-yellow { background: rgba(245,165,36,0.12); color: var(--yellow); border-color: rgba(245,165,36,0.3); }
  .badge-red    { background: rgba(239,68,68,0.12);  color: var(--red);    border-color: rgba(239,68,68,0.3); }
  .badge-blue   { background: rgba(79,124,255,0.14); color: #8eaaff;       border-color: rgba(79,124,255,0.35); }

  /* ── Tarjetas ── */
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .card-head {
    display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap;
    padding: 18px 22px;
    border-bottom: 1px solid var(--border);
  }
  .card-head h2 { font-size: 15px; font-weight: 650; display: flex; align-items: center; gap: 10px; }
  .card-head p  { font-size: 12.5px; color: var(--muted); margin-top: 3px; }
  .card-head > div:first-child { flex: 1 1 280px; min-width: 0; }
  .card-head .head-right { margin-left: auto; display: flex; gap: 8px; align-items: center; min-width: 0; max-width: 100%; }
  .card-body { padding: 20px 22px; }

  /* ── Resumen ── */
  .summary { display: grid; grid-template-columns: 1fr 1fr 2fr; gap: 16px; }
  .tile { padding: 18px 20px; }
  .tile-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }
  .tile-value { font-size: 24px; font-weight: 700; margin-top: 6px; letter-spacing: -0.02em; word-break: break-word; }
  .tile-sub { font-size: 12.5px; color: var(--muted); margin-top: 2px; }

  .methods { display: flex; flex-direction: column; }
  .method {
    display: grid; grid-template-columns: 130px auto minmax(0, 1fr); align-items: center; gap: 12px;
    padding: 7px 0; border-bottom: 1px dashed var(--border);
  }
  .method:last-child { border-bottom: none; padding-bottom: 0; }
  .method:first-of-type { padding-top: 10px; }
  .method-name { display: flex; align-items: center; gap: 9px; font-weight: 600; font-size: 13px; }
  .method-id { font-family: var(--mono); font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* ── Formulario ── */
  .fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 22px; }
  .field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .field-wide { grid-column: 1 / -1; }
  .field-label { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
  .field-label span { font-size: 13px; font-weight: 600; color: var(--text); }
  .field-label code { font-size: 11px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .field-hint { font-size: 12px; color: var(--muted); line-height: 1.45; }
  .field-row { display: flex; gap: 8px; }
  #f-NOMBRE_CAJA { text-transform: uppercase; }
  .param-val:disabled { opacity: .7; cursor: not-allowed; }
  .check { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text); cursor: pointer; width: fit-content; }
  .check input { width: 16px; height: 16px; accent-color: var(--accent); margin: 0; }
  .field-row .param-val { flex: 1; min-width: 0; }

  .param-val {
    width: 100%;
    font: inherit; font-size: 13.5px; color: var(--text);
    background: var(--surface);
    border: 1px solid var(--border-2);
    border-radius: 8px;
    padding: 8px 11px;
    transition: border-color .15s, box-shadow .15s;
  }
  .param-val::placeholder { color: #66718e; }
  .param-val:hover { border-color: #3a4870; }
  .param-val:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(79,124,255,0.22); }
  .param-val[readonly] { color: var(--muted); background: transparent; border-style: dashed; cursor: not-allowed; }
  select.param-val {
    appearance: none; -webkit-appearance: none; cursor: pointer;
    background-image: linear-gradient(45deg, transparent 50%, var(--muted) 50%), linear-gradient(135deg, var(--muted) 50%, transparent 50%);
    background-position: calc(100% - 17px) 50%, calc(100% - 12px) 50%;
    background-size: 5px 5px; background-repeat: no-repeat;
    padding-right: 32px;
  }
  select.param-val option { background: var(--surface); color: var(--text); }

  .env-path {
    font-family: var(--mono); font-size: 11.5px; color: var(--muted);
    background: var(--surface); border: 1px solid var(--border);
    padding: 3px 8px; border-radius: 6px; max-width: 100%;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block;
  }

  /* Barra de guardado fija al pie mientras se edita la configuración */
  .savebar {
    position: sticky; bottom: 16px; z-index: 40;
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    padding: 12px 16px;
    background: rgba(20,28,49,0.96);
    backdrop-filter: blur(8px);
    border: 1px solid var(--border-2);
    border-radius: var(--radius);
    box-shadow: 0 10px 30px rgba(0,0,0,0.45);
  }
  .savebar-text { font-size: 12.5px; color: var(--muted); margin-right: auto; }

  /* ── Botones ── */
  .btn {
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    padding: 8px 16px; border-radius: 8px;
    font: inherit; font-size: 13.5px; font-weight: 600;
    cursor: pointer; border: 1px solid transparent; transition: background .15s, border-color .15s, color .15s;
    white-space: nowrap;
  }
  .btn:hover { text-decoration: none; }
  .btn:disabled { opacity: .45; cursor: not-allowed; }
  .btn-primary { background: var(--accent); color: #fff; }
  .btn-primary:hover:not(:disabled) { background: var(--accent-2); }
  .btn-secondary { background: var(--card-2); color: var(--text); border-color: var(--border-2); }
  .btn-secondary:hover:not(:disabled) { border-color: var(--accent); }
  .btn-sm { padding: 6px 12px; font-size: 12.5px; }
  .btn-ok:hover:not(:disabled)  { border-color: var(--green); color: var(--green); }
  .btn-bad:hover:not(:disabled) { border-color: var(--red);   color: var(--red); }

  .actions { display: flex; gap: 8px; flex-wrap: wrap; }

  /* ── Pestañas de máquinas ── */
  #sec-maquinas .card-head { align-items: center; }
  .machine-panel[hidden] { display: none !important; }
  .machine-desc { font-size: 12.5px; color: var(--muted); margin-bottom: -4px; }
  .tab-state { width: 6px; height: 6px; border-radius: 50%; background: #3a4566; margin-left: 2px; }
  .tab-state.on { background: var(--green); }

  /* ── Transacciones sin confirmar ── */
  .alert-card { border-color: rgba(245,165,36,0.45); }
  .alert-card .card-head { background: rgba(245,165,36,0.06); border-radius: var(--radius) var(--radius) 0 0; }
  .tx-list { display: flex; flex-direction: column; gap: 12px; }
  .tx-item {
    background: var(--surface); border: 1px solid var(--border);
    border-left: 3px solid var(--yellow);
    border-radius: 10px; padding: 14px 16px;
    display: flex; flex-direction: column; gap: 10px;
  }
  .tx-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .tx-title { font-weight: 650; font-size: 14px; text-transform: capitalize; }
  .tx-amount { font-weight: 700; font-size: 15px; font-variant-numeric: tabular-nums; }
  .tx-head .badge { margin-left: auto; }
  .tx-meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 4px 18px; font-size: 12px; color: var(--muted); }
  .tx-meta b { color: var(--text-2); font-weight: 500; font-family: var(--mono); font-size: 11.5px; word-break: break-all; }
  .tx-note { font-size: 12.5px; color: var(--text-2); background: rgba(133,144,171,0.08); border-radius: 6px; padding: 6px 10px; }
  .dv-wrap { overflow-x: auto; }
  .dv-table { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
  .dv-table th, .dv-table td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  .dv-table th { color: var(--muted); font-weight: 500; }
  .dv-table tr.dv-cand td { background: rgba(34,197,94,0.08); }

  /* ── Pestañas y log ── */
  .tabs { display: inline-flex; gap: 2px; padding: 3px; background: var(--surface); border: 1px solid var(--border); border-radius: 9px; flex-wrap: wrap; }
  .tab {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 6px 12px; border-radius: 7px; border: none;
    background: transparent; color: var(--muted);
    font: inherit; font-size: 12.5px; font-weight: 600; cursor: pointer;
  }
  .tab:hover { color: var(--text); }
  .tab.active { background: var(--card-2); color: var(--text); box-shadow: inset 0 0 0 1px var(--border-2); }
  .log-box {
    font-family: var(--mono);
    font-size: 12px; line-height: 1.65;
    color: #aab4c8;
    background: #080c18;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 16px;
    height: 340px;
    overflow: auto;
    white-space: pre-wrap; word-break: break-word;
  }

  /* ── Toast ── */
  #toast {
    position: fixed; bottom: 24px; right: 24px;
    max-width: 420px;
    background: var(--card-2); border: 1px solid var(--border-2);
    border-left-width: 4px;
    border-radius: 10px; padding: 12px 16px;
    font-size: 13.5px; font-weight: 500;
    box-shadow: 0 12px 32px rgba(0,0,0,0.5);
    transform: translateY(20px); opacity: 0; pointer-events: none;
    transition: transform .25s ease, opacity .25s ease;
    z-index: 9999;
  }
  #toast.show { transform: translateY(0); opacity: 1; }
  #toast.ok  { border-left-color: var(--green); }
  #toast.err { border-left-color: var(--red); }

  footer {
    max-width: 1240px; margin: 8px auto 0; padding: 18px 24px 28px;
    color: var(--muted); font-size: 12px;
    display: flex; gap: 6px 18px; flex-wrap: wrap; justify-content: space-between;
  }
  footer code { font-size: 11.5px; }

  /* ── Responsive ── */
  @media (max-width: 1080px) {
    .layout { grid-template-columns: minmax(0, 1fr); }
    .sidenav { display: none; }
    .summary { grid-template-columns: 1fr 1fr; }
    .summary .tile-methods { grid-column: 1 / -1; }
  }
  @media (max-width: 700px) {
    .fields, .summary { grid-template-columns: minmax(0, 1fr); }
    .method { grid-template-columns: 1fr auto; }
    .method-id { grid-column: 1 / -1; }
    .topbar-inner, .layout { padding-left: 14px; padding-right: 14px; }
    .clock { display: none; }
    .savebar-text { display: none; }
    .savebar .btn { flex: 1; }
  }
</style>
</head>
<body>

{% set hints = {
  'HTTP_PORT': 'Puerto donde corre esta API. Se define en el .env, no se puede cambiar desde acá.',
  'ID_SUCURSAL': 'Identificador de la sucursal. Tiene que coincidir con el id_sucursal que manda tu sistema web en cada /pago.',
  'NOMBRE_CAJA': 'Nombre de esta caja puntual. Por defecto usa el nombre del PC si se deja vacío.',
  'TRANSBANK_TERMINAL_ID': 'Identificador de la máquina Transbank de esta caja. El sistema web debe mandar este mismo valor en terminal_id de cada venta Transbank. Debe ser distinto al de las otras máquinas.',
  'GETNET_TERMINAL_ID': 'Identificador de la máquina Getnet de esta caja. El sistema web debe mandar este mismo valor en terminal_id de cada venta Getnet. Debe ser distinto al de las otras máquinas.',
  'USAR_POS_FISICO': 'Esto es solo para Transbank. Poné true únicamente si esta caja tiene un POS Transbank físico conectado. No tiene nada que ver con Getnet.',
  'PUERTOS_COM': 'Lista de puertos COM donde Transbank va a buscar su POS (autodetección). Tampoco aplica a Getnet.',
  'USAR_GETNET': 'Este sí es el de Getnet: poné true si esta caja tiene un POS Getnet A920 Pro conectado.',
  'GETNET_PORT': 'Puerto COM fijo para el POS Getnet (ej. COM3). Dejalo vacío para que se detecte automáticamente — solo forzarlo si la autodetección elige el puerto equivocado.',
  'TRANSBANK_MAX_ESPERA': 'Segundos máximos que una venta Transbank sin confirmar (ej. cable desconectado) mantiene abierta la petición esperando que el POS vuelva; al volver se consulta su última venta. Al cumplirse responde INDETERMINADA y se sigue validando en segundo plano. 0 = sin límite. Default 300 (5 min).',
  'GETNET_MAX_ESPERA': 'Segundos máximos que una venta Getnet sin confirmar (ej. cable desconectado) mantiene abierta la petición esperando que el POS vuelva. Al cumplirse responde INDETERMINADA y se sigue validando en segundo plano. 0 = sin límite. Default 300 (5 min).',
  'MAX_TRANSACTION_TIME': 'Segundos máximos por defecto que se espera la respuesta de una venta (Transbank o Getnet) antes de darla por vencida. Se puede pisar por venta si el sistema web manda "timeout" en el request.',
  'TIMEOUT_SERVER': 'Timeout general (segundos) del servidor HTTP, usado como default en Mercado Pago y otras operaciones. También se puede pisar por request.',
  'ALLOWED_ORIGINS': 'Dominios web permitidos para llamar a esta API desde el navegador (CORS). Vacío = se permite cualquier origen ("*"), o sea que cualquier página abierta en esta PC podría intentar cobrar. Ej: https://mitienda.cl,https://otra.cl',
  'BIND_HOST': '127.0.0.1 = la API solo acepta llamadas desde esta misma PC (lo recomendado si el sistema web corre en el navegador de la caja). 0.0.0.0 = acepta llamadas de toda la red (solo si otro equipo llama a esta caja). Requiere reiniciar.',
  'AGENTES_REMOTOS': 'Habilita /register_agent, /poll, /result y /debug/queues (agentes remotos). Con false quedan deshabilitados y cualquier intento de uso queda registrado en el log técnico.',
  'LOG_RETENCION_DIAS': 'Días que se guardan los logs diarios antes de borrarlos. 0 = no borrar nunca.',
  'USAR_MP': 'Poné true si esta caja cobra con Mercado Pago (terminales Point). Con false, /pago type mercadopago se rechaza.',
  'ALLOWED_MP': 'Por defecto todas las cajas pueden cobrar con Mercado Pago. Para permitir solo algunas, desmarcá la casilla y escribilas en formato sucursal:caja, separadas por coma.',
  'MP_API_URL': 'URL de la API de Orders de Mercado Pago. Debe comenzar con https://api.mercadopago.com/ (el token se envía a esta dirección).',
  'MP_ACCESS_TOKEN': 'Access token de la cuenta de Mercado Pago (APP_USR-...). No se muestra por seguridad: para reemplazarlo presioná Cambiar.',
  'MP_TERMINAL_ID': 'Terminal(es) Point de Mercado Pago de esta caja (ej. NEWLAND_N950__ABC123). Es independiente de los terminales de Getnet/Transbank y de las máquinas TUU. Si hay más de uno, separarlos por coma; el sistema web elige cuál con mp_terminal_id en cada /pago.',
  'ALLOW_MP_TOKEN_IN_REQUEST': '⚠️ Si está en true, cualquiera que llame a /pago puede mandar su propio access_token de Mercado Pago en el request, en vez de usar el MP_ACCESS_TOKEN configurado acá. Útil solo si varias cajas/clientes usan cuentas de MP distintas. Dejar en false si no lo necesitás.',
  'USAR_TUU': 'Poné true si esta caja cobra con máquinas TUU (Pago remoto, PRO2/Kozen). La máquina debe estar en "Modo Integración" para recibir los cobros.',
  'TUU_API_URL': 'URL de la API de Pago remoto de TUU. No cambiar salvo indicación de TUU: la API key se envía a esta dirección.',
  'TUU_DEVICES': 'Número(s) de serie de las máquinas TUU permitidas en esta caja, separados por coma. El sistema web indica la máquina en el terminal_id de cada /pago. Vacío = se acepta cualquier terminal_id.',
  'TUU_API_KEY': 'API key de TUU que se usa cuando el /pago no trae su propia key en access_token. Si cada máquina tiene su key y el sistema web la envía, puede quedar vacía. No se muestra por seguridad: para reemplazarla presioná Cambiar.',
  'ALLOW_TUU_KEY_IN_REQUEST': 'Si está en true, cada /pago TUU puede traer la API key de su máquina en access_token (igual que ALLOW_MP_TOKEN_IN_REQUEST). Si no la trae, se usa TUU_API_KEY. Con false siempre se usa TUU_API_KEY.',
} %}

{# Campo de texto: etiqueta legible + nombre de la variable del .env + ayuda visible. #}
{% macro field(key, label, value='', hint='', placeholder='', type='text', readonly=false, wide=false, id='', autocomplete='off') %}
<div class="field{% if wide %} field-wide{% endif %}">
  <label class="field-label" for="{{ id or 'f-' ~ key }}"><span>{{ label }}</span><code>{{ key }}</code></label>
  <input class="param-val" id="{{ id or 'f-' ~ key }}" type="{{ type }}" name="{{ key }}" value="{{ value }}" autocomplete="{{ autocomplete }}"
         {% if placeholder %}placeholder="{{ placeholder }}"{% endif %}
         {% if readonly %}readonly title="El puerto se define en el .env y no se puede editar desde el panel"{% elif hint %}title="{{ hint }}"{% endif %}>
  {% if hint %}<p class="field-hint">{{ hint }}</p>{% endif %}
</div>
{% endmacro %}

{# Credencial: si ya está configurada queda bloqueada hasta presionar Cambiar
   (un campo bloqueado no se envía, así se mantiene la actual). #}
{% macro secret(key, label, configured, hint='', placeholder='') %}
<div class="field">
  <label class="field-label" for="f-{{ key }}"><span>{{ label }}</span><code>{{ key }}</code></label>
  <div class="field-row">
    <input class="param-val" id="f-{{ key }}" type="password" name="{{ key }}" autocomplete="new-password" title="{{ hint }}"
           {% if configured %}disabled placeholder="•••••••• configurado"{% else %}placeholder="{{ placeholder }}"{% endif %}>
    {% if configured %}<button type="button" class="btn btn-secondary" onclick="editSecret('{{ key }}', this)">Cambiar</button>{% endif %}
  </div>
  {% if hint %}<p class="field-hint">{{ hint }}</p>{% endif %}
</div>
{% endmacro %}

{# Valores true/false: se eligen de una lista, pero se guardan igual que antes en el .env. #}
{% macro toggle(key, label, value, hint='', id='', refresh=false) %}
{% set on = (value | string | lower | trim) == 'true' %}
<div class="field">
  <label class="field-label" for="{{ id or 'f-' ~ key }}"><span>{{ label }}</span><code>{{ key }}</code></label>
  <select class="param-val" id="{{ id or 'f-' ~ key }}" name="{{ key }}" title="{{ hint }}"{% if refresh %} oninput="refreshDetectButtons()" onchange="refreshDetectButtons()"{% endif %}>
    <option value="true"{% if on %} selected{% endif %}>Sí (true)</option>
    <option value="false"{% if not on %} selected{% endif %}>No (false)</option>
  </select>
  {% if hint %}<p class="field-hint">{{ hint }}</p>{% endif %}
</div>
{% endmacro %}

{% set mp_on = (env_vars.get('USAR_MP', 'true') | lower) == 'true' %}
{% set tuu_on = (env_vars.get('USAR_TUU', 'false') | lower) == 'true' %}
{% set known = ['HTTP_PORT','ID_SUCURSAL','NOMBRE_CAJA','TERMINAL_ID','TRANSBANK_TERMINAL_ID','GETNET_TERMINAL_ID','USAR_POS_FISICO','PUERTOS_COM','TRANSBANK_MAX_ESPERA','USAR_GETNET','GETNET_PORT','GETNET_MAX_ESPERA','MAX_TRANSACTION_TIME','TIMEOUT_SERVER','ALLOWED_ORIGINS','API_AUTH_USER','API_AUTH_PASS','USAR_MP','ALLOWED_MP','MP_API_URL','MP_ACCESS_TOKEN','MP_TERMINAL_ID','ALLOW_MP_TOKEN_IN_REQUEST','USAR_TUU','TUU_API_URL','TUU_API_KEY','TUU_DEVICES','ALLOW_TUU_KEY_IN_REQUEST','BIND_HOST','AGENTES_REMOTOS','LOG_RETENCION_DIAS'] %}
{% set extras = namespace(n=0) %}
{% for key in env_vars %}{% if key not in known %}{% set extras.n = extras.n + 1 %}{% endif %}{% endfor %}

<!-- Barra superior -->
<header class="topbar">
  <div class="topbar-inner">
    <div class="brand-mark">AP</div>
    <div class="brand">
      <h1>{{ app_name }}</h1>
      <p>Panel de control · {{ nombre_caja }} · puerto {{ http_port }}</p>
    </div>
    <div class="topbar-right">
      <span class="clock" id="clock"></span>
      <span class="badge badge-green"><span class="dot dot-live"></span>Servicio activo</span>
      <button type="button" class="btn btn-secondary" onclick="cerrarSesion()">Salir</button>
    </div>
  </div>
</header>

<div class="layout">

  <!-- Navegación lateral -->
  <nav class="sidenav">
    <div class="sidenav-group">
      <div class="sidenav-title">Estado</div>
      <a href="#resumen">Resumen</a>
      <a href="#pendientesCard" id="navPendientes" style="display:none;">Sin confirmar <span class="nav-count" id="navPendientesCount">0</span></a>
      <a href="#logs">Log reciente</a>
    </div>
    <div class="sidenav-group">
      <div class="sidenav-title">Configuración</div>
      <a href="#sec-caja">Caja y servidor</a>
      <a href="#sec-maquinas" onclick="showMachine('transbank')"><span class="brand-dot bd-transbank"></span>Transbank</a>
      <a href="#sec-maquinas" onclick="showMachine('getnet')"><span class="brand-dot bd-getnet"></span>Getnet</a>
      <a href="#sec-maquinas" onclick="showMachine('mp')"><span class="brand-dot bd-mp"></span>Mercado Pago</a>
      <a href="#sec-maquinas" onclick="showMachine('tuu')"><span class="brand-dot bd-tuu"></span>TUU</a>
      {% if extras.n %}<a href="#sec-otros">Otros parámetros</a>{% endif %}
      {% if is_windows %}<a href="#sec-autostart">Inicio con Windows</a>{% endif %}
    </div>
  </nav>

  <main>

    <!-- Resumen -->
    <section class="summary" id="resumen">
      <div class="card tile">
        <div class="tile-label">Caja</div>
        <div class="tile-value">{{ nombre_caja }}</div>
        <div class="tile-sub">Sucursal {{ id_sucursal }} · {{ agents_count }} agente(s) registrado(s)</div>
      </div>

      <div class="card tile">
        <div class="tile-label">Servidor</div>
        <div class="tile-value">:{{ http_port }}</div>
        <div class="tile-sub"><a href="http://localhost:{{ http_port }}/status" target="_blank">localhost:{{ http_port }}/status ↗</a></div>
      </div>

      <div class="card tile tile-methods">
        <div class="tile-label">Medios de pago</div>
        <div class="methods">
          <div class="method">
            <span class="method-name"><span class="brand-dot bd-transbank"></span>Transbank</span>
            {% if usar_pos == 'true' %}<span class="badge badge-green">Activo</span>{% else %}<span class="badge">Sin POS físico</span>{% endif %}
            <span class="method-id" title="Terminal · puerto">{{ terminal_transbank }}{% if usar_pos == 'true' %} · {{ pos_port or 'puerto no detectado' }}{% endif %}</span>
          </div>
          <div class="method">
            <span class="method-name"><span class="brand-dot bd-getnet"></span>Getnet</span>
            {% if usar_getnet == 'true' %}<span class="badge badge-green">Activo</span>{% else %}<span class="badge">Sin Getnet</span>{% endif %}
            <span class="method-id">{{ terminal_getnet }}</span>
          </div>
          <div class="method">
            <span class="method-name"><span class="brand-dot bd-mp"></span>Mercado Pago</span>
            {% if not mp_on %}
              <span class="badge">Inactivo</span>
            {% elif mp_allowed == '' %}
              <span class="badge badge-green">Activo</span>
            {% elif mp_allowed %}
              <span class="badge badge-blue" title="Cajas: {{ mp_allowed }}">{{ mp_allowed.split(',') | length }} caja(s)</span>
            {% else %}
              <span class="badge badge-yellow">Inactivo</span>
            {% endif %}
            <span class="method-id">{{ env_vars.get('MP_TERMINAL_ID') or 'sin terminal configurado' }}</span>
          </div>
          <div class="method">
            <span class="method-name"><span class="brand-dot bd-tuu"></span>TUU</span>
            {% if tuu_on %}<span class="badge badge-green">Activo</span>{% else %}<span class="badge">Inactivo</span>{% endif %}
            <span class="method-id">{{ env_vars.get('TUU_DEVICES') or 'sin máquinas configuradas' }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- Transacciones sin confirmar -->
    <section class="card alert-card" id="pendientesCard" style="display:none;">
      <div class="card-head">
        <div>
          <h2><span class="dot" style="color:var(--yellow)"></span>Transacciones sin confirmar</h2>
          <p>Se enviaron al POS pero no llegó (o no se pudo confirmar) el resultado final. El cliente puede haber pagado igual:
             revise el comprobante o la pantalla de la máquina antes de resolver. Mientras queden aquí, la caja o máquina
             correspondiente queda bloqueada para nuevas ventas.</p>
        </div>
      </div>
      <div class="card-body">
        <div class="tx-list" id="pendientesBox"></div>
      </div>
    </section>

    <!-- Configuración editable -->
    <form id="configForm" autocomplete="off" style="display:flex; flex-direction:column; gap:20px;">

      <section class="card" id="sec-caja">
        <div class="card-head">
          <div>
            <h2>Caja y servidor</h2>
            <p>Identificación de esta caja, tiempos de espera y acceso a la API.</p>
          </div>
          <div class="head-right"><span class="env-path" title="Archivo de configuración en uso">{{ env_path }}</span></div>
        </div>
        <div class="card-body fields">
          {% if 'ID_SUCURSAL' in env_vars %}{{ field('ID_SUCURSAL', 'ID de sucursal', env_vars['ID_SUCURSAL'], hints['ID_SUCURSAL']) }}{% endif %}
          {% if 'NOMBRE_CAJA' in env_vars %}{{ field('NOMBRE_CAJA', 'Nombre de la caja', env_vars['NOMBRE_CAJA'], hints['NOMBRE_CAJA']) }}{% endif %}
          {% if 'HTTP_PORT' in env_vars %}{{ field('HTTP_PORT', 'Puerto HTTP', env_vars['HTTP_PORT'], hints['HTTP_PORT'], readonly=true) }}{% endif %}
          {{ field('BIND_HOST', 'Interfaz de escucha', env_vars.get('BIND_HOST', '127.0.0.1'), hints['BIND_HOST']) }}
          {% if 'MAX_TRANSACTION_TIME' in env_vars %}{{ field('MAX_TRANSACTION_TIME', 'Tiempo máximo por venta (s)', env_vars['MAX_TRANSACTION_TIME'], hints['MAX_TRANSACTION_TIME']) }}{% endif %}
          {% if 'TIMEOUT_SERVER' in env_vars %}{{ field('TIMEOUT_SERVER', 'Timeout del servidor (s)', env_vars['TIMEOUT_SERVER'], hints['TIMEOUT_SERVER']) }}{% endif %}
          {{ field('ALLOWED_ORIGINS', 'Orígenes web permitidos (CORS)', env_vars.get('ALLOWED_ORIGINS', ''), hints['ALLOWED_ORIGINS'],
                   placeholder='⚠️ vacío = cualquier página web puede llamar a la API', wide=true) }}
          {{ toggle('AGENTES_REMOTOS', 'Agentes remotos', env_vars.get('AGENTES_REMOTOS', 'false'), hints['AGENTES_REMOTOS']) }}
          {{ field('LOG_RETENCION_DIAS', 'Retención de logs (días)', env_vars.get('LOG_RETENCION_DIAS', '60'), hints['LOG_RETENCION_DIAS']) }}
        </div>
      </section>

      <section class="card" id="sec-maquinas">
        <div class="card-head">
          <div>
            <h2>Máquinas de pago</h2>
            <p>Cada máquina tiene sus propios identificadores y credenciales.</p>
          </div>
          <div class="head-right">
            <div class="tabs" role="tablist">
              <button type="button" class="tab" data-machine-tab="transbank" onclick="showMachine('transbank')"><span class="brand-dot bd-transbank"></span>Transbank<span class="tab-state{% if usar_pos == 'true' %} on{% endif %}"></span></button>
              <button type="button" class="tab" data-machine-tab="getnet" onclick="showMachine('getnet')"><span class="brand-dot bd-getnet"></span>Getnet<span class="tab-state{% if usar_getnet == 'true' %} on{% endif %}"></span></button>
              <button type="button" class="tab" data-machine-tab="mp" onclick="showMachine('mp')"><span class="brand-dot bd-mp"></span>Mercado Pago<span class="tab-state{% if mp_on %} on{% endif %}"></span></button>
              <button type="button" class="tab" data-machine-tab="tuu" onclick="showMachine('tuu')"><span class="brand-dot bd-tuu"></span>TUU<span class="tab-state{% if tuu_on %} on{% endif %}"></span></button>
            </div>
          </div>
        </div>
        <div class="card-body">
          <div class="machine-panel fields" id="sec-transbank" data-machine="transbank" hidden>
            <p class="machine-desc field-wide">POS integrado por puerto COM.</p>
            {% if 'USAR_POS_FISICO' in env_vars %}
              {{ toggle('USAR_POS_FISICO', 'POS Transbank conectado', env_vars['USAR_POS_FISICO'], hints['USAR_POS_FISICO'], id='usarPosInput', refresh=true) }}
            {% endif %}
            {{ field('TRANSBANK_TERMINAL_ID', 'ID de terminal', env_vars.get('TRANSBANK_TERMINAL_ID') or env_vars.get('TERMINAL_ID', ''),
                     hints['TRANSBANK_TERMINAL_ID'], placeholder='identificador de la máquina Transbank (ej. TBK_CAJA1)') }}
            {% if 'PUERTOS_COM' in env_vars %}
            <div class="field">
              <label class="field-label" for="puertosComInput"><span>Puertos COM</span><code>PUERTOS_COM</code></label>
              <div class="field-row">
                <input class="param-val" id="puertosComInput" name="PUERTOS_COM" value="{{ env_vars['PUERTOS_COM'] }}" title="{{ hints['PUERTOS_COM'] }}" autocomplete="off">
                <button type="button" id="btnDetectTransbank" class="btn btn-secondary" onclick="detectComPorts()">Detectar</button>
              </div>
              <p class="field-hint">{{ hints['PUERTOS_COM'] }}</p>
            </div>
            {% endif %}
            {{ field('TRANSBANK_MAX_ESPERA', 'Espera máxima sin confirmar (s)', env_vars.get('TRANSBANK_MAX_ESPERA', '300'), hints['TRANSBANK_MAX_ESPERA'],
                     placeholder='segundos (0 = sin límite)') }}
          </div>
          <div class="machine-panel fields" id="sec-getnet" data-machine="getnet" hidden>
            <p class="machine-desc field-wide">POS A920 Pro por cable USB (puerto COM).</p>
            {% if 'USAR_GETNET' in env_vars %}
              {{ toggle('USAR_GETNET', 'POS Getnet conectado', env_vars['USAR_GETNET'], hints['USAR_GETNET'], id='usarGetnetInput', refresh=true) }}
            {% endif %}
            {{ field('GETNET_TERMINAL_ID', 'ID de terminal', env_vars.get('GETNET_TERMINAL_ID') or env_vars.get('TERMINAL_ID', ''),
                     hints['GETNET_TERMINAL_ID'], placeholder='identificador de la máquina Getnet (ej. GETNET_CAJA1)') }}
            <div class="field">
              <label class="field-label" for="getnetPortInput"><span>Puerto COM</span><code>GETNET_PORT</code></label>
              <div class="field-row">
                <input class="param-val" id="getnetPortInput" name="GETNET_PORT" value="{{ env_vars.get('GETNET_PORT', '') }}" title="{{ hints['GETNET_PORT'] }}" autocomplete="off"
                       placeholder="vacío = detección automática">
                <button type="button" id="btnDetectGetnet" class="btn btn-secondary" onclick="detectGetnetPort()">Detectar</button>
              </div>
              <p class="field-hint">{{ hints['GETNET_PORT'] }}</p>
            </div>
            {{ field('GETNET_MAX_ESPERA', 'Espera máxima sin confirmar (s)', env_vars.get('GETNET_MAX_ESPERA', '300'), hints['GETNET_MAX_ESPERA'],
                     placeholder='segundos (0 = sin límite)') }}
          </div>
          <div class="machine-panel fields" id="sec-mp" data-machine="mp" hidden>
            <p class="machine-desc field-wide">Terminales Point por la API de Orders. El token y los terminales son solo de Mercado Pago.</p>
            {{ toggle('USAR_MP', 'Mercado Pago habilitado', env_vars.get('USAR_MP', 'true'), hints['USAR_MP']) }}
            {{ field('MP_TERMINAL_ID', 'Terminal(es) Point', env_vars.get('MP_TERMINAL_ID', ''), hints['MP_TERMINAL_ID'],
                     placeholder='ej: NEWLAND_N950__ABC123 (varios: separar por coma)') }}
            {{ secret('MP_ACCESS_TOKEN', 'Access token', mp_token_configured, hints['MP_ACCESS_TOKEN'], 'pegar APP_USR-...') }}
            {{ toggle('ALLOW_MP_TOKEN_IN_REQUEST', 'Aceptar token en la petición', env_vars.get('ALLOW_MP_TOKEN_IN_REQUEST', 'false'), hints['ALLOW_MP_TOKEN_IN_REQUEST']) }}
            {% set mp_cajas = env_vars.get('ALLOWED_MP', '') | trim %}
            <div class="field field-wide">
              <label class="field-label" for="f-ALLOWED_MP"><span>Cajas habilitadas</span><code>ALLOWED_MP</code></label>
              <label class="check"><input type="checkbox" id="mpTodasCajas" onchange="toggleMpCajas()"{% if not mp_cajas %} checked{% endif %}> Todas las cajas</label>
              <input class="param-val" id="f-ALLOWED_MP" name="ALLOWED_MP" value="{{ mp_cajas }}" autocomplete="off"
                     placeholder="ej: 70:caja_10,70:caja_11" title="{{ hints['ALLOWED_MP'] }}"{% if not mp_cajas %} hidden{% endif %}>
              <p class="field-hint">{{ hints['ALLOWED_MP'] }}</p>
            </div>
            {% if 'MP_API_URL' in env_vars %}
              {{ field('MP_API_URL', 'URL de la API', env_vars['MP_API_URL'], hints['MP_API_URL'], wide=true) }}
            {% endif %}
          </div>
          <div class="machine-panel fields" id="sec-tuu" data-machine="tuu" hidden>
            <p class="machine-desc field-wide">Máquinas PRO2/Kozen en Modo Integración. La API key y las series son solo de TUU.</p>
            {{ toggle('USAR_TUU', 'Máquinas TUU habilitadas', env_vars.get('USAR_TUU', 'false'), hints['USAR_TUU']) }}
            {{ field('TUU_DEVICES', 'Número(s) de serie', env_vars.get('TUU_DEVICES', ''), hints['TUU_DEVICES'],
                     placeholder='número de serie de la máquina (varias: separar por coma)') }}
            {{ secret('TUU_API_KEY', 'API key', tuu_key_configured, hints['TUU_API_KEY'], 'pegar la API key de TUU') }}
            {{ toggle('ALLOW_TUU_KEY_IN_REQUEST', 'Aceptar API key en la petición', env_vars.get('ALLOW_TUU_KEY_IN_REQUEST', 'false'), hints['ALLOW_TUU_KEY_IN_REQUEST']) }}
            {{ field('TUU_API_URL', 'URL de la API', env_vars.get('TUU_API_URL', 'https://integrations.payment.haulmer.com'), hints['TUU_API_URL'], wide=true) }}
          </div>
        </div>
      </section>

      {% if extras.n %}
      <section class="card" id="sec-otros">
        <div class="card-head">
          <div>
            <h2>Otros parámetros</h2>
            <p>Variables del .env que no tienen una sección propia.</p>
          </div>
        </div>
        <div class="card-body fields">
          {% for key, val in env_vars.items() %}
          {% if key not in known %}{{ field(key, key, val) }}{% endif %}
          {% endfor %}
        </div>
      </section>
      {% endif %}

      <div class="savebar">
        <span class="savebar-text">Los cambios se guardan en el .env y el servicio se reinicia para aplicarlos.</span>
        <a href="/status" target="_blank" class="btn btn-secondary">Ver status JSON</a>
        <a href="/panel" class="btn btn-secondary">Recargar</a>
        <button type="submit" class="btn btn-primary" id="btnGuardar">Guardar y reiniciar</button>
      </div>

    </form>

    {% if is_windows %}
    <section class="card" id="sec-autostart">
      <div class="card-head">
        <div>
          <h2>Inicio con Windows</h2>
          <p>{% if autostart_enabled %}El servicio se iniciará al iniciar sesión.{% else %}El servicio no se iniciará automáticamente.{% endif %}</p>
        </div>
        <div class="head-right">
          {% if autostart_enabled %}<span class="badge badge-green">Habilitado</span>{% else %}<span class="badge badge-yellow">Deshabilitado</span>{% endif %}
        </div>
      </div>
      <div class="card-body actions">
        <button class="btn btn-secondary" type="button" id="btnAutostartOn" onclick="setAutostart(true)">Habilitar</button>
        <button class="btn btn-secondary" type="button" id="btnAutostartOff" onclick="setAutostart(false)">Deshabilitar</button>
        <button class="btn btn-secondary" type="button" onclick="refreshAutostart()">Actualizar</button>
      </div>
    </section>
    {% endif %}

    <!-- Log reciente -->
    <section class="card" id="logs">
      <div class="card-head" style="flex-wrap:wrap;">
        <div>
          <h2>Log reciente</h2>
          <p>Últimas 100 líneas de hoy. Se actualiza cada 5 segundos.</p>
        </div>
        <div class="head-right">
          <button class="btn btn-secondary btn-sm" onclick="loadLog()">Actualizar</button>
        </div>
      </div>
      <div class="card-body" style="display:flex; flex-direction:column; gap:14px;">
        <div class="tabs">
          <button class="tab" id="tabLog-general" onclick="setLogTab('general')">Técnico</button>
          <button class="tab" id="tabLog-getnet" onclick="setLogTab('getnet')"><span class="brand-dot bd-getnet"></span>Getnet</button>
          <button class="tab" id="tabLog-transbank" onclick="setLogTab('transbank')"><span class="brand-dot bd-transbank"></span>Transbank</button>
          <button class="tab" id="tabLog-mercadopago" onclick="setLogTab('mercadopago')"><span class="brand-dot bd-mp"></span>Mercado Pago</button>
          <button class="tab" id="tabLog-tuu" onclick="setLogTab('tuu')"><span class="brand-dot bd-tuu"></span>TUU</button>
        </div>
        <div class="log-box" id="logBox">Cargando...</div>
      </div>
    </section>

  </main>
</div>

<footer>
  <span>{{ app_name }} · http://localhost:{{ http_port }}</span>
  <span>Log de hoy: <code>{{ log_path }}</code></span>
</footer>

<div id="toast"></div>

<script>
// ── Sesión: si venció, cualquier llamada al panel devuelve 401 → pantalla de clave ──
const _fetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const r = await _fetch(...args);
  if (r.status === 401 && String(args[0]).startsWith('/panel')) location.reload();
  return r;
};
async function cerrarSesion() {
  await _fetch('/panel/logout', { method: 'POST' }).catch(() => {});
  location.reload();
}

// ── Reloj ──
function updateClock() {
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString('es-CL');
}
updateClock();
setInterval(updateClock, 1000);

// ── Toast ──
function showToast(msg, ok) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'show ' + (ok ? 'ok' : 'err');
  setTimeout(() => t.className = '', 3500);
}

// ── Pestañas de máquinas ──
// Solo cambia lo que se ve: los campos de las pestañas ocultas siguen en el
// formulario y se guardan igual.
function showMachine(name) {
  if (!document.querySelector(`.machine-panel[data-machine="${name}"]`)) name = 'transbank';
  document.querySelectorAll('.machine-panel').forEach(p => { p.hidden = p.dataset.machine !== name; });
  document.querySelectorAll('[data-machine-tab]').forEach(t => t.classList.toggle('active', t.dataset.machineTab === name));
  try { localStorage.setItem('panel.maquina', name); } catch (_) {}
}
showMachine((() => { try { return localStorage.getItem('panel.maquina'); } catch (_) { return null; } })() || 'transbank');

// Si el error de guardado nombra un campo de otra pestaña, se abre esa pestaña.
function revealField(msg) {
  const field = [...document.querySelectorAll('#configForm [name]')]
    .sort((a, b) => b.name.length - a.name.length)
    .find(el => String(msg || '').includes(el.name));
  if (!field) return;
  const panel = field.closest('.machine-panel');
  if (panel) showMachine(panel.dataset.machine);
  field.focus();
}

// ── Credenciales configuradas: bloqueadas hasta presionar Cambiar ──
function editSecret(key, btn) {
  const input = document.getElementById('f-' + key);
  if (input.disabled) {
    input.disabled = false;
    input.placeholder = 'pegar el nuevo valor';
    btn.textContent = 'Cancelar';
    input.focus();
  } else {
    input.value = '';
    input.disabled = true;
    input.placeholder = '•••••••• configurado';
    btn.textContent = 'Cambiar';
  }
}

// ── Mercado Pago: todas las cajas o solo las escritas ──
function toggleMpCajas() {
  const todas = document.getElementById('mpTodasCajas').checked;
  const input = document.getElementById('f-ALLOWED_MP');
  input.hidden = todas;
  if (!todas) input.focus();
}

// ── Guardar config + reiniciar ──
document.getElementById('configForm').addEventListener('submit', async e => {
  e.preventDefault();
  const data = {};
  new FormData(e.target).forEach((v, k) => {
    if ((k === 'MP_ACCESS_TOKEN' || k === 'TUU_API_KEY') && !String(v || '').trim()) return;
    data[k] = v;
  });
  if (document.getElementById('mpTodasCajas')?.checked) {
    data.ALLOWED_MP = '';
  } else if (!String(data.ALLOWED_MP || '').trim()) {
    showToast('Escribí al menos una caja habilitada para Mercado Pago, o marcá "Todas las cajas"', false);
    showMachine('mp');
    document.getElementById('f-ALLOWED_MP').focus();
    return;
  }

  const btn = document.getElementById('btnGuardar');
  btn.disabled = true;
  btn.textContent = 'Guardando...';

  try {
    // 1. Guardar .env
    const r = await fetch('/panel/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data)
    });
    const j = await r.json();
    if (!j.ok) {
      showToast(j.error || 'Error al guardar', false);
      revealField(j.error);
      btn.disabled = false; btn.textContent = 'Guardar y reiniciar';
      return;
    }

    // 2. Solicitar reinicio (con una venta en curso el servicio no se reinicia)
    const rr = await fetch('/panel/restart', { method: 'POST' }).catch(() => null);
    const jr = rr ? await rr.json().catch(() => ({})) : {};
    if (rr && !jr.ok) {
      showToast('Configuración guardada, pero no se reinició: ' + (jr.error || 'error al reiniciar') +
                '. Presioná Guardar y reiniciar de nuevo cuando termine.', false);
      btn.disabled = false; btn.textContent = 'Guardar y reiniciar';
      return;
    }
    showToast('Configuración guardada. Reiniciando servicio...', true);
    btn.textContent = 'Reiniciando...';

    // 3. Esperar que el servicio vuelva (poll /status)
    let secs = 0;
    const interval = setInterval(async () => {
      secs++;
      btn.textContent = `Reiniciando... (${secs}s)`;
      // La instancia vieja sigue respondiendo ~2 s antes de cerrarse.
      if (secs < 4) return;
      try {
        const ping = await fetch('/status', { cache: 'no-store' });
        if (ping.ok) {
          clearInterval(interval);
          showToast('Servicio reiniciado correctamente', true);
          setTimeout(() => location.reload(), 1200);
        }
      } catch(_) { /* aún reiniciando */ }
      if (secs > 30) {
        clearInterval(interval);
        btn.disabled = false; btn.textContent = 'Guardar y reiniciar';
        showToast('El servicio tardó más de lo esperado. Recarga manualmente.', false);
      }
    }, 1000);

  } catch(err) {
    showToast('Error de conexión: ' + err, false);
    btn.disabled = false; btn.textContent = 'Guardar y reiniciar';
  }
});

// ── Transacciones sin confirmar ──
function fmtFecha(ts) {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleString('es-CL');
}

function fmtMonto(v) {
  const n = Number(v);
  return Number.isFinite(n) ? '$' + n.toLocaleString('es-CL') : '$' + (v ?? '-');
}

// Todo dato que viene del servidor se escapa antes de insertarlo como HTML:
// notas, tickets y nombres de caja pueden venir de clientes de la API.
function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

const NOMBRE_TIPO = { transbank: 'Transbank', getnet: 'Getnet', mercadopago: 'Mercado Pago', tuu: 'TUU' };
const DOT_TIPO = { transbank: 'bd-transbank', getnet: 'bd-getnet', mercadopago: 'bd-mp', tuu: 'bd-tuu' };

async function loadPendientes() {
  try {
    const r = await fetch('/panel/pendientes', { cache: 'no-store' });
    const j = await r.json();
    const card = document.getElementById('pendientesCard');
    const box = document.getElementById('pendientesBox');
    const nav = document.getElementById('navPendientes');
    const items = (j.pendientes || []);

    if (!items.length) {
      card.style.display = 'none';
      nav.style.display = 'none';
      return;
    }
    card.style.display = 'block';
    nav.style.display = 'flex';
    document.getElementById('navPendientesCount').textContent = items.length;

    box.innerHTML = items.map(tx => `
      <div class="tx-item">
        <div class="tx-head">
          <span class="brand-dot ${DOT_TIPO[tx.tipo] || ''}"></span>
          <span class="tx-title">${esc(NOMBRE_TIPO[tx.tipo] || tx.tipo)}</span>
          <span class="tx-amount">${esc(fmtMonto(tx.monto))}</span>
          <span class="badge ${tx.estado === 'INDETERMINADA' ? 'badge-yellow' : 'badge-blue'}">${esc(tx.estado)}</span>
        </div>
        <div class="tx-meta">
          <span>Transacción <b>${esc(tx.tx_id)}</b></span>
          <span>Ticket <b>${esc(tx.ticket ?? '-')}</b></span>
          <span>Caja <b>${esc(tx.id_sucursal)}/${esc(tx.nombre_caja)}</b></span>
          <span>Creada <b>${esc(fmtFecha(tx.created_at))}</b></span>
        </div>
        ${tx.nota ? `<div class="tx-note">${esc(tx.nota)}</div>` : ''}
        ${renderDetalleVentas(tx.tx_id)}
        <div class="actions">
          <button class="btn btn-secondary btn-sm" data-tx="${esc(tx.tx_id)}" onclick="reconciliarTx(this.dataset.tx)">Reconsultar POS</button>
          ${tx.tipo === 'getnet' ? `<button class="btn btn-secondary btn-sm" data-tx="${esc(tx.tx_id)}" onclick="detalleVentasTx(this)">Ver detalle de ventas del POS</button>` : ''}
          <button class="btn btn-secondary btn-sm btn-ok" data-tx="${esc(tx.tx_id)}" onclick="resolverTx(this.dataset.tx, 'APROBADO')">Marcar aprobada</button>
          <button class="btn btn-secondary btn-sm btn-bad" data-tx="${esc(tx.tx_id)}" onclick="resolverTx(this.dataset.tx, 'RECHAZADO')">Marcar rechazada</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    // silencioso: no interrumpir el resto del panel por esto
  }
}
loadPendientes();
setInterval(loadPendientes, 8000);

async function reconciliarTx(txId) {
  try {
    const r = await fetch(`/panel/pendientes/${encodeURIComponent(txId)}/reconciliar`, { method: 'POST' });
    const j = await r.json();
    if (!j.ok) {
      showToast(j.error || 'No se pudo reconciliar', false);
      return;
    }
    showToast(`Resultado: ${j.estado}`, j.estado !== 'INDETERMINADA');
    loadPendientes();
  } catch (e) {
    showToast('Error reconciliando', false);
  }
}

async function resolverTx(txId, estado) {
  const nota = prompt(`Confirmá mirando el comprobante del POS. ¿Marcar esta transacción como ${estado}?\\nNota (opcional):`, '');
  if (nota === null) return;
  try {
    const r = await fetch(`/panel/pendientes/${encodeURIComponent(txId)}/resolver`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ estado, nota })
    });
    const j = await r.json();
    if (!j.ok) {
      showToast(j.error || 'No se pudo resolver', false);
      return;
    }
    showToast(`Marcada como ${estado}`, true);
    loadPendientes();
  } catch (e) {
    showToast('Error resolviendo', false);
  }
}

// Detalle de ventas del POS Getnet (Command 105), por tx_id. Se guarda aquí para
// que el refresco periódico de la lista no borre la tabla.
const detallesVentas = {};

function renderDetalleVentas(txId) {
  const d = detallesVentas[txId];
  if (!d) return '';
  if (!d.ventas.length) {
    return `<div class="tx-note">El POS no tiene ventas en memoria (lista vacía o ya se hizo el cierre del día).
      Si la venta pendiente no aparece en el reporte del POS, no se cobró.</div>`;
  }
  const filas = d.ventas.map(v => `
    <tr class="${v.candidato ? 'dv-cand' : ''}">
      <td>${esc(v.fecha || '-')}</td>
      <td>${esc(fmtMonto(v.monto))}</td>
      <td>${esc(v.autorizacion || '-')}</td>
      <td>${esc([v.marca, v.tipo_tarjeta, v.last4].filter(Boolean).join(' ') || '-')}</td>
      <td>${v.origen === 'caja' ? 'Caja' : (v.origen === 'manual' ? 'Manual en POS' : '-')}</td>
      <td>${esc(v.mensaje || (v.response_code === 0 ? 'Aprobado' : v.response_code) || '-')}</td>
      <td>${v.registrada_ticket ? `Sí (ticket ${esc(v.registrada_ticket)})` : 'No'}</td>
      <td>${v.candidato && v.autorizacion
        ? `<button class="btn btn-secondary btn-sm btn-ok" data-tx="${esc(txId)}" data-auth="${esc(v.autorizacion)}"
             onclick="aprobarConComprobante(this.dataset.tx, this.dataset.auth)">Aprobar con este</button>`
        : ''}</td>
    </tr>`).join('');
  const resumen = d.candidatos === 1
    ? 'Hay 1 venta que coincide con la pendiente (resaltada). Verifique y apruébela con ese comprobante.'
    : d.candidatos > 1
      ? `Hay ${d.candidatos} ventas que podrían ser la pendiente: revise el comprobante impreso antes de elegir.`
      : 'Ninguna venta del POS coincide con la pendiente (monto, hora y origen caja): todo indica que no se cobró.';
  return `<div class="tx-note">${resumen}</div>
    <div class="dv-wrap"><table class="dv-table">
      <tr><th>Fecha POS</th><th>Monto</th><th>Autorización</th><th>Tarjeta</th><th>Origen</th><th>Estado</th><th>En el sistema</th><th></th></tr>
      ${filas}
    </table></div>`;
}

async function detalleVentasTx(btn) {
  const txId = btn.dataset.tx;
  btn.disabled = true;
  btn.textContent = 'Consultando POS...';
  try {
    const r = await fetch(`/panel/pendientes/${encodeURIComponent(txId)}/detalle_ventas`, { method: 'POST' });
    const j = await r.json();
    if (!j.ok) {
      showToast(j.error || 'No se pudo obtener el detalle de ventas', false);
      return;
    }
    detallesVentas[txId] = j;
  } catch (e) {
    showToast('Error consultando el detalle de ventas', false);
  } finally {
    loadPendientes();
  }
}

async function aprobarConComprobante(txId, autorizacion) {
  const nota = prompt(`¿Aprobar la venta pendiente con el comprobante del POS de autorización ${autorizacion}?\\nNota (opcional):`, '');
  if (nota === null) return;
  try {
    const r = await fetch(`/panel/pendientes/${encodeURIComponent(txId)}/resolver`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ estado: 'APROBADO', autorizacion, nota })
    });
    const j = await r.json();
    if (!j.ok) {
      showToast(j.error || 'No se pudo aprobar', false);
      return;
    }
    delete detallesVentas[txId];
    showToast('Aprobada con el comprobante del POS', true);
    loadPendientes();
  } catch (e) {
    showToast('Error aprobando', false);
  }
}

// ── Log ──
let currentLogTab = 'general';

function setLogTab(tipo) {
  currentLogTab = tipo;
  for (const t of ['general', 'getnet', 'transbank', 'mercadopago', 'tuu']) {
    const btn = document.getElementById('tabLog-' + t);
    if (btn) btn.classList.toggle('active', t === tipo);
  }
  loadLog();
}

async function loadLog() {
  try {
    const r = await fetch('/panel/log?tipo=' + encodeURIComponent(currentLogTab));
    const txt = await r.text();
    const box = document.getElementById('logBox');
    box.textContent = txt || '(sin entradas)';
    box.scrollTop = box.scrollHeight;
  } catch(e) {
    document.getElementById('logBox').textContent = 'No se pudo cargar el log.';
  }
}
setLogTab('general');
setInterval(loadLog, 5000);

async function refreshAutostart() {
  try {
    const r = await fetch('/panel/autostart', { cache: 'no-store' });
    const j = await r.json();
    if (!j.ok) {
      showToast(j.error || 'No se pudo leer autostart', false);
      return;
    }
    showToast(j.enabled ? 'Inicio con Windows: habilitado' : 'Inicio con Windows: deshabilitado', true);
    setTimeout(() => location.reload(), 600);
  } catch (e) {
    showToast('Error consultando autostart', false);
  }
}

async function setAutostart(enabled) {
  try {
    const r = await fetch('/panel/autostart', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ enabled })
    });
    const j = await r.json();
    if (!j.ok) {
      showToast(j.error || 'No se pudo configurar autostart', false);
      return;
    }
    showToast(enabled ? 'Inicio con Windows habilitado' : 'Inicio con Windows deshabilitado', true);
    setTimeout(() => location.reload(), 600);
  } catch (e) {
    showToast('Error configurando autostart', false);
  }
}

// Cada botón "Detectar" solo se habilita si su máquina está en true, y la
// detección prueba el protocolo de esa máquina (no lista todos los puertos).
const DETECT_BUTTONS = [
  { btn: 'btnDetectTransbank', flag: 'usarPosInput', serverFlag: {{ usar_pos | tojson }}, name: 'Transbank', key: 'USAR_POS_FISICO' },
  { btn: 'btnDetectGetnet', flag: 'usarGetnetInput', serverFlag: {{ usar_getnet | tojson }}, name: 'Getnet', key: 'USAR_GETNET' },
];
let detecting = false;

function isFlagOn(inputId, serverFlag) {
  const input = document.getElementById(inputId);
  const value = input ? input.value : serverFlag;
  return String(value || '').trim().toLowerCase() === 'true';
}

function refreshDetectButtons() {
  for (const d of DETECT_BUTTONS) {
    const b = document.getElementById(d.btn);
    if (!b) continue;
    const on = isFlagOn(d.flag, d.serverFlag);
    b.disabled = detecting || !on;
    b.title = on ? `Buscar el puerto del POS ${d.name}` : `${d.key} está en false: no se detectan puertos de ${d.name}`;
  }
}
refreshDetectButtons();

function setDetecting(value) {
  detecting = value;
  refreshDetectButtons();
}

async function detectComPorts() {
  if (!isFlagOn('usarPosInput', {{ usar_pos | tojson }})) {
    showToast('USAR_POS_FISICO está en false: no se detectan puertos de Transbank', false);
    return;
  }
  setDetecting(true);
  showToast('Buscando POS Transbank en los puertos...', true);
  try {
    const r = await fetch('/panel/com_ports', { cache: 'no-store' });
    const j = await r.json();
    if (!j.ok) {
      showToast(j.error || 'No se detectó ningún POS Transbank conectado', false);
      return;
    }
    const ports = (j.ports || []).filter(Boolean);
    const input = document.getElementById('puertosComInput');
    if (!input) return;
    if (!ports.length) {
      showToast('No se detectó ningún POS Transbank conectado', false);
      return;
    }
    input.value = ports.join(',');
    showToast(`Puerto Transbank detectado: ${ports.join(', ')}`, true);
  } catch (e) {
    showToast('Error detectando puerto Transbank', false);
  } finally {
    setDetecting(false);
  }
}

async function detectGetnetPort() {
  if (!isFlagOn('usarGetnetInput', {{ usar_getnet | tojson }})) {
    showToast('USAR_GETNET está en false: no se detectan puertos de Getnet', false);
    return;
  }
  setDetecting(true);
  try {
    // A diferencia de /panel/com_ports (que solo lista puertos del sistema),
    // esto prueba cada puerto con el protocolo real de Getnet y devuelve el
    // que efectivamente respondió como POS Getnet.
    const r = await fetch('/panel/getnet_port', { cache: 'no-store' });
    const j = await r.json();
    const input = document.getElementById('getnetPortInput');
    if (!j.ok || !j.port) {
      showToast(j.error || 'No se detectó ningún POS Getnet conectado', false);
      return;
    }
    if (!input) return;
    input.value = j.port;
    showToast(`Puerto Getnet detectado: ${j.port}`, true);
  } catch (e) {
    showToast('Error detectando puerto Getnet', false);
  } finally {
    setDetecting(false);
  }
}
</script>
</body>
</html>
"""


LOGIN_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ app_name }} — Ingresar</title>
<style>
  :root {
    --navy-0:#07122e; --navy-1:#0b1c45; --navy-2:#10275e; --navy-3:#1a3a80;
    --orange:#f7941d; --orange-2:#ef6c0f; --green:#7cbf2a; --green-2:#4e9a1b;
    --ink:#0f1f45; --muted:#5b6785; --line:#d6dceb; --blue:#2f6fde; --red:#d93636;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; min-height: 100vh; overflow-x: hidden; color: #e8eefc;
    font: 14px/1.5 "Segoe UI", system-ui, -apple-system, Roboto, sans-serif;
    background:
      radial-gradient(1200px 700px at 78% 40%, rgba(47,111,222,.35), transparent 60%),
      radial-gradient(900px 600px at 10% 90%, rgba(26,58,128,.55), transparent 60%),
      linear-gradient(135deg, var(--navy-0) 0%, var(--navy-1) 45%, var(--navy-2) 100%);
  }

  /* ── Decoración de fondo (código) ── */
  .deco { position: fixed; inset: 0; pointer-events: none; overflow: hidden; }
  .glyph { position: absolute; font: 700 72px/1 Consolas, "Courier New", monospace; color: rgba(120,150,220,.12); }
  .glyph.g1 { top: 40px; left: 33%; }
  .glyph.g2 { top: 40px; right: 56px; font-size: 64px; color: rgba(140,160,210,.28); }
  .win { position: absolute; border-radius: 16px; background: rgba(9,20,52,.72);
         border: 1px solid rgba(110,140,220,.14); box-shadow: 0 30px 80px rgba(0,0,0,.35); }
  .win.w1 { top: 12%; left: 40%; width: 50%; height: 64%; }
  .win.w2 { bottom: 5%; left: 29%; width: 17%; height: 32%; }
  .win.w3 { bottom: 5%; right: 7%; width: 30%; height: 25%; }
  .dots { position: absolute; top: 14px; left: 18px; display: flex; gap: 8px; }
  .dots i { width: 11px; height: 11px; border-radius: 50%; display: block; }
  .dots i:nth-child(1){ background:#e2554f; } .dots i:nth-child(2){ background:#e5a33a; } .dots i:nth-child(3){ background:#4fbf5f; }
  .code { position: absolute; top: 60px; left: 6%; right: 6%; display: grid; gap: 15px; }
  .ln { display: flex; gap: 10px; align-items: center; }
  .ln b { height: 8px; border-radius: 5px; display: block; opacity: .75; }
  .c-p { background:#7b5bd6; } .c-b { background:#3b7fd9; } .c-y { background:#b8955a; }
  .c-t { background:#3aa7b0; } .c-g { background:#5ea95a; } .c-s { background:#56637f; }

  /* ── Layout ── */
  .wrap { position: relative; z-index: 1; min-height: 100vh; display: grid;
          grid-template-columns: minmax(0, 1fr) 400px; align-items: center;
          gap: 48px; max-width: 1240px; margin: 0 auto; padding: 48px 32px; }

  /* ── Lado izquierdo ── */
  .logo { display: inline-flex; align-items: center; gap: 12px; background: #fff; border-radius: 12px;
          padding: 16px 22px; box-shadow: 0 10px 30px rgba(0,0,0,.25); }
  .logo .txt { line-height: 1; }
  .logo .sup { font-size: 8.5px; font-weight: 700; letter-spacing: .06em; color: var(--ink); text-transform: uppercase; }
  .logo .name { font-size: 25px; font-weight: 900; letter-spacing: -.02em; color: var(--ink); margin: 2px 0 4px; }
  .logo .name span { color: var(--green-2); }
  .logo .tag { font-size: 9.5px; font-style: italic; font-weight: 600; color: var(--green-2); }
  .logo .tag em { color: var(--orange); font-style: normal; }
  h2 { font-size: 34px; line-height: 1.18; margin: 40px 0 12px; font-weight: 800; letter-spacing: -.01em; }
  h2 span { color: var(--orange); }
  .lead { color: #c4cee6; font-size: 15px; max-width: 440px; margin: 0 0 22px; }
  .feats { display: grid; gap: 10px; max-width: 380px; }
  .feat { display: flex; align-items: center; gap: 14px; padding: 11px 14px; border-radius: 10px;
          background: rgba(20,38,88,.62); border: 1px solid rgba(120,150,220,.2); }
  .feat .ic { width: 38px; height: 38px; flex: none; border-radius: 9px; display: grid; place-items: center;
              background: rgba(47,111,222,.25); color: #8fb4ff; }
  .feat .ic.o { background: rgba(247,148,29,.18); color: var(--orange); }
  .feat .ic.g { background: rgba(124,191,42,.18); color: var(--green); }
  .feat strong { display: block; font-size: 13.5px; color: #fff; }
  .feat small { display: block; font-size: 12px; color: #b3bfdc; }
  .firma { margin-top: 26px; font: italic 600 22px/1.25 "Segoe Script", "Brush Script MT", "Comic Sans MS", cursive; color: #fff; }
  .firma::after { content: ""; display: block; width: 170px; height: 3px; margin-top: 4px; border-radius: 3px;
                  background: linear-gradient(90deg, var(--orange), transparent); }

  /* ── Tarjeta de ingreso ── */
  .card { background: linear-gradient(180deg, #f7f9fd, #e9eef7); color: var(--ink); border-radius: 18px;
          padding: 34px 32px 26px; box-shadow: 0 30px 80px rgba(3,10,30,.55); }
  .card .brand { text-align: center; }
  .card .brand h1 { margin: 0; font-size: 28px; font-weight: 900; letter-spacing: .03em; color: var(--ink); }
  .card .brand .sub { color: var(--blue); font-size: 15px; font-weight: 500; }
  .card .brand .bar { width: 58px; height: 3px; border-radius: 3px; background: var(--orange); margin: 6px auto 0; }
  .card h3 { text-align: center; font-size: 26px; margin: 26px 0 2px; font-weight: 800; }
  .card .who { text-align: center; color: var(--muted); font-size: 13.5px; margin: 0 0 22px; }
  .card .who b { color: var(--ink); }
  label.lbl { display: block; font-size: 12.5px; font-weight: 700; color: var(--muted); margin: 0 0 6px 2px; }
  .field { position: relative; }
  .field .ico { position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: #7d89a6; display: flex; }
  .field input { width: 100%; height: 48px; padding: 0 46px 0 42px; border-radius: 8px; border: 1px solid var(--line);
                 background: #fff; color: var(--ink); font: 14px "Segoe UI", system-ui, sans-serif; letter-spacing: .02em; }
  .field input::placeholder { color: #9aa4bc; letter-spacing: 0; }
  .field input:focus { outline: none; border: 2px solid var(--navy-3); padding-left: 41px; box-shadow: 0 0 0 4px rgba(47,111,222,.12); }
  .eye { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); width: 34px; height: 34px; border: 0;
         background: transparent; color: #55617f; cursor: pointer; border-radius: 6px; display: grid; place-items: center; }
  .eye:hover { background: #eef2f9; }
  .hint { font-size: 12px; color: var(--muted); margin: 8px 2px 0; }
  .first { background: #fff4e6; border: 1px solid #f7c58a; color: #7a4a10; border-radius: 8px;
           padding: 10px 12px; font-size: 12.5px; margin: 0 0 16px; }
  .first strong { display: block; color: #b45f06; font-size: 13px; margin-bottom: 2px; }
  .btn { width: 100%; height: 48px; margin-top: 18px; border: 0; border-radius: 8px; cursor: pointer;
         display: flex; align-items: center; justify-content: center; gap: 10px;
         background: linear-gradient(180deg, var(--orange), var(--orange-2)); color: #fff;
         font: 600 15px "Segoe UI", system-ui, sans-serif; box-shadow: 0 10px 22px rgba(239,108,15,.32); }
  .btn:hover:not(:disabled) { filter: brightness(1.05); }
  .btn:disabled { opacity: .7; cursor: default; }
  .err { color: var(--red); font-size: 13px; min-height: 20px; margin-top: 10px; text-align: center; }
  .secure { display: flex; gap: 12px; align-items: flex-start; margin-top: 14px; padding-top: 16px;
            border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }
  .secure svg { flex: none; color: var(--ink); margin-top: 1px; }

  @media (max-width: 920px) {
    .wrap { grid-template-columns: 1fr; max-width: 460px; padding: 24px 16px; gap: 24px; }
    .left .feats, .left .lead, .left .firma, .left h2, .deco { display: none; }
    .left { text-align: center; }
  }
</style>
</head>
<body>
<div class="deco" aria-hidden="true">
  <div class="glyph g1">{ }</div>
  <div class="glyph g2">&lt;/&gt;</div>
  <div class="win w1">
    <div class="dots"><i></i><i></i><i></i></div>
    <div class="code">
      <div class="ln"><b class="c-s" style="width:6%"></b><b class="c-p" style="width:9%"></b><b class="c-b" style="width:20%"></b><b class="c-y" style="width:13%"></b><b class="c-t" style="width:5%"></b></div>
      <div class="ln" style="padding-left:4%"><b class="c-p" style="width:9%"></b><b class="c-y" style="width:11%"></b><b class="c-b" style="width:18%"></b></div>
      <div class="ln" style="padding-left:4%"><b class="c-s" style="width:46%"></b></div>
      <div class="ln" style="padding-left:8%"><b class="c-p" style="width:9%"></b><b class="c-b" style="width:16%"></b><b class="c-y" style="width:11%"></b><b class="c-t" style="width:4%"></b></div>
      <div class="ln" style="padding-left:4%"><b class="c-y" style="width:13%"></b><b class="c-g" style="width:25%"></b></div>
      <div class="ln"><b class="c-p" style="width:9%"></b><b class="c-b" style="width:16%"></b><b class="c-y" style="width:11%"></b><b class="c-t" style="width:4%"></b></div>
      <div class="ln" style="padding-left:8%"><b class="c-p" style="width:11%"></b><b class="c-s" style="width:18%"></b><b class="c-y" style="width:6%"></b></div>
      <div class="ln" style="padding-left:8%"><b class="c-s" style="width:24%"></b><b class="c-s" style="width:11%"></b></div>
      <div class="ln" style="padding-left:4%"><b class="c-p" style="width:9%"></b><b class="c-b" style="width:18%"></b><b class="c-y" style="width:13%"></b><b class="c-t" style="width:5%"></b></div>
      <div class="ln" style="padding-left:8%"><b class="c-s" style="width:38%"></b></div>
      <div class="ln"><b class="c-s" style="width:36%"></b></div>
      <div class="ln" style="padding-left:4%"><b class="c-y" style="width:13%"></b><b class="c-g" style="width:25%"></b></div>
    </div>
  </div>
  <div class="win w2"></div>
  <div class="win w3">
    <div class="dots"><i></i><i></i><i></i></div>
    <div class="code">
      <div class="ln"><b class="c-g" style="width:7%"></b><b class="c-s" style="width:55%"></b></div>
      <div class="ln"><b class="c-s" style="width:70%"></b></div>
      <div class="ln"><b class="c-g" style="width:7%"></b><b class="c-s" style="width:40%"></b><b class="c-y" style="width:26%"></b></div>
      <div class="ln"><b class="c-y" style="width:12%"></b><b class="c-s" style="width:66%"></b></div>
    </div>
  </div>
</div>

<main class="wrap">
  <section class="left">
    <div class="logo" aria-label="Soluciones Informáticas Datamaule SpA">
      <svg width="44" height="34" viewBox="0 0 44 34" aria-hidden="true">
        <g fill="#7cbf2a">
          <rect x="0" y="13" width="5" height="5" rx="1" opacity=".55"/><rect x="7" y="6" width="6" height="6" rx="1" opacity=".75"/>
          <rect x="7" y="14" width="6" height="6" rx="1"/><rect x="7" y="22" width="6" height="6" rx="1" opacity=".75"/>
          <rect x="15" y="2" width="7" height="7" rx="1"/><rect x="15" y="11" width="7" height="7" rx="1" fill="#4e9a1b"/>
          <rect x="15" y="20" width="7" height="7" rx="1"/><rect x="24" y="6" width="8" height="8" rx="1" fill="#4e9a1b"/>
          <rect x="24" y="16" width="8" height="8" rx="1"/><rect x="34" y="11" width="9" height="9" rx="1" fill="#4e9a1b"/>
        </g>
      </svg>
      <div class="txt">
        <div class="sup">Soluciones informáticas</div>
        <div class="name">DATA<span>MAULE</span></div>
        <div class="tag">Innovación <em>•</em> Tecnología <em>•</em> Soporte</div>
      </div>
    </div>

    <h2>Pagos electrónicos<br><span>integrados a tu caja</span></h2>
    <p class="lead">{{ app_name }} conecta tu sistema de ventas con las máquinas de pago, de forma segura y sin intervención manual.</p>

    <div class="feats">
      <div class="feat"><div class="ic o">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20M6 15h4"/></svg>
      </div><div><strong>Transbank y Getnet</strong><small>Ventas con tarjeta desde el POS físico</small></div></div>
      <div class="feat"><div class="ic">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2" width="12" height="20" rx="2"/><path d="M11 18h2"/></svg>
      </div><div><strong>Mercado Pago y TUU</strong><small>Cobro remoto en terminales Point y PRO2</small></div></div>
      <div class="feat"><div class="ic g">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/></svg>
      </div><div><strong>Reconciliación automática</strong><small>Ninguna venta queda sin confirmar</small></div></div>
      <div class="feat"><div class="ic">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 15v2M11 11v6M15 8v9M19 12v5"/></svg>
      </div><div><strong>Soporte y mejora continua</strong><small>Te acompañamos en cada etapa</small></div></div>
    </div>

    <div class="firma">Tecnología que<br>construye oportunidades</div>
  </section>

  <form class="card" id="loginForm">
    <div class="brand">
      <h1>DATAMAULE</h1>
      <div class="sub">{{ app_name }}</div>
      <div class="bar"></div>
    </div>
    <h3>Bienvenido</h3>
    <p class="who">Panel de configuración{% if nombre_caja %} · Caja <b>{{ nombre_caja }}</b>{% endif %}</p>

    {% if primera_vez %}
    <div class="first">
      <strong>Primera configuración</strong>
      Esta caja aún no tiene sucursal asignada. Ingrese con la clave de soporte y configure el
      <b>ID de sucursal</b>. Después, el panel se abrirá solo con la ApiKey del cliente.
    </div>
    {% endif %}
    <label class="lbl" for="password">{% if primera_vez %}Clave de soporte{% else %}ApiKey del cliente{% endif %}</label>
    <div class="field">
      <span class="ico" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="15" r="4"/><path d="M10.8 12.2 20 3M17 6l3 3M14.5 8.5l2 2"/></svg>
      </span>
      <input type="password" id="password" placeholder="{% if primera_vez %}Ingrese la clave de soporte{% else %}Ingrese la ApiKey{% endif %}"
             autocomplete="off" spellcheck="false" autofocus required>
      <button type="button" class="eye" id="eye" title="Mostrar / ocultar">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>
      </button>
    </div>
    {% if not primera_vez %}
    <p class="hint">La ApiKey de la conexión «API PAGO ELECTRONICO» de la sucursal {{ id_sucursal }} en la intranet.</p>
    {% endif %}

    <button type="submit" class="btn" id="btn">
      <span id="btnTxt">Ingresar</span>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
    </button>
    <div class="err" id="err"></div>

    <div class="secure">
      <svg width="18" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5z"/></svg>
      <span>{% if primera_vez %}La clave de soporte solo funciona mientras la caja no tenga sucursal.{% else %}Acceso seguro: la ApiKey se valida con la intranet Datamaule.{% endif %} Solo disponible desde este equipo.</span>
    </div>
  </form>
</main>

<script>
const soporte = {{ 'true' if primera_vez else 'false' }};
const inp = document.getElementById('password');
document.getElementById('eye').addEventListener('click', () => {
  inp.type = inp.type === 'password' ? 'text' : 'password';
  inp.focus();
});
document.getElementById('loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = document.getElementById('btn'), txt = document.getElementById('btnTxt'), err = document.getElementById('err');
  const valor = inp.value;
  btn.disabled = true; err.textContent = '';
  txt.textContent = soporte ? 'Ingresando…' : 'Validando con la intranet…';
  try {
    const r = await fetch('/panel/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(soporte ? { password: valor } : { apikey: valor.trim() })
    });
    const j = await r.json().catch(() => ({}));
    if (j.ok) { location.reload(); return; }
    err.textContent = j.error || 'No se pudo ingresar';
    inp.select();
  } catch (_) {
    err.textContent = 'Sin conexión con el servicio';
  }
  btn.disabled = false; txt.textContent = 'Ingresar';
});
</script>
</body>
</html>
"""
