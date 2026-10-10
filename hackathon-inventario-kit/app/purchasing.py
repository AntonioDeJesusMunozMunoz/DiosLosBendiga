"""
MÓDULO: purchasing.py (Backend 3)
Responsabilidad: Compras, Matching Inteligente, Bahía U-100 y Faltantes.
"""

from __future__ import annotations

import re
import uuid
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Global lock for thread‑safe mutations of the shared ``state`` dictionary.
# ---------------------------------------------------------------------------
_state_lock = threading.RLock()

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def now_iso() -> str:
    """Return the current UTC timestamp in ISO‑8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def normalizar(value: Optional[str]) -> Optional[str]:
    """Remove **all** whitespace and convert to upper case.
    Returns ``None`` for ``None`` or empty strings.
    """
    if not value:
        return None
    return re.sub(r"\s+", "", value).upper()

def get_receiving_location_id(locations: Dict[int, Dict[str, Any]]) -> Optional[int]:
    """Return the ``location_id`` whose ``code`` is ``U-100``.
    If the catalogue of locations has not been received yet, ``None`` is returned.
    """
    for loc in locations.values():
        if loc.get("code") == "U-100":
            return loc.get("location_id")
    return None

# ---------------------------------------------------------------------------
# 1. Matching – Rule 10
# ---------------------------------------------------------------------------

def match_part(part_number: str, catalog_parts: List[Dict[str, Any]]) -> Optional[int]:
    """Return the ``part_id`` that uniquely matches ``part_number``.
    * Normalise both strings (remove whitespace, upper‑case).
    * Ignore catalogue entries with ``sku`` equal to ``None``.
    * If there is exactly one match, return its ``part_id``; otherwise ``None``.
    """
    if not part_number:
        return None
    target = normalizar(part_number)
    matches: List[int] = []
    for part in catalog_parts:
        sku = part.get("sku")
        if sku:
            if normalizar(sku) == target:
                matches.append(part["part_id"])
    return matches[0] if len(matches) == 1 else None

# ---------------------------------------------------------------------------
# 2. Main entry point – Rules 9, 10, 11, 12
# ---------------------------------------------------------------------------

def handle_purchase_received(
    event_id: str,
    event_data: Dict[str, Any],
    occurred_at: str,
    catalog_parts: List[Dict[str, Any]],
    state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Procesa un purchase.item_received y regresa los eventos a publicar.
    event_id viene del SOBRE del evento (evento["event_id"]), no de data.
    Si falta U-100 lanza LookupError y el evento NO queda procesado (se reintenta).
    """
    with _state_lock:
        processed = state.setdefault("processed_event_ids", set())
        if event_id in processed:
            return []

        # Regla 9: solo restock entra al almacén
        if event_data.get("purpose") != "restock":
            processed.add(event_id)
            return []

        # Regla 10: matching
        part_number = event_data.get("part_number", "")
        part_id = match_part(part_number, catalog_parts)

        purchase_line_id = event_data.get("line_id") or event_data.get("purchase_line_id")
        quantity = event_data.get("quantity", 0)
        raw_price = event_data.get("unit_price") or event_data.get("unit_cost", "0.00")
        try:
            unit_cost = str(Decimal(str(raw_price)))
        except Exception:
            unit_cost = "0.00"

        if part_id is None:
            state.setdefault("unmatched_receipts", {})[purchase_line_id] = {
                "id": purchase_line_id,
                "purchase_line_id": purchase_line_id,
                "part_number": part_number,
                "description": event_data.get("description", ""),
                "quantity": quantity,
                "unit_price": unit_cost,
                "currency": event_data.get("currency"),
                "received_at": event_data.get("received_at", occurred_at),
                "work_order_code": event_data.get("work_order_code"),
                "status": "pending",
            }
            events = [{
                "event_id": str(uuid.uuid4()),
                "event_type": "stock.unmatched_receipt",
                "event_version": 1,
                "occurred_at": occurred_at,
                "source": "inventory",
                "key": f"purchase_line:{purchase_line_id}",
                "data": {
                    "purchase_line_id": purchase_line_id,
                    "part_number": part_number,
                    "description": event_data.get("description", ""),
                    "quantity": quantity,
                },
            }]
            processed.add(event_id)
            return events

        # Si falta U-100, esto lanza LookupError y no se marca como procesado
        events = fulfill_receipt(
            part_id=part_id,
            quantity=quantity,
            purchase_line_id=purchase_line_id,
            unit_cost=unit_cost,
            currency=event_data.get("currency", "MXN"),
            work_order_code=event_data.get("work_order_code"),
            occurred_at=occurred_at,
            state=state,
            locations=state.get("locations", {}),
        )
        processed.add(event_id)
        return events

# ---------------------------------------------------------------------------
# 3. Receipt fulfilment – Rules 11, 12, 18
# ---------------------------------------------------------------------------

def fulfill_receipt(
    part_id: int,
    quantity: int,
    purchase_line_id: int,
    unit_cost: str,
    currency: str,
    work_order_code: Optional[str],
    occurred_at: str,
    state: Dict[str, Any],
    locations: Dict[int, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Create a receipt movement, update shortages and optionally emit a reorder suggestion."""
    output_events: List[Dict[str, Any]] = []

    # ---------------------------------------------------------------------
    # Resolve location dynamically (Rule 11)
    # ---------------------------------------------------------------------
    location_id = get_receiving_location_id(locations)
    if location_id is None:
        raise LookupError("La ubicación U-100 todavía no llega; reintentar después")
    # ---------------------------------------------------------------------
    # Record the receipt movement (source of truth for stock balances)
    # ---------------------------------------------------------------------
    movement = {
        "movement_id": state.get("next_mov_id", 1),
        "type": "receipt",
        "part_id": part_id,
        "location_id": location_id,
        "quantity": quantity,
        "unit_cost": unit_cost,
        "currency": currency,
        "purchase_line_id": purchase_line_id,
        "occurred_at": occurred_at,
        "event_id": str(uuid.uuid4()),
    }
    with _state_lock:
        state.setdefault("movements", []).append(movement)
        state["next_mov_id"] = movement["movement_id"] + 1

    output_events.append(
        {
            "event_id": movement["event_id"],
            "event_type": "stock.received",
            "event_version": 1,
            "occurred_at": occurred_at,
            "source": "inventory",
            "key": f"part:{part_id}",
            "data": {
                "part_id": part_id,
                "location_id": location_id,
                "quantity": quantity,
                "unit_cost": unit_cost,
                "currency": currency,
                "purchase_line_id": purchase_line_id,
            },
        }
    )

    # ---------------------------------------------------------------------
    # Rule 12 – satisfy shortages (reservations and resolution)
    # ---------------------------------------------------------------------
    remaining = quantity
    shortages = state.setdefault("shortages", {}).setdefault(part_id, [])

    # Helper to emit a reservation and a resolution event
    def _emit_reservation_and_resolution(short: Dict[str, Any], qty: int, code_key: str) -> None:
        res_id = state.get("next_res_id", 1)
        state["next_res_id"] = res_id + 1
        state.setdefault("reservations", {})[res_id] = {
            "reservation_id": res_id,
            "work_order_id": short.get("work_order_id"),
            "work_order_code": short.get("work_order_code"),
            "part_id": part_id,
            "location_id": location_id,
            "quantity": qty,
            "status": "active",
            "inspection_item_id": short.get("inspection_item_id"),
        }
        # reservation event
        output_events.append(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "stock.reserved",
                "event_version": 1,
                "occurred_at": occurred_at,
                "source": "inventory",
                "key": code_key,
                "data": {
                    "reservation_id": res_id,
                    "work_order_id": short.get("work_order_id"),
                    "work_order_code": short.get("work_order_code"),
                    "part_id": part_id,
                    "quantity": qty,
                    "inspection_item_id": short.get("inspection_item_id"),
                },
            }
        )
        # shortage resolved event
        output_events.append(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "stock.shortage_resolved",
                "event_version": 1,
                "occurred_at": occurred_at,
                "source": "inventory",
                "key": code_key,
                "data": {
                    "work_order_id": short.get("work_order_id"),
                    "work_order_code": short.get("work_order_code"),
                    "part_id": part_id,
                    "quantity": qty,
                },
            }
        )

    # Priority 1 – use explicit work_order_code if supplied
    if work_order_code and remaining > 0:
        for short in shortages:
            if short.get("work_order_code") == work_order_code and short["quantity"] > 0:
                alloc = min(remaining, short["quantity"])
                short["quantity"] -= alloc
                remaining -= alloc
                _emit_reservation_and_resolution(short, alloc, work_order_code)
                if remaining == 0:
                    break

    # Priority 2 – other shortages, FIFO order (oldest first)
    if remaining > 0:
        for short in shortages:
            if short["quantity"] > 0:
                alloc = min(remaining, short["quantity"])
                short["quantity"] -= alloc
                remaining -= alloc
                code_key = short.get("work_order_code") or f"WO-{short.get('work_order_id')}"                
                _emit_reservation_and_resolution(short, alloc, code_key)
                if remaining == 0:
                    break

    # Remove fully satisfied shortages
    state["shortages"][part_id] = [s for s in shortages if s["quantity"] > 0]

    # ---------------------------------------------------------------------
    # Rule 18 – reorder suggestion (pizarrón)
    # ---------------------------------------------------------------------
    reorder_evt = update_reorder_suggestion(part_id, occurred_at, state)
    if reorder_evt:
        output_events.append(reorder_evt)

    return output_events

# ---------------------------------------------------------------------------
# 4. Reorder suggestion – Rule 18
# ---------------------------------------------------------------------------

def update_reorder_suggestion(
    part_id: int,
    occurred_at: str,
    state: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Calcula la sugerencia de compra de una pieza y emite stock.reorder_suggested."""
    state.setdefault("reorder_suggestions", {})
    shortages = state.get("shortages", {}).get(part_id, [])
    total_needed = sum(s["quantity"] for s in shortages if s["quantity"] > 0)
    work_order_ids = sorted({s.get("work_order_id") for s in shortages if s.get("quantity", 0) > 0 and s.get("work_order_id")})

    # Mínimo de la pieza (opcional, si el catálogo trae minimum_quantity)
    min_qty = None
    for p in state.get("catalog_parts", []):
        if p.get("part_id") == part_id:
            min_qty = p.get("minimum_quantity")
            break

    if min_qty is not None:
        current_stock = sum(m["quantity"] for m in state.get("movements", []) if m["part_id"] == part_id)
        if current_stock < min_qty:
            total_needed = max(total_needed, min_qty - current_stock)

    if total_needed > 0:
        state["reorder_suggestions"][part_id] = {
            "part_id": part_id,
            "suggested_quantity": total_needed,
            "work_order_ids": work_order_ids,
        }
        return {
            "event_id": str(uuid.uuid4()),
            "event_type": "stock.reorder_suggested",
            "event_version": 1,
            "occurred_at": occurred_at,
            "source": "inventory",
            "key": f"part:{part_id}",
            "data": {
                "part_id": part_id,
                "suggested_quantity": total_needed,
                "work_order_ids": work_order_ids,
            },
        }

    state["reorder_suggestions"].pop(part_id, None)
    return None

# ---------------------------------------------------------------------------
# 5. API helper functions (to be wired to FastAPI)
# ---------------------------------------------------------------------------

def list_unmatched_receipts(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return all unmatched receipt records."""
    return [r for r in state.get("unmatched_receipts", {}).values() if r.get("status") != "resolved"]
def resolve_unmatched(
    receipt_id: int,
    part_id: int,
    state: Dict[str, Any],
    occurred_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Relaciona a mano una recepción pendiente.
    KeyError  -> 404 (no existe)
    ValueError -> 409 (ya estaba resuelta)
    Regresa {"movement": ..., "events": [...]}; la API debe publicar los events.
    """
    with _state_lock:
        receipt = state.get("unmatched_receipts", {}).get(receipt_id)
        if receipt is None:
            raise KeyError(receipt_id)
        if receipt.get("status") == "resolved":
            raise ValueError(f"La recepción {receipt_id} ya fue resuelta")

        events = fulfill_receipt(
            part_id=part_id,
            quantity=receipt["quantity"],
            purchase_line_id=receipt["purchase_line_id"],
            unit_cost=receipt.get("unit_price") or "0.00",
            currency=receipt.get("currency") or "MXN",
            work_order_code=receipt.get("work_order_code"),
            occurred_at=occurred_at or now_iso(),
            state=state,
            locations=state.get("locations", {}),
        )
        receipt["status"] = "resolved"

        mov = state["movements"][-1]
        movement = {
            "movement_id": mov["movement_id"],
            "type": "receipt",
            "part_id": part_id,
            "location_id": mov["location_id"],
            "quantity": mov["quantity"],
            "occurred_at": mov["occurred_at"],
            "reference": f"purchase_line:{receipt['purchase_line_id']}",
        }
        return {"movement": movement, "events": events}
def list_reorder_suggestions(
    state: Dict[str, Any],
    catalog: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Return the current reorder‑suggestion board, enriched with SKU/name when a catalogue is supplied."""
    suggestions = list(state.get("reorder_suggestions", {}).values())
    if catalog:
        catalog_map = {p["part_id"]: p for p in catalog}
        for s in suggestions:
            p = catalog_map.get(s["part_id"])
            if p:
                s["sku"] = p.get("sku")
                s["name"] = p.get("name", "")
    return suggestions