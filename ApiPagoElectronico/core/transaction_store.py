"""
Registro persistente de intentos de venta contra un POS físico.

Existe para resolver un problema concreto: si se envía una venta a un POS
(por ejemplo Getnet, por cable serie) y la confirmación nunca llega de
vuelta al software (cable cortado, timeout, reinicio del servicio), el
cliente puede haber pagado igual en la máquina sin que el sistema se entere.

Cada intento se registra en SQLite ANTES de enviarse al POS, para que el
rastro sobreviva a un crash o reinicio del proceso, y se actualiza cuando
se conoce (o se reconcilia) el resultado final. Mientras una transacción
quede en estado PENDIENTE o INDETERMINADA para una caja, esa caja debe
bloquearse para nuevas ventas hasta resolverla (automática o manualmente).
"""
import os
import sqlite3
import threading
import json
import time
import logging

from config.settings import DB_FILE, APP_NAME

logger = logging.getLogger(APP_NAME)

ESTADOS_SIN_RESOLVER = ("PENDIENTE", "INDETERMINADA")

_lock = threading.Lock()
_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(os.path.abspath(DB_FILE)), exist_ok=True)  # data/
        _conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        # WAL: un corte de luz a mitad de una escritura no corrompe la base y las
        # lecturas (panel, /pago/estado) no esperan a las escrituras.
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA busy_timeout=5000")
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transacciones (
                tx_id TEXT PRIMARY KEY,
                tipo TEXT NOT NULL,
                ticket TEXT,
                terminal_id TEXT,
                id_sucursal TEXT,
                nombre_caja TEXT,
                monto INTEGER,
                estado TEXT NOT NULL,
                client_id_sucursal TEXT,
                client_nombre_caja TEXT,
                raw_response TEXT,
                nota TEXT,
                resuelto_por TEXT,
                created_at REAL NOT NULL,
                resolved_at REAL
            )
            """
        )
        # Migración para bases creadas antes de agregar resuelto_por (auditoría:
        # quién confirmó una transacción indeterminada, humano o el sistema).
        try:
            _conn.execute("ALTER TABLE transacciones ADD COLUMN resuelto_por TEXT")
            _conn.commit()
        except sqlite3.OperationalError:
            pass  # la columna ya existe
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_estado ON transacciones(estado)")
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_caja ON transacciones(id_sucursal, nombre_caja)")
        _conn.commit()
    return _conn


def registrar_intento(tx_id, tipo, ticket, terminal_id, id_sucursal, nombre_caja, monto,
                       client_id_sucursal=None, client_nombre_caja=None):
    """Registra un intento de venta ANTES de mandarlo al POS."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO transacciones
                (tx_id, tipo, ticket, terminal_id, id_sucursal, nombre_caja, monto, estado,
                 client_id_sucursal, client_nombre_caja, raw_response, nota, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDIENTE', ?, ?, NULL, NULL, ?, NULL)
            """,
            (tx_id, tipo, ticket, terminal_id, id_sucursal, nombre_caja, monto,
             client_id_sucursal, client_nombre_caja, time.time()),
        )
        conn.commit()


def actualizar_estado(tx_id, estado, raw_response=None, nota=None, resuelto_por=None):
    with _lock:
        conn = _get_conn()
        raw_json = json.dumps(raw_response, ensure_ascii=False, default=str) if raw_response is not None else None
        conn.execute(
            """
            UPDATE transacciones
            SET estado = ?, raw_response = COALESCE(?, raw_response), nota = COALESCE(?, nota),
                resuelto_por = COALESCE(?, resuelto_por), resolved_at = ?
            WHERE tx_id = ?
            """,
            (estado, raw_json, nota, resuelto_por, time.time(), tx_id),
        )
        conn.commit()


def asignar_ticket(tx_id, ticket):
    """Guarda la referencia del cobro en el medio de pago cuando se conoce después
    de registrar el intento (ej. el id de la orden de Mercado Pago)."""
    with _lock:
        conn = _get_conn()
        conn.execute("UPDATE transacciones SET ticket = ? WHERE tx_id = ?", (ticket, tx_id))
        conn.commit()


def obtener(tx_id):
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM transacciones WHERE tx_id = ?", (tx_id,)).fetchone()
        return dict(row) if row else None


def hay_pendiente_sin_resolver(id_sucursal, nombre_caja):
    """Devuelve el tx_id de la transacción más antigua sin resolver para esta caja, o None."""
    with _lock:
        conn = _get_conn()
        placeholders = ",".join("?" for _ in ESTADOS_SIN_RESOLVER)
        row = conn.execute(
            f"""
            SELECT tx_id FROM transacciones
            WHERE id_sucursal = ? AND nombre_caja = ? AND estado IN ({placeholders})
            ORDER BY created_at ASC LIMIT 1
            """,
            (id_sucursal, nombre_caja, *ESTADOS_SIN_RESOLVER),
        ).fetchone()
        return row["tx_id"] if row else None


def listar_no_resueltas():
    with _lock:
        conn = _get_conn()
        placeholders = ",".join("?" for _ in ESTADOS_SIN_RESOLVER)
        rows = conn.execute(
            f"SELECT * FROM transacciones WHERE estado IN ({placeholders}) ORDER BY created_at DESC",
            ESTADOS_SIN_RESOLVER,
        ).fetchall()
        return [dict(r) for r in rows]


def operation_ids_conocidos(tipo, excluir_tx_id=None, limite=200):
    """
    OperationId (número de comprobante del POS) de las ventas ya resueltas de
    este tipo. Sirve para reconocer que el "último comprobante" del POS es de
    una venta anterior y no de la que se está reconciliando.
    """
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            """
            SELECT raw_response FROM transacciones
            WHERE tipo = ? AND tx_id != ? AND raw_response IS NOT NULL
            ORDER BY created_at DESC LIMIT ?
            """,
            (tipo, excluir_tx_id or "", limite),
        ).fetchall()
    ids = set()
    for row in rows:
        try:
            raw = json.loads(row["raw_response"])
        except (TypeError, ValueError):
            continue
        resp = raw.get("response") if isinstance(raw, dict) else None
        if not isinstance(resp, dict):
            continue
        for k, v in resp.items():
            if k.lower() == "operationid" and v not in (None, "", 0, "0"):
                ids.add(str(v))
    return ids
