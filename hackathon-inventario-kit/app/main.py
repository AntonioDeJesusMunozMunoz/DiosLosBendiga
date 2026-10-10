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
    "movements": [],
    "reservations": {},
    "processed_event_ids": set(),
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
@app.get("/unmatched-receipts")
def api_unmatched():
    """Recepciones pendientes de relacionar con el catálogo."""
    return {"items": list_unmatched_receipts(state)}


@app.post("/unmatched-receipts/{receipt_id}/resolve")
def api_resolve(receipt_id: int, payload: Dict[str, Any]):
    """Relaciona a mano una recepción con una pieza. Cuerpo: {"part_id": 305}"""
    part_id = payload.get("part_id")
    if part_id is None:
        raise HTTPException(status_code=400, detail="El campo 'part_id' es obligatorio")
    try:
        result = resolve_unmatched(receipt_id, int(part_id), state)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Recepción {receipt_id} no encontrada")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=409, detail=str(e))
    # TODO: publicar result["events"] en inventory.events con el productor de Kafka del equipo
    return result["movement"]


@app.get("/reorder-suggestions")
def api_reorder():
    """Sugerencias de compra vigentes."""
    return {"items": list_reorder_suggestions(state, state.get("catalog_parts"))}

