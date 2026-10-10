"""
dev1_api.py - Real read-only endpoints for Backend Dev 1

GET /parts/{part_id}/availability
GET /parts/{part_id}/ledger

Read-only: uses db.query()/query_one() and the existing helpers.
Never writes, never opens tx(), never touches the outbox.
"""
import secrets
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import db

router = APIRouter(tags=["Dev 1 - Ledger & Stock (Real)"])


def now_iso() -> str:
    """Current UTC time with SECOND precision, format YYYY-MM-DDTHH:MM:SSZ."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TransferRequest(BaseModel):
    part_id: int
    from_location_id: int
    to_location_id: int
    quantity: int = Field(ge=1)


class CountLine(BaseModel):
    part_id: int
    counted_quantity: int = Field(ge=0)


class CountRequest(BaseModel):
    location_id: int
    reason: str
    lines: list[CountLine]


@router.get("/parts/{part_id}/availability")
def get_availability(part_id: int):
    # 404 only if the part doesn't exist at all in the catalog
    part = db.query_one("SELECT part_id FROM parts WHERE part_id = ?", (part_id,))
    if not part:
        raise HTTPException(status_code=404, detail="Pieza no encontrada")

    on_hand = db.on_hand_total(part_id)
    reserved = db.reserved_for_part(part_id)
    available = on_hand - reserved  # Never clamped; may be negative after a cycle count

    # One entry per location with on_hand != 0, ordered by location_id ascending
    rows = db.query(
        """
        SELECT sb.location_id, l.code, sb.on_hand
        FROM stock_balances sb
        LEFT JOIN locations l ON sb.location_id = l.location_id
        WHERE sb.part_id = ? AND sb.on_hand != 0
        ORDER BY sb.location_id ASC
        """,
        (part_id,),
    )

    locations = []
    for r in rows:
        loc: dict = {
            "location_id": r["location_id"],
            "on_hand": r["on_hand"],
            "reserved": 0,          # reservations are per-part, never per-shelf
            "available": r["on_hand"],
        }
        if r["code"] is not None:   # omit key when location row doesn't exist
            loc["code"] = r["code"]
        locations.append(loc)

    return {
        "part_id": part_id,
        "on_hand": on_hand,
        "reserved": reserved,
        "available": available,
        "locations": locations,
    }


@router.get("/parts/{part_id}/ledger")
def get_ledger(part_id: int):
    # 404 only if the part doesn't exist at all in the catalog
    part = db.query_one("SELECT part_id FROM parts WHERE part_id = ?", (part_id,))
    if not part:
        raise HTTPException(status_code=404, detail="Pieza no encontrada")

    # Fetch in chronological order so we can compute a running balance
    rows = db.query(
        """
        SELECT id AS movement_id, type, part_id, location_id,
               quantity, occurred_at, reference
        FROM ledger_movements
        WHERE part_id = ?
        ORDER BY occurred_at ASC, id ASC
        """,
        (part_id,),
    )

    running_balance = 0
    entries = []
    for r in rows:
        running_balance += r["quantity"]
        entries.append({
            "movement_id": r["movement_id"],
            "type":        r["type"],
            "part_id":     r["part_id"],
            "location_id": r["location_id"],
            "quantity":    r["quantity"],
            "occurred_at": r["occurred_at"],
            "reference":   r["reference"],   # may be None/null
            "balance":     running_balance,
        })

    # Newest first: (occurred_at DESC, id DESC)
    entries.reverse()

    return {
        "part_id": part_id,
        "entries": entries,
    }


@router.post("/transfers", status_code=201)
def post_transfer(req: TransferRequest):
    with db.tx() as c:
        if req.from_location_id == req.to_location_id:
            raise HTTPException(status_code=422, detail="Las ubicaciones de origen y destino deben ser distintas")

        part = c.execute("SELECT part_id FROM parts WHERE part_id = ?", (req.part_id,)).fetchone()
        if not part:
            raise HTTPException(status_code=404, detail="Pieza no encontrada")

        from_loc = c.execute("SELECT location_id FROM locations WHERE location_id = ?", (req.from_location_id,)).fetchone()
        if not from_loc:
            raise HTTPException(status_code=404, detail="Ubicación origen no encontrada")

        to_loc = c.execute("SELECT location_id FROM locations WHERE location_id = ?", (req.to_location_id,)).fetchone()
        if not to_loc:
            raise HTTPException(status_code=404, detail="Ubicación destino no encontrada")

        current_stock = db.on_hand_at(c, req.part_id, req.from_location_id)
        if current_stock < req.quantity:
            raise HTTPException(status_code=409, detail="Saldo insuficiente en ubicación origen")

        ts = now_iso()
        ref = f"TR-{secrets.token_hex(4)}"

        out_id = db.record_movement(c, "transfer_out", req.part_id, req.from_location_id, -req.quantity, ts, reference=ref)
        db.record_movement(c, "transfer_in", req.part_id, req.to_location_id, req.quantity, ts, reference=ref)

        db.enqueue_event(c, "stock.transferred", f"part:{req.part_id}", {
            "part_id": req.part_id,
            "from_location_id": req.from_location_id,
            "to_location_id": req.to_location_id,
            "quantity": req.quantity,
        }, ts)

        return {
            "movement_id": out_id,
            "type": "transfer_out",
            "part_id": req.part_id,
            "location_id": req.from_location_id,
            "quantity": -req.quantity,
            "occurred_at": ts,
            "reference": ref,
        }


@router.post("/counts", status_code=201)
def post_count(req: CountRequest):
    with db.tx() as c:
        if not req.reason or not req.reason.strip():
            raise HTTPException(status_code=422, detail="El motivo del conteo es obligatorio")
        stripped_reason = req.reason.strip()

        seen_parts = set()
        for line in req.lines:
            if line.part_id in seen_parts:
                raise HTTPException(status_code=422, detail="Pieza duplicada en las líneas de conteo")
            seen_parts.add(line.part_id)

        loc = c.execute("SELECT location_id FROM locations WHERE location_id = ?", (req.location_id,)).fetchone()
        if not loc:
            raise HTTPException(status_code=404, detail="Ubicación no encontrada")

        for line in req.lines:
            part = c.execute("SELECT part_id FROM parts WHERE part_id = ?", (line.part_id,)).fetchone()
            if not part:
                raise HTTPException(status_code=404, detail=f"Pieza {line.part_id} no encontrada")

        ts = now_iso()
        adjustments = []
        for line in req.lines:
            current = db.on_hand_at(c, line.part_id, req.location_id)
            delta = line.counted_quantity - current
            if delta != 0:
                db.record_movement(c, "adjustment", line.part_id, req.location_id, delta, ts, reference=stripped_reason)
                db.enqueue_event(c, "stock.adjusted", f"part:{line.part_id}", {
                    "part_id": line.part_id,
                    "location_id": req.location_id,
                    "delta": delta,
                    "reason": stripped_reason,
                }, ts)
                adjustments.append({"part_id": line.part_id, "delta": delta})

        return {"adjustments": adjustments}

