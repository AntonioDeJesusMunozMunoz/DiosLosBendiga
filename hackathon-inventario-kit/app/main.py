from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional
import json
import pathlib

# Importar la lógica del backend 3
from .purchasing import (
    list_unmatched_receipts,
    resolve_unmatched,
    list_reorder_suggestions,
)

app = FastAPI(title="Inventario – Backend 3")

# CORS: permite que el frontend (cualquier origen) consuma la API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------
# Cargar / inicializar el estado
# ------------------------------------------------------------------
STATE_FILE = pathlib.Path(__file__).parent / "state.json"
if STATE_FILE.exists():
    state = json.loads(STATE_FILE.read_text())
else:
    state = {
        "stock_by_location": {},
        "shortages": {},
        "unmatched_receipts": {},
        "reorder_suggestions": {},
        "next_res_id": 1,
        "next_mov_id": 1,
    }
# ------------------------------------------------------------------
# Cargar catálogos locales (solo para pruebas; en producción el consumer Kafka los rellenará)
# ------------------------------------------------------------------
catalog_path = pathlib.Path(__file__).parent.parent / "data" / "catalog_parts.json"
locations_path = pathlib.Path(__file__).parent.parent / "data" / "locations.json"

if catalog_path.exists():
    state["catalog_parts"] = json.load(open(catalog_path, encoding="utf-8"))
if locations_path.exists():
    state["locations"] = json.load(open(locations_path, encoding="utf-8"))
# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------
@app.get("/unmatched-receipts", response_model=List[Dict[str, Any]])
def api_unmatched():
    """Devuelve todas las recepciones que no tienen pieza asignada."""
    return list_unmatched_receipts(state)


@app.post("/unmatched-receipts/{receipt_id}/resolve",
          response_model=List[Dict[str, Any]])
def api_resolve(receipt_id: int, payload: Dict[str, Any]):
    """Asocia una pieza a una recepción pendiente.
    El cuerpo JSON debe contener 'part_id' (int).
    Opcionalmente puede incluir 'location_id' (int).
    """
    part_id = payload.get("part_id")
    if part_id is None:
        raise HTTPException(status_code=400,
                            detail="El campo 'part_id' es obligatorio")
    try:
        return resolve_unmatched(receipt_id, int(part_id), state)
    except KeyError:
        raise HTTPException(status_code=404,
                            detail=f"Receipt con id {receipt_id} no encontrado")


@app.get("/reorder-suggestions", response_model=List[Dict[str, Any]])
def api_reorder():
    """Devuelve la tabla de sugerencias de reorden (piezas que aún faltan)."""
    return list_reorder_suggestions(state)


# ------------------------------------------------------------------
# Endpoint: /shortages  (el dashboard lo llama para mostrar faltantes)
# ------------------------------------------------------------------
@app.get("/shortages", response_model=List[Dict[str, Any]])
def api_shortages():
    """Devuelve todos los faltantes activos (shortages con quantity > 0)."""
    shortages = state.get("shortages", {})
    result = []
    catalog = {p["part_id"]: p for p in state.get("catalog_parts", [])}
    for key, shortage in shortages.items():
        if isinstance(shortage, dict):
            part_id = shortage.get("part_id")
            part_info = catalog.get(part_id, {})
            result.append({
                "part_id": part_id,
                "sku": part_info.get("sku"),
                "name": part_info.get("name"),
                "work_order_id": shortage.get("work_order_id"),
                "work_order_code": shortage.get("work_order_code"),
                "missing_quantity": shortage.get("quantity", shortage.get("missing_quantity")),
            })
    return result


# ------------------------------------------------------------------
# Endpoint: /stock  (página de inventario – stock general por pieza)
# ------------------------------------------------------------------
@app.get("/stock", response_model=List[Dict[str, Any]])
def api_stock():
    """Devuelve el saldo de stock agrupado por pieza (suma de movimientos tipo receipt)."""
    movements = state.get("movements", [])
    catalog = {p["part_id"]: p for p in state.get("catalog_parts", [])}
    totals: Dict[int, int] = {}
    for mov in movements:
        pid = mov.get("part_id")
        if pid is not None:
            totals[pid] = totals.get(pid, 0) + mov.get("quantity", 0)
    result = []
    for part_id, qty in totals.items():
        part_info = catalog.get(part_id, {})
        result.append({
            "part_id": part_id,
            "sku": part_info.get("sku"),
            "name": part_info.get("name"),
            "total_quantity": qty,
        })
    return result


# ------------------------------------------------------------------
# Endpoint: /parts/{part_id}/availability  (disponibilidad por pieza)
# ------------------------------------------------------------------
@app.get("/parts/{part_id}/availability", response_model=Dict[str, Any])
def api_availability(part_id: int):
    """Devuelve el saldo disponible de una pieza específica por ubicación."""
    movements = state.get("movements", [])
    catalog = {p["part_id"]: p for p in state.get("catalog_parts", [])}
    by_location: Dict[int, int] = {}
    for mov in movements:
        if mov.get("part_id") == part_id:
            loc = mov.get("location_id", 0)
            by_location[loc] = by_location.get(loc, 0) + mov.get("quantity", 0)
    total = sum(by_location.values())
    part_info = catalog.get(part_id, {})
    if not part_info and not by_location:
        raise HTTPException(status_code=404, detail=f"Pieza {part_id} no encontrada")
    return {
        "part_id": part_id,
        "sku": part_info.get("sku"),
        "name": part_info.get("name"),
        "total_quantity": total,
        "by_location": [
            {"location_id": loc, "quantity": qty}
            for loc, qty in by_location.items()
        ],
    }


# ------------------------------------------------------------------
# Endpoint: /parts/{part_id}/ledger  (Kardex / historial de movimientos)
# ------------------------------------------------------------------
@app.get("/parts/{part_id}/ledger", response_model=Dict[str, Any])
def api_ledger(part_id: int):
    """Devuelve el kardex (historial de movimientos) de una pieza."""
    movements = state.get("movements", [])
    part_movements = [m for m in movements if m.get("part_id") == part_id]
    balance = 0
    entries = []
    for mov in part_movements:
        balance += mov.get("quantity", 0)
        entries.append({
            "movement_id": mov.get("movement_id", mov.get("mov_id", "")),
            "type": mov.get("type", "receipt"),
            "quantity": mov.get("quantity", 0),
            "balance": balance,
            "occurred_at": mov.get("occurred_at"),
            "reference": mov.get("purchase_line_id"),
        })
    return {"part_id": part_id, "entries": entries}


# ------------------------------------------------------------------
# Guardar estado al cerrar la aplicación
# ------------------------------------------------------------------
@app.on_event("shutdown")
def _save_state():
    STATE_FILE.write_text(json.dumps(state, indent=2))