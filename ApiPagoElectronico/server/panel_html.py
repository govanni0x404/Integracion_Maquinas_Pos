"""HTML del panel de control — servido en /panel por Flask."""

PANEL_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ app_name }} — Panel de Control</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

  :root {
    --bg:       #0a0f1e;
    --surface:  #111827;
    --card:     #1a2235;
    --border:   #2a3650;
    --accent:   #3b82f6;
    --accent2:  #6366f1;
    --green:    #10b981;
    --yellow:   #f59e0b;
    --red:      #ef4444;
    --text:     #e2e8f0;
    --muted:    #64748b;
    --radius:   14px;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: 'Inter', system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 24px;
  }

  /* ── Header ── */
  .header {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 32px;
  }
  .header-icon {
    width: 52px; height: 52px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    border-radius: 14px;
    display: flex; align-items: center; justify-content: center;
    font-size: 26px;
    box-shadow: 0 8px 24px rgba(59,130,246,0.35);
  }
  .header h1 { font-size: 22px; font-weight: 700; }
  .header p  { font-size: 13px; color: var(--muted); margin-top: 2px; }

  /* ── Badge de estado ── */
  .badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 12px; border-radius: 999px;
    font-size: 12px; font-weight: 600;
    letter-spacing: 0.02em;
  }
  .badge-green { background: rgba(16,185,129,0.15); color: var(--green); border: 1px solid rgba(16,185,129,0.3); }
  .badge-yellow{ background: rgba(245,158,11,0.15);  color: var(--yellow); border: 1px solid rgba(245,158,11,0.3); }
  .badge-red   { background: rgba(239,68,68,0.15);   color: var(--red);    border: 1px solid rgba(239,68,68,0.3); }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor;
         animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

  /* ── Grid principal ── */
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-bottom: 24px; }

  /* ── Cards ── */
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px;
    transition: border-color .2s;
  }
  .card:hover { border-color: rgba(99,102,241,0.4); }

  .card-title {
    font-size: 11px; font-weight: 600; letter-spacing: .08em;
    text-transform: uppercase; color: var(--muted);
    margin-bottom: 14px;
    display: flex; align-items: center; gap: 8px;
  }
  .card-title span { font-size: 16px; }

  .stat { font-size: 28px; font-weight: 700; color: var(--text); }
  .stat-label { font-size: 12px; color: var(--muted); margin-top: 4px; }

  /* ── Tabla de parámetros ── */
  .param-row {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid var(--border);
  }
  .param-row:last-child { border-bottom: none; }
  .param-key {
    font-size: 12px; font-weight: 500; color: var(--muted);
    min-width: 160px; font-family: 'SF Mono', monospace;
  }
  .param-val {
    font-size: 13px; color: var(--text); flex: 1;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 6px 10px;
    transition: border-color .2s;
  }
  .param-val:focus { outline: none; border-color: var(--accent); }

  /* ── Botones ── */
  .btn {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 10px 20px; border-radius: 10px;
    font-size: 14px; font-weight: 600;
    cursor: pointer; border: none; transition: all .2s;
  }
  .btn-primary {
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    color: white;
    box-shadow: 0 4px 14px rgba(99,102,241,0.35);
  }
  .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 6px 20px rgba(99,102,241,0.45); }
  .btn-secondary {
    background: var(--surface); color: var(--text);
    border: 1px solid var(--border);
  }
  .btn-secondary:hover { border-color: var(--accent); }

  /* ── Toast ── */
  #toast {
    position: fixed; bottom: 24px; right: 24px;
    background: var(--card); border: 1px solid var(--border);
    border-radius: 12px; padding: 14px 20px;
    font-size: 14px; font-weight: 500;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    transform: translateY(80px); opacity: 0;
    transition: all .3s cubic-bezier(.34,1.56,.64,1);
    z-index: 9999;
  }
  #toast.show { transform: translateY(0); opacity: 1; }
  #toast.ok  { border-color: var(--green); color: var(--green); }
  #toast.err { border-color: var(--red);   color: var(--red); }

  /* ── Log preview ── */
  .log-box {
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 11px; line-height: 1.6;
    color: #94a3b8;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px;
    max-height: 200px;
    overflow-y: auto;
    white-space: pre-wrap;
  }

  .section-title {
    font-size: 16px; font-weight: 600; margin-bottom: 14px;
    display: flex; align-items: center; gap: 10px;
  }

  .actions { display: flex; gap: 10px; margin-top: 20px; flex-wrap: wrap; }

  footer {
    text-align: center; color: var(--muted); font-size: 11px;
    margin-top: 40px; padding-top: 20px; border-top: 1px solid var(--border);
  }
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="header-icon">💳</div>
  <div>
    <h1>{{ app_name }}</h1>
    <p>Panel de Control — Puerto {{ http_port }}</p>
  </div>
  <div style="margin-left:auto; display:flex; flex-direction:column; align-items:flex-end; gap:6px;">
    <span class="badge badge-green"><span class="dot"></span>Servicio activo</span>
    <span style="font-size:11px; color:var(--muted);" id="clock"></span>
  </div>
</div>

<!-- Stats row -->
<div class="grid">

  <div class="card">
    <div class="card-title"><span>🏢</span> Identificación</div>
    <div class="stat">{{ nombre_caja }}</div>
    <div class="stat-label">Sucursal {{ id_sucursal }} · Terminal {{ id_terminal }}</div>
  </div>

  <div class="card">
    <div class="card-title"><span>🌐</span> Puerto HTTP</div>
    <div class="stat">{{ http_port }}</div>
    <div class="stat-label">
      <a href="http://localhost:{{ http_port }}/status" target="_blank"
         style="color:var(--accent); text-decoration:none;">
        localhost:{{ http_port }}/status ↗
      </a>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><span>💻</span> Hardware POS</div>
    <div class="stat" style="font-size:18px; margin-top:4px;">
      {% if usar_pos == 'true' %}
        <span class="badge badge-green" style="font-size:13px; padding:6px 14px;">Transbank ✓</span>
      {% else %}
        <span class="badge badge-yellow" style="font-size:13px; padding:6px 14px;">Sin POS físico</span>
      {% endif %}
      {% if usar_getnet == 'true' %}
        <span class="badge badge-green" style="font-size:13px; padding:6px 14px; margin-left:4px;">Getnet ✓</span>
      {% else %}
        <span class="badge badge-yellow" style="font-size:13px; padding:6px 14px; margin-left:4px;">Sin Getnet</span>
      {% endif %}
      {% if mp_allowed == '' %}
        <span class="badge badge-green" style="font-size:13px; padding:6px 14px; margin-left:4px;">Mercado Pago ✓</span>
      {% elif mp_allowed %}
        <span class="badge" style="font-size:13px; padding:6px 14px; margin-left:4px; background:rgba(99,102,241,0.15); color:#818cf8; border:1px solid rgba(99,102,241,0.3);" title="Cajas: {{ mp_allowed }}">Mercado Pago ▸ {{ mp_allowed.split(',') | length }} caja(s)</span>
      {% else %}
        <span class="badge badge-yellow" style="font-size:13px; padding:6px 14px; margin-left:4px;">Mercado Pago —</span>
      {% endif %}
    </div>
    <div class="stat-label" style="margin-top:8px;">{{ agents_count }} agente(s) registrado(s)</div>
  </div>

</div>

<!-- Configuración editable -->
<div class="card" style="margin-bottom:20px;">
  <div class="section-title">⚙️ Configuración (.env)</div>
  <div style="font-size:11px; color:var(--muted); margin-bottom:16px;">
    📄 {{ env_path }}
  </div>

  <form id="configForm" autocomplete="off">

    {% set hints = {
      'HTTP_PORT': 'Puerto donde corre esta API. Se define en el .env, no se puede cambiar desde acá.',
      'ID_SUCURSAL': 'Identificador de la sucursal. Tiene que coincidir con el id_sucursal que manda tu sistema web en cada /pago.',
      'NOMBRE_CAJA': 'Nombre de esta caja puntual. Por defecto usa el nombre del PC si se deja vacío.',
      'TERMINAL_ID': 'ID del terminal que se reporta a Transbank/Getnet. Si se deja vacío, se arma automático a partir de NOMBRE_CAJA.',
      'USAR_POS_FISICO': '⚠️ Esto es solo para Transbank. Poné true únicamente si esta caja tiene un POS Transbank físico conectado. No tiene nada que ver con Getnet.',
      'PUERTOS_COM': 'Lista de puertos COM donde Transbank va a buscar su POS (autodetección). Tampoco aplica a Getnet.',
      'USAR_GETNET': 'Este sí es el de Getnet: poné true si esta caja tiene un POS Getnet A920 Pro conectado.',
      'GETNET_PORT': 'Puerto COM fijo para el POS Getnet (ej. COM3). Dejalo vacío para que se detecte automáticamente — solo forzarlo si la autodetección elige el puerto equivocado.',
      'MAX_TRANSACTION_TIME': 'Segundos máximos que Transbank espera una respuesta antes de darla por vencida.',
      'TIMEOUT_SERVER': 'Timeout general (segundos) del servidor HTTP para operaciones de POS.',
    } %}

    {% set hints = {
      'HTTP_PORT': 'Puerto donde corre esta API. Se define en el .env, no se puede cambiar desde acá.',
      'ID_SUCURSAL': 'Identificador de la sucursal. Tiene que coincidir con el id_sucursal que manda tu sistema web en cada /pago.',
      'NOMBRE_CAJA': 'Nombre de esta caja puntual. Por defecto usa el nombre del PC si se deja vacío.',
      'TERMINAL_ID': 'ID del terminal que se reporta a Transbank/Getnet. Si se deja vacío, se arma automático a partir de NOMBRE_CAJA.',
      'USAR_POS_FISICO': 'Esto es solo para Transbank. Poné true únicamente si esta caja tiene un POS Transbank físico conectado. No tiene nada que ver con Getnet.',
      'PUERTOS_COM': 'Lista de puertos COM donde Transbank va a buscar su POS (autodetección). Tampoco aplica a Getnet.',
      'USAR_GETNET': 'Este sí es el de Getnet: poné true si esta caja tiene un POS Getnet A920 Pro conectado.',
      'GETNET_PORT': 'Puerto COM fijo para el POS Getnet (ej. COM3). Dejalo vacío para que se detecte automáticamente — solo forzarlo si la autodetección elige el puerto equivocado.',
      'MAX_TRANSACTION_TIME': 'Segundos máximos por defecto que se espera la respuesta de una venta (Transbank o Getnet) antes de darla por vencida. Se puede pisar por venta si el sistema web manda "timeout" en el request.',
      'TIMEOUT_SERVER': 'Timeout general (segundos) del servidor HTTP, usado como default en Mercado Pago y otras operaciones. También se puede pisar por request.',
      'ALLOWED_ORIGINS': 'Dominios web permitidos para llamar a esta API desde el navegador (CORS). Vacío = se permite cualquier origen ("*"). Ej: https://mitienda.cl,https://otra.cl',
      'MP_TERMINAL_ID': 'Terminal ID específico para Mercado Pago. Si se deja vacío, se usa el TERMINAL_ID general de arriba. Tiene prioridad sobre él solo para transacciones de Mercado Pago.',
      'ALLOW_MP_TOKEN_IN_REQUEST': '⚠️ Si está en true, cualquiera que llame a /pago puede mandar su propio access_token de Mercado Pago en el request, en vez de usar el MP_ACCESS_TOKEN configurado acá. Útil solo si varias cajas/clientes usan cuentas de MP distintas. Dejar en false si no lo necesitás.',
    } %}

    {% macro terminal_id_field(placeholder='') %}
    {% if 'TERMINAL_ID' in env_vars %}
    <div class="param-row">
      <span class="param-key">TERMINAL_ID</span>
      <input class="param-val sync-field" data-sync="TERMINAL_ID" name="TERMINAL_ID" value="{{ env_vars['TERMINAL_ID'] }}" title="{{ hints['TERMINAL_ID'] }}" autocomplete="off"
             {% if placeholder %}placeholder="{{ placeholder }}"{% endif %}>
    </div>
    {% endif %}
    {% endmacro %}

    <div class="section-title" style="font-size:13px; color:var(--muted); margin-bottom:10px; margin-top:4px;">
      Identificación de esta caja
    </div>

    {% for key in ['HTTP_PORT','ID_SUCURSAL','NOMBRE_CAJA'] %}
    {% if key in env_vars %}
    <div class="param-row">
      <span class="param-key">{{ key }}</span>
      {% if key == 'HTTP_PORT' %}
        <input class="param-val" name="{{ key }}" value="{{ env_vars[key] }}" readonly title="El puerto se define en el .env y no se puede editar desde el panel">
      {% else %}
        <input class="param-val" name="{{ key }}" value="{{ env_vars[key] }}" title="{{ hints.get(key, '') }}" autocomplete="off">
      {% endif %}
    </div>
    {% endif %}
    {% endfor %}

    <div class="section-title" style="font-size:13px; color:var(--muted); margin-bottom:10px; margin-top:20px;">
      ⏱️ General
    </div>

    {% for key in ['MAX_TRANSACTION_TIME','TIMEOUT_SERVER'] %}
    {% if key in env_vars %}
    <div class="param-row">
      <span class="param-key">{{ key }}</span>
      <input class="param-val" name="{{ key }}" value="{{ env_vars[key] }}" title="{{ hints.get(key, '') }}" autocomplete="off">
    </div>
    {% endif %}
    {% endfor %}

    <div class="param-row">
      <span class="param-key">ALLOWED_ORIGINS</span>
      <input class="param-val" name="ALLOWED_ORIGINS" value="{{ env_vars.get('ALLOWED_ORIGINS', '') }}" title="{{ hints['ALLOWED_ORIGINS'] }}" autocomplete="off"
             placeholder="vacío = se permite cualquier origen (*)">
    </div>

    <div class="section-title" style="font-size:13px; color:var(--muted); margin-bottom:10px; margin-top:20px;">
      💳 Transbank
    </div>

    {% for key in ['USAR_POS_FISICO','PUERTOS_COM'] %}
    {% if key in env_vars %}
    <div class="param-row">
      <span class="param-key">{{ key }}</span>
      {% if key == 'PUERTOS_COM' %}
        <div style="display:flex; gap:10px; flex:1; min-width:0;">
          <input class="param-val" id="puertosComInput" name="{{ key }}" value="{{ env_vars[key] }}" title="{{ hints[key] }}" autocomplete="off" style="flex:1; min-width:0;">
          <button type="button" class="btn btn-secondary" onclick="detectComPorts()" style="padding:6px 10px; font-size:12px; border-radius:8px;">Detectar</button>
        </div>
      {% else %}
        <input class="param-val" name="{{ key }}" value="{{ env_vars[key] }}" title="{{ hints.get(key, '') }}" autocomplete="off">
      {% endif %}
    </div>
    {% endif %}
    {% endfor %}
    {{ terminal_id_field() }}

    <div class="section-title" style="font-size:13px; color:var(--muted); margin-bottom:10px; margin-top:20px;">
      💳 Getnet
    </div>

    {% if 'USAR_GETNET' in env_vars %}
    <div class="param-row">
      <span class="param-key">USAR_GETNET</span>
      <input class="param-val" name="USAR_GETNET" value="{{ env_vars['USAR_GETNET'] }}" title="{{ hints['USAR_GETNET'] }}" autocomplete="off">
    </div>
    {% endif %}

    <div class="param-row">
      <span class="param-key">GETNET_PORT</span>
      <div style="display:flex; gap:10px; flex:1; min-width:0;">
        <input class="param-val" id="getnetPortInput" name="GETNET_PORT" value="{{ env_vars.get('GETNET_PORT', '') }}" title="{{ hints['GETNET_PORT'] }}" autocomplete="off"
               placeholder="vacío = detección automática" style="flex:1; min-width:0;">
        <button type="button" class="btn btn-secondary" onclick="detectGetnetPort()" style="padding:6px 10px; font-size:12px; border-radius:8px;">Detectar</button>
      </div>
    </div>
    {{ terminal_id_field() }}

    <div class="section-title" style="font-size:13px; color:var(--muted); margin-bottom:10px; margin-top:20px;">
      💳 Mercado Pago
    </div>

    {% if 'ALLOWED_MP' in env_vars %}
    <div class="param-row">
      <span class="param-key">ALLOWED_MP</span>
      <input class="param-val" name="ALLOWED_MP" value="{{ env_vars['ALLOWED_MP'] }}" autocomplete="off"
             placeholder="vacío = todas las cajas; ej: 70:caja_10,70:caja_11">
    </div>
    {% else %}
    <div class="param-row">
      <span class="param-key">ALLOWED_MP</span>
      <input class="param-val" name="ALLOWED_MP" value="" autocomplete="off"
             placeholder="vacío = TODAS las cajas habilitadas para MP">
    </div>
    {% endif %}

    {% if 'MP_API_URL' in env_vars %}
    <div class="param-row">
      <span class="param-key">MP_API_URL</span>
      <input class="param-val" name="MP_API_URL" value="{{ env_vars['MP_API_URL'] }}" autocomplete="off">
    </div>
    {% endif %}

    <div class="param-row">
      <span class="param-key">MP_ACCESS_TOKEN</span>
      <input class="param-val" type="password" name="MP_ACCESS_TOKEN" value=""
             autocomplete="new-password"
             placeholder="{% if mp_token_configured %}configurado (dejar vacío para mantener){% else %}pegar APP_USR-...{% endif %}">
    </div>

    <div class="param-row">
      <span class="param-key">ALLOW_MP_TOKEN_IN_REQUEST</span>
      <input class="param-val" name="ALLOW_MP_TOKEN_IN_REQUEST" value="{{ env_vars.get('ALLOW_MP_TOKEN_IN_REQUEST', 'false') }}" title="{{ hints['ALLOW_MP_TOKEN_IN_REQUEST'] }}" autocomplete="off">
    </div>
    {{ terminal_id_field(placeholder='ej: NEWLAND_N950') }}

    {% if env_vars %}
    <div class="section-title" style="font-size:13px; color:var(--muted); margin-bottom:10px; margin-top:20px;">
      Otros parámetros
    </div>
    {% set known = ['HTTP_PORT','ID_SUCURSAL','NOMBRE_CAJA','TERMINAL_ID','USAR_POS_FISICO','PUERTOS_COM','USAR_GETNET','GETNET_PORT','MAX_TRANSACTION_TIME','TIMEOUT_SERVER','ALLOWED_ORIGINS','API_AUTH_USER','API_AUTH_PASS','ALLOWED_MP','MP_API_URL','MP_ACCESS_TOKEN','MP_TERMINAL_ID','ALLOW_MP_TOKEN_IN_REQUEST'] %}
    {% for key, val in env_vars.items() %}
    {% if key not in known %}
    <div class="param-row">
      <span class="param-key">{{ key }}</span>
      <input class="param-val" name="{{ key }}" value="{{ val }}" autocomplete="off">
    </div>
    {% endif %}
    {% endfor %}
    {% endif %}

    <div class="actions">
      <button type="submit" class="btn btn-primary" id="btnGuardar">💾 Guardar y reiniciar</button>
      <a href="/panel" class="btn btn-secondary">🔄 Recargar</a>
      <a href="/status" target="_blank" class="btn btn-secondary">📊 Ver status JSON</a>
    </div>

  </form>
</div>

{% if is_windows %}
<div class="card" style="margin-bottom:20px;">
  <div class="section-title">🪟 Inicio con Windows</div>
  <div class="param-row">
    <span class="param-key">Autostart</span>
    {% if autostart_enabled %}
      <div class="param-val" style="display:flex; align-items:center; gap:10px;">
        <span class="badge badge-green">Habilitado</span>
        <span style="color:var(--muted); font-size:12px;">Se iniciará al iniciar sesión</span>
      </div>
    {% else %}
      <div class="param-val" style="display:flex; align-items:center; gap:10px;">
        <span class="badge badge-yellow">Deshabilitado</span>
        <span style="color:var(--muted); font-size:12px;">No se iniciará automáticamente</span>
      </div>
    {% endif %}
  </div>
  <div class="actions">
    <button class="btn btn-secondary" type="button" id="btnAutostartOn" onclick="setAutostart(true)">✅ Habilitar</button>
    <button class="btn btn-secondary" type="button" id="btnAutostartOff" onclick="setAutostart(false)">⛔ Deshabilitar</button>
    <button class="btn btn-secondary" type="button" onclick="refreshAutostart()">🔄 Actualizar</button>
  </div>
</div>
{% endif %}

<!-- Transacciones sin confirmar -->
<div class="card" id="pendientesCard" style="margin-bottom:20px; display:none;">
  <div class="section-title">⚠️ Transacciones sin confirmar</div>
  <div style="font-size:12px; color:var(--muted); margin-bottom:12px;">
    Se enviaron al POS pero no llegó (o no se pudo confirmar) el resultado final.
    El cliente puede haber pagado igual: revise el comprobante impreso en el POS antes de resolver.
    Mientras queden aquí, la caja correspondiente queda bloqueada para nuevas ventas Getnet.
  </div>
  <div id="pendientesBox"></div>
</div>

<!-- Log preview -->
<div class="card">
  <div class="section-title">📋 Log reciente</div>
  <div class="actions" style="margin-top:0; margin-bottom:12px;">
    <button class="btn btn-secondary" id="tabLog-general" onclick="setLogTab('general')">🛠️ Técnico</button>
    <button class="btn btn-secondary" id="tabLog-getnet" onclick="setLogTab('getnet')">💳 Getnet</button>
    <button class="btn btn-secondary" id="tabLog-transbank" onclick="setLogTab('transbank')">💳 Transbank</button>
    <button class="btn btn-secondary" id="tabLog-mercadopago" onclick="setLogTab('mercadopago')">💳 Mercado Pago</button>
  </div>
  <div class="log-box" id="logBox">Cargando...</div>
  <div class="actions">
    <button class="btn btn-secondary" onclick="loadLog()">🔄 Actualizar log</button>
  </div>
</div>

<footer>
  {{ app_name }} · http://localhost:{{ http_port }} · Log de hoy: {{ log_path }}
</footer>

<div id="toast"></div>

<script>
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
  t.textContent = ok ? '✅ ' + msg : '❌ ' + msg;
  t.className = 'show ' + (ok ? 'ok' : 'err');
  setTimeout(() => t.className = '', 3500);
}

// ── Sincronizar campos duplicados (ej. TERMINAL_ID aparece en varias
//    secciones, pero es el mismo valor — editar cualquiera actualiza todos) ──
document.querySelectorAll('#configForm .sync-field').forEach(inp => {
  inp.addEventListener('input', () => {
    document.querySelectorAll(`#configForm .sync-field[data-sync="${inp.dataset.sync}"]`)
      .forEach(other => { if (other !== inp) other.value = inp.value; });
  });
});

// ── Guardar config + reiniciar ──
document.getElementById('configForm').addEventListener('submit', async e => {
  e.preventDefault();
  const data = {};
  new FormData(e.target).forEach((v, k) => {
    if (k === 'MP_ACCESS_TOKEN' && !String(v || '').trim()) return;
    data[k] = v;
  });

  const btn = document.getElementById('btnGuardar');
  btn.disabled = true;
  btn.textContent = '⏳ Guardando...';

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
      btn.disabled = false; btn.textContent = '💾 Guardar y reiniciar';
      return;
    }

    // 2. Solicitar reinicio
    showToast('Configuración guardada. Reiniciando servicio...', true);
    btn.textContent = '🔄 Reiniciando...';
    await fetch('/panel/restart', { method: 'POST' }).catch(() => {});

    // 3. Esperar que el servicio vuelva (poll /status)
    let secs = 0;
    const interval = setInterval(async () => {
      secs++;
      btn.textContent = `🔄 Reiniciando... (${secs}s)`;
      try {
        const ping = await fetch('/status', { cache: 'no-store' });
        if (ping.ok) {
          clearInterval(interval);
          showToast('✅ Servicio reiniciado correctamente', true);
          setTimeout(() => location.reload(), 1200);
        }
      } catch(_) { /* aún reiniciando */ }
      if (secs > 30) {
        clearInterval(interval);
        btn.disabled = false; btn.textContent = '💾 Guardar y reiniciar';
        showToast('El servicio tardó más de lo esperado. Recarga manualmente.', false);
      }
    }, 1000);

  } catch(err) {
    showToast('Error de conexión: ' + err, false);
    btn.disabled = false; btn.textContent = '💾 Guardar y reiniciar';
  }
});

// ── Transacciones sin confirmar ──
function fmtFecha(ts) {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleString('es-CL');
}

async function loadPendientes() {
  try {
    const r = await fetch('/panel/pendientes', { cache: 'no-store' });
    const j = await r.json();
    const card = document.getElementById('pendientesCard');
    const box = document.getElementById('pendientesBox');
    const items = (j.pendientes || []);

    if (!items.length) {
      card.style.display = 'none';
      return;
    }
    card.style.display = 'block';

    box.innerHTML = items.map(tx => `
      <div class="param-row" style="flex-direction:column; align-items:stretch; gap:8px; padding:12px; border:1px solid var(--border, #333); border-radius:8px; margin-bottom:10px;">
        <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:6px;">
          <strong>${tx.tipo} · $${tx.monto ?? '-'}</strong>
          <span class="badge ${tx.estado === 'INDETERMINADA' ? 'badge-yellow' : ''}">${tx.estado}</span>
        </div>
        <div style="font-size:11px; color:var(--muted);">
          tx: ${tx.tx_id}<br>
          ticket: ${tx.ticket ?? '-'} · caja: ${tx.id_sucursal}/${tx.nombre_caja}<br>
          creada: ${fmtFecha(tx.created_at)}
          ${tx.nota ? `<br>nota: ${tx.nota}` : ''}
        </div>
        <div class="actions" style="margin-top:0;">
          <button class="btn btn-secondary" onclick="reconciliarTx('${tx.tx_id}')">🔄 Reconsultar POS</button>
          <button class="btn btn-secondary" onclick="resolverTx('${tx.tx_id}', 'APROBADO')">✅ Marcar aprobada</button>
          <button class="btn btn-secondary" onclick="resolverTx('${tx.tx_id}', 'RECHAZADO')">❌ Marcar rechazada</button>
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
    const r = await fetch(`/panel/pendientes/${txId}/reconciliar`, { method: 'POST' });
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
    const r = await fetch(`/panel/pendientes/${txId}/resolver`, {
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

// ── Log ──
let currentLogTab = 'general';

function setLogTab(tipo) {
  currentLogTab = tipo;
  for (const t of ['general', 'getnet', 'transbank', 'mercadopago']) {
    const btn = document.getElementById('tabLog-' + t);
    if (btn) btn.classList.toggle('btn-primary', t === tipo);
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

async function detectComPorts() {
  const btns = Array.from(document.querySelectorAll('button')).filter(b => (b.textContent || '').trim() === 'Detectar');
  for (const b of btns) b.disabled = true;
  try {
    const r = await fetch('/panel/com_ports', { cache: 'no-store' });
    const j = await r.json();
    if (!j.ok) {
      showToast(j.error || 'No se pudieron detectar puertos', false);
      return;
    }
    const ports = (j.ports || []).filter(Boolean);
    const input = document.getElementById('puertosComInput');
    if (!input) return;
    if (!ports.length) {
      showToast('No se detectaron puertos COM', false);
      return;
    }
    input.value = ports.join(',');
    showToast(`Puertos detectados: ${ports.join(', ')}`, true);
  } catch (e) {
    showToast('Error detectando puertos', false);
  } finally {
    for (const b of btns) b.disabled = false;
  }
}

async function detectGetnetPort() {
  const btns = Array.from(document.querySelectorAll('button')).filter(b => (b.textContent || '').trim() === 'Detectar');
  for (const b of btns) b.disabled = true;
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
    for (const b of btns) b.disabled = false;
  }
}
</script>
</body>
</html>
"""
