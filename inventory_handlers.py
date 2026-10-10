"""
inventory_handlers.py - Dev 2: business logic for the four input topics.

Every handler has the signature  handler(c, event)  and runs INSIDE a db.tx()
opened by processor.Processor. `c` is the connection. Handlers never commit,
never open their own transaction and never touch Kafka: they write rows and call
db.enqueue_event(...) so output events land in the outbox atomically.

Raise errors.MissingDependency when an event references something we do not know
yet (order, part, BOM line): it gets parked and retried (contract rule 20).

Dev 2 owns one extra table, `needs`: the need of an order per BOM line (rule 2).
reservations and shortages are the effects of a need; `needs` remembers how much
was asked for and which inspection set it (needed for voided and for replacement).
"""
import db
import dev3_hooks
from db import available_for_part, enqueue_event
from errors import MissingDependency

# Families that are services (chrome plating, grinding), not physical stock.
# Lines for these parts get no need, no reservation and no shortage.
# Empty the set to inventory them instead.
NON_STOCKED_FAMILIES = {"SERVICIO"}

DEV2_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS needs (
         work_order_id        INTEGER NOT NULL,
         bom_line_id          INTEGER NOT NULL,
         work_order_code      TEXT,
         inspection_item_id   INTEGER NOT NULL,
         part_id              INTEGER,
         name                 TEXT NOT NULL,
         required_quantity    INTEGER,            -- NULL = amount unknown
         source_inspection_id INTEGER,
         status               TEXT NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active','cancelled')),
         PRIMARY KEY (work_order_id, bom_line_id)
       )""",
    "CREATE INDEX IF NOT EXISTS idx_needs_inspection ON needs(work_order_id, source_inspection_id)",
]


def init_dev2_schema():
    """Create Dev 2's tables. Safe to call on every startup (after db.init_db())."""
    with db.tx() as c:
        for stmt in DEV2_SCHEMA:
            c.execute(stmt)


# ------------------------------------------------------------------ catalog
def handle_catalog(c, ev):
    t, d = ev["event_type"], ev["data"]
    if t == "part.upserted":
        sku = d.get("sku")
        unit = d.get("unit")
        c.execute(
            "INSERT INTO parts (part_id, sku, sku_norm, name, family, grp, subgroup, unit_name, active) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(part_id) DO UPDATE SET sku=excluded.sku, sku_norm=excluded.sku_norm, "
            "name=excluded.name, family=excluded.family, grp=excluded.grp, "
            "subgroup=excluded.subgroup, unit_name=excluded.unit_name, active=excluded.active",
            (d["part_id"], sku, sku.strip().upper() if sku else None, d["name"],
             d.get("family"), d.get("group"), d.get("subgroup"),
             unit["name"] if unit else None, 1 if d.get("active", True) else 0))
    elif t == "location.upserted":
        # ASSUMED fields (schema not provided): location_id, code, name, is_workbench, active
        c.execute(
            "INSERT INTO locations (location_id, code, name, is_workbench, active) VALUES (?,?,?,?,?) "
            "ON CONFLICT(location_id) DO UPDATE SET code=excluded.code, name=excluded.name, "
            "is_workbench=excluded.is_workbench, active=excluded.active",
            (d["location_id"], d["code"], d.get("name"),
             1 if d.get("is_workbench") else 0, 1 if d.get("active", True) else 0))
    elif t == "bom.upserted":
        # The event carries the COMPLETE parts list of the model: replace the old one.
        c.execute("DELETE FROM bom_lines WHERE model_id=?", (d["model_id"],))
        for ln in d["lines"]:
            c.execute(
                "INSERT OR REPLACE INTO bom_lines "
                "(model_id, bom_line_id, part_id, name, group_name, qty_per_unit, sort_order) "
                "VALUES (?,?,?,?,?,?,?)",
                (d["model_id"], ln["bom_line_id"], ln["part_id"], ln["name"],
                 ln["group_name"], ln["qty_per_unit"], ln["sort_order"]))
    else:
        raise ValueError(f"unknown catalog event {t}")


# -------------------------------------------------------------- work orders
def handle_work_order(c, ev):
    t, d = ev["event_type"], ev["data"]
    if t == "work_order.opened":
        c.execute(
            "INSERT INTO work_orders (work_order_id, code, model_id, department, received_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(work_order_id) DO UPDATE SET code=excluded.code, model_id=excluded.model_id, "
            "department=excluded.department, received_at=excluded.received_at",
            (d["work_order_id"], d["code"], d["model_id"], d["department"], d["received_at"]))
    elif t == "work_order.stage_changed":
        cur = c.execute("UPDATE work_orders SET stage=? WHERE work_order_id=?",
                        (d["to_stage"], d["work_order_id"]))
        if cur.rowcount == 0:
            raise MissingDependency(f"work order {d['work_order_id']} not seen yet")
    elif t == "work_order.deleted":
        wo = _get_order(c, d["work_order_id"])
        if wo is None:
            raise MissingDependency(f"work order {d['work_order_id']} not seen yet")
        c.execute("UPDATE work_orders SET deleted=1 WHERE work_order_id=?", (wo["work_order_id"],))
        touched = set()
        for need in c.execute("SELECT * FROM needs WHERE work_order_id=? AND status='active'",
                              (wo["work_order_id"],)).fetchall():
            touched |= _release_need(c, need)
        _notify_reorder(c, touched, ev)
    else:
        raise ValueError(f"unknown work order event {t}")


# -------------------------------------------------------------- inspections
def handle_inspection(c, ev):
    t = ev["event_type"]
    if t in ("inspection.submitted", "inspection.rejected", "inspection.discarded"):
        return  # rule 1: they never move inventory
    if t == "inspection.approved":
        _approve(c, ev)
    elif t == "inspection.voided":
        _void(c, ev)
    else:
        raise ValueError(f"unknown inspection event {t}")


def _approve(c, ev):
    d = ev["data"]
    wo = _get_order(c, d["work_order_id"])
    if wo is None:
        raise MissingDependency(f"work order {d['work_order_id']} not seen yet")
    if wo["deleted"]:
        return  # the order was removed: nothing to reserve for it

    touched = set()
    for item in d["items"]:
        bl = item["bom_line_id"]

        if item["action"] != "buy":
            # repair / ok: customer's own part, never a need (rule 4). If a previous
            # approval had made this line a need, the newest approval replaces it.
            need = _get_need(c, wo["work_order_id"], bl)
            if need is not None and need["status"] == "active":
                touched |= _release_need(c, need)
            continue

        part_id = item["part_id"]
        if part_id is not None:
            part = c.execute("SELECT family FROM parts WHERE part_id=?", (part_id,)).fetchone()
            if part is None:
                raise MissingDependency(f"part {part_id} not seen yet")
            if (part["family"] or "").strip().upper() in NON_STOCKED_FAMILIES:
                continue

        qty = item["quantity"]
        if qty is None:  # rule 5: fall back to the model's parts list
            line = c.execute("SELECT qty_per_unit FROM bom_lines WHERE model_id=? AND bom_line_id=?",
                             (wo["model_id"], bl)).fetchone()
            if line is None:
                raise MissingDependency(f"bom line {bl} of model {wo['model_id']} not seen yet")
            qty = line["qty_per_unit"]  # may still be None -> shortage without quantity

        touched |= _apply_line(c, ev, wo, d["inspection_id"], item, qty)

    _notify_reorder(c, touched, ev)


def _void(c, ev):
    d = ev["data"]
    touched = set()
    rows = c.execute(
        "SELECT * FROM needs WHERE work_order_id=? AND source_inspection_id=? AND status='active'",
        (d["work_order_id"], d["inspection_id"])).fetchall()
    for need in rows:
        touched |= _release_need(c, need)
    _notify_reorder(c, touched, ev)


# ------------------------------------------------------------ need logic
def _apply_line(c, ev, wo, inspection_id, item, qty):
    """Set the need of one (order, bom_line) to `qty` (replace, never add) and move
    reservations/shortages by the difference. Returns the set of part ids touched."""
    wo_id, bl, part_id = wo["work_order_id"], item["bom_line_id"], item["part_id"]
    touched = {part_id} if part_id is not None else set()
    need = _get_need(c, wo_id, bl)
    active = need is not None and need["status"] == "active"

    if qty is None:
        # Amount unknown: shortage with missing_quantity = null, nothing reserved.
        if active:
            return set()
        _upsert_need(c, wo, item, inspection_id, None)
        _raise_shortage(c, ev, wo, item, inspection_id, None)
        return touched

    old = (need["required_quantity"] or 0) if active else 0
    _upsert_need(c, wo, item, inspection_id, qty)
    delta = qty - old

    if delta > 0:
        # New or bigger need: reserve min(available, difference), rest is shortage.
        can = max(available_for_part(c, part_id), 0) if part_id is not None else 0
        reserve_now = min(can, delta)
        if reserve_now > 0:
            _reserve(c, ev, wo, item, inspection_id, reserve_now)
        if delta - reserve_now > 0:
            _raise_shortage(c, ev, wo, item, inspection_id, delta - reserve_now)
    elif delta < 0:
        _shrink(c, wo_id, bl, -delta)
    return touched


def _reserve(c, ev, wo, item, inspection_id, qty):
    wo_id, bl = wo["work_order_id"], item["bom_line_id"]
    row = c.execute("SELECT * FROM reservations WHERE work_order_id=? AND bom_line_id=?",
                    (wo_id, bl)).fetchone()
    if row is None:
        cur = c.execute(
            "INSERT INTO reservations (work_order_id, bom_line_id, inspection_item_id, "
            "source_inspection_id, part_id, quantity, issued_quantity, status) "
            "VALUES (?,?,?,?,?,?,0,'active')",
            (wo_id, bl, item["inspection_item_id"], inspection_id, item["part_id"], qty))
        rid = cur.lastrowid
    else:
        base = row["quantity"] if row["status"] == "active" else row["issued_quantity"]
        c.execute(
            "UPDATE reservations SET quantity=?, status='active', inspection_item_id=?, "
            "source_inspection_id=?, part_id=? WHERE id=?",
            (base + qty, item["inspection_item_id"], inspection_id, item["part_id"], row["id"]))
        rid = row["id"]
    enqueue_event(c, "stock.reserved", wo["code"], {
        "reservation_id": rid, "work_order_id": wo_id, "part_id": item["part_id"],
        "quantity": qty, "inspection_item_id": item["inspection_item_id"], "bom_line_id": bl,
    }, ev["occurred_at"])


def _raise_shortage(c, ev, wo, item, inspection_id, add):
    """Create or grow the open shortage of this line. `add` None = unknown amount.
    Emits stock.shortage_detected with the line's TOTAL open missing quantity."""
    wo_id, bl = wo["work_order_id"], item["bom_line_id"]
    s = _open_shortage(c, wo_id, bl)
    if s is None:
        total = add
        c.execute(
            "INSERT INTO shortages (work_order_id, work_order_code, bom_line_id, inspection_item_id, "
            "source_inspection_id, part_id, name, missing_quantity, status, opened_at) "
            "VALUES (?,?,?,?,?,?,?,?,'open',?)",
            (wo_id, wo["code"], bl, item["inspection_item_id"], inspection_id,
             item["part_id"], item["name"], total, ev["occurred_at"]))
    else:
        if add is None:
            return  # still unknown, nothing new to report
        total = (s["missing_quantity"] or 0) + add
        c.execute(
            "UPDATE shortages SET missing_quantity=?, inspection_item_id=?, source_inspection_id=?, "
            "part_id=?, name=? WHERE id=?",
            (total, item["inspection_item_id"], inspection_id, item["part_id"], item["name"], s["id"]))
    enqueue_event(c, "stock.shortage_detected", wo["code"], {
        "work_order_id": wo_id, "part_id": item["part_id"], "name": item["name"],
        "missing_quantity": total, "inspection_item_id": item["inspection_item_id"],
    }, ev["occurred_at"])


def _shrink(c, wo_id, bl, amount):
    """The need dropped by `amount`: shrink the open shortage first, then release
    reservation (never below what was already issued). No events (rule 15)."""
    take = 0
    s = _open_shortage(c, wo_id, bl)
    if s is not None:
        cur = s["missing_quantity"] or 0
        take = min(amount, cur)
        left = cur - take
        if left == 0:
            c.execute("UPDATE shortages SET missing_quantity=0, status='cancelled' WHERE id=?", (s["id"],))
        else:
            c.execute("UPDATE shortages SET missing_quantity=? WHERE id=?", (left, s["id"]))
    rest = amount - take
    if rest > 0:
        row = c.execute("SELECT * FROM reservations WHERE work_order_id=? AND bom_line_id=? AND status='active'",
                        (wo_id, bl)).fetchone()
        if row is not None:
            new_q = max(row["quantity"] - rest, row["issued_quantity"])
            status = "active"
            if new_q == row["issued_quantity"]:
                status = "fulfilled" if new_q > 0 else "cancelled"
            c.execute("UPDATE reservations SET quantity=?, status=? WHERE id=?", (new_q, status, row["id"]))


def _release_need(c, need):
    """Cancel a need: free its active reservation, close its open shortage."""
    wo_id, bl = need["work_order_id"], need["bom_line_id"]
    c.execute("UPDATE reservations SET status='cancelled' "
              "WHERE work_order_id=? AND bom_line_id=? AND status='active'", (wo_id, bl))
    c.execute("UPDATE shortages SET status='cancelled' "
              "WHERE work_order_id=? AND bom_line_id=? AND status='open'", (wo_id, bl))
    c.execute("UPDATE needs SET status='cancelled' WHERE work_order_id=? AND bom_line_id=?", (wo_id, bl))
    return {need["part_id"]} if need["part_id"] is not None else set()


# ---------------------------------------------------------------- small helpers
def _get_order(c, work_order_id):
    return c.execute("SELECT * FROM work_orders WHERE work_order_id=?", (work_order_id,)).fetchone()


def _get_need(c, wo_id, bl):
    return c.execute("SELECT * FROM needs WHERE work_order_id=? AND bom_line_id=?", (wo_id, bl)).fetchone()


def _open_shortage(c, wo_id, bl):
    return c.execute("SELECT * FROM shortages WHERE work_order_id=? AND bom_line_id=? AND status='open' "
                     "ORDER BY id LIMIT 1", (wo_id, bl)).fetchone()


def _upsert_need(c, wo, item, inspection_id, required):
    c.execute(
        "INSERT INTO needs (work_order_id, bom_line_id, work_order_code, inspection_item_id, part_id, "
        "name, required_quantity, source_inspection_id, status) VALUES (?,?,?,?,?,?,?,?,'active') "
        "ON CONFLICT(work_order_id, bom_line_id) DO UPDATE SET "
        "inspection_item_id=excluded.inspection_item_id, part_id=excluded.part_id, name=excluded.name, "
        "required_quantity=excluded.required_quantity, "
        "source_inspection_id=excluded.source_inspection_id, status='active'",
        (wo["work_order_id"], item["bom_line_id"], wo["code"], item["inspection_item_id"],
         item["part_id"], item["name"], required, inspection_id))


def _notify_reorder(c, part_ids, ev):
    if part_ids:
        dev3_hooks.recompute_reorder_suggestions(c, set(part_ids), ev["occurred_at"])


# ------------------------------------------------------------------ dispatch
HANDLERS = {
    "shop.catalog": handle_catalog,
    "shop.work_orders": handle_work_order,
    "shop.inspections": handle_inspection,
    "shop.purchasing": dev3_hooks.handle_purchasing,
}

# Events that can unblock parked ones: when one of these succeeds, retry the parked events now.
TRIGGER_TYPES = {"part.upserted", "bom.upserted", "work_order.opened"}
