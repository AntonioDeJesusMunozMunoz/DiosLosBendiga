"""
dev3_hooks.py - Backend Dev 3: Compras, Matching, Recepción U-100, Surtido de faltantes y Reabasto.

Mismas reglas que purchasing.py (reglas 9, 10, 11, 12 y 18 del contrato), pero
guardando en las tablas SQLite del equipo (db.py) en lugar del diccionario `state`.

Las dos funciones que llama el equipo corren DENTRO de un db.tx() que abre el
processor de Dev 2: `c` es la conexión. Aquí nunca se hace COMMIT ni se abre otro
tx(); solo se escriben filas y se llama db.enqueue_event(...) para que los eventos
de salida queden en el outbox junto con el cambio (todo o nada).

    handle_purchasing(c, event)                          <- topic shop.purchasing
    recompute_reorder_suggestions(c, part_ids, occurred_at)  <- lo llama Dev 2

receive_into(...) también la usa dev3_api.py al resolver una recepción a mano.
"""
import json
import logging
import re

import db

log = logging.getLogger(__name__)

RECEIVING_CODE = "U-100"
RECEIVING_LOCATION_ID = 100     # regla 11: location_id 100


# ------------------------------------------------------------------ utilidades
def normalizar(value):
    """Quita TODOS los espacios y pasa a mayúsculas. 'tornillo 6x12' -> 'TORNILLO6X12'."""
    if not value:
        return None
    return re.sub(r"\s+", "", str(value)).upper() or None


def receiving_location_id(c):
    """location_id de la bahía de recepción U-100 (100 si el catálogo no la trae)."""
    row = c.execute("SELECT location_id FROM locations WHERE code=? LIMIT 1",
                    (RECEIVING_CODE,)).fetchone()
    return row["location_id"] if row else RECEIVING_LOCATION_ID


# ------------------------------------------------------------- regla 10: matching
def match_part(c, part_number):
    """part_id si part_number coincide con EXACTAMENTE una pieza activa; si no, None."""
    target = normalizar(part_number)
    if target is None:
        return None
    matches = [r["part_id"]
               for r in c.execute("SELECT part_id, sku FROM parts WHERE sku IS NOT NULL AND active=1")
               if normalizar(r["sku"]) == target]
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------- entrada: topic shop.purchasing
def handle_purchasing(c, event):
    """Un evento purchase.item_received (reglas 9 a 12)."""
    if event.get("event_type") != "purchase.item_received":
        raise ValueError(f"unknown purchasing event {event.get('event_type')}")
    d = event["data"]
    occurred_at = event["occurred_at"]

    # Regla 9: lo que es para un cliente no entra al almacén ni emite nada
    if d.get("purpose") != "restock":
        return

    part_id = match_part(c, d.get("part_number"))
    line_id = d["line_id"]

    if part_id is None:
        # Regla 10: ninguna o más de una coincidencia -> revisión manual
        c.execute(
            "INSERT INTO unmatched_receipts (purchase_line_id, part_number, description, quantity, "
            "unit_price, currency, received_at, work_order_code, status) "
            "VALUES (?,?,?,?,?,?,?,?,'open')",
            (line_id, d.get("part_number"), d.get("description"), d.get("quantity"),
             d.get("unit_price"), d.get("currency"), d.get("received_at") or occurred_at,
             d.get("work_order_code")))
        db.enqueue_event(c, "stock.unmatched_receipt", f"purchase_line:{line_id}", {
            "purchase_line_id": line_id,
            "part_number": d.get("part_number"),
            "description": d.get("description"),
            "quantity": d.get("quantity"),
        }, occurred_at)
        return

    receive_into(c, part_id=part_id, location_id=receiving_location_id(c),
                 quantity=d["quantity"], purchase_line_id=line_id,
                 unit_cost=d.get("unit_price"), currency=d.get("currency"),
                 work_order_code=d.get("work_order_code"), occurred_at=occurred_at)


# ------------------------------------------------- reglas 11 y 12: entrada + surtido
def receive_into(c, part_id, location_id, quantity, purchase_line_id, unit_cost,
                 currency, work_order_code, occurred_at):
    """Registra la entrada, emite stock.received, surte faltantes y recalcula reabasto.
    Regresa el movement_id de la entrada."""
    movement_id = db.record_movement(
        c, "receipt", part_id, location_id, quantity, occurred_at,
        reference=f"purchase_line:{purchase_line_id}", unit_cost=unit_cost, currency=currency)
    db.enqueue_event(c, "stock.received", f"part:{part_id}", {
        "part_id": part_id,
        "location_id": location_id,
        "quantity": quantity,
        "unit_cost": unit_cost,
        "currency": currency,
        "purchase_line_id": purchase_line_id,
    }, occurred_at)

    _fill_shortages(c, part_id, work_order_code, occurred_at)
    recompute_reorder_suggestions(c, {part_id}, occurred_at)
    return movement_id


def _fill_shortages(c, part_id, work_order_code, occurred_at):
    """Regla 12: primero la orden de work_order_code (si sigue viva), luego las demás
    de la más antigua a la más nueva. Nunca reserva más de lo disponible."""
    remaining = max(db.available_for_part(c, part_id), 0)
    if remaining <= 0:
        return

    rows = c.execute(
        "SELECT s.*, w.code AS wo_code FROM shortages s "
        "JOIN work_orders w ON w.work_order_id = s.work_order_id "
        "WHERE s.part_id=? AND s.status='open' AND w.deleted=0 "
        "  AND s.missing_quantity IS NOT NULL AND s.missing_quantity > 0 "
        "ORDER BY CASE WHEN ? IS NOT NULL AND COALESCE(s.work_order_code, w.code)=? THEN 0 ELSE 1 END, "
        "         s.opened_at, s.id",
        (part_id, work_order_code, work_order_code)).fetchall()

    for s in rows:
        if remaining <= 0:
            break
        alloc = min(remaining, s["missing_quantity"])
        remaining -= alloc
        code = s["work_order_code"] or s["wo_code"]

        # Subir la reserva de esa (orden, línea). Puede no existir o estar cancelled/fulfilled.
        r = c.execute("SELECT * FROM reservations WHERE work_order_id=? AND bom_line_id=?",
                      (s["work_order_id"], s["bom_line_id"])).fetchone()
        if r is None:
            cur = c.execute(
                "INSERT INTO reservations (work_order_id, bom_line_id, inspection_item_id, "
                "source_inspection_id, part_id, quantity, issued_quantity, status) "
                "VALUES (?,?,?,?,?,?,0,'active')",
                (s["work_order_id"], s["bom_line_id"], s["inspection_item_id"],
                 s["source_inspection_id"], part_id, alloc))
            reservation_id = cur.lastrowid
        else:
            base = r["quantity"] if r["status"] == "active" else r["issued_quantity"]
            c.execute("UPDATE reservations SET quantity=?, status='active', part_id=? WHERE id=?",
                      (base + alloc, part_id, r["id"]))
            reservation_id = r["id"]

        # Bajar el faltante (o cerrarlo)
        left = s["missing_quantity"] - alloc
        if left == 0:
            c.execute("UPDATE shortages SET missing_quantity=0, status='resolved' WHERE id=?", (s["id"],))
        else:
            c.execute("UPDATE shortages SET missing_quantity=? WHERE id=?", (left, s["id"]))

        db.enqueue_event(c, "stock.reserved", code, {
            "reservation_id": reservation_id,
            "work_order_id": s["work_order_id"],
            "part_id": part_id,
            "quantity": alloc,
            "inspection_item_id": s["inspection_item_id"],
            "bom_line_id": s["bom_line_id"],
        }, occurred_at)
        db.enqueue_event(c, "stock.shortage_resolved", code, {
            "work_order_id": s["work_order_id"],
            "part_id": part_id,
            "quantity": alloc,
        }, occurred_at)


# ------------------------------------------------------------- regla 18: reabasto
def recompute_reorder_suggestions(c, part_ids, occurred_at):
    """Recalcula la sugerencia de cada pieza. Emite stock.reorder_suggested solo
    cuando la sugerencia nace o cambia; la borra si ya no hace falta."""
    for part_id in part_ids:
        if part_id is None:
            continue
        rows = c.execute(
            "SELECT work_order_id, missing_quantity FROM shortages "
            "WHERE part_id=? AND status='open'", (part_id,)).fetchall()
        total = sum(r["missing_quantity"] or 0 for r in rows)
        wo_ids = sorted({r["work_order_id"] for r in rows})
        if rows and total == 0:
            total = 1          # faltante sin cantidad conocida: al menos 1

        # Política opcional de mínimo (tabla part_policies)
        pol = c.execute("SELECT min_qty, reorder_qty FROM part_policies WHERE part_id=?",
                        (part_id,)).fetchone()
        if pol and pol["min_qty"] is not None:
            avail = db.available_for_part(c, part_id)
            if avail < pol["min_qty"]:
                total = max(total, pol["reorder_qty"] or (pol["min_qty"] - avail))

        old = c.execute("SELECT suggested_quantity, work_order_ids FROM reorder_suggestions "
                        "WHERE part_id=?", (part_id,)).fetchone()
        if total <= 0:
            if old is not None:
                c.execute("DELETE FROM reorder_suggestions WHERE part_id=?", (part_id,))
            continue

        ids_json = json.dumps(wo_ids)
        if old is not None and old["suggested_quantity"] == total and old["work_order_ids"] == ids_json:
            continue           # no cambió: no se emite nada
        c.execute(
            "INSERT INTO reorder_suggestions (part_id, suggested_quantity, work_order_ids) VALUES (?,?,?) "
            "ON CONFLICT(part_id) DO UPDATE SET suggested_quantity=excluded.suggested_quantity, "
            "work_order_ids=excluded.work_order_ids",
            (part_id, total, ids_json))
        db.enqueue_event(c, "stock.reorder_suggested", f"part:{part_id}", {
            "part_id": part_id,
            "suggested_quantity": total,
            "work_order_ids": wo_ids,
        }, occurred_at)