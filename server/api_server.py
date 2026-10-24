import re
import time
import uuid
import json
import threading
import queue
import logging
import traceback

from flask import Flask, jsonify, request, session, render_template_string
from flask_cors import CORS

from config.settings import (
    APP_NAME,
    ID_SUCURSAL,
    NOMBRE_CAJA,
    ID_TERMINAL,
    TRANSBANK_TERMINAL_ID,
    GETNET_TERMINAL_ID,
    MP_API_URL,
    MP_ACCESS_TOKEN,
    MP_TERMINALES,
    ALLOW_MP_TOKEN_IN_REQUEST,
    USAR_MP,
    ALLOWED_MP,
    ALLOWED_ORIGINS,
    TIMEOUT_SERVER,
    MAX_TRANSACTION_TIME,
    HTTP_PORT,
    BIND_HOST,
    AGENTES_REMOTOS,
    USAR_POS_FISICO,
    USAR_GETNET,
    GETNET_MAX_ESPERA,
    TRANSBANK_MAX_ESPERA,
    USAR_TUU,
    TUU_API_KEY,
    TUU_DISPOSITIVOS,
    ALLOW_TUU_KEY_IN_REQUEST,
)
from server.auth import require_basic_auth, check_basic_auth_header, check_local_panel_request, clave_panel_valida
from server.panel_html import PANEL_HTML, LOGIN_HTML
from pos.pos_module import POSModule
from core import transaction_store as tx_store
# _http_json se referencia por este nombre de módulo: los tests lo reemplazan acá.
from core.puertos_com import vincular as vincular_puertos
from core.http_util import http_json as _http_json, error_de_red_ambiguo as _error_de_red_ambiguo
from core.business_logging import getnet_log, transbank_log, mercadopago_log, tuu_log, fmt_monto
from pos import tuu_module as tuu
from pos import getnet_module as getnet_mod


logger = logging.getLogger(APP_NAME)

RUTAS_AGENTES_REMOTOS = {"/register_agent", "/poll", "/result", "/debug/queues"}
# Endpoints que ejecutan algo en un POS y todavía aceptan GET por compatibilidad.
RUTAS_COBRO_GET = {"/pago", "/pago/iniciar", "/pago/detalle"}

# Cuánto espera /pago/cancelar a que el POS Getnet responda al Command 116
# antes de dejar la venta INDETERMINADA (el cliente pudo haber ingresado el PIN).
ESPERA_CANCELACION_GETNET = 20

# Medio de pago -> cómo se reconcilia una venta INDETERMINADA (monitor y panel).
RESUELTO_POR_MONITOR = {"getnet": "Command 101", "transbank": "Última venta 0250",
                        "mercadopago": "API Mercado Pago", "tuu": "API TUU"}

def _env_en_uso():
    """El .env que cargó config.settings al arrancar: el panel lee y guarda ese
    mismo archivo, no otro que encuentre buscando por su cuenta."""
    from pathlib import Path
    from config import settings
    ruta = (settings._dotenv_info or {}).get("path")
    return Path(ruta) if ruta and Path(ruta).exists() else None


# Variable de entorno con la que el reinicio del panel le indica a la instancia
# nueva qué PID está reemplazando (ver core.singleton.tomar_instancia_unica).
VAR_REEMPLAZA_PID = "APIPAGO_REEMPLAZA_PID"

# El cliente pide la máquina TUU con type="haulmer"; adentro (base de datos,
# logs, monitor) se sigue llamando "tuu". "tuu" ya no se acepta en el request.
TIPO_TUU_EN_REQUEST = "haulmer"


def _mayus(valor):
    """nombre_caja y type se reciben y se guardan siempre en MAYÚSCULAS."""
    return valor.strip().upper() if isinstance(valor, str) else valor


def _entrada(data):
    """Copia del JSON/query de la petición con nombre_caja y type en mayúsculas."""
    data = dict(data or {})
    for campo in ("nombre_caja", "type"):
        if campo in data:
            data[campo] = _mayus(data[campo])
    return data


def _tipo_request(valor):
    """type del request (en cualquier caso) → nombre interno de la máquina."""
    valor = valor.strip().lower() if isinstance(valor, str) else valor
    if valor == TIPO_TUU_EN_REQUEST:
        return "tuu"
    if valor == "tuu":
        return "tuu_no_aceptado"
    return valor


def _caja_habilitada_mp(id_sucursal, nombre_caja):
    """ALLOWED_MP vacío = todas las cajas; si no, solo las "sucursal:caja" listadas."""
    if not ALLOWED_MP:
        return True
    permitidas = {c.strip().lower() for c in ALLOWED_MP}
    return f"{id_sucursal}:{nombre_caja}".strip().lower() in permitidas


def _clave_sesion_panel():
    """Clave que firma la cookie de sesión del panel. Se guarda en data/ para que
    la sesión sobreviva a "Guardar y reiniciar"; si no se puede, una por proceso."""
    import os
    from config.settings import DATA_DIR
    ruta = DATA_DIR / "panel_sesion.key"
    try:
        if ruta.exists() and len(ruta.read_bytes()) >= 32:
            return ruta.read_bytes()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        clave = os.urandom(32)
        ruta.write_bytes(clave)
        return clave
    except Exception as e:
        logger.warning("No se pudo guardar la clave de sesión del panel (%s): la sesión dura hasta reiniciar", e)
        return os.urandom(32)


# Rutas del panel que se pueden usar sin haber ingresado la clave.
RUTAS_PANEL_SIN_SESION = {"/panel", "/panel/login"}


def _lista(valor):
    return [x.strip() for x in str(valor or "").split(",") if x.strip()]


def validar_identificadores_maquinas(env):
    """
    Cada máquina de la caja necesita sus propios identificadores y credenciales:
    si dos integraciones comparten uno, una venta puede terminar en la máquina
    equivocada. env: valores finales del .env (los actuales + los editados).
    Returns: texto del error, o None si está todo bien. Nunca incluye secretos.
    """
    # Solo se comparan los valores escritos en cada campo: el TERMINAL_ID de un
    # .env anterior (compartido por Transbank y Getnet) cuenta una sola vez.
    grupos = {
        "TRANSBANK_TERMINAL_ID": _lista(env.get("TRANSBANK_TERMINAL_ID")),
        "GETNET_TERMINAL_ID": _lista(env.get("GETNET_TERMINAL_ID")),
        "MP_TERMINAL_ID": _lista(env.get("MP_TERMINAL_ID")),
        "TUU_DEVICES": _lista(env.get("TUU_DEVICES")),
    }
    if not grupos["TRANSBANK_TERMINAL_ID"] and not grupos["GETNET_TERMINAL_ID"]:
        grupos["TERMINAL_ID"] = _lista(env.get("TERMINAL_ID"))
    vistos = {}
    for campo, valores in grupos.items():
        for v in valores:
            if v in vistos and vistos[v] != campo:
                return (f"{vistos[v]} y {campo} no pueden tener el mismo valor ('{v}'): "
                        "cada máquina necesita su propio identificador")
            vistos[v] = campo

    mp_token = (env.get("MP_ACCESS_TOKEN") or "").strip()
    tuu_key = (env.get("TUU_API_KEY") or "").strip()
    if mp_token and tuu_key and mp_token == tuu_key:
        return "MP_ACCESS_TOKEN y TUU_API_KEY no pueden ser iguales: cada una es la credencial de su propia API"
    secretos = {"MP_ACCESS_TOKEN": mp_token, "TUU_API_KEY": tuu_key}
    for campo, valores in grupos.items():
        for v in valores:
            for nombre, secreto in secretos.items():
                if secreto and v == secreto:
                    return f"{campo} contiene el valor de {nombre}: ahí va el identificador de la máquina, no la credencial"
            if v.startswith(("APP_USR-", "TEST-")):
                return (f"{campo} parece contener un access token de Mercado Pago (empieza con "
                        f"'{v.split('-')[0]}-'): el token va en MP_ACCESS_TOKEN")
    return None


def _monto_valido(amount):
    """Monto de venta en pesos: entero positivo (acepta "1500" o 1500, no 15.5 ni -1)."""
    if isinstance(amount, bool):
        return None
    try:
        valor = int(str(amount).strip())
    except (TypeError, ValueError):
        return None
    return valor if 0 < valor <= 100_000_000 else None


def _estado_db(result):
    """Traduce el status de un resultado de venta al estado persistente de tx_store."""
    return {
        "success": "APROBADO", "approved": "APROBADO",
        "error": "ERROR", "indeterminada": "INDETERMINADA",
    }.get((result or {}).get("status"), "RECHAZADO")


# Estados de una orden Point en los que ya no se puede cobrar: el resultado es
# definitivo. Cualquier otro estado (created, at_terminal, action_required o uno
# que MP agregue en el futuro) se trata como "el cliente todavía puede pagar".
MP_ESTADOS_FINALES = ("processed", "canceled", "expired", "failed", "refunded", "rejected", "approved", "success")
# Para el log de negocio: qué significa cada estado intermedio de la orden.
MP_DESCRIPCION_ESTADOS = {
    "created": "orden enviada, esperando que el terminal la tome",
    "at_terminal": "el cobro está en pantalla del terminal, esperando al cliente",
    "action_required": "el terminal pide una acción al cliente",
}
MP_REINTENTOS_CREACION = 3
MP_PAUSA_REINTENTO = 2
MP_INTERVALO_CONSULTA = 2
# La orden expira sola en MP un poco después de que dejamos de esperarla, así
# una orden que no se pudo cancelar no deja el terminal bloqueado 16 minutos.
MP_MARGEN_EXPIRACION = 120


def _mp_aprobado(order_info):
    """
    La API de Orders de Mercado Pago informa un cobro exitoso como
    status="processed" + status_detail="accredited" (visto en producción);
    "approved"/"success" se aceptan por compatibilidad.
    """
    status = (order_info or {}).get("status")
    if status in ("approved", "success"):
        return True
    return status == "processed" and (order_info or {}).get("status_detail") in (None, "accredited")


def _mp_headers(access_token, idempotency_key=None):
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    return headers


def _mp_describir_error(code, body):
    """Texto legible para el log de negocio a partir de una respuesta de error de MP."""
    if code is None:
        return f"sin respuesta de Mercado Pago ({(body or {}).get('message', 'error de red')})"
    if code == 401:
        return "HTTP 401: el MP_ACCESS_TOKEN es inválido o está vencido"
    if code == 403:
        return "HTTP 403: el token no tiene permiso sobre este terminal (¿terminal de otra cuenta?)"
    if code == 404:
        return "HTTP 404: Mercado Pago no encuentra el recurso (¿terminal_id mal escrito o no vinculado?)"
    if code == 429:
        return "HTTP 429: demasiadas solicitudes a Mercado Pago"
    detalle = ""
    if isinstance(body, dict):
        errores = body.get("errors")
        if isinstance(errores, list) and errores:
            detalle = "; ".join(
                str(e.get("message") or e.get("code") or e.get("details") or e) if isinstance(e, dict) else str(e)
                for e in errores
            )
        detalle = detalle or str(body.get("message") or body.get("error") or "")
    return f"HTTP {code}" + (f": {detalle}" if detalle else "")


def _mp_detalle_pago(order_info):
    """Resumen del pago de la orden para el log (id, medio, cuotas, motivo)."""
    pagos = ((order_info or {}).get("transactions") or {}).get("payments") or []
    pago = pagos[0] if pagos and isinstance(pagos[0], dict) else {}
    medio = pago.get("payment_method") or {}
    partes = []
    if pago.get("id"):
        partes.append(f"pago {pago['id']}")
    if medio.get("id") or medio.get("type"):
        partes.append("medio " + " / ".join(str(x) for x in (medio.get("id"), medio.get("type")) if x))
    if medio.get("installments"):
        partes.append(f"{medio['installments']} cuota(s)")
    if pago.get("reference_id"):
        partes.append(f"referencia {pago['reference_id']}")
    detalle = pago.get("status_detail") or (order_info or {}).get("status_detail")
    if detalle:
        partes.append(f"detalle {detalle}")
    return " — ".join(partes)


def _mp_crear_orden(payload, access_token, idempotency_key, ctx=""):
    """
    Crea la orden reintentando ante errores de red, 429 y 5xx. Es seguro
    reintentar: con la misma X-Idempotency-Key MP devuelve la orden ya creada
    en vez de crear otra.
    Returns: (http_status|None, body, ambiguo). ambiguo=True si, sin respuesta
    exitosa, la orden igual pudo haberse creado.
    """
    ambiguo = False
    code, body = None, {}
    for intento in range(1, MP_REINTENTOS_CREACION + 1):
        try:
            code, body = _http_json("POST", MP_API_URL, headers=_mp_headers(access_token, idempotency_key),
                                    payload=payload, timeout=15)
        except Exception as e:
            ambiguo = ambiguo or _error_de_red_ambiguo(e)
            code, body = None, {"message": str(e)}
            logger.warning("Creando orden MP (intento %s/%s): error de red %s", intento, MP_REINTENTOS_CREACION, e)
        else:
            if code in (200, 201) and isinstance(body, dict) and body.get("id"):
                return code, body, False
            if 400 <= code < 500 and code != 429:
                return code, body, False  # MP la rechazó: no se creó
            ambiguo = ambiguo or code >= 500
            logger.warning("Creando orden MP (intento %s/%s): HTTP %s %s", intento, MP_REINTENTOS_CREACION, code, body)
        if intento < MP_REINTENTOS_CREACION:
            mercadopago_log.info(
                f"↪️ Crear la orden falló ({ctx}) — intento {intento}/{MP_REINTENTOS_CREACION}: "
                f"{_mp_describir_error(code, body)}. Se reintenta con la misma clave de idempotencia "
                "(no se duplica el cobro)..."
            )
            time.sleep(MP_PAUSA_REINTENTO)
    return code, body, ambiguo


def _mp_consultar_orden(order_id, access_token):
    """
    Estado actual de la orden.
    Returns: (orden, None) o (None, motivo) si no se pudo saber (red, 401, 5xx...).
    """
    try:
        code, info = _http_json("GET", f"{MP_API_URL}/{order_id}", headers=_mp_headers(access_token), timeout=10)
    except Exception as e:
        logger.warning("No se pudo consultar la orden MP %s: %s", order_id, e)
        return None, _mp_describir_error(None, {"message": str(e)})
    if code != 200 or not isinstance(info, dict) or not info.get("status"):
        logger.warning("Consulta de la orden MP %s respondió HTTP %s: %s", order_id, code, info)
        return None, _mp_describir_error(code, info)
    return info, None


def _mp_cancelar_orden(order_id, access_token):
    """Pide a MP cancelar la orden. Solo lo acepta mientras el cliente no haya
    pagado. Returns: (orden, None) si MP la devolvió, o (None, motivo)."""
    try:
        code, body = _http_json("POST", f"{MP_API_URL}/{order_id}/cancel",
                                headers=_mp_headers(access_token, f"cancel_{order_id}"), timeout=10)
    except Exception as e:
        logger.warning("No se pudo cancelar la orden MP %s: %s", order_id, e)
        return None, _mp_describir_error(None, {"message": str(e)})
    if code in (200, 201) and isinstance(body, dict):
        return body, None
    logger.warning("MP no canceló la orden %s: HTTP %s %s", order_id, code, body)
    return None, _mp_describir_error(code, body)


def _mp_resultado_final(order_info, order_id, ctx):
    status = order_info.get("status")
    logger.info("Respuesta final MercadoPago: %s", json.dumps(order_info, ensure_ascii=False))
    aprobado = _mp_aprobado(order_info)
    detalle = _mp_detalle_pago(order_info)
    if aprobado:
        mercadopago_log.info(f"✅ Cobro APROBADO — orden {order_id} — {ctx}" + (f" — {detalle}" if detalle else ""))
    else:
        mercadopago_log.info(
            f"⛔ Cobro NO realizado — orden {order_id} — {ctx} — estado {status}"
            + (f" — {detalle}" if detalle else "") + ". No se cobró: es seguro reintentar."
        )
    # Se normaliza a "approved" (contrato documentado para el cliente web);
    # el estado original de MP queda en mp_status y en response.
    return {
        "status": "approved" if aprobado else status,
        "mp_status": status,
        "order_id": order_id,
        "response": order_info,
    }


def _mp_cerrar_orden_pendiente(order_id, access_token, ctx, motivo):
    """
    Se dejó de esperar (timeout o cancelación del cliente) con la orden todavía
    abierta en el terminal: si queda así, el cliente puede pagar después y el
    cobro no queda registrado. Se cancela en MP y se vuelve a consultar; si no
    se logra cerrar (el cliente ya está pagando), queda INDETERMINADA y la
    termina de confirmar el monitor de reconciliación.
    """
    mercadopago_log.info(f"✋ {motivo} — se pide a Mercado Pago cancelar la orden {order_id} ({ctx})...")
    info, error = _mp_cancelar_orden(order_id, access_token)
    if (info or {}).get("status") in MP_ESTADOS_FINALES:
        mercadopago_log.info(f"🚫 Mercado Pago canceló la orden {order_id}.")
    else:
        mercadopago_log.info(
            f"↪️ Mercado Pago no canceló la orden {order_id} ({error or 'sigue abierta'}): "
            "el cliente puede estar pagando. Se consulta su estado..."
        )
        info, error = _mp_consultar_orden(order_id, access_token)
    if info and info.get("status") in MP_ESTADOS_FINALES:
        return _mp_resultado_final(info, order_id, ctx)

    estado = (info or {}).get("status") or error or "desconocido"
    mercadopago_log.info(
        f"⚠️ SIN CONFIRMACIÓN — orden {order_id} — {ctx}: no se pudo cerrar (estado: {estado}). "
        "El cliente PUEDE pagar todavía. El terminal queda bloqueado y se sigue consultando a "
        "Mercado Pago hasta conocer el resultado (o resolver desde el panel)."
    )
    return {
        "status": "indeterminada",
        "order_id": order_id,
        "response": info,
        "message": "La orden sigue abierta en el terminal y no se pudo cancelar. Se reconcilia sola "
                   "consultando a Mercado Pago; no reintentar el cobro.",
    }


def resolver_terminal_mp(solicitado):
    """
    Elige el terminal Point para un cobro. El terminal de Mercado Pago es
    independiente de TERMINAL_ID (Getnet/Transbank): nunca se usa uno por el otro.
    - Con MP_TERMINAL_ID configurado, el solicitado debe ser uno de ellos. Si hay
      uno solo y el request trae otra cosa (ej. el TERMINAL_ID de Getnet), se usa
      el configurado.
    - Sin MP_TERMINAL_ID, se usa el que venga en el request.
    Returns: (terminal_id, error) — error es un texto si no se pudo elegir.
    """
    solicitado = str(solicitado).strip() if solicitado else ""
    if MP_TERMINALES:
        if solicitado in MP_TERMINALES:
            return solicitado, None
        if len(MP_TERMINALES) == 1:
            if solicitado:
                logger.info("terminal_id MP '%s' no configurado; se usa MP_TERMINAL_ID=%s", solicitado, MP_TERMINALES[0])
            return MP_TERMINALES[0], None
        if not solicitado:
            return None, f"Hay varios terminales Mercado Pago configurados: indique mp_terminal_id ({', '.join(MP_TERMINALES)})"
        return None, f"El terminal '{solicitado}' no está configurado en MP_TERMINAL_ID ({', '.join(MP_TERMINALES)})"
    if solicitado:
        return solicitado, None
    return None, "Falta el terminal de Mercado Pago: configure MP_TERMINAL_ID o envíe mp_terminal_id"


def process_mercadopago(terminal_id, access_token, amount, timeout=TIMEOUT_SERVER, idempotency_key=None,
                        external_reference=None, cancel=None, on_order_created=None):
    """
    Procesa un pago con Mercado Pago Point.
    cancel: threading.Event para dejar de esperar a pedido del cliente.
    on_order_created(order_id): se llama apenas MP confirma la orden (para persistirla).
    Retorna un dict con el resultado; status "indeterminada" si no se sabe si se cobró.
    """
    external_reference = external_reference or f"ext_ref_{uuid.uuid4().hex[:8]}"
    ctx = f"tx {external_reference} — terminal {terminal_id} — monto {fmt_monto(amount)}"
    logger.info("Procesando MercadoPago terminal=%s monto=%s timeout=%s", terminal_id, amount, timeout)
    mercadopago_log.info(f"🟢 Cobro iniciado — {ctx} — espera máxima {timeout} s")

    idempotency_key = idempotency_key or str(uuid.uuid4())

    payload = {
        "type": "point",
        "external_reference": external_reference,
        "expiration_time": f"PT{int(timeout) + MP_MARGEN_EXPIRACION}S",
        "transactions": {"payments": [{"amount": str(amount)}]},
        "config": {
            "point": {
                "terminal_id": terminal_id,
                "print_on_terminal": "no_ticket"
            }
        },
        "description": "Venta POS"
    }

    order_id = None
    try:
        status_code, data_resp, ambiguo = _mp_crear_orden(payload, access_token, idempotency_key, ctx)
        logger.info("Respuesta inicial MercadoPago (%s): %s", status_code, json.dumps(data_resp, ensure_ascii=False))

        if not (status_code in (200, 201) and data_resp.get("id")):
            error = _mp_describir_error(status_code, data_resp)
            if ambiguo:
                mercadopago_log.info(
                    f"⚠️ SIN CONFIRMACIÓN — {ctx}: Mercado Pago no respondió al crear la orden "
                    f"({error}). La orden PUDO haberse creado: revisar en el portal de Mercado Pago "
                    "y resolver desde el panel. No reintentar el cobro."
                )
                return {"status": "indeterminada", "http_status": status_code, "response": data_resp,
                        "message": "No se pudo confirmar si Mercado Pago creó la orden. Revisar el portal de "
                                   "Mercado Pago y resolver desde el panel; no reintentar el cobro."}
            logger.warning("Error creando orden MP: %s %s", status_code, data_resp)
            mercadopago_log.info(
                f"❌ No se pudo crear la orden de cobro ({ctx}): {error}. "
                "No se envió nada al terminal, es seguro reintentar."
            )
            if status_code is None:
                return {"status": "error", "message": data_resp.get("message"), "response": data_resp}
            return {"status": "failed", "http_status": status_code, "response": data_resp}

        order_id = data_resp["id"]
        if on_order_created:
            on_order_created(order_id)
        logger.info("Orden MP creada: %s; esperando resultado...", order_id)
        mercadopago_log.info(f"⏳ Orden creada (id {order_id}) — {ctx} — esperando que el cliente pague en el terminal...")

        start = time.time()
        last_logged_status = None
        error_consulta = None
        motivo = f"Se agotó el tiempo de espera ({timeout} s)"
        while time.time() - start < timeout:
            if cancel is not None and cancel.is_set():
                motivo = "El cliente canceló la espera"
                break

            order_info, error = _mp_consultar_orden(order_id, access_token)
            if order_info is None:
                # Error transitorio: NO es un rechazo, el cliente puede estar pagando.
                if error != error_consulta:
                    mercadopago_log.info(
                        f"↪️ Orden {order_id}: no se pudo consultar su estado ({error}). "
                        "No es un rechazo: se sigue consultando..."
                    )
                    error_consulta = error
                time.sleep(MP_INTERVALO_CONSULTA)
                continue
            if error_consulta:
                mercadopago_log.info(f"🔌 Orden {order_id}: se recuperó la comunicación con Mercado Pago.")
                error_consulta = None

            status = order_info.get("status")
            logger.debug("Estado actual orden MP %s: %s", order_id, json.dumps(order_info, ensure_ascii=False))
            if status not in MP_ESTADOS_FINALES:
                if status != last_logged_status:
                    logger.info("Orden MP %s en estado %s", order_id, status)
                    mercadopago_log.info(
                        f"↪️ Orden {order_id}: estado {status} — {MP_DESCRIPCION_ESTADOS.get(status, 'cliente aún no completa el pago')} "
                        f"({int(time.time() - start)} s de {timeout} s)"
                    )
                    last_logged_status = status
                time.sleep(MP_INTERVALO_CONSULTA)
                continue

            logger.info("Orden %s finalizada con estado: %s", order_id, status)
            return _mp_resultado_final(order_info, order_id, ctx)

        logger.warning("%s — orden MP %s sin resultado", motivo, order_id)
        return _mp_cerrar_orden_pendiente(order_id, access_token, ctx, motivo)

    except Exception as e:
        logger.error("Error MercadoPago: %s\n%s", e, traceback.format_exc())
        if order_id:
            # La orden ya estaba en el terminal: un error nuestro no dice si se cobró.
            mercadopago_log.info(f"⚠️ Error inesperado con la orden {order_id} ya creada ({ctx}): {e}. Queda SIN CONFIRMACIÓN.")
            return {"status": "indeterminada", "order_id": order_id, "message": str(e)}
        mercadopago_log.info(f"❌ Error inesperado procesando el cobro ({ctx}): {e}")
        return {"status": "error", "message": str(e)}


class APIServer:
    """
    Servidor Flask que:
    - Recibe peticiones HTTP (/pago, /status, etc.)
    - Maneja colas de tareas por cada caja
    - Procesa tareas locales en un worker
    - Coordina agentes remotos
    """

    def __init__(self, pos_module: POSModule, getnet_module=None):
        self.pos = pos_module
        self.getnet = getnet_module
        if self.getnet and self.pos:
            # La detección de cada máquina nunca sondea el puerto de la otra.
            vincular_puertos(self.pos, self.getnet, USAR_POS_FISICO)
        self.app = Flask(__name__)
        # Los requests legítimos son JSON de pocos cientos de bytes.
        self.app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
        # Sesión del panel: cookie firmada, solo para este sitio y no legible por JS.
        from datetime import timedelta
        self.app.secret_key = _clave_sesion_panel()
        self.app.config.update(
            SESSION_COOKIE_NAME="apipago_panel",
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Strict",
            PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        )
        cors_origins = ALLOWED_ORIGINS or "*"
        # El panel (/panel/*) queda fuera de CORS: solo se usa desde el mismo origen.
        CORS(
            self.app,
            resources={r"^/(?!panel(/|$)).*": {"origins": cors_origins}},
            allow_headers=["Content-Type", "Authorization", "X-Pos-Token"],
            methods=["GET", "POST", "OPTIONS"],
            supports_credentials=False,
        )

        self.queues = {}
        self.queues_lock = threading.Lock()

        self.agents = {}
        self.agents_lock = threading.Lock()

        self.tasks = {}
        self.tasks_lock = threading.Lock()

        self.busy_boxes = set()
        self.busy_lock = threading.Lock()

        self.local_worker_thread = None
        self._stop_local_worker = threading.Event()

        self.cleanup_thread = None
        self._stop_cleanup = threading.Event()

        self.recon_monitor_thread = None
        # tx_id Getnet cuya petición HTTP sigue abierta esperando que el POS vuelva
        # (las resuelve el worker, no el monitor en background).
        self._esperando_getnet = set()
        # tx_id -> API key de TUU recibida en el request (solo en memoria, para el monitor).
        self._tuu_keys = {}
        # tx_id -> filas del último detalle de ventas (Command 105) mostrado en el
        # panel. "Aprobar con este comprobante" toma los datos de acá, no del navegador.
        self._detalle_ventas = {}
        # tx_id cancelados mientras esperaban en la cola (protegido por tasks_lock).
        # Aparte de self.tasks porque /pago borra su tarea apenas responde, antes de
        # que el worker la saque de la cola.
        self._canceladas_en_cola = set()
        self._esperando_lock = threading.Lock()
        self._stop_recon_monitor = threading.Event()

        self.setup_routes()
        self.register_local_agent()
        self.start_local_worker()
        self.start_cleanup_task()
        self.start_reconciliation_monitor()

        logger.info("APIServer inicializado")

    # ---------- Queue / Agent helpers ----------
    def ensure_queue(self, id_sucursal, nombre_caja):
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
                "info": info or {}
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
        info = {
            "host": "local",
            "terminal_id": ID_TERMINAL,
            "usa_pos_fisico": self.pos and self.pos.is_online(),
            "local": True
        }
        self.register_agent(str(ID_SUCURSAL), str(NOMBRE_CAJA), info)
        logger.info("Agente local registrado %s/%s", ID_SUCURSAL, NOMBRE_CAJA)

    # ---------- Local worker ----------
    def start_local_worker(self):
        self._stop_local_worker.clear()

        def worker():
            logger.info("Local worker iniciado")
            q = self.ensure_queue(str(ID_SUCURSAL), str(NOMBRE_CAJA))

            while not self._stop_local_worker.is_set():
                try:
                    task = q.get(timeout=1)
                except queue.Empty:
                    continue

                try:
                    tx_id = task.get("tx_id")
                    tipo = task.get("type", "transbank")
                    custom_timeout = task.get("timeout", MAX_TRANSACTION_TIME)

                    logger.info("Procesando: tx=%s, tipo=%s, timeout=%s", tx_id, tipo, custom_timeout)

                    # Cancelar y tomar la tarea se deciden bajo el mismo lock: o la
                    # cancelación llega antes (no se envía nada al POS) o el worker
                    # la toma primero (y cancelar ya sabe que está en el POS).
                    with self.tasks_lock:
                        entry = self.tasks.get(tx_id)
                        cancelada = tx_id in self._canceladas_en_cola
                        self._canceladas_en_cola.discard(tx_id)
                        if entry is not None and not cancelada:
                            entry["estado"] = "PROCESANDO"
                    if cancelada:
                        logger.info("Venta %s cancelada antes de llegar al POS: no se envía", tx_id)
                        continue

                    if tipo == "transbank":
                        amount = task.get("amount")
                        result = self.pos.do_sale_with_timeout(
                            amount,
                            timeout=custom_timeout,
                            ticket=task.get("ticket"),
                            on_late_result=lambda res, tx_id=tx_id: self.aplicar_resultado_tardio(tx_id, res),
                        )
                        logger.info("Resultado Transbank: %s", result.get("status") if isinstance(result, dict) else result)
                        if isinstance(result, dict) and result.get("status") == "indeterminada":
                            result = self._esperar_pos_tras_indeterminada(tx_id, result, self.esperar_resolucion_transbank)
                    elif tipo == "getnet":
                        if not self.getnet:
                            result = {"status": "error", "message": "Getnet no disponible"}
                        else:
                            amount = task.get("amount")
                            ticket = task.get("ticket")
                            with self.tasks_lock:
                                cancel_venta = (self.tasks.get(tx_id) or {}).get("cancel")
                            result = self.getnet.do_sale_with_timeout(amount, timeout=custom_timeout, ticket=ticket,
                                                                      cancel=cancel_venta)
                            logger.info("Resultado Getnet: %s", result.get("status"))

                            if result.get("status") == "indeterminada":
                                result = self._esperar_pos_tras_indeterminada(tx_id, result, self.esperar_resolucion_getnet)
                    elif tipo == "mercadopago":
                        terminal_id, _ = resolver_terminal_mp(task.get("mp_terminal_id") or task.get("terminal_id"))
                        access_token = None
                        if ALLOW_MP_TOKEN_IN_REQUEST:
                            access_token = task.get("access_token")
                        if not access_token:
                            access_token = MP_ACCESS_TOKEN
                        amount = task.get("amount")
                        idempotency_key = f"mp_{tx_id}"
                        result = process_mercadopago(
                            terminal_id,
                            access_token,
                            amount,
                            timeout=custom_timeout,
                            idempotency_key=idempotency_key,
                            external_reference=tx_id,
                        )
                    else:
                        result = {"status": "error", "message": "Tipo no soportado"}

                    if tipo in ("getnet", "transbank"):
                        tx = tx_store.obtener(tx_id)
                        if tx:
                            estado_db = _estado_db(result)
                            # Si una respuesta tardía del POS, el monitor o un operador ya
                            # resolvió la transacción, no se pisa esa resolución.
                            ya_resuelta = tx.get("estado") not in tx_store.ESTADOS_SIN_RESOLVER
                            if not ya_resuelta and not (estado_db == "INDETERMINADA" and tx.get("estado") != "PENDIENTE"):
                                tx_store.actualizar_estado(
                                    tx_id, estado_db, raw_response=result,
                                    resuelto_por="POS tras reconexión" if result.get("reconciliado") else None,
                                )

                    with self.tasks_lock:
                        entry = self.tasks.get(tx_id)
                        if entry:
                            entry["result"] = result
                            if result.get("status") in ("success", "approved"):
                                entry["estado"] = "APROBADO"
                            elif result.get("status") == "indeterminada":
                                entry["estado"] = "INDETERMINADA"
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
                        if tx_store.obtener(tx_id):
                            tx_store.actualizar_estado(tx_id, "ERROR", raw_response={"status": "error", "message": str(e)})
                        with self.busy_lock:
                            self.busy_boxes.discard((task.get("id_sucursal"), task.get("nombre_caja")))
                        _tipo_log = {"getnet": getnet_log, "transbank": transbank_log, "mercadopago": mercadopago_log, "tuu": tuu_log}.get(task.get("type"))
                        if _tipo_log:
                            _tipo_log.info(f"❌ Error interno inesperado procesando la venta (tx {tx_id}): {e}")
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

    # ---------- Cleanup task ----------
    def start_cleanup_task(self):
        self._stop_cleanup.clear()

        def cleanup():
            while not self._stop_cleanup.is_set():
                time.sleep(300)  # 5 minutos
                try:
                    now = time.time()
                    max_age = 600  # 10 minutos
                    with self.tasks_lock:
                        to_delete = [tx_id for tx_id, task in self.tasks.items() if self._tarea_vencida(task, now, max_age)]
                        for tx_id in to_delete:
                            self.tasks.pop(tx_id, None)
                    if to_delete:
                        logger.info("Limpieza automática: %d transacciones eliminadas", len(to_delete))
                except Exception as e:
                    logger.error("Error en cleanup task: %s", e)

        self.cleanup_thread = threading.Thread(target=cleanup, daemon=True)
        self.cleanup_thread.start()
        logger.info("Tarea de limpieza automática iniciada")

    @staticmethod
    def _tarea_vencida(task, now, max_age):
        """Solo se limpia una tarea YA TERMINADA. Una venta en curso (ej. Getnet
        esperando que se reconecte el cable) mantiene viva su petición /pago
        el tiempo que haga falta: si se borrara, el resultado del POS llegaría
        sin nadie a quien entregarlo y /pago quedaría colgado para siempre."""
        return task.get("result") is not None and now - task.get("timestamp", now) > max_age

    def stop_cleanup_task(self):
        self._stop_cleanup.set()
        try:
            if self.cleanup_thread:
                self.cleanup_thread.join(timeout=1)
        except Exception:
            pass
        logger.info("Tarea de limpieza detenida")

    # ---------- Monitor de reconciliación automática (Getnet, Mercado Pago y TUU) ----------
    def start_reconciliation_monitor(self, interval=30):
        """
        Reintenta periódicamente resolver transacciones que quedaron INDETERMINADA:
        - Getnet: comando enviado al POS sin confirmación; se consulta el Command 101
          (Último Comprobante). No depende de is_online() para decidir cuándo intentar
          —esa señal queda cacheada y no siempre refleja si el cable realmente volvió—
          así que simplemente reintenta cada `interval` segundos hasta que el POS
          conteste algo útil; el intento en sí (conectar + leer) es la prueba real de
          si ya hay comunicación de nuevo.
        - Mercado Pago: orden que no se pudo cancelar al dejar de esperarla; se
          consulta su estado en la API hasta que sea definitivo (a más tardar
          expira sola en MP).
        - TUU: solicitud que no se puede cancelar por API; se consulta por GET
          hasta que la máquina la complete, la rechace o se cancele en ella.
        """
        self._stop_recon_monitor.clear()

        def monitor():
            logger.info("Monitor de reconciliación iniciado (cada %ss)", interval)
            while not self._stop_recon_monitor.wait(interval):
                try:
                    with self._esperando_lock:
                        esperando = set(self._esperando_getnet)
                    pendientes = [
                        tx for tx in tx_store.listar_no_resueltas()
                        if tx.get("estado") == "INDETERMINADA" and tx["tx_id"] not in esperando
                    ]
                    for tx in pendientes:
                        if tx.get("tipo") == "getnet" and not self.getnet:
                            continue
                        if tx.get("tipo") == "transbank" and not (self.pos and USAR_POS_FISICO):
                            continue
                        if tx.get("tipo") not in RESUELTO_POR_MONITOR:
                            continue
                        _tipo_log = {"getnet": getnet_log, "transbank": transbank_log,
                                     "mercadopago": mercadopago_log, "tuu": tuu_log}[tx["tipo"]]
                        _tipo_log.info(
                            f"🔁 Monitor automático: reintentando reconciliar el ticket {tx.get('ticket')} "
                            f"(tx {tx['tx_id']})..."
                        )
                        recon = self.reconciliar(tx)
                        nuevo_estado = self.aplicar_reconciliacion(
                            tx, recon,
                            resuelto_por=f"monitor automático ({RESUELTO_POR_MONITOR[tx['tipo']]})",
                        )
                        if nuevo_estado == "INDETERMINADA":
                            continue  # sigue sin poder confirmarse, se reintenta en el próximo ciclo
                        logger.info("Monitor: tx=%s reconciliada automáticamente -> %s", tx["tx_id"], nuevo_estado)
                        _tipo_log.info(
                            f"✅ Monitor automático: ticket {tx.get('ticket')} (tx {tx['tx_id']}) reconciliado como {nuevo_estado}."
                        )
                except Exception as e:
                    logger.error("Error en monitor de reconciliación: %s", e)

        self.recon_monitor_thread = threading.Thread(target=monitor, daemon=True, name="ReconMonitor")
        self.recon_monitor_thread.start()

    def stop_reconciliation_monitor(self):
        self._stop_recon_monitor.set()
        try:
            if self.recon_monitor_thread:
                self.recon_monitor_thread.join(timeout=1)
        except Exception:
            pass
        logger.info("Monitor de reconciliación detenido")

    # ---------- HTTP routes ----------
    def setup_routes(self):
        app = self.app

        # ── Panel de control (/panel) ─────────────────────────────
        @app.route("/")
        def root_redirect():
            from flask import redirect
            return redirect("/panel")

        @app.before_request
        def _proteger_panel():
            if request.path == "/panel" or request.path.startswith("/panel/"):
                rechazo = check_local_panel_request()
                if rechazo is not None:
                    return rechazo
                if request.path not in RUTAS_PANEL_SIN_SESION and not session.get("panel_ok"):
                    return jsonify({"ok": False, "error": "Sesión del panel vencida: recargue la página e ingrese la clave",
                                    "login": True}), 401
            return None

        @app.route("/panel/login", methods=["POST"])
        def panel_login():
            data = request.get_json(force=True, silent=True) or {}
            if not clave_panel_valida(str(data.get("password") or "")):
                logger.warning("[PANEL] Clave del panel incorrecta")
                return jsonify({"ok": False, "error": "Clave incorrecta"}), 401
            session.clear()
            session.permanent = True
            session["panel_ok"] = True
            logger.info("[PANEL] Ingreso al panel de control")
            return jsonify({"ok": True})

        @app.route("/panel/logout", methods=["POST"])
        def panel_logout():
            session.clear()
            logger.info("[PANEL] Salida del panel de control")
            return jsonify({"ok": True})

        @app.before_request
        def _proteger_endpoints_sensibles():
            if request.path in RUTAS_AGENTES_REMOTOS and not AGENTES_REMOTOS:
                # Se deja rastro por si algún cliente todavía los usa.
                logger.warning("[SEGURIDAD] Llamada a endpoint de agentes remotos deshabilitado: %s %s desde %s",
                               request.method, request.path, request.remote_addr)
                return jsonify({"error": "Endpoint deshabilitado (AGENTES_REMOTOS=false)"}), 410

            if request.method == "GET" and request.path in RUTAS_COBRO_GET:
                # Un fetch()/XHR del sistema web manda Sec-Fetch-Dest: empty; un <img>,
                # <iframe>, <script> o un link abierto por otra página manda image/
                # iframe/script/document. Si el navegador tiene la clave Basic en
                # caché, esas peticiones irían autenticadas solas (CSRF): se rechazan.
                dest = request.headers.get("Sec-Fetch-Dest")
                if dest and dest != "empty":
                    logger.warning("[SEGURIDAD] GET %s rechazado (Sec-Fetch-Dest=%s, origen=%s): posible CSRF",
                                   request.path, dest, request.headers.get("Origin") or request.referrer)
                    return jsonify({"error": "Use POST con JSON para esta operación"}), 403
                logger.info("[COMPAT] GET %s (se recomienda POST con JSON)", request.path)
            return None

        @app.route("/auth/test")
        def auth_test():
            """Diagnóstico para clientes: indica si las credenciales enviadas son válidas.
            No expone nada de las credenciales configuradas en el servidor."""
            auth_header = request.headers.get("Authorization", "")
            match = check_basic_auth_header(auth_header) if auth_header else False
            return jsonify({
                "header_present": bool(auth_header),
                "match": match,
                "verdict": "CREDENCIALES CORRECTAS" if match else "CREDENCIALES INCORRECTAS o ausentes",
            })

        @app.route("/panel")
        def panel_ui():
            import os, sys
            import platform
            from pathlib import Path
            from config.settings import (
                HTTP_PORT, ID_SUCURSAL, NOMBRE_CAJA, ID_TERMINAL,
                USAR_POS_FISICO, USAR_GETNET, MAX_TRANSACTION_TIME, TIMEOUT_SERVER
            )
            if not session.get("panel_ok"):
                return render_template_string(LOGIN_HTML, app_name=APP_NAME, nombre_caja=NOMBRE_CAJA)

            # Buscar .env — en --onefile el .env está junto al .exe
            env_path = str(_env_en_uso() or "") or None
            exe_dir = Path(sys.executable).parent
            for p in [] if env_path else [
                exe_dir / ".env",                           # junto al .exe (--onefile portable)
                exe_dir.parent / ".env",                   # un nivel arriba
                exe_dir.parent.parent / ".env",            # dos niveles arriba
                Path(sys.argv[0]).resolve().parent / ".env",
                Path(os.getcwd()) / ".env",
            ]:
                if p.exists():
                    env_path = str(p)
                    break

            env_vars = {}
            if env_path:
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, _, v = line.partition("=")
                            env_vars[k.strip()] = v.strip()

            # Estado del servidor
            pos_port = self.pos.get_current_port() if self.pos else "N/A"
            mp_token_configured = bool((env_vars.get("MP_ACCESS_TOKEN", "") or "").strip())
            env_vars_ui = dict(env_vars)
            env_vars_ui.pop("MP_ACCESS_TOKEN", None)
            tuu_key_configured = bool((env_vars.get("TUU_API_KEY", "") or "").strip())
            env_vars_ui.pop("TUU_API_KEY", None)
            # Si el .env no los define, el panel muestra los valores por defecto
            # con los que realmente corre el servicio (Getnet sí, Transbank no).
            env_vars_ui.setdefault("USAR_POS_FISICO", "false")
            env_vars_ui.setdefault("USAR_GETNET", "true")
            env_vars_ui.setdefault("USAR_MP", "true")
            is_windows = platform.system() == "Windows"

            autostart_enabled = False
            if is_windows:
                try:
                    import winreg
                    run_key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path, 0, winreg.KEY_READ) as k:
                        winreg.QueryValueEx(k, APP_NAME)
                        autostart_enabled = True
                except Exception:
                    autostart_enabled = False

            from core.logging_config import current_log_file

            return render_template_string(PANEL_HTML, **{
                "app_name":      APP_NAME,
                "http_port":     HTTP_PORT,
                "id_sucursal":   ID_SUCURSAL,
                "nombre_caja":   NOMBRE_CAJA,
                "id_terminal":   ID_TERMINAL,
                "terminal_transbank": TRANSBANK_TERMINAL_ID,
                "terminal_getnet": GETNET_TERMINAL_ID,
                "usar_pos":      str(USAR_POS_FISICO).lower(),
                "usar_getnet":   str(USAR_GETNET).lower(),
                "max_tx_time":   MAX_TRANSACTION_TIME,
                "timeout":       TIMEOUT_SERVER,
                "pos_port":      pos_port,
                "env_path":      env_path or "no encontrado",
                "log_path":      current_log_file(),
                "env_vars":      env_vars_ui,
                "agents_count":  len(self.agents),
                # Mercado Pago: vacío = todas las cajas habilitadas
                "mp_allowed":    env_vars.get("ALLOWED_MP", ""),
                "mp_enabled":    env_vars.get("ALLOWED_MP", None) is not None
                                 or "ALLOWED_MP" not in env_vars,
                "mp_token_configured": mp_token_configured,
                "tuu_key_configured": tuu_key_configured,
                "is_windows": is_windows,
                "autostart_enabled": autostart_enabled,
            })

        @app.route("/panel/config", methods=["POST"])
        def panel_save_config():
            """Guarda cambios al .env desde el panel web."""
            import sys, os
            from pathlib import Path
            import re
            from config.settings import _ENV_PROTECTED_KEYS
            raw = request.get_json(force=True, silent=True)
            if not isinstance(raw, dict):
                return jsonify({"ok": False, "error": "Se esperaba un objeto JSON"}), 400
            data = {}
            for k, v in raw.items():
                k = str(k).strip()
                if k == "HTTP_PORT" or k in _ENV_PROTECTED_KEYS:
                    continue
                if not re.fullmatch(r"[A-Z][A-Z0-9_]*", k):
                    return jsonify({"ok": False, "error": f"Clave inválida: {k!r}"}), 400
                v = "" if v is None else str(v)
                if k == "NOMBRE_CAJA":
                    v = v.strip().upper()
                if "\n" in v or "\r" in v:
                    return jsonify({"ok": False, "error": f"El valor de {k} no puede tener saltos de línea"}), 400
                if k == "MP_API_URL" and v and not v.startswith("https://api.mercadopago.com/"):
                    # El token de Mercado Pago se envía a esta URL: no puede apuntar a otro host.
                    return jsonify({"ok": False, "error": "MP_API_URL debe comenzar con https://api.mercadopago.com/"}), 400
                if k == "TUU_API_URL" and v and not (v.rstrip("/") + "/").startswith("https://integrations.payment.haulmer.com/"):
                    # La API key de TUU se envía a esta URL: no puede apuntar a otro host.
                    return jsonify({"ok": False, "error": "TUU_API_URL debe comenzar con https://integrations.payment.haulmer.com"}), 400
                data[k] = v

            env_path = _env_en_uso()
            for p in [] if env_path else [
                Path(sys.executable).parent / ".env",
                Path(sys.executable).parent.parent.parent / ".env",
                Path(sys.argv[0]).resolve().parent / ".env",
                Path(os.getcwd()) / ".env",
            ]:
                if p.exists():
                    env_path = p
                    break

            if not env_path:
                return jsonify({"ok": False, "error": ".env no encontrado"}), 404

            # Leer .env actual
            with open(env_path, encoding="utf-8") as f:
                lines = f.readlines()

            actuales = {}
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k, _, v = stripped.partition("=")
                    actuales[k.strip()] = v.strip()
            error = validar_identificadores_maquinas({**actuales, **data})
            if error:
                return jsonify({"ok": False, "error": error}), 400

            # Actualizar valores
            updated = set()
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    if k in data:
                        new_lines.append(f"{k}={data[k]}\n")
                        updated.add(k)
                        continue
                new_lines.append(line)

            # Añadir claves nuevas que no estaban
            for k, v in data.items():
                if k not in updated:
                    new_lines.append(f"{k}={v}\n")

            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            logger.info("Configuración actualizada vía panel: %s", list(data.keys()))
            return jsonify({"ok": True, "message": "Configuración guardada. Reinicia el servicio para aplicar."})

        @app.route("/panel/log")
        def panel_log():
            """
            Devuelve las últimas 100 líneas del log de HOY, como texto plano.
            ?tipo=general (default, técnico) | getnet | transbank | mercadopago | tuu (narrados por venta).
            Cada uno vive en un archivo por día (<tipo>-YYYY-MM-DD.log); los días
            anteriores quedan en el disco con su propio archivo, no se muestran acá.
            """
            from core.logging_config import daily_log_path, LOG_BASENAME

            tipo = request.args.get("tipo", "general")
            if tipo == "general":
                log_path = str(daily_log_path(LOG_BASENAME))
            elif tipo in ("getnet", "transbank", "mercadopago", "tuu"):
                log_path = str(daily_log_path(tipo))
            else:
                return "tipo inválido", 400

            try:
                with open(log_path, encoding="utf-8") as f:
                    lines = f.readlines()[-100:]
                return "".join(lines) or "(sin entradas todavía)", 200, {"Content-Type": "text/plain; charset=utf-8"}
            except FileNotFoundError:
                return "(sin entradas todavía)", 200, {"Content-Type": "text/plain; charset=utf-8"}
            except Exception as e:
                return f"Error leyendo log: {e}", 500

        @app.route("/panel/pendientes", methods=["GET"])
        def panel_pendientes():
            """Transacciones que quedaron sin confirmar y requieren reconciliación."""
            try:
                pendientes = tx_store.listar_no_resueltas()
                return jsonify({"ok": True, "pendientes": pendientes})
            except Exception as e:
                return jsonify({"ok": False, "error": str(e), "pendientes": []}), 500

        @app.route("/panel/pendientes/<tx_id>/reconciliar", methods=["POST"])
        def panel_pendientes_reconciliar(tx_id):
            """Reconsulta el resultado real: al POS Getnet (Command 101) o a la API de Mercado Pago / TUU."""
            tx = tx_store.obtener(tx_id)
            if not tx:
                return jsonify({"ok": False, "error": "Transacción no encontrada"}), 404
            if not ((tx.get("tipo") == "getnet" and self.getnet) or (tx.get("tipo") == "transbank" and self.pos)
                    or tx.get("tipo") in ("mercadopago", "tuu")):
                return jsonify({"ok": False, "error": "Reconciliación no disponible para esta transacción"}), 400

            _tipo_log = {"getnet": getnet_log, "transbank": transbank_log,
                         "mercadopago": mercadopago_log, "tuu": tuu_log}[tx["tipo"]]
            _tipo_log.info(f"👤 Un operador pidió desde el panel reconsultar el ticket {tx.get('ticket')} (tx {tx_id}).")
            recon = self.reconciliar(tx)
            nuevo_estado = self.aplicar_reconciliacion(
                tx, recon,
                resuelto_por=f"sistema ({RESUELTO_POR_MONITOR[tx['tipo']]}, gatillado desde panel)",
                guardar_no_resuelto=True,
            )
            logger.info("Reconciliación manual (panel) tx=%s -> %s", tx_id, nuevo_estado)
            return jsonify({"ok": True, "estado": nuevo_estado, "detalle": recon})

        @app.route("/panel/pendientes/<tx_id>/detalle_ventas", methods=["POST"])
        def panel_pendientes_detalle_ventas(tx_id):
            """Detalle de ventas del POS Getnet (Command 105) para revisar una venta pendiente,
            con las que podrían ser ella marcadas como candidatas."""
            tx = tx_store.obtener(tx_id)
            if not tx:
                return jsonify({"ok": False, "error": "Transacción no encontrada"}), 404
            if tx.get("tipo") != "getnet" or not self.getnet:
                return jsonify({"ok": False, "error": "El detalle de ventas solo está disponible para Getnet"}), 400

            getnet_log.info(f"👤 Un operador pidió desde el panel el detalle de ventas del POS (Command 105) "
                            f"para revisar el ticket {tx.get('ticket')} (tx {tx_id}).")
            res = self.getnet.get_sales_detail()
            if res["status"] in ("error", "ocupado"):
                getnet_log.info(f"↪️ No se pudo obtener el detalle de ventas: {res.get('message')}")
                return jsonify({"ok": False, "error": res.get("message")}), 409 if res["status"] == "ocupado" else 502

            filas = getnet_mod.clasificar_detalle_ventas(
                res["ventas"], tx.get("ticket"), tx.get("monto"), tx.get("created_at"),
                tx_store.tickets_por_autorizacion("getnet", excluir_tx_id=tx_id),
                tx_store.tickets_registrados("getnet", excluir_tx_id=tx_id),
            )
            self._detalle_ventas[tx_id] = {"filas": filas, "ventas": res["ventas"]}
            candidatos = sum(1 for f in filas if f["candidato"])
            getnet_log.info(
                f"📋 Detalle de ventas del POS: {len(filas)} venta(s) en memoria, {candidatos} candidata(s) para el "
                f"ticket {tx.get('ticket')} (monto {fmt_monto(tx.get('monto'))}). "
                f"JSON: {json.dumps(res.get('response'), ensure_ascii=False, default=str)}"
            )
            return jsonify({"ok": True, "ventas": filas, "candidatos": candidatos,
                            "monto": tx.get("monto"), "enviada": tx.get("created_at")})

        @app.route("/panel/pendientes/<tx_id>/resolver", methods=["POST"])
        def panel_pendientes_resolver(tx_id):
            """Resolución manual: el operador confirma el resultado mirando el comprobante impreso en el POS.
            Con "autorizacion" se aprueba con ese comprobante del último detalle de ventas consultado."""
            body = request.get_json(silent=True) or {}
            comprobante = None
            autorizacion = str(body.get("autorizacion") or "").strip()
            if autorizacion:
                if body.get("estado") != "APROBADO":
                    return jsonify({"ok": False, "error": "Solo se puede aprobar con un comprobante"}), 400
                detalle = self._detalle_ventas.get(tx_id) or {}
                venta = next((v for v, f in zip(detalle.get("ventas", []), detalle.get("filas", []))
                              if f["autorizacion"] == autorizacion and not f["registrada_ticket"]), None)
                if venta is None:
                    return jsonify({"ok": False, "error": "Ese comprobante no está en el último detalle consultado "
                                                          "o ya pertenece a otra venta. Vuelva a consultar el detalle."}), 409
                tx = tx_store.obtener(tx_id) or {}
                comprobante = getnet_mod.normalizar_comprobante(venta, tx.get("ticket"), "detalle_ventas")
            ok, err, code = self.resolver_transaccion(
                tx_id, body.get("estado"), resuelto_por="panel", nota=body.get("nota"), comprobante=comprobante
            )
            if not ok:
                return jsonify({"ok": False, "error": err}), code
            self._detalle_ventas.pop(tx_id, None)
            return jsonify({"ok": True, "estado": body.get("estado")})

        @app.route('/pago/resolver/<tx_id>', methods=['POST'])
        @require_basic_auth
        def pago_resolver(tx_id):
            """
            Resolución manual autenticada de una transacción PENDIENTE/INDETERMINADA
            (ej. el cajero confirma en tu sistema web mirando el comprobante del POS).
            Body: {"estado": "APROBADO"|"RECHAZADO", "resuelto_por": "<nombre/id del cajero>", "nota": "..."}
            """
            body = request.get_json(silent=True) or {}
            estado = body.get("estado")
            resuelto_por = body.get("resuelto_por") or "api"
            ok, err, code = self.resolver_transaccion(tx_id, estado, resuelto_por=resuelto_por, nota=body.get("nota"))
            if not ok:
                return jsonify({"status": "error", "message": err}), code
            return jsonify({
                "status": "ok",
                "transaction_id": tx_id,
                "estado": estado,
                "resuelto_por": resuelto_por,
            }), 200

        def _venta_en_curso():
            with self.busy_lock:
                return bool(self.busy_boxes)

        @app.route("/panel/com_ports", methods=["GET"])
        def panel_com_ports():
            """Detecta el puerto del POS Transbank probando cada puerto con el
            protocolo de Transbank (POLL). Solo devuelve puertos donde realmente
            respondió un POS Transbank, y nunca sondea el puerto de Getnet."""
            if not USAR_POS_FISICO or not self.pos:
                return jsonify({"ok": False, "ports": [], "csv": "",
                                "error": "Transbank deshabilitado (USAR_POS_FISICO=false). "
                                         "Si lo acabás de cambiar, guardá y reiniciá."}), 409
            if _venta_en_curso():
                return jsonify({"ok": False, "ports": [], "csv": "",
                                "error": "Hay una venta en curso, intentá de nuevo cuando termine"}), 409
            try:
                getnet_port = self.getnet.get_current_port() if self.getnet else None
                port = self.pos.detect_port(exclude=[getnet_port])
                if port:
                    return jsonify({"ok": True, "ports": [port], "csv": port})
                return jsonify({"ok": False, "ports": [], "csv": "",
                                "error": "No se detectó ningún POS Transbank conectado"}), 404
            except Exception as e:
                return jsonify({"ok": False, "error": str(e), "ports": [], "csv": ""}), 500

        @app.route("/panel/getnet_port", methods=["GET"])
        def panel_getnet_port():
            """Prueba cada puerto con el protocolo real de Getnet (POLL) y devuelve
            el que efectivamente respondió como POS Getnet. Nunca sondea el puerto
            de Transbank."""
            if not USAR_GETNET or not self.getnet:
                return jsonify({"ok": False, "port": None,
                                "error": "Getnet deshabilitado (USAR_GETNET=false). "
                                         "Si lo acabás de cambiar, guardá y reiniciá."}), 409
            if _venta_en_curso():
                return jsonify({"ok": False, "port": None,
                                "error": "Hay una venta en curso, intentá de nuevo cuando termine"}), 409
            try:
                transbank_port = self.pos.get_current_port() if (USAR_POS_FISICO and self.pos) else None
                with self.getnet._serial_lock:
                    port = self.getnet._find_getnet_port(exclude=[transbank_port])
                if port:
                    return jsonify({"ok": True, "port": port})
                return jsonify({"ok": False, "error": "No se detectó ningún POS Getnet conectado", "port": None}), 404
            except Exception as e:
                return jsonify({"ok": False, "error": str(e), "port": None}), 500

        @app.route("/panel/autostart", methods=["GET", "POST"])
        def panel_autostart():
            import platform
            if platform.system() != "Windows":
                return jsonify({"ok": True, "supported": False, "enabled": False})

            def _get_command():
                import sys
                from pathlib import Path
                exe = Path(sys.executable).resolve()
                name = exe.name.lower()
                if exe.suffix.lower() == ".exe" and name not in ("python.exe", "pythonw.exe"):
                    return f"\"{str(exe)}\""
                main = Path(sys.argv[0]).resolve()
                if main.suffix.lower() == ".py":
                    pyw = exe
                    if name == "python.exe":
                        candidate = exe.parent / "pythonw.exe"
                        if candidate.exists():
                            pyw = candidate
                    if pyw.name.lower() == "python.exe":
                        return None
                    return f"\"{str(pyw)}\" \"{str(main)}\""
                return None

            run_key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            value_name = APP_NAME

            try:
                import winreg
                if request.method == "GET":
                    try:
                        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path, 0, winreg.KEY_READ) as k:
                            winreg.QueryValueEx(k, value_name)
                            return jsonify({"ok": True, "supported": True, "enabled": True})
                    except Exception:
                        return jsonify({"ok": True, "supported": True, "enabled": False})

                data = request.get_json(force=True, silent=True) or {}
                enabled = bool(data.get("enabled"))

                if enabled:
                    cmd = _get_command()
                    if not cmd:
                        return jsonify({"ok": False, "error": "No se pudo determinar el comando de inicio automático"}), 400
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path, 0, winreg.KEY_SET_VALUE) as k:
                        winreg.SetValueEx(k, value_name, 0, winreg.REG_SZ, cmd)
                    return jsonify({"ok": True, "supported": True, "enabled": True})

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path, 0, winreg.KEY_SET_VALUE) as k:
                    try:
                        winreg.DeleteValue(k, value_name)
                    except FileNotFoundError:
                        pass
                return jsonify({"ok": True, "supported": True, "enabled": False})
            except Exception as e:
                logger.error("Error configurando autostart: %s", e)
                return jsonify({"ok": False, "error": str(e)}), 500

        @app.route("/panel/restart", methods=["POST"])
        def panel_restart():
            """Reinicia el servicio relanzando el propio proceso (.exe compilado o python app.py).
            Con una venta en curso no se reinicia: cortaría la venta a la mitad."""
            if _venta_en_curso():
                logger.warning("Reinicio vía panel rechazado: hay una venta en curso")
                return jsonify({"ok": False, "error": "Hay una venta en curso: espere a que termine para reiniciar"}), 409
            import sys
            import platform
            import subprocess
            import os
            import signal
            from pathlib import Path

            is_windows = platform.system() == "Windows"
            exe = sys.executable
            if getattr(sys, "frozen", False):
                # PyInstaller: sys.executable ya es ApiPagoElectronico.exe
                cmd = [exe] + sys.argv[1:]
            else:
                cmd = [exe, str(Path(sys.argv[0]).resolve())] + sys.argv[1:]

            def _relaunch():
                time.sleep(2.0)
                try:
                    env = os.environ.copy()
                    env.pop("_MEIPASS2", None)
                    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                    env[VAR_REEMPLAZA_PID] = str(os.getpid())
                    if is_windows:
                        no_window_flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                        subprocess.Popen(cmd, creationflags=no_window_flag, env=env)
                    else:
                        subprocess.Popen(
                            cmd,
                            start_new_session=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            env=env,
                        )
                except Exception as e:
                    logger.error("Error al relanzar: %s", e)
                finally:
                    os.kill(os.getpid(), signal.SIGTERM)

            logger.info("Reinicio vía panel: relanzando %s", cmd)
            threading.Thread(target=_relaunch, daemon=True).start()
            return jsonify({"ok": True, "message": "Reiniciando..."}), 200

        @app.route("/register_agent", methods=["POST"])
        @require_basic_auth
        def http_register_agent():
            data = request.get_json(force=True, silent=True) or {}
            id_sucursal = data.get("id_sucursal")
            nombre_caja = _mayus(data.get("nombre_caja"))
            meta = data.get("meta", {})

            if not id_sucursal or not nombre_caja:
                return jsonify({"error": "id_sucursal y nombre_caja requeridos"}), 400

            id_s = str(id_sucursal)
            nc = str(nombre_caja)

            self.register_agent(id_s, nc, meta)
            logger.info("Agent registered: %s/%s meta=%s", id_s, nc, meta)
            return jsonify({"status": "ok"})

        @app.route("/poll", methods=["GET"])
        @require_basic_auth
        def http_poll():
            id_sucursal = request.args.get("id_sucursal")
            nombre_caja = _mayus(request.args.get("nombre_caja"))
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
        @require_basic_auth
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
        
        @app.route('/pago/refund', methods=['POST'])
        @require_basic_auth
        def refund():
            try:
                data = request.get_json()
                
                if not data or 'operation_id' not in data:
                    return jsonify({
                        "status": "error",
                        "message": "Falta el campo 'operation_id' en el body"
                    }), 400
                
                operation_id = data['operation_id']
                
                # Validar que operation_id sea un número
                try:
                    operation_id = int(operation_id)
                except (ValueError, TypeError):
                    return jsonify({
                        "status": "error",
                        "message": "operation_id debe ser un número válido"
                    }), 400
                
                logger.info(f"Solicitud de anulación recibida: operation_id={operation_id}")
                
                # Ejecutar anulación con timeout
                result = self.pos.do_refund_with_timeout(operation_id)
                
                if result["status"] == "success":
                    return jsonify(result), 200
                elif result["status"] == "failed":
                    return jsonify(result), 400
                else:
                    return jsonify(result), 500
                    
            except Exception as e:
                logger.exception("Error en endpoint /api/refund")
                return jsonify({
                    "status": "error",
                    "message": str(e)
                }), 500
            
        @app.route('/pago/detalle', methods=['POST', 'GET'])
        @require_basic_auth
        def detalle():
            try:
                # Obtener parámetro print_on_pos / type del body o query string
                if request.method == 'POST':
                    data = request.get_json(silent=True) or {}
                    print_on_pos = data.get('print_on_pos', False)
                    tipo = data.get('type', 'transbank')
                else:  # GET
                    print_on_pos = request.args.get('print_on_pos', 'false').lower() == 'true'
                    tipo = request.args.get('type', 'transbank')

                logger.info(f"Solicitud de detalle recibida: type={tipo} print_on_pos={print_on_pos}")

                if tipo == 'getnet':
                    if not self.getnet:
                        return jsonify({"status": "error", "message": "POS Getnet no habilitado en esta máquina"}), 503

                    # Command 101 (Último Comprobante): consulta directa a la máquina.
                    result = self.getnet.get_last_receipt(timeout=15)
                    if result["status"] == "found":
                        return jsonify(result), 200
                    elif result["status"] == "not_found":
                        return jsonify(result), 404
                    else:
                        return jsonify(result), 500

                # Transbank (comportamiento original)
                result = self.pos.do_details_with_timeout(print_on_pos)

                if result["status"] == "success":
                    return jsonify(result), 200
                elif result["status"] == "failed":
                    return jsonify(result), 400
                else:
                    return jsonify(result), 500

            except Exception as e:
                logger.exception("Error en endpoint /pago/detalle")
                return jsonify({
                    "status": "error",
                    "message": str(e)
                }), 500

        @app.route("/pago", methods=["POST", "GET"])
        @require_basic_auth
        def http_pago():
            if request.method == "GET":
                data = request.args.to_dict()
            else:
                data = request.get_json(force=True, silent=True) or {}
            data = _entrada(data)
            id_sucursal = str(data.get("id_sucursal"))
            nombre_caja = str(data.get("nombre_caja"))
            pos_type = _tipo_request(data.get("type"))
            logger.info("[HTTP /pago] Petición (%s): id_sucursal=%s, nombre_caja=%s, type=%s", request.method, id_sucursal, nombre_caja, data.get("type"))

            # Validar solo id_sucursal (nombre_caja puede ser cualquier caja del cliente)
            if id_sucursal != str(ID_SUCURSAL):
                return jsonify({
                    "status": "forbidden",
                    "message": f"El id_sucursal '{id_sucursal}' no corresponde a este servicio (esperado: {ID_SUCURSAL})."
                }), 403

            if not id_sucursal or not nombre_caja or not pos_type:
                return jsonify({"error": "Es necesario el id_sucursal, nombre_caja y el tipo(type) de pos"}), 400

            custom_timeout = data.get("timeout")
            if custom_timeout:
                try:
                    timeout = int(custom_timeout)
                    timeout = max(30, min(timeout, 300))
                    logger.info("Timeout personalizado: %s segundos", timeout)
                except (ValueError, TypeError):
                    logger.warning("Timeout inválido, usando default")
                    timeout = TIMEOUT_SERVER
            else:
                timeout = TIMEOUT_SERVER

            if pos_type == "getnet":
                if not self.getnet:
                    return jsonify({
                        "error": "POS Getnet no habilitado en esta máquina"
                    }), 503
                
                # Validar campos obligatorios para Getnet
                terminal_id = data.get("terminal_id")
                amount = data.get("amount")
                custom_timeout = data.get("timeout")
                
                # Validaciones obligatorias
                if terminal_id is None:
                    return jsonify({"error": "Es necesario el terminal_id de Getnet"}), 400
                if amount is None:
                    return jsonify({"error": "Es necesario el monto de venta de Getnet"}), 400
                amount = _monto_valido(amount)
                if amount is None:
                    return jsonify({"error": "El monto debe ser un entero positivo"}), 400
                if custom_timeout is None:
                    return jsonify({"error": "Es necesario el timeout de Getnet"}), 400
                
                # Validar rango de timeout
                try:
                    timeout = int(custom_timeout)
                    timeout = max(30, min(timeout, 300))
                    logger.info("Timeout Getnet: %s segundos", timeout)
                except (ValueError, TypeError):
                    return jsonify({"error": "timeout debe ser un número válido"}), 400
                
                # Validar que terminal_id coincida con el del .env
                if str(terminal_id) != GETNET_TERMINAL_ID:
                    return jsonify({
                        "status": "forbidden",
                        "message": "El terminal_id no coincide con las credenciales de configuración "
                                   "(se pidió type=getnet: debe ser el GETNET_TERMINAL_ID)"
                    }), 403

                # tx_id opcional del cliente: como la petición puede quedar abierta
                # mucho tiempo (cable desconectado), el cliente necesita conocerlo de
                # antemano para poder cancelar la espera con /pago/cancelar/<tx_id>.
                tx_id_cliente = data.get("tx_id")
                if tx_id_cliente is not None:
                    tx_id_cliente = str(tx_id_cliente).strip()
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tx_id_cliente):
                        return jsonify({"error": "tx_id inválido (solo letras, números, '-' y '_', máx. 64)"}), 400
                    if tx_store.obtener(tx_id_cliente):
                        return jsonify({"error": "tx_id ya fue usado en otra venta", "transaction_id": tx_id_cliente}), 409

                runner_id_sucursal = str(ID_SUCURSAL)
                runner_nombre_caja = str(NOMBRE_CAJA)

                # Si una venta anterior en esta caja quedó sin confirmar (PENDIENTE o
                # INDETERMINADA), no se permite intentar una nueva hasta resolverla:
                # el cliente pudo haber pagado ya en el POS y no queremos cobrarle
                # dos veces. Esta verificación es persistente (sobrevive un reinicio
                # del servicio), a diferencia de busy_boxes que es solo en memoria.
                pendiente = tx_store.hay_pendiente_sin_resolver(runner_id_sucursal, runner_nombre_caja)
                if pendiente:
                    logger.warning("Venta Getnet bloqueada: transacción previa sin resolver tx=%s", pendiente)
                    getnet_log.info(
                        f"🔒 Venta rechazada por el sistema: la caja {runner_nombre_caja} tiene una transacción "
                        f"anterior ({pendiente}) sin confirmar. No se le pidió nada al POS. "
                        "Debe resolverse desde el panel antes de poder vender de nuevo."
                    )
                    return jsonify({
                        "status": "unresolved_previous_transaction",
                        "message": "Hay una venta Getnet previa en esta caja sin confirmar. "
                                    "Debe resolverse desde el panel antes de intentar una nueva venta.",
                        "pending_transaction_id": pendiente,
                    }), 409

                key = (runner_id_sucursal, runner_nombre_caja)
                with self.busy_lock:
                    if key in self.busy_boxes:
                        logger.warning("Caja ocupada (runner): %s/%s", runner_id_sucursal, runner_nombre_caja)
                        getnet_log.info(
                            f"🔁 Venta rechazada: la caja {runner_nombre_caja} ya tiene otra venta Getnet en curso "
                            f"(monto {fmt_monto(amount)} intentado)."
                        )
                        return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                    self.busy_boxes.add(key)

                tx_id = tx_id_cliente or str(uuid.uuid4())
                # TicketNumber ("Número de Boleta") del protocolo Getnet: tiene que ser
                # puramente numérico. En pruebas con hardware real, tickets de 22 y 13
                # dígitos (milisegundos desde epoch) se ignoraban en silencio, mientras que
                # uno de 10 dígitos (segundos desde epoch) sí funcionó — el firmware del
                # POS parece guardar este campo como entero de 32 bits internamente (máx.
                # 2.147.483.647) aunque el protocolo lo declare como string: milisegundos
                # desde epoch desborda ese rango, segundos desde epoch no. Se usa segundos.
                ticket = str(int(time.time()))

                tx_store.registrar_intento(
                    tx_id=tx_id,
                    tipo="getnet",
                    ticket=ticket,
                    terminal_id=terminal_id,
                    id_sucursal=runner_id_sucursal,
                    nombre_caja=runner_nombre_caja,
                    monto=amount,
                    client_id_sucursal=id_sucursal,
                    client_nombre_caja=nombre_caja,
                )

                with self.tasks_lock:
                    event = threading.Event()
                    self.tasks[tx_id] = {
                        "event": event,
                        "cancel": threading.Event(),
                        "type": "getnet",
                        "result": None,
                        "estado": "PENDIENTE",
                        "id_sucursal": runner_id_sucursal,
                        "nombre_caja": runner_nombre_caja,
                        "client_id_sucursal": id_sucursal,
                        "client_nombre_caja": nombre_caja,
                        "timestamp": time.time(),
                        "timeout": timeout
                    }

                task_payload = {
                    "tx_id": tx_id,
                    "id_sucursal": runner_id_sucursal,
                    "nombre_caja": runner_nombre_caja,
                    "client_id_sucursal": id_sucursal,
                    "client_nombre_caja": nombre_caja,
                    "type": "getnet",
                    "terminal_id": terminal_id,
                    "amount": amount,
                    "timeout": timeout,
                    "ticket": ticket,
                }

                q = self.ensure_queue(runner_id_sucursal, runner_nombre_caja)
                q.put(task_payload)
                logger.info(
                    "Tarea Getnet encolada: tx=%s ticket=%s client=%s/%s runner=%s/%s",
                    tx_id,
                    ticket,
                    id_sucursal,
                    nombre_caja,
                    runner_id_sucursal,
                    runner_nombre_caja,
                )

                # Sin límite de tiempo: `timeout` es cuánto se espera la respuesta del
                # POS con el cable conectado; si la venta queda sin confirmar, el worker
                # sigue esperando a que el POS vuelva (esperar_resolucion_getnet) y la
                # petición se mantiene abierta hasta el resultado real o hasta que el
                # cliente cancele con /pago/cancelar/<tx_id>.
                start_wait = time.time()
                while not event.wait(timeout=1):
                    if self._stop_local_worker.is_set():
                        break
                wait_time = time.time() - start_wait

                with self.tasks_lock:
                    entry = self.tasks.pop(tx_id, {})
                    res = entry.get("result")
                    final_estado = entry.get("estado", "DESCONOCIDO")

                with self.busy_lock:
                    self.busy_boxes.discard(key)

                logger.info("Respuesta Getnet: tx=%s, estado=%s, tiempo=%.2fs",
                            tx_id, final_estado, wait_time)

                respuesta_pago = {
                    "transaction_id": tx_id,
                    "result": res,
                    "estado": final_estado,
                    "tiempo_total": round(wait_time, 2)
                }
                if final_estado == "INDETERMINADA":
                    # No es un resultado final: el cliente pudo haber pagado.
                    respuesta_pago["message"] = (
                        "Venta sin confirmar: no reintentar ni darla por rechazada. Se reconcilia sola cuando "
                        f"el POS vuelva a responder; consulte /pago/estado/{tx_id} hasta obtener APROBADO o RECHAZADO."
                    )
                # Se deja en el log el mismo JSON que recibe el cliente en /pago,
                # para poder procesarlo directamente desde el log sin depender
                # de que el cliente haya guardado la respuesta HTTP original.
                getnet_log.info(
                    f"📄 Respuesta /pago tx={tx_id} (estado={final_estado}): "
                    f"{json.dumps(respuesta_pago, ensure_ascii=False, default=str)}"
                )
                return jsonify(respuesta_pago)

            # TRANSBANK FLOW
            elif pos_type == "transbank":
                
                #validar campos obligatorios
                terminal_id_transbank = data.get("terminal_id")
                amount_transbank = data.get("amount")
                timeout_transbank = data.get("timeout")
                
                #validaciones obligatorias
                if terminal_id_transbank is None:
                    return jsonify({"error": "Es necesario el terminal_id de Transbank"}), 400
                if amount_transbank is None:
                    return jsonify({"error": "Es necesario el monto de venta de Transbank"}), 400
                amount_transbank = _monto_valido(amount_transbank)
                if amount_transbank is None:
                    return jsonify({"error": "El monto debe ser un entero positivo"}), 400
                if timeout_transbank is None:
                    return jsonify({"error": "Es necesario el timeout de Transbank"}), 400

                #validar rango de timeout
                try:
                    timeout_transbank = int(timeout_transbank)
                    timeout_transbank = max(30, min(timeout_transbank, 300))
                    logger.info("Timeout Transbank: %s segundos", timeout_transbank)
                except (ValueError, TypeError):
                    return jsonify({"error": "timeout debe ser un número válido"}), 400

                #validar que terminal_id coincida con el del .env
                if str(terminal_id_transbank) != TRANSBANK_TERMINAL_ID:
                    return jsonify({
                        "status": "forbidden",
                        "message": "El terminal_id no coincide con las credenciales de configuración "
                                   "(se pidió type=transbank: debe ser el TRANSBANK_TERMINAL_ID)"
                    }), 403

                runner_id_sucursal = str(ID_SUCURSAL)
                runner_nombre_caja = str(NOMBRE_CAJA)

                # Igual que Getnet: una venta previa sin confirmar bloquea la caja.
                pendiente = tx_store.hay_pendiente_sin_resolver(runner_id_sucursal, runner_nombre_caja)
                if pendiente:
                    logger.warning("Venta Transbank bloqueada: transacción previa sin resolver tx=%s", pendiente)
                    transbank_log.info(
                        f"🔒 Venta rechazada por el sistema: la caja {runner_nombre_caja} tiene una transacción "
                        f"anterior ({pendiente}) sin confirmar. No se le pidió nada al POS. "
                        "Debe resolverse desde el panel antes de poder vender de nuevo."
                    )
                    return jsonify({
                        "status": "unresolved_previous_transaction",
                        "message": "Hay una venta previa en esta caja sin confirmar. "
                                   "Debe resolverse desde el panel antes de intentar una nueva venta.",
                        "pending_transaction_id": pendiente,
                    }), 409

                key = (runner_id_sucursal, runner_nombre_caja)
                with self.busy_lock:
                    if key in self.busy_boxes:
                        logger.warning("Caja ocupada (runner): %s/%s", runner_id_sucursal, runner_nombre_caja)
                        return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                    self.busy_boxes.add(key)

                tx_id = str(uuid.uuid4())
                ticket_transbank = time.strftime("%H%M%S")

                tx_store.registrar_intento(
                    tx_id=tx_id,
                    tipo="transbank",
                    ticket=ticket_transbank,
                    terminal_id=terminal_id_transbank,
                    id_sucursal=runner_id_sucursal,
                    nombre_caja=runner_nombre_caja,
                    monto=amount_transbank,
                    client_id_sucursal=id_sucursal,
                    client_nombre_caja=nombre_caja,
                )

                with self.tasks_lock:
                    event = threading.Event()
                    self.tasks[tx_id] = {
                        "event": event,
                        "cancel": threading.Event(),
                        "type": "transbank",
                        "result": None,
                        "estado": "PENDIENTE",
                        "id_sucursal": runner_id_sucursal,
                        "nombre_caja": runner_nombre_caja,
                        "client_id_sucursal": id_sucursal,
                        "client_nombre_caja": nombre_caja,
                        "timestamp": time.time(),
                        "timeout": timeout_transbank
                    }

                task_payload = {
                    "tx_id": tx_id,
                    "id_sucursal": runner_id_sucursal,
                    "nombre_caja": runner_nombre_caja,
                    "client_id_sucursal": id_sucursal,
                    "client_nombre_caja": nombre_caja,
                    "type": "transbank",
                    "id_terminal": terminal_id_transbank,
                    "amount": amount_transbank,
                    "timeout": timeout_transbank,
                    "ticket": ticket_transbank,
                }

                q = self.ensure_queue(runner_id_sucursal, runner_nombre_caja)
                q.put(task_payload)
                logger.info(
                    "Tarea encolada: tx=%s, timeout=%s, client=%s/%s runner=%s/%s",
                    tx_id,
                    timeout_transbank,
                    id_sucursal,
                    nombre_caja,
                    runner_id_sucursal,
                    runner_nombre_caja,
                )

                # Igual que Getnet: `timeout` es cuánto se espera la respuesta del POS
                # con el cable conectado; si la venta queda sin confirmar (ej. se
                # desconectó el cable), el worker espera a que el POS vuelva
                # (esperar_resolucion_transbank) y la petición sigue abierta hasta el
                # resultado real, TRANSBANK_MAX_ESPERA o /pago/cancelar/<tx_id>.
                start_wait = time.time()
                while not event.wait(timeout=1):
                    if self._stop_local_worker.is_set():
                        break
                wait_time = time.time() - start_wait

                with self.tasks_lock:
                    entry = self.tasks.pop(tx_id, {})
                    res = entry.get("result")
                    final_estado = entry.get("estado", "DESCONOCIDO")

                with self.busy_lock:
                    self.busy_boxes.discard(key)

                logger.info("Respuesta: tx=%s, estado=%s, tiempo=%.2fs", tx_id, final_estado, wait_time)

                respuesta_pago = {
                    "transaction_id": tx_id,
                    "result": res,
                    "estado": final_estado,
                    "tiempo_total": round(wait_time, 2)
                }
                if final_estado == "INDETERMINADA":
                    respuesta_pago["message"] = (
                        "Venta sin confirmar: no reintentar ni darla por rechazada. Se reconcilia sola cuando "
                        f"el POS vuelva a responder; consulte /pago/estado/{tx_id} hasta obtener APROBADO o RECHAZADO."
                    )
                transbank_log.info(
                    f"📄 Respuesta /pago tx={tx_id} (estado={final_estado}): "
                    f"{json.dumps(respuesta_pago, ensure_ascii=False, default=str)}"
                )
                return jsonify(respuesta_pago)

            # MERCADO PAGO FLOW
            elif pos_type == "mercadopago":
                if not USAR_MP:
                    mercadopago_log.info(f"❌ Venta rechazada por el sistema ({nombre_caja}): Mercado Pago no está habilitado en esta máquina (USAR_MP).")
                    return jsonify({"error": "Mercado Pago no habilitado en esta máquina (USAR_MP)"}), 503
                if not _caja_habilitada_mp(id_sucursal, nombre_caja):
                    mercadopago_log.info(f"❌ Venta rechazada por el sistema: la caja {id_sucursal}:{nombre_caja} no está habilitada para Mercado Pago (ALLOWED_MP).")
                    return jsonify({"error": f"La caja {id_sucursal}:{nombre_caja} no está habilitada para Mercado Pago (ALLOWED_MP)"}), 403
                # mp_terminal_id: campo propio para no mezclarlo con el terminal_id de Getnet/Transbank.
                terminal_id, terminal_error = resolver_terminal_mp(data.get("mp_terminal_id") or data.get("terminal_id"))
                access_token = None
                if ALLOW_MP_TOKEN_IN_REQUEST:
                    access_token = data.get("access_token")
                if not access_token:
                    access_token = MP_ACCESS_TOKEN
                amount = data.get("amount")
                if not access_token:
                    return jsonify({"error": "Falta configuración MP_ACCESS_TOKEN"}), 500
                if amount is None or amount == 0:
                    return jsonify({"error": "Es necesario el monto de venta"}), 400
                amount = _monto_valido(amount)
                if amount is None:
                    return jsonify({"error": "El monto debe ser un entero positivo"}), 400
                if terminal_error:
                    mercadopago_log.info(f"❌ Venta rechazada por el sistema (monto {fmt_monto(amount)}): {terminal_error}")
                    return jsonify({"error": terminal_error, "terminales_configurados": MP_TERMINALES}), 400

                # El tx_id viaja a MP como external_reference y como base de la
                # X-Idempotency-Key: se valida igual que en Getnet.
                tx_id_input = data.get("tx_id") or data.get("external_reference")
                if tx_id_input is not None:
                    tx_id_input = str(tx_id_input).strip()
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tx_id_input):
                        return jsonify({"error": "tx_id inválido (solo letras, números, '-' y '_', máx. 64)"}), 400
                    if tx_store.obtener(tx_id_input):
                        return jsonify({"error": "tx_id ya fue usado en otra venta", "transaction_id": tx_id_input}), 409
                tx_id = tx_id_input or str(uuid.uuid4())

                # El recurso físico es el terminal: una orden sin confirmar en él
                # bloquea nuevas ventas en ESE terminal (no en las cajas Getnet/Transbank).
                runner_id_sucursal = str(ID_SUCURSAL)
                caja_mp = _mayus(f"mp:{terminal_id}")
                pendiente = tx_store.hay_pendiente_sin_resolver(runner_id_sucursal, caja_mp)
                if pendiente:
                    logger.warning("Venta MP bloqueada: transacción previa sin resolver tx=%s", pendiente)
                    mercadopago_log.info(
                        f"🔒 Venta rechazada por el sistema: el terminal {terminal_id} tiene una orden anterior "
                        f"({pendiente}) sin confirmar. No se creó ninguna orden nueva."
                    )
                    return jsonify({
                        "status": "unresolved_previous_transaction",
                        "message": "Hay una venta Mercado Pago previa en este terminal sin confirmar. "
                                   "Se reconcilia sola o puede resolverse desde el panel.",
                        "pending_transaction_id": pendiente,
                    }), 409

                key = (runner_id_sucursal, caja_mp)
                with self.busy_lock:
                    if key in self.busy_boxes:
                        mercadopago_log.info(
                            f"🔁 Venta rechazada: el terminal {terminal_id} ya tiene otro cobro en curso "
                            f"(monto {fmt_monto(amount)} intentado, caja {nombre_caja})."
                        )
                        return jsonify({"status": "busy", "message": "Terminal Mercado Pago ocupado"}), 429
                    self.busy_boxes.add(key)

                cancel = threading.Event()
                res, estado = {"status": "error", "message": "Error interno"}, "ERROR"
                try:
                    tx_store.registrar_intento(
                        tx_id=tx_id,
                        tipo="mercadopago",
                        ticket=None,  # se completa con el id de la orden apenas MP la crea
                        terminal_id=terminal_id,
                        id_sucursal=runner_id_sucursal,
                        nombre_caja=caja_mp,
                        monto=amount,
                        client_id_sucursal=id_sucursal,
                        client_nombre_caja=nombre_caja,
                    )
                    with self.tasks_lock:
                        self.tasks[tx_id] = {
                            "event": threading.Event(),
                            "cancel": cancel,
                            "type": "mercadopago",
                            "result": None,
                            "estado": "PENDIENTE",
                            "id_sucursal": runner_id_sucursal,
                            "nombre_caja": caja_mp,
                            "timestamp": time.time(),
                            "timeout": timeout,
                        }

                    res = process_mercadopago(
                        terminal_id,
                        access_token,
                        amount,
                        timeout=timeout,
                        idempotency_key=f"mp_{tx_id}",
                        external_reference=tx_id,
                        cancel=cancel,
                        on_order_created=lambda order_id: tx_store.asignar_ticket(tx_id, order_id),
                    )
                    estado = _estado_db(res)
                    tx = tx_store.obtener(tx_id)
                    if tx and tx.get("estado") in tx_store.ESTADOS_SIN_RESOLVER:
                        tx_store.actualizar_estado(tx_id, estado, raw_response=res)
                    elif tx:
                        estado = tx.get("estado")  # resuelta por otra vía mientras tanto
                finally:
                    with self.tasks_lock:
                        entry = self.tasks.pop(tx_id, None)
                    if entry:
                        entry["event"].set()
                    with self.busy_lock:
                        self.busy_boxes.discard(key)

                status = res.get("status")
                response_obj = res.get("response") or {}
                status_detail = None
                if isinstance(response_obj, dict):
                    status_detail = response_obj.get("status_detail")

                data_out = {
                    "status": status,
                    "status_detail": status_detail,
                    "order_id": res.get("order_id") or response_obj.get("id") if isinstance(response_obj, dict) else res.get("order_id"),
                    "response": response_obj,
                }
                success = status in ("approved", "success")

                respuesta = {
                    "success": success,
                    "transaction_id": tx_id,
                    "estado": estado,
                    "data": data_out,
                    **res,
                }
                if estado == "INDETERMINADA":
                    respuesta["message"] = (
                        f"{res.get('message', 'Venta sin confirmar.')} "
                        f"Consulte /pago/estado/{tx_id} hasta obtener APROBADO o RECHAZADO."
                    )
                # Igual que en Getnet: el mismo JSON que recibe el cliente queda en el
                # log, para poder procesarlo aunque el cliente no haya guardado la respuesta.
                mercadopago_log.info(
                    f"📄 Respuesta /pago tx={tx_id} (estado={estado}): "
                    f"{json.dumps(respuesta, ensure_ascii=False, default=str)}"
                )
                return jsonify(respuesta), 200

            # TUU FLOW (Pago remoto por API, sin cancelación)
            elif pos_type == "tuu":
                return self._pago_tuu(data, id_sucursal, nombre_caja, timeout)

            else:
                return jsonify({"error": "Tipo POS no soportado"}), 400

        @app.route("/status")
        def http_status():
            with self.agents_lock:
                agents_count = sum(len(boxes) for boxes in self.agents.values())
            return jsonify({
                "status": "ok",
                "port": HTTP_PORT,
                "id_sucursal": ID_SUCURSAL,
                "nombre_caja": NOMBRE_CAJA,
                "id_terminal": ID_TERMINAL,
                "terminal_transbank": TRANSBANK_TERMINAL_ID,
                "terminal_getnet": GETNET_TERMINAL_ID,
                "usa_pos_fisico": self.pos.is_online() if self.pos else False,
                "agents_count": agents_count,
                "current_port": self.pos.get_current_port() if self.pos else None
            })

        @app.route("/online", methods=["POST", "GET"])
        @require_basic_auth
        def http_online():
            try:
                if request.method == "GET":
                    data = request.args.to_dict()
                else:
                    data = request.get_json(force=True, silent=True) or {}
                data = _entrada(data)

                tipo = _tipo_request(data.get("type"))
                logger.info("[HTTP /online] método=%s tipo=%s", request.method, data.get("type"))

                if tipo == "transbank":
                    if not self.pos:
                        return jsonify({
                            "success": True,
                            "online": False,
                            "puerto": None,
                            "message": "POS no inicializado"
                        }), 503

                    pos_online = False
                    try:
                        logger.info("[HTTP /online] verificando POS transbank...")
                        pos_online = self.pos.is_online()
                        logger.info("[HTTP /online] POS online=%s", pos_online)
                    except Exception as e:
                        logger.warning("Error al verificar estado del POS: %s", e)
                        pos_online = False

                    return jsonify({
                        "success": True,
                        "online": pos_online,
                        "puerto": self.pos.get_current_port(),
                        "message": "POS conectado" if pos_online else "POS no detectado"
                    }), 200 if pos_online else 503
                elif tipo == "getnet":
                    if not self.getnet:
                        return jsonify({
                            "success": False,
                            "online": False,
                            "message": "Getnet no habilitado"
                        }), 503
                    
                    online = self.getnet.is_online()
                    
                    return jsonify({
                        "success": True,
                        "online": online,
                        "puerto": self.getnet.get_current_port(),
                        "message": "Getnet conectado" if online else "Getnet no detectado"
                    }), 200 if online else 503
                elif tipo == "mercadopago":
                    token_ok = bool(MP_ACCESS_TOKEN)
                    online = USAR_MP and token_ok
                    return jsonify({
                        "success": True,
                        "online": online,
                        "terminales": MP_TERMINALES,
                        "message": ("Mercado Pago configurado" if online else
                                    "Mercado Pago no habilitado (USAR_MP)" if not USAR_MP else
                                    "Falta MP_ACCESS_TOKEN en configuración"),
                    }), 200 if online else 503
                elif tipo == "tuu":
                    # Solo revisa la configuración: no gasta cuota de la API de TUU.
                    key_ok = bool(TUU_API_KEY) or ALLOW_TUU_KEY_IN_REQUEST
                    faltan = [n for n, ok in (("USAR_TUU=true", USAR_TUU),
                                              ("TUU_API_KEY o ALLOW_TUU_KEY_IN_REQUEST=true", key_ok)) if not ok]
                    return jsonify({
                        "success": True,
                        "online": not faltan,
                        "dispositivos": TUU_DISPOSITIVOS,
                        "message": "TUU configurado" if not faltan else f"Falta configurar: {', '.join(faltan)}",
                    }), 200 if not faltan else 503
                else:
                    # Sin tipo especificado → ping simple
                    logger.info("[HTTP /online] ping simple (sin tipo)")
                    return jsonify({
                        "success": True,
                        "message": "Servicio activo"
                    }), 200
                    
            except Exception as e:
                logger.error("Error en /online: %s", e)
                return jsonify({
                    "success": False,
                    "online": False,
                    "message": f"Error verificando POS: {e}"
            }), 500

        @app.route("/debug/queues")
        @require_basic_auth
        def debug_queues():
            with self.queues_lock:
                info = {}
                for cid, boxes in self.queues.items():
                    for box, q in boxes.items():
                        info[f"{cid}:{box}"] = {"queued": q.qsize()}
            return jsonify(info)

        @app.route("/pago/iniciar", methods=["POST", "GET"])
        @require_basic_auth
        def http_pago_iniciar():
            if request.method == "GET":
                data = request.args.to_dict()
            else:
                data = request.get_json(force=True, silent=True) or {}
            data = _entrada(data)
            id_sucursal = data.get("id_sucursal")
            nombre_caja = data.get("nombre_caja")

            # Validación contra el .env
            if id_sucursal != str(ID_SUCURSAL) or nombre_caja != str(NOMBRE_CAJA):
                return jsonify({
                    "status": "forbidden",
                    "message": f"El id_sucursal y el nombre_caja no coinciden con la configuración de esta máquina"
                }), 403

            pos_type = _tipo_request(data.get("type"))

            logger.info("[HTTP /pago/iniciar] Petición recibida: %s", json.dumps(data, ensure_ascii=False))

            if id_sucursal is not None:
                id_sucursal = str(id_sucursal)
            if nombre_caja is not None:
                nombre_caja = str(nombre_caja)

            if not id_sucursal or not nombre_caja or not pos_type:
                return jsonify({"error": "id_sucursal, nombre_caja y type requeridos"}), 400

            pendiente = tx_store.hay_pendiente_sin_resolver(id_sucursal, nombre_caja)
            if pendiente:
                logger.warning("Pago iniciar bloqueado: transacción previa sin resolver tx=%s", pendiente)
                return jsonify({
                    "status": "unresolved_previous_transaction",
                    "message": "Hay una venta previa en esta caja sin confirmar. "
                               "Debe resolverse desde el panel antes de intentar una nueva venta.",
                    "pending_transaction_id": pendiente,
                }), 409

            key = (id_sucursal, nombre_caja)
            with self.busy_lock:
                if key in self.busy_boxes:
                    logger.warning("Caja ocupada: %s/%s", id_sucursal, nombre_caja)
                    return jsonify({"status": "busy", "message": "Caja ocupada"}), 429
                self.busy_boxes.add(key)

            tx_id = str(uuid.uuid4())

            custom_timeout = data.get("timeout", MAX_TRANSACTION_TIME)
            try:
                custom_timeout = max(30, min(int(custom_timeout), 300))
            except:
                custom_timeout = MAX_TRANSACTION_TIME

            if pos_type == "transbank":
                amount = data.get("amount")
                id_terminal = data.get("id_terminal", TRANSBANK_TERMINAL_ID)

                amount = _monto_valido(amount)
                if amount is None:
                    with self.busy_lock:
                        self.busy_boxes.discard(key)
                    return jsonify({"error": "amount requerido (entero positivo)"}), 400

                ticket = time.strftime("%H%M%S")
                tx_store.registrar_intento(
                    tx_id=tx_id,
                    tipo="transbank",
                    ticket=ticket,
                    terminal_id=id_terminal,
                    id_sucursal=id_sucursal,
                    nombre_caja=nombre_caja,
                    monto=amount,
                )

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
                        "cancel": threading.Event(),
                    }

                task_payload = {
                    "tx_id": tx_id,
                    "id_sucursal": id_sucursal,
                    "nombre_caja": nombre_caja,
                    "type": "transbank",
                    "id_terminal": id_terminal,
                    "amount": amount,
                    "timeout": custom_timeout,
                    "ticket": ticket,
                }

                q = self.ensure_queue(id_sucursal, nombre_caja)
                q.put(task_payload)

                logger.info("Pago iniciado tx=%s -> %s/%s (respuesta inmediata)", tx_id, id_sucursal, nombre_caja)

                return jsonify({
                    "status": "ok",
                    "transaction_id": tx_id,
                    "estado": "PENDIENTE",
                    "timeout_configurado": custom_timeout,
                    "message": "Pago iniciado. Use /pago/estado/{tx_id} para consultar resultado"
                }), 202

            else:
                with self.busy_lock:
                    self.busy_boxes.discard(key)
                return jsonify({"error": "Tipo POS no soportado"}), 400

        @app.route("/pago/estado/<tx_id>", methods=["GET"])
        @require_basic_auth
        def http_pago_estado(tx_id):
            with self.tasks_lock:
                task = self.tasks.get(tx_id)
                if not task:
                    # La tarea en memoria pudo haberse limpiado (más de 10 min sin
                    # resolverse) sin que la transacción esté realmente cerrada.
                    # La base persistente sigue teniendo el estado real.
                    tx = tx_store.obtener(tx_id)
                    if tx:
                        return jsonify({
                            "transaction_id": tx_id,
                            "estado": tx.get("estado"),
                            "result": json.loads(tx["raw_response"]) if tx.get("raw_response") else None,
                            "fuente": "transaction_store",
                        }), 200
                    return jsonify({"error": "Transacción no encontrada", "transaction_id": tx_id}), 404

                if task["result"] is None:
                    # En curso: PENDIENTE, PROCESANDO o ESPERANDO_POS (Getnet esperando
                    # que el POS vuelva a conectarse para confirmar la venta).
                    return jsonify({
                        "transaction_id": tx_id,
                        "estado": task.get("estado", "PENDIENTE"),
                        "tiempo_transcurrido": int(time.time() - task["timestamp"])
                    }), 200

                result = task.get("result")
                estado = "DESCONOCIDO"
                if result:
                    status = result.get("status")
                    if status in ("success", "approved"):
                        estado = "APROBADO"
                    elif status == "error":
                        estado = "ERROR"
                    elif status == "indeterminada":
                        # No confundir con RECHAZADO: el cliente pudo haber pagado
                        # igual en el POS y sigue pendiente de reconciliación.
                        estado = "INDETERMINADA"
                        tx = tx_store.obtener(tx_id)
                        if tx and tx.get("estado") not in tx_store.ESTADOS_SIN_RESOLVER:
                            # Ya se reconcilió (monitor, respuesta tardía u operador).
                            estado = tx.get("estado")
                    elif status == "timeout":
                        estado = "TIMEOUT"
                    else:
                        estado = "RECHAZADO"
                task["estado"] = estado

                return jsonify({
                    "transaction_id": tx_id,
                    "estado": estado,
                    "result": result,
                    "tiempo_total": int(time.time() - task["timestamp"])
                }), 200

        @app.route("/pago/cancelar/<tx_id>", methods=["POST"])
        @require_basic_auth
        def http_pago_cancelar(tx_id):
            with self.tasks_lock:
                task = self.tasks.get(tx_id)
                if not task:
                    return jsonify({"error": "Transacción no encontrada"}), 404
                if task.get("result") is not None:
                    return jsonify({"error": "La transacción ya finalizó", "estado": task.get("estado")}), 400
                tipo = task.get("type")

            if tipo == "mercadopago":
                # La orden sigue abierta en el terminal: /pago la cancela en MP y
                # responde el resultado real (CANCELADO -> RECHAZADO, o INDETERMINADA
                # si el cliente ya estaba pagando).
                task["cancel"].set()
                mercadopago_log.info(f"✋ El cliente pidió cancelar la venta (tx {tx_id}, {task.get('nombre_caja')}).")
                return jsonify({"status": "ok", "transaction_id": tx_id, "estado": "CANCELANDO",
                                "message": "Se pidió cancelar la orden en Mercado Pago. El resultado final llega "
                                           f"en la respuesta de /pago o en /pago/estado/{tx_id}."}), 202

            if tipo == "tuu":
                # TUU no permite retirar la solicitud por API: solo se deja de esperar.
                # /pago responde INDETERMINADA; si el cobro se cancela en la máquina, el
                # GET pasa a "Canceled" y el monitor la resuelve sola.
                task["cancel"].set()
                tuu_log.info(f"✋ El cliente dejó de esperar la venta (tx {tx_id}, {task.get('nombre_caja')}); "
                             "la solicitud sigue viva en TUU.")
                return jsonify({"status": "ok", "transaction_id": tx_id, "estado": "INDETERMINADA",
                                "message": "TUU no permite cancelar por API: cancele el cobro en la máquina (Modo "
                                           "Integración). La venta queda sin confirmar y se reconcilia sola; "
                                           "no reintente el cobro."}), 202

            # Todavía en la cola (Transbank o Getnet): nunca llegó al POS, así que
            # se cancela de verdad y el worker no la envía.
            with self.tasks_lock:
                en_cola = task.get("estado") == "PENDIENTE" and task.get("result") is None
                if en_cola:
                    self._canceladas_en_cola.add(tx_id)
                    task["estado"] = "CANCELADO"
                    task["result"] = {"status": "cancelled", "message": "Cancelada antes de enviarse al POS"}
                    if task.get("cancel"):
                        task["cancel"].set()
                    task["event"].set()
            if en_cola:
                return self._cancelada_antes_de_enviar(tx_id, task)

            if tipo == "getnet" and self.getnet:
                if self.getnet.solicitar_cancelacion():
                    # La venta está esperando en el POS: se le pide cancelarla (Command 116)
                    # y se espera a que el worker informe el resultado.
                    if task["event"].wait(timeout=ESPERA_CANCELACION_GETNET):
                        estado = task.get("estado")
                        logger.info("Venta Getnet %s terminó tras pedir cancelación: %s", tx_id, estado)
                        return jsonify({"status": "ok", "transaction_id": tx_id, "estado": estado,
                                        "result": task.get("result")}), 200
                elif task.get("estado") == "PROCESANDO":
                    # El worker la tomó pero todavía se está conectando al puerto: el
                    # módulo Getnet revisa esta señal justo antes de enviar la venta (y
                    # si ya la envió, manda el Command 116).
                    task["cancel"].set()
                    if task["event"].wait(timeout=ESPERA_CANCELACION_GETNET):
                        estado = task.get("estado")
                        logger.info("Venta Getnet %s terminó tras pedir cancelación: %s", tx_id, estado)
                        return jsonify({"status": "ok", "transaction_id": tx_id, "estado": estado,
                                        "result": task.get("result")}), 200

            with self.tasks_lock:
                if task.get("result") is not None:
                    return jsonify({"status": "ok", "transaction_id": tx_id, "estado": task.get("estado"),
                                    "result": task.get("result")}), 200

                if tipo == "getnet" or (tipo == "transbank" and task.get("estado") == "ESPERANDO_POS"):
                    # La venta ya pudo llegar al POS: cancelar solo corta la espera de
                    # la petición. La transacción sigue INDETERMINADA (caja bloqueada) y
                    # el monitor la sigue reconciliando en background.
                    task["cancel"].set()
                    task["result"] = {
                        "status": "indeterminada",
                        "message": "Se dejó de esperar por pedido del cliente. La venta sigue sin confirmar: "
                                   f"se reconcilia sola cuando el POS vuelva; consulte /pago/estado/{tx_id}.",
                    }
                    task["estado"] = "INDETERMINADA"
                    task["event"].set()
                    logger.info("Espera %s cancelada por el cliente: %s", tipo, tx_id)
                    (getnet_log if tipo == "getnet" else transbank_log).info(
                        f"✋ El cliente dejó de esperar la venta (tx {tx_id}); sigue INDETERMINADA.")
                    return jsonify({"status": "ok", "transaction_id": tx_id, "estado": "INDETERMINADA",
                                    "message": task["result"]["message"]}), 200

            # Transbank con la venta ya en el POS: el SDK no tiene comando para
            # cancelarla. Responder "CANCELADO" y liberar la caja dejaba al cliente
            # pagando mientras el sistema decía que no hubo venta (doble cobro).
            transbank_log.info(
                f"✋ Se pidió cancelar la venta (tx {tx_id}) pero ya está en el POS Transbank: no se puede cancelar "
                "desde el sistema. Debe cancelarse en la máquina; el resultado llega igual."
            )
            return jsonify({
                "status": "in_progress", "transaction_id": tx_id, "estado": task.get("estado"),
                "message": "La venta ya se envió al POS Transbank y no se puede cancelar desde el sistema. "
                           f"Cancélela en la máquina; el resultado llega en /pago o en /pago/estado/{tx_id}.",
            }), 409

        @app.route("/pago/limpiar", methods=["POST"])
        @require_basic_auth
        def http_pago_limpiar():
            now = time.time()
            max_age = 600  # 10 minutos
            with self.tasks_lock:
                old_txs = [tx_id for tx_id, task in self.tasks.items() if self._tarea_vencida(task, now, max_age)]
                for tx_id in old_txs:
                    self.tasks.pop(tx_id, None)
            logger.info("Limpiadas %d transacciones antiguas", len(old_txs))
            return jsonify({"status": "ok", "eliminadas": len(old_txs)}), 200

    def _cancelada_antes_de_enviar(self, tx_id, task):
        """Cierra una venta cancelada mientras esperaba en la cola: el POS nunca la
        recibió, así que es un rechazo definitivo y la caja queda libre."""
        nota = "Cancelada por el cliente antes de enviarse al POS"
        tx = tx_store.obtener(tx_id)
        if tx and tx.get("estado") in tx_store.ESTADOS_SIN_RESOLVER:
            tx_store.actualizar_estado(tx_id, "RECHAZADO", raw_response=task.get("result"), nota=nota,
                                       resuelto_por="cancelación del cliente")
        self.internal_free_box(task.get("id_sucursal"), task.get("nombre_caja"), "cancelacion")
        _tipo_log = getnet_log if task.get("type") == "getnet" else transbank_log
        _tipo_log.info(f"🚫 Venta CANCELADA antes de enviarse al POS (tx {tx_id}): el POS no recibió nada, es seguro reintentar.")
        logger.info("Transacción cancelada en la cola: %s", tx_id)
        return jsonify({"status": "ok", "transaction_id": tx_id, "estado": "CANCELADO",
                        "message": "La venta se canceló antes de llegar al POS: no se cobró."}), 200

    def internal_free_box(self, id_sucursal, nombre_caja, reason=""):
        key = (id_sucursal, nombre_caja)
        with self.busy_lock:
            if key in self.busy_boxes:
                self.busy_boxes.discard(key)
                logger.info("Caja %s/%s liberada (%s)", id_sucursal, nombre_caja, reason)

    def reconciliar_getnet(self, tx):
        """Pregunta al POS Getnet por el resultado de una transacción del tx_store
        (respuesta pendiente tras reconectar, o Command 101)."""
        return self.getnet.resolver_venta_pendiente(
            tx.get("ticket"),
            amount=tx.get("monto"),
            sent_at=tx.get("created_at"),
            known_operation_ids=tx_store.operation_ids_conocidos("getnet", excluir_tx_id=tx.get("tx_id")),
            known_auth_codes=tx_store.codigos_autorizacion_conocidos("getnet", excluir_tx_id=tx.get("tx_id")),
        )

    def reconciliar_mercadopago(self, tx):
        """Consulta a la API de Mercado Pago el estado de la orden de una transacción del tx_store."""
        order_id = tx.get("ticket")
        tx_id = tx.get("tx_id")
        if not order_id:
            mercadopago_log.info(
                f"↪️ No se puede reconciliar la tx {tx_id}: no se conoce el id de la orden (Mercado Pago no confirmó "
                "su creación). Revisar en el portal de Mercado Pago y resolver desde el panel."
            )
            return {"status": "no_resuelto",
                    "message": "No se conoce el id de la orden (MP no confirmó su creación). "
                               "Revisar en el portal de Mercado Pago y resolver a mano."}
        if not MP_ACCESS_TOKEN:
            return {"status": "no_resuelto", "message": "Falta MP_ACCESS_TOKEN para consultar la orden"}

        mercadopago_log.info(f"🔍 Reconciliando la orden {order_id} (tx {tx_id}): consultando su estado a Mercado Pago...")
        info, error = _mp_consultar_orden(order_id, MP_ACCESS_TOKEN)
        if info is None:
            mercadopago_log.info(f"↪️ No se pudo consultar la orden {order_id} ({error}). Sigue INDETERMINADA.")
            return {"status": "no_resuelto", "message": f"No se pudo consultar la orden {order_id} en Mercado Pago ({error})"}
        status = info.get("status")
        if status not in MP_ESTADOS_FINALES:
            mercadopago_log.info(
                f"↪️ La orden {order_id} sigue en estado {status}: el cliente todavía puede pagar. "
                "Se vuelve a consultar más tarde."
            )
            return {"status": "no_resuelto", "response": info,
                    "message": f"La orden {order_id} sigue en estado {status}: el cliente todavía puede pagar"}
        aprobado = _mp_aprobado(info)
        detalle = _mp_detalle_pago(info)
        mercadopago_log.info(
            f"{'✅' if aprobado else '↪️'} Reconciliación: Mercado Pago informa la orden {order_id} (tx {tx_id}, "
            f"monto {fmt_monto(tx.get('monto'))}) como {'APROBADA' if aprobado else 'NO cobrada'} — estado {status}"
            + (f" — {detalle}" if detalle else "")
        )
        return {"status": "aprobado" if aprobado else "rechazado", "response": info,
                "message": f"Mercado Pago informa la orden {order_id} en estado {status}"
                           f"{' (' + info['status_detail'] + ')' if info.get('status_detail') else ''}"}

    def reconciliar(self, tx):
        """Resultado real de una transacción sin confirmar, según su medio de pago."""
        if tx.get("tipo") == "mercadopago":
            return self.reconciliar_mercadopago(tx)
        if tx.get("tipo") == "tuu":
            return tuu.reconciliar_tuu(tx, self._tuu_keys.get(tx.get("tx_id")) or TUU_API_KEY)
        if tx.get("tipo") == "transbank":
            if not self.pos:
                return {"status": "no_resuelto", "message": "POS Transbank no inicializado"}
            return self.pos.consultar_ultima_venta(tx.get("ticket"), amount=tx.get("monto"), sent_at=tx.get("created_at"))
        return self.reconciliar_getnet(tx)

    def aplicar_reconciliacion(self, tx, recon, resuelto_por, guardar_no_resuelto=False):
        """Persiste el resultado de una reconciliación y, si es definitivo, libera la caja.
        guardar_no_resuelto: registrar también el intento fallido (para mostrarlo en el panel)."""
        estado_map = {"aprobado": "APROBADO", "rechazado": "RECHAZADO", "no_resuelto": "INDETERMINADA"}
        nuevo_estado = estado_map.get(recon.get("status"), "INDETERMINADA")
        if nuevo_estado == "INDETERMINADA" and not guardar_no_resuelto:
            return nuevo_estado
        tx_store.actualizar_estado(
            tx["tx_id"], nuevo_estado, raw_response=recon, nota=recon.get("message"),
            resuelto_por=resuelto_por if nuevo_estado != "INDETERMINADA" else None,
        )
        if nuevo_estado != "INDETERMINADA":
            with self.busy_lock:
                self.busy_boxes.discard((tx.get("id_sucursal"), tx.get("nombre_caja")))
            self._tuu_keys.pop(tx["tx_id"], None)
        return nuevo_estado

    def _esperar_pos_tras_indeterminada(self, tx_id, result, esperar):
        """
        La venta ya está en el POS pero no llegó la respuesta (ej. se cortó el
        cable). La petición HTTP se mantiene abierta mientras se espera a que el
        POS vuelva a responder, hasta conocer el resultado real o hasta que el
        cliente cancele la espera (/pago/cancelar).
        """
        if tx_store.obtener(tx_id):
            # Visible como INDETERMINADA en el panel mientras se espera.
            tx_store.actualizar_estado(tx_id, "INDETERMINADA", raw_response=result)
        with self.tasks_lock:
            entry = self.tasks.get(tx_id) or {}
            entry["estado"] = "ESPERANDO_POS"
            cancel = entry.get("cancel") or threading.Event()
        resuelto = esperar(tx_id, cancel)
        return {**resuelto, "ticket": result.get("ticket")} if resuelto else result

    def esperar_resolucion_transbank(self, tx_id, cancel, intervalo=5, espera_tras_intento=15, reescaneo=30,
                                     max_espera=None):
        """Igual que esperar_resolucion_getnet, para el POS Transbank (también por cable)."""
        return self._esperar_resolucion(
            tx_id, cancel, "transbank", intervalo, espera_tras_intento, reescaneo,
            TRANSBANK_MAX_ESPERA if max_espera is None else max_espera)

    def esperar_resolucion_getnet(self, tx_id, cancel, intervalo=2, espera_tras_intento=5, reescaneo=30,
                                  max_espera=None):
        """Ver _esperar_resolucion."""
        return self._esperar_resolucion(
            tx_id, cancel, "getnet", intervalo, espera_tras_intento, reescaneo,
            GETNET_MAX_ESPERA if max_espera is None else max_espera)

    def _esperar_resolucion(self, tx_id, cancel, tipo, intervalo, espera_tras_intento, reescaneo, max_espera):
        """
        Mantiene viva una venta Getnet o Transbank sin confirmar hasta conocer su resultado real.
        - Mientras el puerto del POS no aparece en el sistema (cable desconectado)
          solo se espera, sin tocar el puerto.
        - Cuando vuelve, se consulta al POS (reconciliar_getnet) y se repite hasta
          que dé APROBADO o RECHAZADO.
        Termina antes si el cliente cancela la espera, si se detiene el servicio,
        si la transacción se resolvió por otra vía (panel / API), o al cumplirse
        GETNET_MAX_ESPERA / TRANSBANK_MAX_ESPERA segundos (0 = sin límite). En esos casos
        la venta sigue INDETERMINADA y el monitor la continúa validando en segundo plano.
        Devuelve el resultado ({"status": "success"|"failed", ...}) o None si se dejó de esperar.
        """
        maquina = self.getnet if tipo == "getnet" else self.pos
        _log = getnet_log if tipo == "getnet" else transbank_log
        with self._esperando_lock:
            self._esperando_getnet.add(tx_id)
        limite = time.time() + max_espera if max_espera else None
        avisado = False
        ultimo_intento = 0.0
        try:
            while True:
                tx = tx_store.obtener(tx_id)
                if not tx:
                    return None
                if tx.get("estado") not in tx_store.ESTADOS_SIN_RESOLVER:
                    status = "success" if tx.get("estado") == "APROBADO" else "failed"
                    return {"status": status, "reconciliado": True, "resuelto_por": tx.get("resuelto_por")}
                if cancel.is_set() or self._stop_local_worker.is_set():
                    return None
                if limite and time.time() >= limite:
                    logger.warning("Espera %s tx=%s: se cumplió el máximo de %ss", tipo, tx_id, max_espera)
                    _log.info(
                        f"⏱️ Se cumplió el tiempo máximo de espera ({max_espera // 60} min {max_espera % 60} s) "
                        f"para la venta (tx {tx_id}). Se responde INDETERMINADA; se sigue validando en segundo plano."
                    )
                    return None

                presente = maquina.puerto_presente() if maquina else False
                if presente is False or (presente is None and time.time() - ultimo_intento < reescaneo):
                    if not avisado:
                        _log.info(
                            f"🔌 Esperando que el POS vuelva a conectarse para confirmar la venta (tx {tx_id}). "
                            "La petición sigue abierta; se puede cancelar la espera con /pago/cancelar."
                        )
                        avisado = True
                    cancel.wait(min(intervalo, max(0.0, limite - time.time())) if limite else intervalo)
                    continue

                ultimo_intento = time.time()
                recon = self.reconciliar(tx)
                logger.info("Espera %s tx=%s -> %s (%s)", tipo, tx_id, recon.get("status"), recon.get("message"))
                if recon.get("status") in ("aprobado", "rechazado"):
                    return {
                        "status": "success" if recon["status"] == "aprobado" else "failed",
                        "response": recon.get("response"),
                        "reconciliado": True,
                        "reconcile_message": recon.get("message"),
                    }
                cancel.wait(min(espera_tras_intento, max(0.0, limite - time.time())) if limite else espera_tras_intento)
        finally:
            with self._esperando_lock:
                self._esperando_getnet.discard(tx_id)

    def aplicar_resultado_tardio(self, tx_id, result):
        """
        El POS respondió después del timeout (hoy solo Transbank): si la transacción
        sigue sin resolver, se actualiza con el resultado real y se libera la caja.
        Si ya fue resuelta (p. ej. manualmente desde el panel) solo se deja registro.
        """
        tx = tx_store.obtener(tx_id)
        if not tx:
            return
        estado = _estado_db(result)
        if estado == "INDETERMINADA":
            # Ej. el hilo de la venta terminó porque se cortó el cable: no es un
            # resultado. La venta sigue sin confirmar (la resuelve la consulta de
            # última venta cuando el POS vuelva).
            logger.info("Resultado tardío tx=%s sin confirmación: se ignora", tx_id)
            return
        if tx.get("estado") not in tx_store.ESTADOS_SIN_RESOLVER:
            if tx.get("estado") != estado:
                logger.warning(
                    "Resultado tardío tx=%s (%s) difiere del estado ya resuelto (%s por %s)",
                    tx_id, estado, tx.get("estado"), tx.get("resuelto_por"),
                )
                transbank_log.info(
                    f"🚨 ATENCIÓN: el POS informó tarde la tx {tx_id} como {estado}, pero ya había sido "
                    f"resuelta como {tx.get('estado')} por {tx.get('resuelto_por')}. Revisar manualmente."
                )
            return

        tx_store.actualizar_estado(
            tx_id, estado, raw_response=result,
            nota="Resultado recibido del POS después del timeout",
            resuelto_por="respuesta tardía del POS",
        )
        with self.tasks_lock:
            entry = self.tasks.get(tx_id)
            if entry:
                entry["result"] = result
                entry["estado"] = estado
        with self.busy_lock:
            self.busy_boxes.discard((tx.get("id_sucursal"), tx.get("nombre_caja")))
        logger.info("Resultado tardío aplicado tx=%s -> %s", tx_id, estado)

    # ---------- TUU (Pago remoto) ----------
    def resolver_dispositivo_tuu(self, terminal_id):
        """La máquina TUU (número de serie) viene en terminal_id. Con TUU_DEVICES
        configurado debe ser una de ellas; sin él se acepta la del request.
        Returns: (serie, error) — error es un texto si no se pudo elegir."""
        terminal_id = str(terminal_id).strip() if terminal_id is not None else ""
        if not terminal_id:
            return None, "Es necesario el terminal_id (número de serie de la máquina TUU)"
        if TUU_DISPOSITIVOS and terminal_id not in TUU_DISPOSITIVOS:
            return None, f"La máquina '{terminal_id}' no está configurada en TUU_DEVICES ({', '.join(TUU_DISPOSITIVOS)})"
        return terminal_id, None

    def _pago_tuu(self, data, id_sucursal, nombre_caja, timeout):
        """/pago con type=haulmer. Se procesa en el hilo de la petición, como Mercado Pago."""
        # Lo que llegó, para el log de negocio. Nunca incluye access_token: solo
        # se registra de dónde salió la API key, no su valor.
        pedido = (f"caja {data.get('nombre_caja')} — máquina {data.get('terminal_id')} — monto {data.get('amount')} — "
                  f"timeout {data.get('timeout')} — método {data.get('payment_method') or 'debito'}"
                  + (f" — tx {data.get('tx_id')}" if data.get("tx_id") is not None else ""))
        tuu_log.info(f"📥 Petición /pago recibida: {pedido}")

        def rechazar(mensaje, http_code=400, **extra):
            tuu_log.info(f"❌ Venta rechazada por el sistema ({pedido}): {mensaje}. No se envió nada a la máquina.")
            return jsonify({"error": mensaje, **extra}), http_code

        if not USAR_TUU:
            return rechazar("TUU no habilitado en esta máquina (USAR_TUU)", 503)
        if not data.get("nombre_caja"):
            return rechazar("Es necesario el nombre_caja")
        api_key, origen_key = None, "la TUU_API_KEY del .env"
        if ALLOW_TUU_KEY_IN_REQUEST:
            api_key = str(data.get("access_token") or "").strip() or None
            if api_key:
                origen_key = "el access_token de la petición"
        elif data.get("access_token"):
            origen_key = "la TUU_API_KEY del .env (se ignoró el access_token de la petición: ALLOW_TUU_KEY_IN_REQUEST=false)"
        api_key = api_key or TUU_API_KEY
        if not api_key:
            return rechazar("Falta la API key de TUU: envíela en access_token (con "
                            "ALLOW_TUU_KEY_IN_REQUEST=true) o configure TUU_API_KEY")

        if data.get("amount") is None:
            return rechazar("Es necesario el monto de venta (amount)")
        if data.get("timeout") is None:
            return rechazar("Es necesario el timeout")
        try:
            timeout = max(30, min(int(data.get("timeout")), 300))
        except (ValueError, TypeError):
            return rechazar("timeout debe ser un número válido")

        amount = _monto_valido(data.get("amount"))
        if amount is None or not (tuu.TUU_MONTO_MIN <= amount <= tuu.TUU_MONTO_MAX):
            return rechazar(f"El monto debe ser un entero entre {tuu.TUU_MONTO_MIN} y {tuu.TUU_MONTO_MAX}")

        metodo = str(data.get("payment_method") or "debito").strip().lower()
        if metodo not in tuu.TUU_METODOS_PAGO:
            return rechazar("payment_method debe ser 'debito' o 'credito'")

        device, device_error = self.resolver_dispositivo_tuu(data.get("terminal_id"))
        if device_error:
            return rechazar(device_error, dispositivos_configurados=TUU_DISPOSITIVOS)

        tx_id_input = data.get("tx_id")
        if tx_id_input is not None:
            tx_id_input = str(tx_id_input).strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tx_id_input):
                return rechazar("tx_id inválido (solo letras, números, '-' y '_', máx. 64)")
            if tx_store.obtener(tx_id_input):
                return rechazar("tx_id ya fue usado en otra venta", 409, transaction_id=tx_id_input)
        tuu_log.info(f"🔑 API key de TUU: se usa {origen_key}.")
        tx_id = tx_id_input or str(uuid.uuid4())

        # El recurso físico es la máquina: una solicitud sin resolver en ella
        # bloquea ventas nuevas. Nunca se crea una segunda solicitud con otra
        # clave: ambas podrían llegar a la máquina y cobrar dos veces.
        runner_id_sucursal = str(ID_SUCURSAL)
        caja_tuu = _mayus(f"tuu:{device}")
        pendiente = tx_store.hay_pendiente_sin_resolver(runner_id_sucursal, caja_tuu)
        if pendiente:
            logger.warning("Venta TUU bloqueada: transacción previa sin resolver tx=%s", pendiente)
            tuu_log.info(
                f"🔒 Venta rechazada por el sistema: la máquina {device} tiene una solicitud anterior "
                f"({pendiente}) sin confirmar. No se creó ninguna solicitud nueva."
            )
            return jsonify({
                "status": "unresolved_previous_transaction",
                "message": "Hay una venta TUU previa en esta máquina sin confirmar. Revise la máquina; "
                           "se reconcilia sola o puede resolverse desde el panel.",
                "pending_transaction_id": pendiente,
            }), 409

        key = (runner_id_sucursal, caja_tuu)
        with self.busy_lock:
            if key in self.busy_boxes:
                tuu_log.info(
                    f"🔁 Venta rechazada: la máquina {device} ya tiene otro cobro en curso "
                    f"(monto {fmt_monto(amount)} intentado, caja {nombre_caja})."
                )
                return jsonify({"status": "busy", "message": "Máquina TUU ocupada"}), 429
            self.busy_boxes.add(key)

        cancel = threading.Event()
        res, estado = {"status": "error", "message": "Error interno"}, "ERROR"
        try:
            # Se registra ANTES de llamar a TUU, con la clave de idempotencia como
            # ticket: si el proceso cae a mitad, el monitor puede consultarla.
            tx_store.registrar_intento(
                tx_id=tx_id,
                tipo="tuu",
                ticket=tuu.clave_idempotencia(tx_id),
                terminal_id=device,
                id_sucursal=runner_id_sucursal,
                nombre_caja=caja_tuu,
                monto=amount,
                client_id_sucursal=id_sucursal,
                client_nombre_caja=nombre_caja,
            )
            with self.tasks_lock:
                self.tasks[tx_id] = {
                    "event": threading.Event(),
                    "cancel": cancel,
                    "type": "tuu",
                    "result": None,
                    "estado": "PENDIENTE",
                    "id_sucursal": runner_id_sucursal,
                    "nombre_caja": caja_tuu,
                    "timestamp": time.time(),
                    "timeout": timeout,
                }
            if api_key != TUU_API_KEY:
                # La key del request solo vive en memoria (nunca en la base ni en
                # los logs): el monitor la necesita si la venta queda INDETERMINADA.
                self._tuu_keys[tx_id] = api_key

            res = tuu.process_tuu(device, api_key, amount, tx_id, timeout,
                                  payment_method=tuu.TUU_METODOS_PAGO[metodo], cancel=cancel)
            estado = _estado_db(res)
            tx = tx_store.obtener(tx_id)
            if tx and tx.get("estado") in tx_store.ESTADOS_SIN_RESOLVER:
                tx_store.actualizar_estado(tx_id, estado, raw_response=res)
            elif tx:
                estado = tx.get("estado")  # resuelta por otra vía mientras tanto
        finally:
            with self.tasks_lock:
                entry = self.tasks.pop(tx_id, None)
            if entry:
                entry["event"].set()
            with self.busy_lock:
                self.busy_boxes.discard(key)
            if estado not in tx_store.ESTADOS_SIN_RESOLVER:
                self._tuu_keys.pop(tx_id, None)

        respuesta = {
            "success": estado == "APROBADO",
            "transaction_id": tx_id,
            "estado": estado,
            "data": {
                "status": res.get("status"),
                "tuu_status": res.get("tuu_status"),
                "idempotency_key": res.get("idempotency_key"),
                "sequence_number": res.get("sequence_number"),
                "response": res.get("response"),
            },
            **res,
        }
        if estado == "INDETERMINADA":
            respuesta["message"] = (
                f"{tuu.MENSAJE_INDETERMINADA} Consulte /pago/estado/{tx_id} hasta obtener APROBADO o RECHAZADO."
            )
        tuu_log.info(
            f"📄 Respuesta /pago tx={tx_id} (estado={estado}): "
            f"{json.dumps(respuesta, ensure_ascii=False, default=str)}"
        )
        return jsonify(respuesta), 200

    def resolver_transaccion(self, tx_id, estado, resuelto_por, nota=None, comprobante=None):
        """
        Resuelve (manual o automáticamente) una transacción PENDIENTE/INDETERMINADA:
        actualiza el registro persistente con quién y cuándo, y recién ahí libera la
        caja. Usado tanto por el panel (humano mirando el comprobante del POS) como
        por /pago/resolver/<tx_id> (API autenticada) y el monitor de reconciliación.

        comprobante: datos de la venta en el POS (detalle de ventas) con que se
        aprobó; queda en raw_response["response"] igual que una venta normal, así
        su autorización ya no se confunde con la de otra venta al reconciliar.

        Returns: (ok: bool, error_message: str|None, http_code: int)
        """
        tx = tx_store.obtener(tx_id)
        if not tx:
            return False, "Transacción no encontrada", 404
        if estado not in ("APROBADO", "RECHAZADO"):
            return False, "estado debe ser APROBADO o RECHAZADO", 400

        nota_final = nota or (
            f"Aprobada con el comprobante del POS (autorización {comprobante.get('AuthorizationCode')})"
            if comprobante else f"Resuelto manualmente por {resuelto_por or 'operador desconocido'}"
        )
        raw = {"manual": True, "nota": nota_final, "resuelto_por": resuelto_por}
        if comprobante:
            raw.update(status="success", response=comprobante)
        tx_store.actualizar_estado(
            tx_id, estado,
            raw_response=raw,
            nota=nota_final,
            resuelto_por=resuelto_por,
        )
        with self.busy_lock:
            self.busy_boxes.discard((tx.get("id_sucursal"), tx.get("nombre_caja")))
        self._tuu_keys.pop(tx_id, None)

        logger.info("Resolución tx=%s -> %s por %s", tx_id, estado, resuelto_por)
        _tipo_log = {"getnet": getnet_log, "transbank": transbank_log, "mercadopago": mercadopago_log, "tuu": tuu_log}.get(tx.get("tipo"))
        if _tipo_log:
            _tipo_log.info(
                f"👤 {resuelto_por or 'Un operador'} resolvió MANUALMENTE (mirando el comprobante del POS) el "
                f"ticket {tx.get('ticket')} (tx {tx_id}) como {estado}. Nota: {nota_final}"
            )
        return True, None, 200

    def run(self):
        if not ALLOWED_ORIGINS:
            logger.warning(
                "[SEGURIDAD] ALLOWED_ORIGINS vacío: cualquier página web abierta en esta PC puede llamar a la API "
                "desde el navegador. Configure el dominio del sistema web (ej. https://misistema.cl)."
            )
        try:
            from waitress import serve
        except ImportError:
            logger.warning("waitress no instalado: se usa el servidor de desarrollo de Flask")
            logger.info("Servidor Flask arrancando en %s:%s", BIND_HOST, HTTP_PORT)
            self.app.run(host=BIND_HOST, port=HTTP_PORT, threaded=True, use_reloader=False)
            return
        logger.info("Servidor waitress arrancando en %s:%s", BIND_HOST, HTTP_PORT)
        # Un /pago de Getnet o Mercado Pago deja un hilo ocupado hasta que el cliente
        # termina de pagar (minutos): se dejan hilos de sobra para no encolar /status,
        # /pago/estado o el panel detrás de ventas en curso.
        serve(self.app, host=BIND_HOST, port=HTTP_PORT, threads=24, channel_timeout=900, ident=APP_NAME)
