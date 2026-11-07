"""
Historial de ventas: arma, en un formato claro y estable, el detalle de cada
venta guardada en la base (core.transaction_store). Solo lee: no cambia nada.

Lo usan dos rutas:
- GET /ventas        → para otros sistemas (pide la misma clave Basic que /pago).
- GET /panel/ventas  → para la sección "Historial de ventas" del panel.
"""
import json
import time
from datetime import datetime, timedelta

from core import transaction_store as tx_store

NOMBRE_MAQUINA = {"transbank": "Transbank", "getnet": "Getnet", "mercadopago": "Mercado Pago", "tuu": "TUU"}
TIPO_DESDE_REQUEST = {"haulmer": "tuu"}  # en /pago la caja manda "haulmer"; adentro se llama tuu
ESTADOS = ("APROBADO", "RECHAZADO", "ERROR", "PENDIENTE", "INDETERMINADA")
LIMITE_MAXIMO = 1000

# Claves (en minúsculas) que se buscan dentro de la respuesta del POS.
_CLAVES_AUTORIZACION = ("authorizationcode", "authorization_code", "codigoautorizacion", "auth_code")
_CLAVES_TARJETA = ("last4digits", "last_4_digits", "last4", "card_number", "cardnumber")
_CLAVES_COMPROBANTE = ("operationid", "operation_id", "sequencenumber", "sequence_number")


def _buscar(dato, claves, profundidad=0):
    """Primer valor no vacío de alguna de las claves, buscando en dicts anidados."""
    if profundidad > 4:
        return None
    if isinstance(dato, dict):
        for k, v in dato.items():
            if str(k).lower() in claves and v not in (None, "", 0, "0"):
                return v
        for v in dato.values():
            encontrado = _buscar(v, claves, profundidad + 1)
            if encontrado not in (None, ""):
                return encontrado
    elif isinstance(dato, list):
        for v in dato:
            encontrado = _buscar(v, claves, profundidad + 1)
            if encontrado not in (None, ""):
                return encontrado
    return None


def _fecha(ts):
    return datetime.fromtimestamp(ts) if ts else None


def _fue_cancelada(tx, raw):
    texto = " ".join(str(tx.get(c) or "") for c in ("nota", "resuelto_por")).lower()
    if "cancel" in texto or "anul" in texto:
        return True
    status = str((raw or {}).get("status") or "").lower() if isinstance(raw, dict) else ""
    return status in ("canceled", "cancelled", "cancelado", "refunded")


def venta_publica(tx):
    """Una fila de la base → el detalle que se entrega hacia afuera."""
    try:
        raw = json.loads(tx["raw_response"]) if tx.get("raw_response") else None
    except (TypeError, ValueError):
        raw = tx.get("raw_response")
    enviada = _fecha(tx.get("created_at"))
    procesada = _fecha(tx.get("resolved_at"))
    estado = tx.get("estado")
    tarjeta = _buscar(raw, _CLAVES_TARJETA)
    return {
        "transaction_id": tx.get("tx_id"),
        "fecha": enviada.strftime("%Y-%m-%d") if enviada else None,
        "monto": tx.get("monto"),
        "estado": estado,
        "aprobada": estado == "APROBADO",
        "cancelada": _fue_cancelada(tx, raw),
        "sin_resolver": estado in tx_store.ESTADOS_SIN_RESOLVER,
        "maquina": NOMBRE_MAQUINA.get(tx.get("tipo"), tx.get("tipo")),
        "tipo": tx.get("tipo"),
        "terminal_id": tx.get("terminal_id"),
        "id_sucursal": tx.get("id_sucursal"),
        "nombre_caja": tx.get("nombre_caja"),
        "ticket": tx.get("ticket"),
        "hora_envio": enviada.strftime("%H:%M:%S") if enviada else None,
        "hora_proceso": procesada.strftime("%H:%M:%S") if procesada else None,
        "enviada_en": enviada.isoformat(timespec="seconds") if enviada else None,
        "procesada_en": procesada.isoformat(timespec="seconds") if procesada else None,
        "duracion_segundos": round(tx["resolved_at"] - tx["created_at"], 1)
                             if tx.get("resolved_at") and tx.get("created_at") else None,
        "codigo_autorizacion": _buscar(raw, _CLAVES_AUTORIZACION),
        "comprobante_pos": _buscar(raw, _CLAVES_COMPROBANTE),
        "tarjeta_ultimos4": str(tarjeta)[-4:] if tarjeta not in (None, "") else None,
        "nota": tx.get("nota"),
        "resuelto_por": tx.get("resuelto_por"),
        "respuesta_pos": raw,
    }


def _dia(texto, nombre):
    try:
        return datetime.strptime(texto.strip(), "%Y-%m-%d")
    except (AttributeError, ValueError):
        raise ValueError(f"'{nombre}' debe tener el formato AAAA-MM-DD (ej. 2026-11-07)")


def _entero(texto, nombre, defecto, minimo, maximo):
    if texto in (None, ""):
        return defecto
    try:
        valor = int(str(texto).strip())
    except ValueError:
        raise ValueError(f"'{nombre}' debe ser un número entero")
    return max(minimo, min(maximo, valor))


def consultar(args):
    """
    args: los parámetros de la URL (request.args). Todos son opcionales:
      desde, hasta   AAAA-MM-DD (ambos días incluidos; sin ninguno = hoy)
      tipo           transbank | getnet | mercadopago | tuu (o haulmer)
      estado         APROBADO | RECHAZADO | ERROR | PENDIENTE | INDETERMINADA
      id_sucursal, nombre_caja
      limite (máx. 1000, por defecto 200), offset (para paginar)
    Devuelve el dict de la respuesta. Lanza ValueError si un parámetro está mal.
    """
    hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    desde = _dia(args["desde"], "desde") if args.get("desde") else None
    hasta = _dia(args["hasta"], "hasta") if args.get("hasta") else None
    if desde is None and hasta is None:
        desde = hasta = hoy
    if desde and hasta and hasta < desde:
        raise ValueError("'hasta' no puede ser anterior a 'desde'")

    tipo = (args.get("tipo") or "").strip().lower() or None
    tipo = TIPO_DESDE_REQUEST.get(tipo, tipo)
    if tipo and tipo not in NOMBRE_MAQUINA:
        raise ValueError(f"'tipo' debe ser uno de: {', '.join(NOMBRE_MAQUINA)}")
    estado = (args.get("estado") or "").strip().upper() or None
    if estado and estado not in ESTADOS:
        raise ValueError(f"'estado' debe ser uno de: {', '.join(ESTADOS)}")
    limite = _entero(args.get("limite"), "limite", 200, 1, LIMITE_MAXIMO)
    offset = _entero(args.get("offset"), "offset", 0, 0, 10_000_000)

    filas, total, resumen = tx_store.listar_historial(
        desde=time.mktime(desde.timetuple()) if desde else None,
        hasta=time.mktime((hasta + timedelta(days=1)).timetuple()) if hasta else None,
        tipo=tipo, estado=estado,
        id_sucursal=args.get("id_sucursal"), nombre_caja=args.get("nombre_caja"),
        limite=limite, offset=offset,
    )
    ventas = [venta_publica(f) for f in filas]
    aprobadas = resumen.get("APROBADO", {"cantidad": 0, "monto": 0})
    return {
        "ok": True,
        "filtros": {
            "desde": desde.strftime("%Y-%m-%d") if desde else None,
            "hasta": hasta.strftime("%Y-%m-%d") if hasta else None,
            "tipo": tipo, "estado": estado,
            "id_sucursal": args.get("id_sucursal") or None, "nombre_caja": args.get("nombre_caja") or None,
            "limite": limite, "offset": offset,
        },
        "total": total,
        "devueltas": len(ventas),
        "hay_mas": offset + len(ventas) < total,
        "resumen": {
            "cantidad_aprobadas": aprobadas["cantidad"],
            "monto_aprobado": aprobadas["monto"],
            "por_estado": resumen,
        },
        "ventas": ventas,
    }
