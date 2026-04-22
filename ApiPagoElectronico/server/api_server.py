import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

import config.settings as settings
from flask import Flask, Response, jsonify, render_template_string, request

from server.auth import require_basic_auth

logger = logging.getLogger(settings.APP_NAME)


def process_mercadopago(terminal_id: str, access_token: str, amount, timeout: int):
    return {"status": "error", "message": "Mercado Pago no está habilitado en esta versión"}


_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


def _unquote_env_value(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
        return v[1:-1]
    return v


def _quote_env_value_if_needed(value: str) -> str:
    if value is None:
        return ""
    v = str(value)
    if v == "":
        return ""
    needs_quotes = any(ch.isspace() for ch in v) or any(ch in v for ch in ['"', "'", "#"])
    if not needs_quotes:
        return v
    escaped = v.replace('"', '\\"')
    return f'"{escaped}"'


def _read_env_file() -> tuple[Path, list[str], dict[str, str]]:
    env_path = settings.get_env_path()
    if not env_path.exists():
        return env_path, [], {}
    raw = env_path.read_text(encoding="utf-8", errors="ignore")
    lines = raw.splitlines(True)
    values: dict[str, str] = {}
    for line in lines:
        m = _ENV_LINE_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        val = m.group(2).rstrip("\r\n")
        values[key] = _unquote_env_value(val)
    return env_path, lines, values


def _write_env_file(env_path: Path, existing_lines: list[str], updates: dict[str, str]) -> None:
    remaining = dict(updates)
    out_lines: list[str] = []

    for line in existing_lines:
        m = _ENV_LINE_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        key = m.group(1)
        if key not in remaining:
            out_lines.append(line)
            continue
        new_val = _quote_env_value_if_needed(remaining.pop(key))
        newline = "\n"
        if line.endswith("\r\n"):
            newline = "\r\n"
        out_lines.append(f"{key}={new_val}{newline}")

    if remaining:
        if out_lines and not out_lines[-1].endswith("\n"):
            out_lines.append("\n")
        if out_lines and out_lines[-1].strip() != "":
            out_lines.append("\n")
        for key, val in remaining.items():
            out_lines.append(f"{key}={_quote_env_value_if_needed(val)}\n")

    tmp_path = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp_path.write_text("".join(out_lines), encoding="utf-8")
    os.replace(tmp_path, env_path)


def _backup_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _create_env_backup(env_path: Path) -> dict | None:
    if not env_path.exists():
        return None
    stamp = _backup_stamp()
    backup_path = env_path.with_name(f"{env_path.name}.bak-{stamp}")
    tmp_path = backup_path.with_suffix(backup_path.suffix + ".tmp")
    data = env_path.read_bytes()
    tmp_path.write_bytes(data)
    os.replace(tmp_path, backup_path)
    try:
        mtime = backup_path.stat().st_mtime
    except Exception:
        mtime = None
    return {"name": backup_path.name, "path": str(backup_path), "mtime": mtime}


def _list_env_backups(env_path: Path, limit: int = 40) -> list[dict]:
    if not env_path.parent.exists():
        return []
    prefix = f"{env_path.name}.bak-"
    backups = [p for p in env_path.parent.glob(f"{env_path.name}.bak-*") if p.is_file() and p.name.startswith(prefix)]
    backups.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    out: list[dict] = []
    for p in backups[: max(1, int(limit))]:
        try:
            st = p.stat()
            out.append({"name": p.name, "path": str(p), "mtime": st.st_mtime, "size": st.st_size})
        except Exception:
            out.append({"name": p.name, "path": str(p), "mtime": None, "size": None})
    return out


def _validate_backup_name(env_path: Path, name: str) -> Path | None:
    if not isinstance(name, str) or not name:
        return None
    if "/" in name or "\\" in name:
        return None
    prefix = f"{env_path.name}.bak-"
    if not name.startswith(prefix):
        return None
    path = env_path.with_name(name)
    if not path.exists() or not path.is_file():
        return None
    return path


class APIServer:
    def __init__(self, pos_module, getnet_module=None):
        settings.refresh_settings()
        self.app = Flask(settings.APP_NAME)
        self.pos = pos_module
        self.getnet = getnet_module

        self.queues_lock = threading.Lock()
        self.queues: dict[str, dict[str, queue.Queue]] = {}

        self.agents_lock = threading.Lock()
        self.agents: dict[str, dict[str, dict]] = {}

        self.tasks_lock = threading.Lock()
        self.tasks: dict[str, dict] = {}

        self.busy_lock = threading.Lock()
        self.busy_boxes: set[tuple[str, str]] = set()

        self._stop_local_worker = threading.Event()
        self._stop_cleanup = threading.Event()
        self.local_worker_thread = None
        self.cleanup_thread = None

        self.setup_routes()
        self.register_local_agent()
        self.start_local_worker()
        self.start_cleanup_task()

    def ensure_queue(self, id_sucursal: str, nombre_caja: str) -> queue.Queue:
        with self.queues_lock:
            if id_sucursal not in self.queues:
                self.queues[id_sucursal] = {}
            if nombre_caja not in self.queues[id_sucursal]:
                self.queues[id_sucursal][nombre_caja] = queue.Queue()
            return self.queues[id_sucursal][nombre_caja]

    def register_agent(self, id_sucursal, nombre_caja, info=None):
        with self.agents_lock:
            self.agents.setdefault(id_sucursal, {})[nombre_caja] = {
                "last_seen": time.time(),
                "info": info or {},
            }
        self.ensure_queue(id_sucursal, nombre_caja)
        with self.busy_lock:
            key = (id_sucursal, nombre_caja)
            if key in self.busy_boxes:
                with self.tasks_lock:
                    active = any(
                        t.get("id_sucursal") == id_sucursal and t.get("nombre_caja") == nombre_caja
                        for t in self.tasks.values()
                    )
                if not active:
                    self.busy_boxes.discard(key)
                    logger.info("Caja %s/%s liberada por reconexión", id_sucursal, nombre_caja)

    def register_local_agent(self):
        settings.refresh_settings()
        info = {
            "host": "local",
            "terminal_id": settings.ID_TERMINAL,
            "usa_pos_fisico": self.pos and self.pos.is_online(),
            "local": True,
        }
        self.register_agent(str(settings.ID_SUCURSAL), str(settings.NOMBRE_CAJA), info)
        logger.info("Agente local registrado %s/%s", settings.ID_SUCURSAL, settings.NOMBRE_CAJA)

    def start_local_worker(self):
        self._stop_local_worker.clear()

        def worker():
            logger.info("Local worker iniciado")
            settings.refresh_settings()
            q = self.ensure_queue(str(settings.ID_SUCURSAL), str(settings.NOMBRE_CAJA))

            while not self._stop_local_worker.is_set():
                try:
                    task = q.get(timeout=1)
                except queue.Empty:
                    continue

                try:
                    tx_id = task.get("tx_id")
                    tipo = task.get("type", "transbank")
                    custom_timeout = task.get("timeout", settings.MAX_TRANSACTION_TIME)

                    logger.info("Procesando: tx=%s, tipo=%s, timeout=%s", tx_id, tipo, custom_timeout)

                    with self.tasks_lock:
                        if tx_id in self.tasks:
                            self.tasks[tx_id]["estado"] = "PROCESANDO"

                    if tipo == "transbank":
                        amount = task.get("amount")
                        result = self.pos.do_sale_with_timeout(amount, timeout=custom_timeout)
                        logger.info(
                            "Resultado Transbank: %s",
                            result.get("status") if isinstance(result, dict) else result,
                        )
                    elif tipo == "getnet":
                        if not self.getnet:
                            result = {"status": "error", "message": "Getnet no disponible"}
                        else:
                            amount = task.get("amount")
                            result = self.getnet.do_sale_with_timeout(amount, timeout=custom_timeout)
                            logger.info("Resultado Getnet: %s", result.get("status"))
                    elif tipo == "mercadopago":
                        terminal_id = task.get("id_terminal") or settings.ID_TERMINAL
                        access_token = task.get("access_token")
                        amount = task.get("amount")
                        result = process_mercadopago(terminal_id, access_token, amount, timeout=custom_timeout)
                    else:
                        result = {"status": "error", "message": "Tipo no soportado"}

                    with self.tasks_lock:
                        entry = self.tasks.get(tx_id)
                        if entry:
                            entry["result"] = result
                            if result.get("status") == "success":
                                entry["estado"] = "APROBADO"
                            elif result.get("status") == "error":
                                entry["estado"] = "ERROR"
                            elif result.get("status") == "timeout":
                                entry["estado"] = "TIMEOUT"
                            else:
                                entry["estado"] = "RECHAZADO"
                            entry["event"].set()
                            logger.info("Worker completó: tx=%s, estado=%s", tx_id, entry["estado"])

                    with self.busy_lock:
                        self.busy_boxes.discard((task.get("id_sucursal"), task.get("nombre_caja")))

                except Exception as e:
                    logger.error("Error en local worker: %s\n%s", e, traceback.format_exc())
                    try:
                        with self.tasks_lock:
                            if tx_id in self.tasks:
                                self.tasks[tx_id]["result"] = {"status": "error", "message": str(e)}
                                self.tasks[tx_id]["estado"] = "ERROR"
                                self.tasks[tx_id]["event"].set()
                    except Exception:
                        pass

        self.local_worker_thread = threading.Thread(target=worker, daemon=True)
        self.local_worker_thread.start()
        logger.info("Local worker thread iniciado")

    def stop_local_worker(self):
        self._stop_local_worker.set()
        try:
            if self.local_worker_thread:
                self.local_worker_thread.join(timeout=1)
        except Exception:
            pass
        logger.info("Local worker detenido")

    def start_cleanup_task(self):
        self._stop_cleanup.clear()

        def cleanup():
            while not self._stop_cleanup.is_set():
                time.sleep(300)
                try:
                    now = time.time()
                    max_age = 600
                    with self.tasks_lock:
                        to_delete = [
                            tx_id
                            for tx_id, task in self.tasks.items()
                            if now - task.get("timestamp", now) > max_age
                        ]
                        for tx_id in to_delete:
                            self.tasks.pop(tx_id, None)
                    if to_delete:
                        logger.info("Limpieza automática: %d transacciones eliminadas", len(to_delete))
                except Exception as e:
                    logger.error("Error en cleanup task: %s", e)

        self.cleanup_thread = threading.Thread(target=cleanup, daemon=True)
        self.cleanup_thread.start()
        logger.info("Tarea de limpieza automática iniciada")

    def stop_cleanup_task(self):
        self._stop_cleanup.set()
        try:
            if self.cleanup_thread:
                self.cleanup_thread.join(timeout=1)
        except Exception:
            pass
        logger.info("Tarea de limpieza detenida")

    def setup_routes(self):
        app = self.app

        panel_html = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Panel de Administración</title>
  <style>
    :root{
      --bg:#0b1020;
      --panel:#0f1730;
      --panel2:#111b3a;
      --border:rgba(255,255,255,.08);
      --text:#e9eefc;
      --muted:rgba(233,238,252,.72);
      --brand:#6ee7ff;
      --brand2:#a78bfa;
      --good:#34d399;
      --bad:#fb7185;
      --warn:#fbbf24;
      --shadow:0 14px 50px rgba(0,0,0,.45);
      --radius:16px;
      --radius2:12px;
      --font: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Noto Sans", "Liberation Sans", sans-serif;
    }
    *{box-sizing:border-box}
    html,body{height:100%}
    body{
      margin:0;
      font-family:var(--font);
      background:
        radial-gradient(900px 500px at 10% 10%, rgba(110,231,255,.16), transparent 60%),
        radial-gradient(900px 500px at 90% 0%, rgba(167,139,250,.18), transparent 60%),
        radial-gradient(900px 500px at 70% 90%, rgba(52,211,153,.10), transparent 60%),
        var(--bg);
      color:var(--text);
    }
    .layout{display:grid; grid-template-columns: 280px 1fr; min-height:100vh}
    .sidebar{
      padding:22px 18px;
      border-right:1px solid var(--border);
      background: linear-gradient(180deg, rgba(255,255,255,.03), transparent 40%);
    }
    .brand{
      display:flex; align-items:center; gap:10px;
      padding:12px 12px;
      border:1px solid var(--border);
      border-radius:14px;
      background:rgba(255,255,255,.03);
      box-shadow: var(--shadow);
    }
    .logo{
      width:40px;height:40px;border-radius:12px;
      background: linear-gradient(135deg, var(--brand), var(--brand2));
      filter:saturate(1.1);
    }
    .brand h1{font-size:14px;margin:0}
    .brand p{margin:0; font-size:12px; color:var(--muted)}
    .nav{margin-top:16px; display:flex; flex-direction:column; gap:8px}
    .nav a{
      text-decoration:none; color:var(--text);
      padding:10px 12px;
      border-radius:12px;
      border:1px solid transparent;
      background:rgba(255,255,255,.02);
    }
    .nav a.active{
      border-color:var(--border);
      background:rgba(110,231,255,.08);
    }
    .content{padding:26px 26px 40px}
    .topbar{
      display:flex; align-items:center; justify-content:space-between; gap:16px;
      margin-bottom:18px;
    }
    .title h2{margin:0; font-size:22px; letter-spacing:.2px}
    .title .sub{margin-top:6px; color:var(--muted); font-size:13px}
    .actions{display:flex; gap:10px; align-items:center}
    .btn{
      border:1px solid var(--border);
      background:rgba(255,255,255,.04);
      color:var(--text);
      padding:10px 12px;
      border-radius:12px;
      cursor:pointer;
      font-weight:600;
    }
    .btn.primary{
      background: linear-gradient(135deg, rgba(110,231,255,.18), rgba(167,139,250,.18));
      border-color: rgba(110,231,255,.25);
    }
    .grid{display:grid; grid-template-columns: repeat(12, 1fr); gap:14px}
    .card{
      grid-column: span 12;
      border:1px solid var(--border);
      background:rgba(255,255,255,.03);
      border-radius:var(--radius);
      box-shadow: var(--shadow);
      overflow:hidden;
    }
    .card .hd{
      padding:14px 16px;
      display:flex; align-items:center; justify-content:space-between; gap:12px;
      background: linear-gradient(180deg, rgba(255,255,255,.05), transparent);
      border-bottom:1px solid var(--border);
    }
    .card .hd .k{font-weight:800; letter-spacing:.2px}
    .card .bd{padding:16px}
    .row{display:grid; grid-template-columns: 1.1fr 1.9fr; gap:10px; align-items:center; padding:10px 0; border-bottom:1px dashed rgba(255,255,255,.08)}
    .row:last-child{border-bottom:none}
    label{font-size:13px; color:var(--muted)}
    input, select, textarea{
      width:100%;
      padding:10px 12px;
      border-radius:12px;
      border:1px solid rgba(255,255,255,.10);
      background:rgba(15,23,48,.65);
      color:var(--text);
      outline:none;
    }
    input:focus, select:focus, textarea:focus{border-color:rgba(110,231,255,.35); box-shadow:0 0 0 3px rgba(110,231,255,.12)}
    .hint{margin-top:6px; color:var(--muted); font-size:12px}
    .pill{
      display:inline-flex; align-items:center; gap:8px;
      border:1px solid var(--border);
      padding:8px 10px;
      border-radius:999px;
      color:var(--muted);
      font-size:12px;
      background:rgba(255,255,255,.03);
    }
    .toast{
      position: fixed;
      right: 18px;
      bottom: 18px;
      padding: 12px 14px;
      border-radius: 14px;
      border:1px solid var(--border);
      background: rgba(15,23,48,.92);
      box-shadow: var(--shadow);
      display:none;
      max-width: 420px;
    }
    .toast.show{display:block}
    .toast.ok{border-color: rgba(52,211,153,.35)}
    .toast.err{border-color: rgba(251,113,133,.35)}
    .split{display:flex; gap:10px; align-items:center}
    .kbd{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size: 12px;
      color: var(--text);
      background: rgba(255,255,255,.04);
      border: 1px solid var(--border);
      padding: 2px 8px;
      border-radius: 999px;
    }
    @media (max-width: 980px){
      .layout{grid-template-columns: 1fr}
      .sidebar{border-right:none; border-bottom:1px solid var(--border)}
      .row{grid-template-columns:1fr}
      .actions{flex-wrap:wrap; justify-content:flex-start}
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">
        <div class="logo"></div>
        <div>
          <h1>ApiPagoElectronico</h1>
          <p>Panel de administración</p>
        </div>
      </div>
      <nav class="nav">
        <a class="active" href="/panel">Configuración</a>
        <a href="/status" target="_blank" rel="noreferrer">Status JSON</a>
      </nav>
      <div style="margin-top:14px">
        <div class="pill"><span>Ruta</span><span class="kbd">/panel</span></div>
      </div>
    </aside>
    <main class="content">
      <div class="topbar">
        <div class="title">
          <h2>Configuración (.env)</h2>
          <div class="sub" id="envPath">Cargando…</div>
        </div>
        <div class="actions">
          <button class="btn" id="btnReload">Recargar</button>
          <button class="btn" id="btnRestart">Reiniciar servicio</button>
          <button class="btn primary" id="btnSave">Guardar cambios</button>
        </div>
      </div>

      <div class="grid">
        <section class="card" id="restartCard" style="grid-column: span 12; display:none;">
          <div class="hd">
            <div class="k">Acciones</div>
            <div class="pill" id="restartPill">Acción requerida</div>
          </div>
          <div class="bd">
            <div class="hint" id="restartText"></div>
            <div class="split" style="margin-top:12px">
              <button class="btn primary" id="btnRestart2">Reiniciar ahora</button>
              <div class="pill"><span>Tip</span><span class="kbd">HTTP_PORT</span><span>requiere reinicio</span></div>
            </div>
          </div>
        </section>

        <section class="card" style="grid-column: span 12;">
          <div class="hd">
            <div class="k">Servicio</div>
            <div class="pill" id="runningPort">Puerto actual: …</div>
          </div>
          <div class="bd" id="serviceForm"></div>
        </section>

        <section class="card" style="grid-column: span 12;">
          <div class="hd">
            <div class="k">Dispositivos</div>
          </div>
          <div class="bd" id="devicesForm"></div>
        </section>

        <section class="card" style="grid-column: span 12;">
          <div class="hd">
            <div class="k">TimeOuts</div>
          </div>
          <div class="bd" id="timeoutsForm"></div>
        </section>

        <section class="card" style="grid-column: span 12;">
          <div class="hd">
            <div class="k">Seguridad (Basic Auth)</div>
          </div>
          <div class="bd" id="authForm"></div>
        </section>

        <section class="card" style="grid-column: span 12;">
          <div class="hd">
            <div class="k">Mercado Pago</div>
          </div>
          <div class="bd" id="mpForm"></div>
        </section>

        <section class="card" style="grid-column: span 12;">
          <div class="hd">
            <div class="k">Backups</div>
            <div class="pill" id="backupInfo">—</div>
          </div>
          <div class="bd">
            <div class="row">
              <div>
                <label>Backups disponibles</label>
                <div class="hint">Se crea un backup automático antes de cada guardado.</div>
              </div>
              <div>
                <div class="split">
                  <select id="backupSelect"></select>
                  <button class="btn" id="btnBackupRefresh">Actualizar</button>
                </div>
                <div class="split" style="margin-top:10px">
                  <button class="btn" id="btnBackupCreate">Crear backup ahora</button>
                  <button class="btn primary" id="btnBackupRestore">Restaurar seleccionado</button>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  </div>

  <div class="toast" id="toast"></div>

  <script>
    const KNOWN_SECTIONS = {
      service: document.getElementById("serviceForm"),
      devices: document.getElementById("devicesForm"),
      timeouts: document.getElementById("timeoutsForm"),
      auth: document.getElementById("authForm"),
      mp: document.getElementById("mpForm"),
    };

    const toastEl = document.getElementById("toast");
    function toast(msg, ok=true){
      toastEl.textContent = msg;
      toastEl.className = "toast show " + (ok ? "ok" : "err");
      setTimeout(()=>{ toastEl.className = "toast"; }, 3200);
    }

    function row(label, inputEl, hint){
      const wrap = document.createElement("div");
      wrap.className = "row";
      const left = document.createElement("div");
      const lab = document.createElement("label");
      lab.textContent = label;
      left.appendChild(lab);
      if (hint){
        const h = document.createElement("div");
        h.className = "hint";
        h.textContent = hint;
        left.appendChild(h);
      }
      const right = document.createElement("div");
      right.appendChild(inputEl);
      wrap.appendChild(left);
      wrap.appendChild(right);
      return wrap;
    }

    function inputFor(field, value){
      if (field.type === "boolean"){
        const sel = document.createElement("select");
        const a = document.createElement("option");
        a.value = "true"; a.textContent = "true";
        const b = document.createElement("option");
        b.value = "false"; b.textContent = "false";
        sel.appendChild(a); sel.appendChild(b);
        sel.value = (String(value).toLowerCase() === "true") ? "true" : "false";
        sel.dataset.key = field.key;
        return sel;
      }
      const inp = document.createElement("input");
      inp.type = field.type === "password" ? "password" : (field.type || "text");
      inp.value = value ?? "";
      inp.placeholder = field.placeholder || "";
      inp.dataset.key = field.key;
      return inp;
    }

    function clearForms(){
      Object.values(KNOWN_SECTIONS).forEach(el => el.innerHTML = "");
    }

    let current = null;
    let lastRestartRequired = false;
    let lastNewPort = null;

    function setRestartNotice(show, text, newPort){
      const card = document.getElementById("restartCard");
      const msg = document.getElementById("restartText");
      if (!card || !msg) return;
      if (!show){
        card.style.display = "none";
        msg.textContent = "";
        return;
      }
      card.style.display = "";
      msg.textContent = text || "Se requiere reinicio para aplicar cambios.";
      lastNewPort = newPort ?? null;
    }

    async function loadBackups(){
      const res = await fetch("/panel/api/backups", { headers: { "Accept": "application/json" } });
      const data = await res.json().catch(()=> ({}));
      if (!res.ok){
        toast(data.error || ("No se pudieron cargar backups (" + res.status + ")"), false);
        return;
      }
      const sel = document.getElementById("backupSelect");
      const info = document.getElementById("backupInfo");
      if (!sel) return;
      sel.innerHTML = "";
      const list = data.backups || [];
      if (info) info.textContent = list.length ? (list.length + " backups") : "Sin backups";
      if (!list.length){
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "Sin backups disponibles";
        sel.appendChild(opt);
        return;
      }
      for (const b of list){
        const opt = document.createElement("option");
        opt.value = b.name;
        opt.textContent = b.name;
        sel.appendChild(opt);
      }
    }

    async function createBackup(){
      const res = await fetch("/panel/api/backups/create", {
        method: "POST",
        headers: { "Accept":"application/json" }
      });
      const data = await res.json().catch(()=> ({}));
      if (!res.ok){
        toast(data.error || ("No se pudo crear backup (" + res.status + ")"), false);
        return;
      }
      toast("Backup creado: " + (data.backup?.name || ""), true);
      await loadBackups();
    }

    async function restoreBackup(){
      const sel = document.getElementById("backupSelect");
      const name = sel ? sel.value : "";
      if (!name){
        toast("Selecciona un backup", false);
        return;
      }
      if (!confirm("¿Restaurar " + name + " y sobreescribir el .env actual?")){
        return;
      }
      const res = await fetch("/panel/api/backups/restore", {
        method: "POST",
        headers: { "Content-Type":"application/json", "Accept":"application/json" },
        body: JSON.stringify({ name })
      });
      const data = await res.json().catch(()=> ({}));
      if (!res.ok){
        toast(data.error || ("No se pudo restaurar (" + res.status + ")"), false);
        return;
      }
      toast("Restaurado: " + name, true);
      await load();
      await loadBackups();
      if (data.restart_required){
        setRestartNotice(true, "Restauración aplicada. Reinicia el servicio para aplicar el nuevo puerto.", data.new_port);
      } else {
        setRestartNotice(false);
      }
    }

    async function restartService(){
      const res = await fetch("/panel/api/restart", {
        method: "POST",
        headers: { "Accept":"application/json" }
      });
      const data = await res.json().catch(()=> ({}));
      if (!res.ok){
        toast(data.error || ("No se pudo reiniciar (" + res.status + ")"), false);
        return;
      }
      toast("Reiniciando servicio...", true);
      const targetPort = lastNewPort || (current?.values?.HTTP_PORT ? parseInt(current.values.HTTP_PORT, 10) : null);
      setTimeout(()=>{
        if (targetPort && Number.isFinite(targetPort)){
          const proto = window.location.protocol;
          const host = window.location.hostname;
          window.location.href = proto + "//" + host + ":" + targetPort + "/panel";
          return;
        }
        window.location.reload();
      }, 1500);
    }

    async function load(){
      const res = await fetch("/panel/api/env", { headers: { "Accept": "application/json" } });
      if (!res.ok){
        toast("No se pudo cargar .env (" + res.status + ")", false);
        return;
      }
      current = await res.json();
      document.getElementById("envPath").textContent = current.env_path || "";
      document.getElementById("runningPort").textContent = "Puerto actual: " + (current.running_port ?? "—");
      setRestartNotice(false);

      clearForms();
      for (const field of current.known_fields){
        const value = (current.values && (field.key in current.values)) ? current.values[field.key] : "";
        const inp = inputFor(field, value);
        const sectionEl = KNOWN_SECTIONS[field.section] || KNOWN_SECTIONS.service;
        sectionEl.appendChild(row(field.label, inp, field.hint));
      }
    }

    function collectUpdates(){
      const inputs = document.querySelectorAll("[data-key]");
      const updates = {};
      inputs.forEach(el => {
        const key = el.dataset.key;
        updates[key] = el.value;
      });
      return updates;
    }

    async function save(){
      const updates = collectUpdates();
      const res = await fetch("/panel/api/env", {
        method: "POST",
        headers: { "Content-Type":"application/json", "Accept":"application/json" },
        body: JSON.stringify({ values: updates })
      });
      const data = await res.json().catch(()=> ({}));
      if (!res.ok){
        toast(data.error || ("Error guardando (" + res.status + ")"), false);
        return;
      }
      toast("Cambios guardados");
      await load();
      if (data.restart_required){
        setRestartNotice(true, "Guardado. Para aplicar el nuevo puerto, reinicia el servicio.", data.new_port);
        toast("Guardado. Se requiere reinicio.", true);
      } else {
        setRestartNotice(false);
      }
      if (data.backup && data.backup.name){
        toast("Backup creado: " + data.backup.name, true);
        await loadBackups();
      }
    }

    document.getElementById("btnReload").addEventListener("click", load);
    document.getElementById("btnSave").addEventListener("click", save);
    document.getElementById("btnRestart").addEventListener("click", restartService);
    document.getElementById("btnRestart2").addEventListener("click", restartService);
    document.getElementById("btnBackupRefresh").addEventListener("click", loadBackups);
    document.getElementById("btnBackupCreate").addEventListener("click", createBackup);
    document.getElementById("btnBackupRestore").addEventListener("click", restoreBackup);
    load().then(loadBackups).catch(e => toast("Error: " + e, false));
  </script>
</body>
</html>
        """

        known_fields = [
            {
                "section": "service",
                "key": "HTTP_PORT",
                "label": "Puerto HTTP",
                "type": "number",
                "hint": "El panel corre en /panel dentro del mismo puerto. Cambiar el puerto requiere reiniciar el servicio.",
                "placeholder": "5005",
            },
            {
                "section": "service",
                "key": "ID_SUCURSAL",
                "label": "ID Sucursal",
                "type": "number",
                "hint": "Debe coincidir con las peticiones entrantes.",
            },
            {
                "section": "service",
                "key": "NOMBRE_CAJA",
                "label": "Nombre Caja",
                "type": "text",
                "hint": "Debe coincidir con las peticiones entrantes.",
            },
            {
                "section": "service",
                "key": "TERMINAL_ID",
                "label": "Terminal ID",
                "type": "text",
                "hint": "Para validación del terminal en Transbank/Getnet.",
            },
            {
                "section": "devices",
                "key": "USAR_POS_FISICO",
                "label": "Usar POS Físico (Transbank)",
                "type": "boolean",
            },
            {
                "section": "devices",
                "key": "PUERTOS_COM",
                "label": "Puertos COM (separados por coma)",
                "type": "text",
                "placeholder": "COM6,COM7 o /dev/ttyACM0,...",
            },
            {
                "section": "devices",
                "key": "USAR_GETNET",
                "label": "Usar Getnet",
                "type": "boolean",
            },
            {
                "section": "timeouts",
                "key": "MAX_TRANSACTION_TIME",
                "label": "Máximo transacción (seg)",
                "type": "number",
            },
            {
                "section": "timeouts",
                "key": "TIMEOUT_SERVER",
                "label": "Timeout servidor (seg)",
                "type": "number",
            },
            {
                "section": "auth",
                "key": "API_AUTH_USER",
                "label": "Usuario Basic Auth",
                "type": "text",
            },
            {
                "section": "auth",
                "key": "API_AUTH_PASS",
                "label": "Clave Basic Auth",
                "type": "password",
            },
            {
                "section": "mp",
                "key": "ALLOWED_MP",
                "label": "Cajas autorizadas (ALLOWED_MP)",
                "type": "text",
                "hint": "Formato: id_sucursal:nombre_caja,id_sucursal:nombre_caja. Vacío permite todas.",
            },
            {
                "section": "mp",
                "key": "MP_API_URL",
                "label": "MP API URL",
                "type": "url",
            },
        ]

        @app.route("/panel", methods=["GET"])
        @require_basic_auth
        def http_panel():
            return render_template_string(panel_html)

        @app.route("/panel/api/env", methods=["GET"])
        @require_basic_auth
        def http_panel_env_get():
            env_path, _lines, values = _read_env_file()
            running_port = settings.HTTP_PORT
            return jsonify(
                {
                    "env_path": str(env_path),
                    "running_port": running_port,
                    "known_fields": known_fields,
                    "values": values,
                }
            )

        @app.route("/panel/api/env", methods=["POST"])
        @require_basic_auth
        def http_panel_env_post():
            payload = request.get_json(force=True, silent=True) or {}
            values = payload.get("values")
            if not isinstance(values, dict):
                return jsonify({"error": "Body inválido"}), 400

            env_path, lines, current_values = _read_env_file()
            old_running_port = settings.HTTP_PORT
            updates: dict[str, str] = {}
            for k, v in values.items():
                if not isinstance(k, str):
                    continue
                if v is None:
                    v = ""
                updates[k] = str(v)

            for key in ("USAR_POS_FISICO", "USAR_GETNET"):
                if key in updates:
                    updates[key] = "true" if updates[key].strip().lower() == "true" else "false"

            new_port = None
            if "HTTP_PORT" in updates:
                try:
                    new_port = int(updates["HTTP_PORT"])
                except Exception:
                    return jsonify({"error": "HTTP_PORT debe ser numérico"}), 400

            backup = _create_env_backup(env_path)
            _write_env_file(env_path, lines, updates)
            settings.reload_env()
            restart_required = settings.HTTP_PORT != old_running_port

            if "TERMINAL_ID" in updates and "ID_TERMINAL" in current_values and "TERMINAL_ID" not in current_values:
                pass

            return jsonify({"status": "ok", "restart_required": restart_required, "new_port": new_port, "backup": backup})

        @app.route("/panel/api/backups", methods=["GET"])
        @require_basic_auth
        def http_panel_backups_get():
            env_path, _lines, _values = _read_env_file()
            return jsonify({"env_path": str(env_path), "backups": _list_env_backups(env_path)})

        @app.route("/panel/api/backups/create", methods=["POST"])
        @require_basic_auth
        def http_panel_backups_create():
            env_path, _lines, _values = _read_env_file()
            backup = _create_env_backup(env_path)
            if not backup:
                return jsonify({"error": "No existe .env para respaldar"}), 400
            return jsonify({"status": "ok", "backup": backup})

        @app.route("/panel/api/backups/restore", methods=["POST"])
        @require_basic_auth
        def http_panel_backups_restore():
            payload = request.get_json(force=True, silent=True) or {}
            name = payload.get("name")
            env_path, lines, _values = _read_env_file()
            backup_path = _validate_backup_name(env_path, name)
            if not backup_path:
                return jsonify({"error": "Backup inválido"}), 400

            old_running_port = settings.HTTP_PORT
            pre_backup = _create_env_backup(env_path)

            data = backup_path.read_bytes()
            tmp_path = env_path.with_suffix(env_path.suffix + ".tmp")
            tmp_path.write_bytes(data)
            os.replace(tmp_path, env_path)

            settings.reload_env()
            restart_required = settings.HTTP_PORT != old_running_port
            return jsonify(
                {
                    "status": "ok",
                    "restored_from": backup_path.name,
                    "backup_before_restore": pre_backup,
                    "restart_required": restart_required,
                    "new_port": settings.HTTP_PORT,
                }
            )

        @app.route("/panel/api/restart", methods=["POST"])
        @require_basic_auth
        def http_panel_restart():
            try:
                from core.singleton import cleanup_lock
            except Exception:
                cleanup_lock = None

            def do_restart():
                try:
                    try:
                        self.stop_local_worker()
                    except Exception:
                        pass
                    try:
                        self.stop_cleanup_task()
                    except Exception:
                        pass
                    try:
                        if cleanup_lock:
                            cleanup_lock()
                    except Exception:
                        pass

                    frozen = bool(getattr(sys, "frozen", False)) or Path(sys.executable).suffix.lower() == ".exe"
                    if frozen:
                        cmd = [sys.executable] + sys.argv[1:]
                    else:
                        cmd = [sys.executable, sys.argv[0]] + sys.argv[1:]

                    cwd = str(settings.get_base_dir())
                    subprocess.Popen(cmd, cwd=cwd, close_fds=True)
                except Exception:
                    pass
                time.sleep(0.2)
                os._exit(0)

            threading.Thread(target=do_restart, daemon=True).start()
            return jsonify({"status": "restarting"})

        @app.route("/register_agent", methods=["POST"])
        def http_register_agent():
            data = request.get_json(force=True, silent=True) or {}
            id_sucursal = data.get("id_sucursal")
            nombre_caja = data.get("nombre_caja")
            meta = data.get("meta", {})

            if not id_sucursal or not nombre_caja:
                return jsonify({"error": "id_sucursal y nombre_caja requeridos"}), 400

            id_s = str(id_sucursal)
            nc = str(nombre_caja)

            self.register_agent(id_s, nc, meta)
            logger.info("Agent registered: %s/%s meta=%s", id_s, nc, meta)
            return jsonify({"status": "ok"})

        @app.route("/poll", methods=["GET"])
        def http_poll():
            id_sucursal = request.args.get("id_sucursal")
            nombre_caja = request.args.get("nombre_caja")
            if not id_sucursal or not nombre_caja:
                return jsonify({"error": "id_sucursal y nombre_caja requeridos"}), 400

            self.register_agent(str(id_sucursal), str(nombre_caja))
            q = self.ensure_queue(str(id_sucursal), str(nombre_caja))
            try:
                task = q.get(timeout=2)
                logger.info("Despachando tarea -> %s/%s tx=%s", id_sucursal, nombre_caja, task.get("tx_id"))
                return jsonify({"task": task})
            except queue.Empty:
                return jsonify({"task": None, "heartbeat": True})

        @app.route("/result", methods=["POST"])
        def http_result():
            data = request.get_json(force=True, silent=True) or {}
            tx_id = data.get("tx_id")
            result = data.get("result")
            if not tx_id or result is None:
                return jsonify({"error": "tx_id y result requeridos"}), 400

            with self.tasks_lock:
                task = self.tasks.get(tx_id)
                if not task:
                    logger.warning("Resultado para tx_id desconocido: %s", tx_id)
                    return jsonify({"status": "unknown_tx"}), 404
                if task.get("result") is not None:
                    logger.warning("Resultado duplicado tx=%s", tx_id)
                    return jsonify({"status": "already_processed"}), 200
                task["result"] = result
                task["event"].set()

            logger.info("Resultado guardado tx=%s: %s", tx_id, result)
            try:
                id_s = task.get("id_sucursal")
                nc = task.get("nombre_caja")
                with self.busy_lock:
                    self.busy_boxes.discard((id_s, nc))
            except Exception:
                pass

            return jsonify({"status": "ok"})

        @app.route("/pago/refund", methods=["POST"])
        def refund():
            try:
                data = request.get_json()

                if not data or "operation_id" not in data:
                    return (
                        jsonify({"status": "error", "message": "Falta el campo 'operation_id' en el body"}),
                        400,
                    )

                operation_id = data["operation_id"]

                try:
                    operation_id = int(operation_id)
                except (ValueError, TypeError):
                    return jsonify({"status": "error", "message": "operation_id debe ser un número válido"}), 400

                logger.info("Solicitud de anulación recibida: operation_id=%s", operation_id)

                result = self.pos.do_refund_with_timeout(operation_id)

                if result["status"] == "success":
                    return jsonify(result), 200
                if result["status"] == "failed":
                    return jsonify(result), 400
                return jsonify(result), 500

            except Exception as e:
                logger.exception("Error en endpoint /pago/refund: %s", e)
                return jsonify({"status": "error", "message": str(e)}), 500

        @app.route("/pago/detalle", methods=["POST", "GET"])
        def detalle():
            try:
                print_on_pos = False

                if request.method == "POST":
                    data = request.get_json(silent=True) or {}
                    print_on_pos = data.get("print_on_pos", False)
                else:
                    print_on_pos = request.args.get("print_on_pos", "false").lower() == "true"

                logger.info("Solicitud de detalle recibida: print_on_pos=%s", print_on_pos)

                result = self.pos.do_details_with_timeout(print_on_pos)

                if result["status"] == "success":
                    return jsonify(result), 200
                if result["status"] == "failed":
                    return jsonify(result), 400
                return jsonify(result), 500

            except Exception as e:
                logger.exception("Error en endpoint /pago/detalle: %s", e)
                return jsonify({"status": "error", "message": str(e)}), 500

        @app.route("/pago", methods=["POST"])
        @require_basic_auth
        def http_pago():
            settings.refresh_settings()
            data = request.get_json(force=True, silent=True) or {}
            id_sucursal = str(data.get("id_sucursal"))
            nombre_caja = str(data.get("nombre_caja"))
            pos_type = data.get("type")
            logger.info(
                "[HTTP /pago] Petición: id_sucursal=%s, nombre_caja=%s, type=%s",
                id_sucursal,
                nombre_caja,
                pos_type,
            )

            if id_sucursal != str(settings.ID_SUCURSAL) or nombre_caja != str(settings.NOMBRE_CAJA):
                return (
                    jsonify(
                        {
                            "status": "forbidden",
                            "message": "El id_sucursal y/o el nombre_caja no coinciden con las credenciales de las configuraciones.",
                        }
                    ),
                    403,
                )

            if not id_sucursal or not nombre_caja or not pos_type:
                return jsonify({"error": "Es necesario el id_sucursal, nombre_caja y el tipo(type) de pos"}), 400

            is_local = id_sucursal == str(settings.ID_SUCURSAL) and nombre_caja == str(settings.NOMBRE_CAJA)

            custom_timeout = data.get("timeout")
            if custom_timeout:
                try:
                    timeout = int(custom_timeout)
                    timeout = max(30, min(timeout, 300))
                    logger.info("Timeout personalizado: %s segundos", timeout)
                except (ValueError, TypeError):
                    logger.warning("Timeout inválido, usando default")
                    timeout = settings.TIMEOUT_SERVER
            else:
                timeout = settings.TIMEOUT_SERVER

            if pos_type == "getnet":
                if not self.getnet:
                    return jsonify({"error": "POS Getnet no habilitado en esta máquina"}), 503

                terminal_id = data.get("terminal_id")
                amount = data.get("amount")
                custom_timeout = data.get("timeout")

                if terminal_id is None:
                    return jsonify({"error": "Es necesario el terminal_id de Getnet"}), 400
                if amount is None:
                    return jsonify({"error": "Es necesario el motno de venta de Getnet"}), 400
                if custom_timeout is None:
                    return jsonify({"error": "Es necesario el timeout de Getnet"}), 400

                try:
                    timeout = int(custom_timeout)
                    timeout = max(30, min(timeout, 300))
                    logger.info("Timeout Getnet: %s segundos", timeout)
                except (ValueError, TypeError):
                    return jsonify({"error": "timeout debe ser un número válido"}), 400

                if terminal_id != settings.ID_TERMINAL:
                    return jsonify({"status": "forbidden", "message": "El terminal_id no coincide con las credenciales de configuración"}), 403

                key = (id_sucursal, nombre_caja)
                with self.busy_lock:
                    if key in self.busy_boxes:
                        logger.warning("Caja ocupada: %s/%s", id_sucursal, nombre_caja)
                        return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                    self.busy_boxes.add(key)

                tx_id = str(uuid.uuid4())

                with self.tasks_lock:
                    event = threading.Event()
                    self.tasks[tx_id] = {
                        "event": event,
                        "result": None,
                        "estado": "PENDIENTE",
                        "id_sucursal": id_sucursal,
                        "nombre_caja": nombre_caja,
                        "timestamp": time.time(),
                        "timeout": timeout,
                    }

                task_payload = {
                    "tx_id": tx_id,
                    "id_sucursal": id_sucursal,
                    "nombre_caja": nombre_caja,
                    "type": "getnet",
                    "terminal_id": terminal_id,
                    "amount": amount,
                    "timeout": timeout,
                }

                q = self.ensure_queue(id_sucursal, nombre_caja)
                q.put(task_payload)
                logger.info("Tarea Getnet encolada: tx=%s", tx_id)

                start_wait = time.time()
                finished = event.wait(timeout=timeout)
                wait_time = time.time() - start_wait

                if not finished:
                    with self.tasks_lock:
                        self.tasks.pop(tx_id, None)
                    self.internal_free_box(id_sucursal, nombre_caja, "timeout")
                    logger.error("TIMEOUT Getnet tx=%s después de %.2fs", tx_id, wait_time)
                    return jsonify({"status": "timeout", "transaction_id": tx_id, "message": f"Timeout {timeout}s excedido"}), 504

                with self.tasks_lock:
                    entry = self.tasks.pop(tx_id, {})
                    res = entry.get("result")
                    final_estado = entry.get("estado", "DESCONOCIDO")

                with self.busy_lock:
                    self.busy_boxes.discard(key)

                logger.info("Respuesta Getnet: tx=%s, estado=%s, tiempo=%.2fs", tx_id, final_estado, wait_time)

                return jsonify(
                    {
                        "transaction_id": tx_id,
                        "result": res,
                        "estado": final_estado,
                        "tiempo_total": round(wait_time, 2),
                    }
                )

            if pos_type == "transbank":
                terminal_id_transbank = data.get("terminal_id")
                amount_transbank = data.get("amount")
                timeout_transbank = data.get("timeout")

                if terminal_id_transbank is None:
                    return jsonify({"error": "Es necesario el terminal_id de Transbank"}), 400
                if amount_transbank is None:
                    return jsonify({"error": "Es necesario el monto de venta de Transbank"}), 400
                if timeout_transbank is None:
                    return jsonify({"error": "Es necesario el timeout de Transbank"}), 400

                try:
                    timeout_transbank = int(timeout_transbank)
                    timeout_transbank = max(30, min(timeout_transbank, 300))
                    logger.info("Timeout Transbank: %s segundos", timeout_transbank)
                except (ValueError, TypeError):
                    return jsonify({"error": "timeout debe ser un número válido"}), 400

                if terminal_id_transbank != settings.ID_TERMINAL:
                    return jsonify({"status": "forbidden", "message": "El terminal_id no coincide con las credenciales de configuración"}), 403

                key = (id_sucursal, nombre_caja)
                with self.busy_lock:
                    if key in self.busy_boxes:
                        logger.warning("Caja ocupada: %s/%s", id_sucursal, nombre_caja)
                        return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                    self.busy_boxes.add(key)

                tx_id = str(uuid.uuid4())

                with self.tasks_lock:
                    event = threading.Event()
                    self.tasks[tx_id] = {
                        "event": event,
                        "result": None,
                        "estado": "PENDIENTE",
                        "id_sucursal": id_sucursal,
                        "nombre_caja": nombre_caja,
                        "timestamp": time.time(),
                        "timeout": timeout_transbank,
                    }

                task_payload = {
                    "tx_id": tx_id,
                    "id_sucursal": id_sucursal,
                    "nombre_caja": nombre_caja,
                    "type": "transbank",
                    "id_terminal": terminal_id_transbank,
                    "amount": amount_transbank,
                    "timeout": timeout_transbank,
                }

                q = self.ensure_queue(id_sucursal, nombre_caja)
                q.put(task_payload)
                logger.info("Tarea encolada: tx=%s, timeout=%s, local=%s", tx_id, timeout_transbank, is_local)

                start_wait = time.time()
                finished = event.wait(timeout=timeout_transbank)
                wait_time = time.time() - start_wait

                if not finished:
                    with self.tasks_lock:
                        self.tasks.pop(tx_id, None)
                    self.internal_free_box(id_sucursal, nombre_caja, "timeout")
                    logger.error("TIMEOUT tx=%s después de %.2fs", tx_id, wait_time)
                    return jsonify({"status": "timeout", "transaction_id": tx_id, "message": f"Timeout {timeout}s excedido (esperó {wait_time:.1f}s)"}), 504

                with self.tasks_lock:
                    entry = self.tasks.pop(tx_id, {})
                    res = entry.get("result")
                    final_estado = entry.get("estado", "DESCONOCIDO")

                with self.busy_lock:
                    self.busy_boxes.discard(key)

                logger.info("Respuesta: tx=%s, estado=%s, tiempo=%.2fs", tx_id, final_estado, wait_time)

                return jsonify(
                    {
                        "transaction_id": tx_id,
                        "result": res,
                        "estado": final_estado,
                        "tiempo_total": round(wait_time, 2),
                    }
                )

            if pos_type == "mercadopago":
                terminal_id = data.get("terminal_id", settings.ID_TERMINAL)
                access_token = data.get("access_token")
                amount = data.get("amount")
                if access_token is None or access_token == "":
                    return jsonify({"error": "Es necesario el access_token de mercado pago"}), 400
                if amount is None or amount == 0:
                    return jsonify({"error": "Es necesario el monto de venta"}), 400
                if terminal_id is None or terminal_id == "":
                    return jsonify({"error": "Es necesario el terminal_id de mercado pago"}), 400

                res = process_mercadopago(terminal_id, access_token, amount, timeout=timeout)
                return jsonify(res), 200

            return jsonify({"error": "Tipo POS no soportado"}), 400

        @app.route("/status")
        def http_status():
            settings.refresh_settings()
            with self.agents_lock:
                agents_count = sum(len(boxes) for boxes in self.agents.values())
            return jsonify(
                {
                    "status": "ok",
                    "port": settings.HTTP_PORT,
                    "id_sucursal": settings.ID_SUCURSAL,
                    "nombre_caja": settings.NOMBRE_CAJA,
                    "id_terminal": settings.ID_TERMINAL,
                    "usa_pos_fisico": self.pos.is_online() if self.pos else False,
                    "agents_count": agents_count,
                    "current_port": self.pos.get_current_port() if self.pos else None,
                }
            )

        @app.route("/online", methods=["POST", "GET"])
        @require_basic_auth
        def http_online():
            try:
                data = request.get_json(force=True, silent=True) or {}
                tipo = data.get("type")
                if tipo == "transbank":
                    if not self.pos:
                        return jsonify({"success": True, "online": False, "puerto": None, "message": "POS no inicializado"}), 503

                    pos_online = False
                    try:
                        pos_online = self.pos.is_online()
                    except Exception as e:
                        logger.warning("Error al verificar estado del POS: %s", e)
                        pos_online = False

                    return jsonify(
                        {
                            "success": True,
                            "online": pos_online,
                            "puerto": self.pos.get_current_port(),
                            "message": "POS conectado" if pos_online else "POS no detectado",
                        }
                    ), (200 if pos_online else 503)
                if tipo == "getnet":
                    if not self.getnet:
                        return jsonify({"success": False, "online": False, "message": "Getnet no habilitado"}), 503

                    online = self.getnet.is_online()

                    return jsonify(
                        {
                            "success": True,
                            "online": online,
                            "puerto": self.getnet.get_current_port(),
                            "message": "Getnet conectado" if online else "Getnet no detectado",
                        }
                    ), (200 if online else 503)

                return jsonify({"success": True, "message": "POS conectado"}), 200

            except Exception as e:
                logger.error("Error en /online: %s", e)
                return jsonify({"success": False, "online": False, "message": f"Error verificando POS: {e}"}), 500

        @app.route("/debug/queues")
        def debug_queues():
            with self.queues_lock:
                info = {}
                for cid, boxes in self.queues.items():
                    for box, q in boxes.items():
                        info[f"{cid}:{box}"] = {"queued": q.qsize()}
            return jsonify(info)

        @app.route("/pago/iniciar", methods=["POST"])
        @require_basic_auth
        def http_pago_iniciar():
            settings.refresh_settings()
            data = request.get_json(force=True, silent=True) or {}
            id_sucursal = data.get("id_sucursal")
            nombre_caja = data.get("nombre_caja")

            if id_sucursal != str(settings.ID_SUCURSAL) or nombre_caja != str(settings.NOMBRE_CAJA):
                return jsonify({"status": "forbidden", "message": "El id_sucursal y el nombre_caja no coinciden con la configuración de esta máquina"}), 403

            pos_type = data.get("type")

            logger.info("[HTTP /pago/iniciar] Petición recibida: %s", json.dumps(data, ensure_ascii=False))

            if id_sucursal is not None:
                id_sucursal = str(id_sucursal)
            if nombre_caja is not None:
                nombre_caja = str(nombre_caja)

            if not id_sucursal or not nombre_caja or not pos_type:
                return jsonify({"error": "id_sucursal, nombre_caja y type requeridos"}), 400

            key = (id_sucursal, nombre_caja)
            with self.busy_lock:
                if key in self.busy_boxes:
                    logger.warning("Caja ocupada: %s/%s", id_sucursal, nombre_caja)
                    return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                self.busy_boxes.add(key)

            tx_id = str(uuid.uuid4())

            custom_timeout = data.get("timeout", settings.MAX_TRANSACTION_TIME)
            try:
                custom_timeout = max(30, min(int(custom_timeout), 300))
            except Exception:
                custom_timeout = settings.MAX_TRANSACTION_TIME

            if pos_type == "transbank":
                amount = data.get("amount")
                id_terminal = data.get("id_terminal", settings.ID_TERMINAL)

                if amount is None:
                    with self.busy_lock:
                        self.busy_boxes.discard(key)
                    return jsonify({"error": "amount requerido"}), 400

                with self.tasks_lock:
                    self.tasks[tx_id] = {
                        "event": threading.Event(),
                        "result": None,
                        "estado": "PENDIENTE",
                        "id_sucursal": id_sucursal,
                        "nombre_caja": nombre_caja,
                        "timestamp": time.time(),
                        "timeout": custom_timeout,
                        "type": pos_type,
                        "amount": amount,
                    }

                task_payload = {
                    "tx_id": tx_id,
                    "id_sucursal": id_sucursal,
                    "nombre_caja": nombre_caja,
                    "type": "transbank",
                    "id_terminal": id_terminal,
                    "amount": amount,
                    "timeout": custom_timeout,
                }

                q = self.ensure_queue(id_sucursal, nombre_caja)
                q.put(task_payload)

                logger.info("Pago iniciado tx=%s -> %s/%s (respuesta inmediata)", tx_id, id_sucursal, nombre_caja)

                return jsonify(
                    {
                        "status": "ok",
                        "transaction_id": tx_id,
                        "estado": "PENDIENTE",
                        "timeout_configurado": custom_timeout,
                        "message": "Pago iniciado. Use /pago/estado/{tx_id} para consultar resultado",
                    }
                ), 202

            with self.busy_lock:
                self.busy_boxes.discard(key)
            return jsonify({"error": "Tipo POS no soportado"}), 400

        @app.route("/pago/estado/<tx_id>", methods=["GET"])
        @require_basic_auth
        def http_pago_estado(tx_id):
            with self.tasks_lock:
                task = self.tasks.get(tx_id)
                if not task:
                    return jsonify({"error": "Transacción no encontrada", "transaction_id": tx_id}), 404

                if task.get("estado") == "PENDIENTE" and task["result"] is None:
                    return jsonify(
                        {"transaction_id": tx_id, "estado": "PENDIENTE", "tiempo_transcurrido": int(time.time() - task["timestamp"])}
                    ), 200

                result = task.get("result")
                estado = "DESCONOCIDO"
                if result:
                    estado = "APROBADO" if result.get("status") == "success" else "RECHAZADO"
                    if result.get("status") == "error":
                        estado = "ERROR"
                task["estado"] = estado

                return jsonify(
                    {
                        "transaction_id": tx_id,
                        "estado": estado,
                        "result": result,
                        "tiempo_total": int(time.time() - task["timestamp"]),
                    }
                ), 200

        @app.route("/pago/cancelar/<tx_id>", methods=["POST"])
        @require_basic_auth
        def http_pago_cancelar(tx_id):
            with self.tasks_lock:
                task = self.tasks.get(tx_id)
                if not task:
                    return jsonify({"error": "Transacción no encontrada"}), 404
                if task.get("result") is not None:
                    return jsonify({"error": "La transacción ya finalizó", "estado": task.get("estado")}), 400

                task["result"] = {"status": "cancelled", "message": "Cancelado por usuario"}
                task["estado"] = "CANCELADO"
                task["event"].set()

                id_sucursal = task.get("id_sucursal")
                nombre_caja = task.get("nombre_caja")
                self.internal_free_box(id_sucursal, nombre_caja, "cancelacion")

                logger.info("Transacción cancelada: %s", tx_id)
                return jsonify({"status": "ok", "transaction_id": tx_id, "estado": "CANCELADO"}), 200

        @app.route("/pago/limpiar", methods=["POST"])
        @require_basic_auth
        def http_pago_limpiar():
            now = time.time()
            max_age = 600
            with self.tasks_lock:
                old_txs = [tx_id for tx_id, task in self.tasks.items() if now - task["timestamp"] > max_age]
                for tx_id in old_txs:
                    self.tasks.pop(tx_id, None)
            logger.info("Limpiadas %d transacciones antiguas", len(old_txs))
            return jsonify({"status": "ok", "eliminadas": len(old_txs)}), 200

    def internal_free_box(self, id_sucursal, nombre_caja, reason=""):
        key = (id_sucursal, nombre_caja)
        with self.busy_lock:
            if key in self.busy_boxes:
                self.busy_boxes.discard(key)
                logger.info("Caja %s/%s liberada (%s)", id_sucursal, nombre_caja, reason)

    def run(self):
        settings.refresh_settings()
        logger.info("Servidor Flask arrancando en puerto %s", settings.HTTP_PORT)
        self.app.run(host="0.0.0.0", port=settings.HTTP_PORT, threaded=True, use_reloader=False)
