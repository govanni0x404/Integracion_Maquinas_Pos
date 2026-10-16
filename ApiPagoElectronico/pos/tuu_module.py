"""
TUU — Pago remoto (máquinas PRO2/Kozen) por la API de Haulmer.

Funciona como Mercado Pago Point: el cobro se crea en la nube
(POST /RemotePayment/v2/Create), la máquina lo toma cuando está en
"Modo Integración" y el resultado se consulta por polling
(GET /RemotePayment/v2/GetPaymentRequest/{idempotencyKey}).

Diferencias que obligan a ser conservadores:
- NO hay cancelación por API: una solicitud pendiente sigue viva en TUU y
  puede aparecer en la máquina más tarde. Al dejar de esperar, la venta queda
  INDETERMINADA (nunca RECHAZADA) y la máquina bloqueada hasta resolverla.
- Reusar una idempotencyKey responde 400 MR-191 en vez de devolver el cobro
  ya creado: en un reintento significa "el primer POST sí llegó".
- Límite de 1 solicitud por minuto por máquina (HTTP 429) y 5 pendientes (MR-180).

La API key va solo en el header X-API-Key: nunca se registra ni se devuelve.
"""
import hashlib
import json
import logging
import re
import time
import traceback

from config.settings import APP_NAME, TUU_API_URL
from core.http_util import http_json as _http_json, error_de_red_ambiguo as _error_de_red_ambiguo
from core.business_logging import tuu_log, fmt_monto

logger = logging.getLogger(APP_NAME)

# -1 = la máquina NO pregunta propina ni vuelto (con 0 sí pregunta).
TUU_TIP_AMOUNT = -1
TUU_CASHBACK_AMOUNT = -1
TUU_METODOS_PAGO = {"debito": 2, "credito": 1}
TUU_MONTO_MIN = 100
TUU_MONTO_MAX = 99_999_999
TUU_DESCRIPCION = "Venta POS"

# Estados definitivos del GET. "Pending", "Sent", "Processing" o cualquier otro
# (incluso uno nuevo que TUU agregue) se trata como "todavía se puede cobrar".
TUU_ESTADOS_FINALES = ("Completed", "Failed", "Canceled")
TUU_DESCRIPCION_ESTADOS = {
    "Pending": "solicitud registrada, esperando que la máquina la tome (¿está en Modo Integración?)",
    "Sent": "solicitud enviada a la máquina",
    "Processing": "la máquina está procesando el cobro",
}
TUU_REINTENTOS_CREACION = 3
TUU_PAUSA_REINTENTO = 5
TUU_INTERVALO_CONSULTA = 2
# Un 404 justo después de un POST con timeout puede ser que TUU aún lo esté
# procesando: se confirma con una segunda consulta antes de darlo por no creado.
TUU_PAUSA_CONFIRMAR_404 = 5

NO_EXISTE = "no_existe"

MENSAJE_INDETERMINADA = (
    "Venta TUU sin confirmar. Revise la máquina (abra el Modo Integración y cancele o complete el cobro). "
    "No reintente el cobro; se reconcilia sola."
)

TUU_CODIGOS_ERROR = {
    "MR-100": "número de serie de la máquina no encontrado (revisar TUU_DEVICES)",
    "MR-161": "número de serie de la máquina no encontrado (revisar TUU_DEVICES)",
    "KEY-002": "falta la API key (revisar TUU_API_KEY)",
    "KEY-003": "API key inválida (revisar TUU_API_KEY)",
    "MR-191": "la clave de idempotencia ya fue usada",
    "MR-180": "la máquina ya tiene 5 solicitudes pendientes (cola llena)",
    "RP-030": "la configuración de la máquina no permite propina",
    "RP-031": "la configuración de la máquina no permite vuelto",
    "RP-032": "la configuración de la máquina no permite este método de pago",
}
_RE_CODIGO = re.compile(r"\b(?:MR|KEY|RP)-\d{3}\b")


def clave_idempotencia(tx_id):
    """Determinista y de 36 caracteres (máximo de TUU): se puede reconstruir y
    consultar a partir del tx_id aunque la creación haya dado timeout."""
    return "tuu_" + hashlib.sha1(str(tx_id).encode("utf-8")).hexdigest()[:32]


def _headers(api_key):
    return {"X-API-Key": api_key, "Content-Type": "application/json", "Accept": "application/json"}


def _codigo_error(body):
    """Código de error de TUU (MR-191, KEY-003...) buscado en cualquier parte del body."""
    try:
        texto = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
    except (TypeError, ValueError):
        texto = str(body)
    m = _RE_CODIGO.search(texto or "")
    return m.group(0) if m else None


def _describir_error(code, body):
    """Texto legible para el log de negocio a partir de una respuesta de error de TUU."""
    if code is None:
        return f"sin respuesta de TUU ({(body or {}).get('message', 'error de red')})"
    codigo = _codigo_error(body)
    if codigo in TUU_CODIGOS_ERROR:
        return f"HTTP {code} {codigo}: {TUU_CODIGOS_ERROR[codigo]}"
    if code == 401:
        return "HTTP 401: la TUU_API_KEY es inválida"
    if code == 429:
        return "HTTP 429: límite de 1 solicitud por minuto por máquina"
    if code == 404:
        return "HTTP 404: TUU no encuentra el recurso"
    detalle = ""
    if isinstance(body, dict):
        detalle = str(body.get("message") or body.get("error") or body.get("title") or "")
    return f"HTTP {code}" + (f" {codigo}" if codigo else "") + (f": {detalle}" if detalle else "")


def _consultar(key, api_key):
    """
    Estado actual de la solicitud.
    Returns: (info, None), (NO_EXISTE, None) si TUU no conoce la clave (404),
    o (None, motivo) si no se pudo saber (red, 401, 429, 5xx...).
    """
    try:
        code, info = _http_json("GET", f"{TUU_API_URL}/RemotePayment/v2/GetPaymentRequest/{key}",
                                headers=_headers(api_key), timeout=10)
    except Exception as e:
        logger.warning("No se pudo consultar la solicitud TUU %s: %s", key, e)
        return None, _describir_error(None, {"message": str(e)})
    if code == 404:
        return NO_EXISTE, None
    if code != 200 or not isinstance(info, dict) or not info.get("status"):
        logger.warning("Consulta de la solicitud TUU %s respondió HTTP %s: %s", key, code, info)
        return None, _describir_error(code, info)
    return info, None


def _verificar_creacion(key, api_key):
    """
    Tras un POST sin respuesta clara: ¿TUU registró la solicitud?
    Returns: ("existe", info, None) | ("no_existe", None, None) | ("desconocido", None, motivo).
    "no_existe" solo con dos 404 seguidos; cualquier error deja "desconocido".
    """
    no_existe, motivo = 0, None
    for i in range(2):
        if i:
            time.sleep(TUU_PAUSA_CONFIRMAR_404)
        info, motivo = _consultar(key, api_key)
        if info == NO_EXISTE:
            no_existe += 1
        elif info is not None:
            return "existe", info, None
    if no_existe == 2:
        return "no_existe", None, None
    return "desconocido", None, motivo or "TUU respondió 404 y luego no se pudo volver a consultar"


def _crear(payload, api_key, key, ctx=""):
    """
    Crea la solicitud. Ante una respuesta ambigua (timeout, conexión cortada,
    5xx, o MR-191 = la clave ya existe) NO se reenvía a ciegas: primero se
    consulta por GET con la misma clave, y solo si TUU no la conoce se reintenta.
    Returns: (resultado, http_status|None, body) con resultado
    "creado" | "no_creado" | "ambiguo".
    """
    url = f"{TUU_API_URL}/RemotePayment/v2/Create"
    code, body = None, {}
    for intento in range(1, TUU_REINTENTOS_CREACION + 1):
        try:
            code, body = _http_json("POST", url, headers=_headers(api_key), payload=payload, timeout=15)
        except Exception as e:
            code, body = None, {"message": str(e)}
            logger.warning("Creando cobro TUU (intento %s/%s): error de red %s", intento, TUU_REINTENTOS_CREACION, e)
            verificar = _error_de_red_ambiguo(e)
        else:
            if code in (200, 201):
                return "creado", code, body
            codigo = _codigo_error(body)
            logger.warning("Creando cobro TUU (intento %s/%s): HTTP %s %s", intento, TUU_REINTENTOS_CREACION, code, body)
            if codigo == "MR-191":
                # La clave ya existe en TUU: un intento anterior sí llegó.
                verificar = True
            elif 400 <= code < 500:
                # Rechazo explícito (incluye 429): TUU no registró nada.
                return "no_creado", code, body
            else:
                verificar = True  # 5xx: pudo haberse registrado

        if verificar:
            tuu_log.info(
                f"↪️ Crear el cobro no tuvo respuesta clara ({ctx}) — intento {intento}/{TUU_REINTENTOS_CREACION}: "
                f"{_describir_error(code, body)}. Se consulta a TUU si la solicitud quedó registrada..."
            )
            existe, info, motivo = _verificar_creacion(key, api_key)
            if existe == "existe":
                tuu_log.info(f"🔎 TUU sí registró la solicitud {key}: se continúa esperando el resultado.")
                return "creado", 200, info
            if existe == "desconocido":
                logger.warning("No se pudo confirmar si TUU creó la solicitud %s: %s", key, motivo)
                return "ambiguo", code, body
            tuu_log.info(f"↪️ TUU no conoce la solicitud {key}: no se creó.")

        if intento < TUU_REINTENTOS_CREACION:
            tuu_log.info(
                f"↪️ Se reintenta crear el cobro ({ctx}) con la misma clave de idempotencia "
                f"(intento {intento + 1}/{TUU_REINTENTOS_CREACION})..."
            )
            time.sleep(TUU_PAUSA_REINTENTO)
    return "no_creado", code, body


def clasificar(info, monto):
    """
    Reglas de negocio sobre la respuesta de GetPaymentRequest.
    Returns: (estado, motivo) con estado "approved" | "rejected" | "indeterminada",
    o (None, motivo) si el estado todavía no es final.
    """
    status = (info or {}).get("status")
    seq = (info or {}).get("sequenceNumber")
    if status not in TUU_ESTADOS_FINALES:
        return None, f"estado {status} — {TUU_DESCRIPCION_ESTADOS.get(status, 'la máquina aún no termina el cobro')}"
    if status == "Completed":
        try:
            monto_ok = int(info.get("amount")) == int(monto)
        except (TypeError, ValueError):
            monto_ok = False
        if not seq:
            return "indeterminada", "TUU informa Completed sin sequenceNumber: revisar la máquina"
        if not monto_ok:
            return "indeterminada", (
                f"TUU informa Completed por {fmt_monto(info.get('amount'))} pero la venta es por "
                f"{fmt_monto(monto)}: revisar la máquina y resolver desde el panel"
            )
        return "approved", f"pagado — secuencia {seq}"
    if status == "Failed":
        if seq:
            return "rejected", f"rechazado por el banco/cliente — secuencia {seq}"
        # Sin datos del pago (ej. tarjeta mal leída): la máquina vuelve a "Esperando
        # una misión" sin cobrar, así que se libera para reintentar. Decisión del
        # negocio: se acepta el riesgo del caso raro en que la máquina mostró
        # "Pago exitoso" con esta misma respuesta (el log lo deja advertido).
        return "rejected", ("fallido sin datos del pago (ej. tarjeta mal leída), no se cobró. Si la máquina "
                            "llegara a mostrar 'Pago exitoso', revisar el portal de TUU")
    return "rejected", "cancelado en la máquina, no se cobró"


def _resultado_final(info, key, monto, ctx):
    estado, motivo = clasificar(info, monto)
    status = info.get("status")
    logger.info("Respuesta final TUU: %s", json.dumps(info, ensure_ascii=False))
    detalle = " — ".join(
        f"{k} {info[k]}" for k in ("bank", "bin", "transactionReference") if info.get(k)
    )
    if estado == "approved":
        tuu_log.info(f"✅ Cobro APROBADO — clave {key} — {ctx} — {motivo}" + (f" — {detalle}" if detalle else ""))
    elif estado == "rejected":
        tuu_log.info(f"⛔ Cobro NO realizado — clave {key} — {ctx} — {motivo}. No se cobró: es seguro reintentar.")
    else:
        alerta = "🚨 ALERTA: " if status == "Completed" else "⚠️ "
        tuu_log.info(f"{alerta}SIN CONFIRMACIÓN — clave {key} — {ctx} — {motivo}. La máquina queda bloqueada.")
    res = {
        "status": estado,
        "tuu_status": status,
        "idempotency_key": key,
        "sequence_number": info.get("sequenceNumber"),
        "response": info,
    }
    if estado == "indeterminada":
        res["message"] = f"{MENSAJE_INDETERMINADA} ({motivo})"
    return res


def process_tuu(device, api_key, amount, tx_id, timeout, payment_method=TUU_METODOS_PAGO["debito"], cancel=None):
    """
    Procesa un cobro TUU de principio a fin.
    cancel: threading.Event para dejar de esperar a pedido del cliente (la
    solicitud NO se retira de TUU: la venta queda INDETERMINADA).
    Retorna un dict con status "approved" | "rejected" | "failed" | "error" | "indeterminada".
    """
    key = clave_idempotencia(tx_id)
    metodo = "crédito" if payment_method == TUU_METODOS_PAGO["credito"] else "débito"
    ctx = f"tx {tx_id} — máquina {device} — monto {fmt_monto(amount)} — {metodo}"
    logger.info("Procesando TUU device=%s monto=%s timeout=%s clave=%s", device, amount, timeout, key)
    tuu_log.info(f"🟢 Cobro iniciado — {ctx} — clave {key} — espera máxima {timeout} s")

    payload = {
        "idempotencyKey": key,
        "amount": int(amount),
        "device": device,
        "paymentMethod": payment_method,
        "tipAmount": TUU_TIP_AMOUNT,
        "cashbackAmount": TUU_CASHBACK_AMOUNT,
        "description": TUU_DESCRIPCION,
    }

    enviado = False
    last_info = None
    try:
        enviado = True
        resultado, code, body = _crear(payload, api_key, key, ctx)
        logger.info("Respuesta creación TUU (%s): %s", code, json.dumps(body, ensure_ascii=False))

        if resultado == "no_creado":
            error = _describir_error(code, body)
            extra = " Espere 1 minuto antes de reintentar." if code == 429 else ""
            tuu_log.info(
                f"❌ No se pudo crear el cobro ({ctx}): {error}. "
                f"No se envió nada a la máquina, es seguro reintentar.{extra}"
            )
            return {"status": "error" if code is None else "failed", "http_status": code,
                    "error_code": _codigo_error(body), "idempotency_key": key,
                    "message": error + extra, "response": body}
        if resultado == "ambiguo":
            tuu_log.info(
                f"⚠️ SIN CONFIRMACIÓN — {ctx}: no se pudo saber si TUU registró la solicitud "
                f"({_describir_error(code, body)}). La máquina queda bloqueada y se sigue consultando."
            )
            return {"status": "indeterminada", "http_status": code, "idempotency_key": key,
                    "message": MENSAJE_INDETERMINADA, "response": body}

        tuu_log.info(f"⏳ Solicitud registrada en TUU (clave {key}) — {ctx} — esperando el pago en la máquina...")

        start = time.time()
        last_logged = None
        error_consulta = None
        motivo = f"Se agotó el tiempo de espera ({timeout} s)"
        while time.time() - start < timeout:
            if cancel is not None and cancel.is_set():
                motivo = "El cliente dejó de esperar"
                break

            info, error = _consultar(key, api_key)
            if info is None or info == NO_EXISTE:
                # Error transitorio (o 404 de una solicitud recién creada): NO es un rechazo.
                error = error or "TUU todavía no muestra la solicitud"
                if error != error_consulta:
                    tuu_log.info(f"↪️ Clave {key}: no se pudo consultar su estado ({error}). "
                                 "No es un rechazo: se sigue consultando...")
                    error_consulta = error
                time.sleep(TUU_INTERVALO_CONSULTA)
                continue
            if error_consulta:
                tuu_log.info(f"🔌 Clave {key}: se recuperó la comunicación con TUU.")
                error_consulta = None

            last_info = info
            status = info.get("status")
            if status in TUU_ESTADOS_FINALES:
                return _resultado_final(info, key, amount, ctx)
            if status != last_logged:
                _, descripcion = clasificar(info, amount)
                tuu_log.info(f"↪️ Clave {key}: {descripcion} ({int(time.time() - start)} s de {timeout} s)")
                last_logged = status
            time.sleep(TUU_INTERVALO_CONSULTA)

        logger.warning("%s — solicitud TUU %s sin resultado", motivo, key)
        tuu_log.info(
            f"⚠️ SIN CONFIRMACIÓN — {motivo} — clave {key} — {ctx}. TUU no permite cancelar por API: la "
            "solicitud sigue viva y puede aparecer en la máquina. La máquina queda bloqueada y se sigue "
            "consultando hasta conocer el resultado (o resolver desde el panel)."
        )
        return {"status": "indeterminada", "tuu_status": (last_info or {}).get("status"),
                "idempotency_key": key, "message": MENSAJE_INDETERMINADA, "response": last_info}

    except Exception as e:
        logger.error("Error TUU: %s\n%s", e, traceback.format_exc())
        if enviado:
            tuu_log.info(f"⚠️ Error inesperado con la solicitud {key} ({ctx}): {e}. Queda SIN CONFIRMACIÓN.")
            return {"status": "indeterminada", "idempotency_key": key, "message": MENSAJE_INDETERMINADA}
        tuu_log.info(f"❌ Error inesperado procesando el cobro ({ctx}): {e}")
        return {"status": "error", "message": str(e)}


def reconciliar_tuu(tx, api_key):
    """Consulta a TUU el estado de una transacción del tx_store (monitor y panel).
    Returns: {"status": "aprobado"|"rechazado"|"no_resuelto", "response", "message"}."""
    tx_id = tx.get("tx_id")
    key = tx.get("ticket") or clave_idempotencia(tx_id)
    if not api_key:
        return {"status": "no_resuelto",
                "message": "No hay API key para consultar la solicitud (la del request se pierde al reiniciar el "
                           "servicio): configure TUU_API_KEY o resuelva desde el panel"}

    tuu_log.info(f"🔍 Reconciliando la solicitud {key} (tx {tx_id}): consultando su estado a TUU...")
    info, error = _consultar(key, api_key)
    if info is None:
        tuu_log.info(f"↪️ No se pudo consultar la solicitud {key} ({error}). Sigue INDETERMINADA.")
        return {"status": "no_resuelto", "message": f"No se pudo consultar la solicitud {key} en TUU ({error})"}
    if info == NO_EXISTE:
        msg = (f"TUU no conoce la solicitud {key}: casi seguro nunca se creó. Verifique que la máquina no la "
               "muestre y resuelva como RECHAZADO desde el panel.")
        tuu_log.info(f"↪️ {msg}")
        return {"status": "no_resuelto", "message": msg}

    estado, motivo = clasificar(info, tx.get("monto"))
    if estado is None:
        tuu_log.info(f"↪️ La solicitud {key} sigue sin resultado ({motivo}). Se vuelve a consultar más tarde.")
        return {"status": "no_resuelto", "response": info, "message": f"La solicitud {key} sigue sin resultado: {motivo}"}
    if estado == "indeterminada":
        alerta = "🚨 ALERTA: " if info.get("status") == "Completed" else "⚠️ "
        tuu_log.info(f"{alerta}Reconciliación de la solicitud {key} (tx {tx_id}): {motivo}. Requiere revisión manual.")
        return {"status": "no_resuelto", "response": info, "message": motivo}
    aprobado = estado == "approved"
    tuu_log.info(
        f"{'✅' if aprobado else '↪️'} Reconciliación: TUU informa la solicitud {key} (tx {tx_id}, monto "
        f"{fmt_monto(tx.get('monto'))}) como {'APROBADA' if aprobado else 'NO cobrada'} — {motivo}"
    )
    return {"status": "aprobado" if aprobado else "rechazado", "response": info,
            "message": f"TUU informa la solicitud {key} en estado {info.get('status')} ({motivo})"}
