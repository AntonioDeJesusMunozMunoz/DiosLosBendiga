"""
dev1_api.py - Real read-only endpoints for Backend Dev 1

GET /parts/{part_id}/availability
GET /parts/{part_id}/ledger

Read-only: uses db.query()/query_one() and the existing helpers.
Never writes, never opens tx(), never touches the outbox.
"""
from fastapi import APIRouter, HTTPException
import db

router = APIRouter(tags=["Dev 1 - Ledger & Stock (Real)"])


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

