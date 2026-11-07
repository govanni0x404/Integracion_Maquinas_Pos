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
    --bg:        #071330;
    --surface:   #0b1a3d;
    --card:      #0f2049;
    --card-2:    #132755;
    --border:    #1e3468;
    --border-2:  #2a4380;
    --text:      #e6eaf3;
    --text-2:    #b4bdd3;
    --muted:     #8590ab;
    --accent:    #4f86ff;
    --accent-2:  #3b6fe6;
    --orange:    #f7941d;
    --orange-2:  #ef6c0f;
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
    background:
      radial-gradient(700px 500px at 0% 0%, rgba(229,72,77,.10), transparent 70%),
      radial-gradient(700px 500px at 100% 10%, rgba(155,109,255,.12), transparent 70%),
      radial-gradient(800px 600px at 100% 100%, rgba(0,168,232,.10), transparent 70%),
      radial-gradient(700px 500px at 0% 100%, rgba(255,122,26,.08), transparent 70%),
      var(--bg);
    background-attachment: fixed;
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
    background: rgba(7,19,48,0.92);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--border);
  }
  .topbar::before {
    content: ""; display: block; height: 3px;
    background: linear-gradient(90deg, var(--c-transbank) 0 25%, var(--c-getnet) 25% 50%, var(--c-mp) 50% 75%, var(--c-tuu) 75% 100%);
  }
  .topbar-inner {
    max-width: 1240px; margin: 0 auto;
    padding: 14px 24px;
    display: flex; align-items: center; gap: 14px;
  }
  .brand-mark {
    width: 38px; height: 38px; border-radius: 10px;
    background: linear-gradient(145deg, var(--orange), var(--orange-2));
    box-shadow: 0 6px 16px rgba(239,108,15,.35);
    display: grid; place-items: center;
    color: #fff; font-weight: 700; font-size: 15px; letter-spacing: -0.02em;
  }
  .brand h1 { font-size: 16px; font-weight: 650; letter-spacing: -0.01em; }
  .brand p  { font-size: 12px; color: var(--muted); }
  .brand .eyebrow { font-size: 10.5px; font-weight: 800; letter-spacing: .12em; color: var(--orange); }
  .topbar-right { margin-left: auto; display: flex; align-items: center; gap: 14px; }
  .clock { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }

  /* ── Layout ── */
  .layout {
    max-width: 1240px; margin: 0 auto;
    padding: 24px;
    display: grid; grid-template-columns: 210px minmax(0, 1fr); gap: 28px;
    align-items: start;
  }
  .sidenav { position: sticky; top: 92px; }
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
  .tile-methods { position: relative; overflow: hidden; }
  .tile-methods::before {
    content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, var(--c-transbank) 0 25%, var(--c-getnet) 25% 50%, var(--c-mp) 50% 75%, var(--c-tuu) 75% 100%);
  }
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
    background: rgba(15,32,73,0.96);
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
  .btn-primary { background: linear-gradient(180deg, var(--orange), var(--orange-2)); color: #fff;
                 box-shadow: 0 6px 16px rgba(239,108,15,.28); }
  .btn-primary:hover:not(:disabled) { filter: brightness(1.06); }
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

  /* ── Historial de ventas ── */
  .hv-filtros { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; align-items: end; }
  .hv-filtros label { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--muted); }
  .hv-resumen { display: flex; flex-wrap: wrap; gap: 8px; font-size: 12.5px; color: var(--text-2); }
  .hv-dia { margin-top: 6px; display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap;
            font-size: 13px; font-weight: 650; color: var(--text); padding: 8px 2px; border-bottom: 1px solid var(--border-2); }
  .hv-dia span { font-weight: 500; color: var(--muted); font-size: 12px; }
  .hv-tabla-wrap { overflow-x: auto; }
  .hv-tabla { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  .hv-tabla th { text-align: left; font-weight: 600; color: var(--muted); padding: 7px 8px; white-space: nowrap; }
  .hv-tabla td { padding: 7px 8px; border-top: 1px solid var(--border); color: var(--text-2); white-space: nowrap; }
  .hv-tabla td.num { text-align: right; font-variant-numeric: tabular-nums; color: var(--text); font-weight: 600; }
  .hv-tabla td.mono { font-family: var(--mono); font-size: 11.5px; }
  .hv-tabla tr.hv-fila { cursor: pointer; }
  .hv-tabla tr.hv-fila:hover td { background: rgba(79,124,255,0.06); }
  .hv-detalle td { white-space: normal; background: rgba(133,144,171,0.06); }
  .hv-detalle pre { font-family: var(--mono); font-size: 11px; white-space: pre-wrap; word-break: break-all;
                    max-height: 260px; overflow: auto; margin-top: 6px; color: var(--text-2); }
  .hv-vacio { color: var(--muted); font-size: 13px; padding: 14px 2px; }

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
  .tab.active[data-machine-tab="transbank"] { box-shadow: inset 0 0 0 1px var(--c-transbank), inset 0 -3px 0 var(--c-transbank); }
  .tab.active[data-machine-tab="getnet"]    { box-shadow: inset 0 0 0 1px var(--c-getnet),    inset 0 -3px 0 var(--c-getnet); }
  .tab.active[data-machine-tab="mp"]        { box-shadow: inset 0 0 0 1px var(--c-mp),        inset 0 -3px 0 var(--c-mp); }
  .tab.active[data-machine-tab="tuu"]       { box-shadow: inset 0 0 0 1px var(--c-tuu),       inset 0 -3px 0 var(--c-tuu); }
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
    <div class="brand-mark" aria-hidden="true">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2.5"/><rect x="8" y="5" width="8" height="5" rx="1"/><path d="M8.5 14h.01M12 14h.01M15.5 14h.01M8.5 18h.01M12 18h.01M15.5 18h.01"/></svg>
    </div>
    <div class="brand">
      <div class="eyebrow">DATAMAULE</div>
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
      <a href="#sec-historial">Historial de ventas</a>
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

    <!-- Historial de ventas (solo lectura) -->
    <section class="card" id="sec-historial">
      <div class="card-head" style="flex-wrap:wrap;">
        <div>
          <h2>Historial de ventas</h2>
          <p>Todas las ventas enviadas a las máquinas, de la más nueva a la más antigua. Haga clic en una fila para ver el detalle completo.
             Otros sistemas pueden obtener lo mismo en <code>GET /ventas</code> (con la clave de la API).</p>
        </div>
        <div class="head-right">
          <button class="btn btn-secondary btn-sm" onclick="exportarHistorialCsv()">Descargar CSV</button>
        </div>
      </div>
      <div class="card-body" style="display:flex; flex-direction:column; gap:14px;">
        <div class="hv-filtros">
          <label>Desde <input type="date" class="param-val" id="hvDesde"></label>
          <label>Hasta <input type="date" class="param-val" id="hvHasta"></label>
          <label>Máquina
            <select class="param-val" id="hvTipo">
              <option value="">Todas</option>
              <option value="transbank">Transbank</option>
              <option value="getnet">Getnet</option>
              <option value="mercadopago">Mercado Pago</option>
              <option value="tuu">TUU</option>
            </select>
          </label>
          <label>Estado
            <select class="param-val" id="hvEstado">
              <option value="">Todos</option>
              <option value="APROBADO">Aprobado</option>
              <option value="RECHAZADO">Rechazado</option>
              <option value="ERROR">Error</option>
              <option value="PENDIENTE">Pendiente</option>
              <option value="INDETERMINADA">Indeterminada</option>
            </select>
          </label>
          <button class="btn btn-primary btn-sm" onclick="loadHistorial(false)">Buscar</button>
        </div>
        <div class="hv-resumen" id="hvResumen"></div>
        <div id="hvBox"><div class="hv-vacio">Cargando...</div></div>
        <div class="actions"><button class="btn btn-secondary btn-sm" id="hvMas" style="display:none;" onclick="loadHistorial(true)">Cargar más</button></div>
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

// ── Historial de ventas ──
let hvVentas = [];
let hvOffset = 0;
const HV_LIMITE = 200;
const HV_BADGE = { APROBADO: 'badge-green', RECHAZADO: 'badge-red', ERROR: 'badge-red',
                   PENDIENTE: 'badge-blue', INDETERMINADA: 'badge-yellow' };

function hvHoy() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function hvParams(offset, limite) {
  const p = new URLSearchParams();
  const v = id => document.getElementById(id).value;
  if (v('hvDesde')) p.set('desde', v('hvDesde'));
  if (v('hvHasta')) p.set('hasta', v('hvHasta'));
  if (v('hvTipo')) p.set('tipo', v('hvTipo'));
  if (v('hvEstado')) p.set('estado', v('hvEstado'));
  p.set('limite', limite); p.set('offset', offset);
  return p;
}

function hvFechaLarga(iso) {
  const [a, m, d] = iso.split('-').map(Number);
  const s = new Date(a, m - 1, d).toLocaleDateString('es-CL', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function hvEstadoBadge(v) {
  let b = `<span class="badge ${HV_BADGE[v.estado] || ''}">${esc(v.estado)}</span>`;
  if (v.cancelada) b += ` <span class="badge">Cancelada</span>`;
  return b;
}

function renderHistorial(resumen, total) {
  const box = document.getElementById('hvBox');
  if (!hvVentas.length) {
    box.innerHTML = '<div class="hv-vacio">No hay ventas para esos filtros.</div>';
  } else {
    const dias = {};
    for (const v of hvVentas) (dias[v.fecha] = dias[v.fecha] || []).push(v);
    box.innerHTML = Object.keys(dias).map(dia => {
      const lista = dias[dia];
      const ok = lista.filter(v => v.aprobada);
      const montoOk = ok.reduce((s, v) => s + (Number(v.monto) || 0), 0);
      return `
        <div class="hv-dia">${esc(hvFechaLarga(dia))}
          <span>${lista.length} venta(s) · ${ok.length} aprobada(s) · ${esc(fmtMonto(montoOk))} aprobado</span></div>
        <div class="hv-tabla-wrap"><table class="hv-tabla">
          <thead><tr><th>Enviada</th><th>Procesada</th><th>Máquina</th><th>Caja</th><th style="text-align:right">Monto</th>
                     <th>Estado</th><th>Autorización</th><th>Transacción</th></tr></thead>
          <tbody>${lista.map(v => {
            const i = hvVentas.indexOf(v);
            return `
            <tr class="hv-fila" onclick="hvToggle(${i})">
              <td class="mono">${esc(v.hora_envio || '-')}</td>
              <td class="mono">${esc(v.hora_proceso || '-')}${v.duracion_segundos != null ? ` <span style="color:var(--muted)">(${esc(v.duracion_segundos)} s)</span>` : ''}</td>
              <td><span class="brand-dot ${DOT_TIPO[v.tipo] || ''}"></span> ${esc(v.maquina)}</td>
              <td>${esc(v.id_sucursal)}/${esc(v.nombre_caja)}</td>
              <td class="num">${esc(fmtMonto(v.monto))}</td>
              <td>${hvEstadoBadge(v)}</td>
              <td class="mono">${esc(v.codigo_autorizacion ?? '-')}</td>
              <td class="mono">${esc(v.transaction_id)}</td>
            </tr>
            <tr class="hv-detalle" id="hvDet${i}" style="display:none;"><td colspan="8">
              <div class="tx-meta">
                <span>Terminal <b>${esc(v.terminal_id ?? '-')}</b></span>
                <span>Ticket <b>${esc(v.ticket ?? '-')}</b></span>
                <span>Comprobante POS <b>${esc(v.comprobante_pos ?? '-')}</b></span>
                <span>Tarjeta <b>${v.tarjeta_ultimos4 ? '**** ' + esc(v.tarjeta_ultimos4) : '-'}</b></span>
                <span>Enviada <b>${esc(v.enviada_en ?? '-')}</b></span>
                <span>Procesada <b>${esc(v.procesada_en ?? '-')}</b></span>
                <span>Resuelta por <b>${esc(v.resuelto_por ?? '-')}</b></span>
              </div>
              ${v.nota ? `<div class="tx-note" style="margin-top:6px">${esc(v.nota)}</div>` : ''}
              ${v.respuesta_pos ? `<pre>${esc(JSON.stringify(v.respuesta_pos, null, 2))}</pre>` : ''}
            </td></tr>`;
          }).join('')}</tbody>
        </table></div>`;
    }).join('');
  }
  const pe = resumen.por_estado || {};
  document.getElementById('hvResumen').innerHTML =
    `<span class="badge">${esc(total)} venta(s)</span>` +
    `<span class="badge badge-green">${esc(resumen.cantidad_aprobadas)} aprobada(s) · ${esc(fmtMonto(resumen.monto_aprobado))}</span>` +
    Object.keys(pe).filter(k => k !== 'APROBADO').map(k =>
      `<span class="badge ${HV_BADGE[k] || ''}">${esc(pe[k].cantidad)} ${esc(k.toLowerCase())}</span>`).join('');
}

function hvToggle(i) {
  const fila = document.getElementById('hvDet' + i);
  if (fila) fila.style.display = fila.style.display === 'none' ? '' : 'none';
}

async function loadHistorial(mas) {
  const desde = document.getElementById('hvDesde');
  const hasta = document.getElementById('hvHasta');
  if (!desde.value && !hasta.value) { desde.value = hvHoy(); hasta.value = hvHoy(); }
  if (!mas) { hvVentas = []; hvOffset = 0; }
  try {
    const r = await fetch('/panel/ventas?' + hvParams(hvOffset, HV_LIMITE), { cache: 'no-store' });
    const j = await r.json();
    if (!j.ok) { showToast(j.error || 'No se pudo cargar el historial', false); return; }
    hvVentas = hvVentas.concat(j.ventas);
    hvOffset += j.devueltas;
    renderHistorial(j.resumen, j.total);
    document.getElementById('hvMas').style.display = j.hay_mas ? '' : 'none';
  } catch (e) {
    document.getElementById('hvBox').innerHTML = '<div class="hv-vacio">No se pudo cargar el historial.</div>';
  }
}

async function exportarHistorialCsv() {
  try {
    let todas = [], offset = 0, j;
    do {
      const r = await fetch('/panel/ventas?' + hvParams(offset, 1000), { cache: 'no-store' });
      j = await r.json();
      if (!j.ok) { showToast(j.error || 'No se pudo exportar', false); return; }
      todas = todas.concat(j.ventas); offset += j.devueltas;
    } while (j.hay_mas && j.devueltas);
    const cols = ['fecha', 'hora_envio', 'hora_proceso', 'duracion_segundos', 'maquina', 'terminal_id', 'id_sucursal',
                  'nombre_caja', 'monto', 'estado', 'cancelada', 'codigo_autorizacion', 'comprobante_pos',
                  'tarjeta_ultimos4', 'ticket', 'transaction_id', 'resuelto_por', 'nota'];
    const celda = x => '"' + String(x ?? '').replace(/"/g, '""') + '"';
    const csv = [cols.join(';')].concat(todas.map(v => cols.map(c => celda(v[c])).join(';'))).join('\\r\\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['\\ufeff' + csv], { type: 'text/csv;charset=utf-8' }));
    a.download = `ventas_${j.filtros.desde || ''}_${j.filtros.hasta || ''}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (e) {
    showToast('No se pudo exportar el historial', false);
  }
}
loadHistorial(false);

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
    --navy-0:#06112b; --navy-1:#0a1a42; --navy-2:#0f2558; --navy-3:#1a3a80;
    --orange:#f7941d; --orange-2:#ef6c0f; --green:#7cbf2a; --green-2:#4e9a1b; --ok:#22c55e;
    --tb:#e5484d; --gn:#ff7a1a; --mp:#00a8e8; --tuu:#9b6dff;
    --ink:#0f1f45; --muted:#5b6785; --line:#d6dceb; --blue:#2f6fde; --red:#d93636;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0; min-height: 100vh; overflow-x: hidden; color: #e8eefc;
    font: 14px/1.5 "Segoe UI", system-ui, -apple-system, Roboto, sans-serif;
    background:
      radial-gradient(520px 420px at 12% 18%, rgba(229,72,77,.16), transparent 70%),
      radial-gradient(560px 460px at 55% 30%, rgba(255,122,26,.12), transparent 70%),
      radial-gradient(620px 520px at 60% 92%, rgba(0,168,232,.16), transparent 70%),
      radial-gradient(560px 480px at 95% 70%, rgba(155,109,255,.18), transparent 70%),
      linear-gradient(135deg, var(--navy-0) 0%, var(--navy-1) 50%, var(--navy-2) 100%);
  }
  /* Trama de puntos muy suave */
  body::before { content: ""; position: fixed; inset: 0; pointer-events: none;
    background-image: radial-gradient(rgba(150,175,235,.10) 1px, transparent 1.4px); background-size: 26px 26px; }
  /* Franja de colores de las máquinas */
  .stripe { position: fixed; top: 0; left: 0; right: 0; height: 4px; z-index: 2;
    background: linear-gradient(90deg, var(--tb) 0 25%, var(--gn) 25% 50%, var(--mp) 50% 75%, var(--tuu) 75% 100%); }

  /* ── Layout ── */
  .wrap { position: relative; z-index: 1; min-height: 100vh; display: grid;
          grid-template-columns: minmax(0, 440px) minmax(0, 1fr) 400px; align-items: center;
          gap: 36px; max-width: 1320px; margin: 0 auto; padding: 48px 32px; }

  /* ── Lado izquierdo ── */
  .logo { display: inline-flex; align-items: center; gap: 12px; background: #fff; border-radius: 12px;
          padding: 14px 20px; box-shadow: 0 10px 30px rgba(0,0,0,.25); }
  .logo .txt { line-height: 1; }
  .logo .sup { font-size: 8.5px; font-weight: 700; letter-spacing: .06em; color: var(--ink); text-transform: uppercase; }
  .logo .name { font-size: 24px; font-weight: 900; letter-spacing: -.02em; color: var(--ink); margin: 2px 0 4px; }
  .logo .name span { color: var(--green-2); }
  .logo .tag { font-size: 9.5px; font-style: italic; font-weight: 600; color: var(--green-2); }
  .logo .tag em { color: var(--orange); font-style: normal; }
  .eyebrow { display: inline-flex; align-items: center; gap: 8px; margin-top: 34px; padding: 5px 12px; border-radius: 999px;
             background: rgba(255,255,255,.06); border: 1px solid rgba(150,175,235,.2); font-size: 12px; color: #c4cee6; }
  .eyebrow i { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 0 3px rgba(34,197,94,.2); }
  h2 { font-size: 33px; line-height: 1.15; margin: 14px 0 12px; font-weight: 800; letter-spacing: -.01em; }
  h2 span { background: linear-gradient(90deg, var(--tb), var(--gn) 35%, var(--orange) 55%, var(--mp) 80%, var(--tuu));
            -webkit-background-clip: text; background-clip: text; color: transparent; }
  .lead { color: #c4cee6; font-size: 14.5px; margin: 0 0 20px; }
  .maqs { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .maq { position: relative; padding: 12px 12px 12px 14px; border-radius: 10px; overflow: hidden;
         background: rgba(18,34,82,.66); border: 1px solid rgba(120,150,220,.18); }
  .maq::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--c); }
  .maq .ic { width: 32px; height: 32px; border-radius: 8px; display: grid; place-items: center; margin-bottom: 8px;
             color: var(--c); background: rgba(255,255,255,.08); background: color-mix(in srgb, var(--c) 18%, transparent); }
  .maq strong { display: block; font-size: 13.5px; color: #fff; }
  .maq small { display: block; font-size: 11.5px; color: #aab7d6; line-height: 1.35; }
  .maq.tb { --c: var(--tb); } .maq.gn { --c: var(--gn); } .maq.mp { --c: var(--mp); } .maq.tuu { --c: var(--tuu); }
  .firma { margin-top: 24px; font: italic 600 21px/1.25 "Segoe Script", "Brush Script MT", "Comic Sans MS", cursive; color: #fff; }
  .firma::after { content: ""; display: block; width: 190px; height: 3px; margin-top: 6px; border-radius: 3px;
                  background: linear-gradient(90deg, var(--tb), var(--gn), var(--mp), var(--tuu)); }

  /* ── Ilustración de máquinas ── */
  .scene { position: relative; align-self: stretch; display: grid; place-items: center; min-height: 520px; }
  .scene svg { width: 100%; max-width: 440px; height: auto; filter: drop-shadow(0 30px 50px rgba(0,0,0,.45)); }
  .float { animation: flotar 6s ease-in-out infinite; transform-box: fill-box; }
  .float.d2 { animation-delay: -2s; } .float.d3 { animation-delay: -4s; }
  @keyframes flotar { 0%,100% { transform: translateY(0) } 50% { transform: translateY(-8px) } }
  .onda { animation: onda 2.4s ease-out infinite; transform-box: fill-box; transform-origin: left center; }
  .onda.o2 { animation-delay: .4s; } .onda.o3 { animation-delay: .8s; }
  @keyframes onda { 0% { opacity: 0 } 30% { opacity: 1 } 100% { opacity: 0 } }
  @media (prefers-reduced-motion: reduce) { .float, .onda { animation: none; } }

  /* ── Tarjeta de ingreso ── */
  .card { position: relative; overflow: hidden; background: linear-gradient(180deg, #f7f9fd, #e9eef7); color: var(--ink);
          border-radius: 18px; padding: 36px 32px 26px; box-shadow: 0 30px 80px rgba(3,10,30,.55); }
  .card::before { content: ""; position: absolute; top: 0; left: 0; right: 0; height: 5px;
    background: linear-gradient(90deg, var(--tb) 0 25%, var(--gn) 25% 50%, var(--mp) 50% 75%, var(--tuu) 75% 100%); }
  .card .brand { display: flex; align-items: center; justify-content: center; gap: 12px; }
  .card .brand .pos { width: 44px; height: 44px; border-radius: 12px; display: grid; place-items: center; color: #fff;
                      background: linear-gradient(145deg, var(--orange), var(--orange-2)); box-shadow: 0 8px 18px rgba(239,108,15,.35); }
  .card .brand h1 { margin: 0; font-size: 22px; font-weight: 900; letter-spacing: .03em; color: var(--ink); line-height: 1.1; }
  .card .brand .sub { color: var(--blue); font-size: 13.5px; font-weight: 600; }
  .card h3 { text-align: center; font-size: 25px; margin: 24px 0 2px; font-weight: 800; }
  .card .who { text-align: center; color: var(--muted); font-size: 13.5px; margin: 0 0 20px; }
  .card .who b { color: var(--ink); }
  .first { background: #fff4e6; border: 1px solid #f7c58a; color: #7a4a10; border-radius: 8px;
           padding: 10px 12px; font-size: 12.5px; margin: 0 0 16px; }
  .first strong { display: block; color: #b45f06; font-size: 13px; margin-bottom: 2px; }
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
  .btn { width: 100%; height: 48px; margin-top: 18px; border: 0; border-radius: 8px; cursor: pointer;
         display: flex; align-items: center; justify-content: center; gap: 10px;
         background: linear-gradient(180deg, var(--orange), var(--orange-2)); color: #fff;
         font: 600 15px "Segoe UI", system-ui, sans-serif; box-shadow: 0 10px 22px rgba(239,108,15,.32); }
  .btn:hover:not(:disabled) { filter: brightness(1.05); }
  .btn:disabled { opacity: .7; cursor: default; }
  .err { color: var(--red); font-size: 13px; min-height: 20px; margin-top: 10px; text-align: center; }
  .secure { display: flex; gap: 12px; align-items: flex-start; margin-top: 12px; padding-top: 16px;
            border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }
  .secure svg { flex: none; color: var(--ink); margin-top: 1px; }
  .brands { display: flex; justify-content: center; gap: 14px; margin-top: 14px; font-size: 11px; font-weight: 600; color: var(--muted); flex-wrap: wrap; }
  .brands span { display: inline-flex; align-items: center; gap: 5px; }
  .brands i { width: 8px; height: 8px; border-radius: 3px; display: block; }

  @media (max-width: 1180px) {
    .wrap { grid-template-columns: minmax(0, 1fr) 400px; }
    .scene { display: none; }
  }
  @media (max-width: 860px) {
    .wrap { grid-template-columns: 1fr; max-width: 460px; padding: 28px 16px; gap: 22px; }
    .left .maqs, .left .lead, .left .firma, .left h2, .left .eyebrow { display: none; }
    .left { text-align: center; }
  }
</style>
</head>
<body>
<div class="stripe" aria-hidden="true"></div>

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

    <div class="eyebrow"><i></i>Pasarela de pagos para tu caja</div>
    <h2>Todas tus máquinas<br>de pago, <span>en una sola caja</span></h2>
    <p class="lead">{{ app_name }} conecta tu sistema de ventas con cada máquina: cobra, confirma y reconcilia sin intervención manual.</p>

    <div class="maqs">
      <div class="maq tb"><div class="ic">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2"/><rect x="8" y="5" width="8" height="5" rx="1"/><path d="M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"/></svg>
      </div><strong>Transbank</strong><small>POS integrado por puerto COM</small></div>
      <div class="maq gn"><div class="ic">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2" width="12" height="20" rx="2"/><path d="M9 9l2 2 4-4M10 18h4"/></svg>
      </div><strong>Getnet</strong><small>POS por USB con reconexión automática</small></div>
      <div class="maq mp"><div class="ic">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="5"/><path d="M10 9a4 4 0 0 1 0 6M13 7a7 7 0 0 1 0 10"/></svg>
      </div><strong>Mercado Pago</strong><small>Terminales Point en la nube</small></div>
      <div class="maq tuu"><div class="ic">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="5" width="12" height="17" rx="2"/><path d="M8 2h8v3H8zM9 10h6M9 14h4"/></svg>
      </div><strong>TUU</strong><small>Pago remoto en máquinas PRO2</small></div>
    </div>

    <div class="firma">Una caja, todas las<br>formas de cobrar</div>
  </section>

  <div class="scene" aria-hidden="true">
    <svg viewBox="0 0 440 520" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="gCard" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f7941d"/><stop offset="1" stop-color="#e5484d"/></linearGradient>
        <linearGradient id="gScreen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#14306e"/><stop offset="1" stop-color="#0b1c45"/></linearGradient>
        <linearGradient id="gBody" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#22305a"/><stop offset="1" stop-color="#141f40"/></linearGradient>
      </defs>

      <!-- TUU: POS de mano con impresora (morado) -->
      <g class="float d3">
        <g transform="translate(36 40) rotate(-10)">
          <rect x="0" y="0" width="104" height="182" rx="18" fill="url(#gBody)" stroke="#9b6dff" stroke-width="2.5"/>
          <rect x="14" y="-12" width="76" height="22" rx="6" fill="#9b6dff"/>
          <rect x="22" y="-30" width="60" height="26" rx="2" fill="#f4f6fb"/>
          <path d="M30 -22h44M30 -14h30" stroke="#9aa4bc" stroke-width="3" stroke-linecap="round"/>
          <rect x="12" y="22" width="80" height="104" rx="8" fill="url(#gScreen)"/>
          <rect x="22" y="36" width="36" height="6" rx="3" fill="#9b6dff"/>
          <rect x="22" y="52" width="60" height="10" rx="3" fill="#e8eefc" opacity=".9"/>
          <rect x="22" y="70" width="44" height="6" rx="3" fill="#56637f"/>
          <rect x="22" y="100" width="60" height="16" rx="5" fill="#9b6dff"/>
          <circle cx="52" cy="152" r="10" fill="none" stroke="#9b6dff" stroke-width="2.5"/>
        </g>
      </g>

      <!-- Tarjeta de crédito -->
      <g class="float d2">
        <g transform="translate(176 26) rotate(12)">
          <rect x="0" y="0" width="160" height="100" rx="12" fill="url(#gCard)"/>
          <rect x="16" y="30" width="28" height="22" rx="4" fill="#ffd79a"/>
          <path d="M16 41h28M30 30v22" stroke="#d99a3a" stroke-width="1.5"/>
          <rect x="16" y="70" width="90" height="7" rx="3.5" fill="#fff" opacity=".75"/>
          <circle cx="128" cy="76" r="11" fill="#fff" opacity=".45"/><circle cx="140" cy="76" r="11" fill="#fff" opacity=".7"/>
          <g stroke="#fff" stroke-width="3" fill="none" stroke-linecap="round">
            <path d="M126 22a10 10 0 0 1 0 16"/><path d="M134 16a18 18 0 0 1 0 28"/>
          </g>
        </g>
      </g>

      <!-- Getnet: Smart POS mostrando "Aprobado" (naranja) -->
      <g class="float">
        <g transform="translate(232 150) rotate(7)">
          <rect x="0" y="0" width="140" height="262" rx="22" fill="url(#gBody)" stroke="#ff7a1a" stroke-width="2.5"/>
          <rect x="14" y="18" width="112" height="176" rx="10" fill="url(#gScreen)"/>
          <rect x="28" y="32" width="50" height="7" rx="3.5" fill="#ff7a1a"/>
          <text x="70" y="88" text-anchor="middle" font-family="Segoe UI, system-ui, sans-serif" font-size="25" font-weight="800" fill="#ffffff">$12.990</text>
          <circle cx="70" cy="128" r="20" fill="#22c55e"/>
          <path d="M60 128l7 7 13-14" stroke="#fff" stroke-width="4.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
          <text x="70" y="174" text-anchor="middle" font-family="Segoe UI, system-ui, sans-serif" font-size="13" font-weight="700" fill="#86efac">APROBADO</text>
          <rect x="44" y="216" width="52" height="16" rx="8" fill="#ff7a1a"/>
        </g>
      </g>

      <!-- Transbank: POS de mesón con teclado y boleta (rojo) -->
      <g class="float d2">
        <g transform="translate(40 252) rotate(-6)">
          <rect x="26" y="-44" width="96" height="62" rx="3" fill="#f4f6fb"/>
          <path d="M38 -32h72M38 -22h50M38 -12h64M38 -2h40" stroke="#9aa4bc" stroke-width="3.5" stroke-linecap="round"/>
          <rect x="0" y="0" width="150" height="236" rx="20" fill="url(#gBody)" stroke="#e5484d" stroke-width="2.5"/>
          <rect x="14" y="-4" width="122" height="16" rx="6" fill="#e5484d"/>
          <rect x="18" y="26" width="114" height="62" rx="8" fill="url(#gScreen)"/>
          <rect x="30" y="40" width="44" height="7" rx="3.5" fill="#e5484d"/>
          <rect x="30" y="56" width="80" height="11" rx="3" fill="#e8eefc" opacity=".9"/>
          <g fill="#2c3a68">
            <rect x="22" y="104" width="30" height="22" rx="6"/><rect x="60" y="104" width="30" height="22" rx="6"/><rect x="98" y="104" width="30" height="22" rx="6"/>
            <rect x="22" y="134" width="30" height="22" rx="6"/><rect x="60" y="134" width="30" height="22" rx="6"/><rect x="98" y="134" width="30" height="22" rx="6"/>
            <rect x="22" y="164" width="30" height="22" rx="6"/><rect x="60" y="164" width="30" height="22" rx="6"/><rect x="98" y="164" width="30" height="22" rx="6"/>
          </g>
          <rect x="22" y="194" width="30" height="22" rx="6" fill="#e5484d"/><rect x="60" y="194" width="30" height="22" rx="6" fill="#2c3a68"/><rect x="98" y="194" width="30" height="22" rx="6" fill="#22c55e"/>
        </g>
      </g>

      <!-- Mercado Pago: lector Point sin contacto (celeste) -->
      <g class="float d3">
        <g transform="translate(262 404) rotate(-8)">
          <rect x="0" y="0" width="112" height="104" rx="26" fill="#00a8e8"/>
          <rect x="6" y="6" width="100" height="92" rx="22" fill="none" stroke="#7fdcff" stroke-width="1.5" opacity=".6"/>
          <g stroke="#fff" stroke-width="5" fill="none" stroke-linecap="round">
            <path class="onda" d="M44 38a16 16 0 0 1 0 28"/>
            <path class="onda o2" d="M56 28a30 30 0 0 1 0 48"/>
            <path class="onda o3" d="M68 18a44 44 0 0 1 0 68"/>
          </g>
          <circle cx="34" cy="52" r="5" fill="#fff"/>
        </g>
      </g>
    </svg>
  </div>

  <form class="card" id="loginForm">
    <div class="brand">
      <div class="pos" aria-hidden="true">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="2.5"/><rect x="8" y="5" width="8" height="5" rx="1"/><path d="M8.5 14h.01M12 14h.01M15.5 14h.01M8.5 18h.01M12 18h.01M15.5 18h.01"/></svg>
      </div>
      <div>
        <h1>DATAMAULE</h1>
        <div class="sub">{{ app_name }}</div>
      </div>
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
    <div class="brands" aria-label="Máquinas compatibles">
      <span><i style="background:var(--tb)"></i>Transbank</span>
      <span><i style="background:var(--gn)"></i>Getnet</span>
      <span><i style="background:var(--mp)"></i>Mercado Pago</span>
      <span><i style="background:var(--tuu)"></i>TUU</span>
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
