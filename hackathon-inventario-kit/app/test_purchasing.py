"""
Pruebas del Backend 3 (Compras). Correr con:
    python app/test_purchasing.py
"""
import uuid

from purchasing import (
    match_part, handle_purchase_received, resolve_unmatched,
    list_unmatched_receipts, list_reorder_suggestions,
)

T = "2026-10-09T18:00:00Z"
LOCATIONS = {100: {"location_id": 100, "code": "U-100"}}
CATALOG = [
    {"part_id": 1, "sku": "7014 CTA/P4", "name": "RODAMIENTO DE PRECISION"},
    {"part_id": 2, "sku": "TORNILLO 6X12", "name": "TORNILLO CABEZA ALLEN"},
    {"part_id": 3, "sku": "ARANDELA CON SELLOS 8 MM", "name": "ARANDELA SELLADA"},
    {"part_id": 4, "sku": "SKU_DUPLICADO", "name": "PIEZA A"},
    {"part_id": 5, "sku": "SKU_DUPLICADO", "name": "PIEZA B"},
    {"part_id": 6, "sku": None, "name": "SIN SKU"},
]


def nuevo_state():
    return {
        "shortages": {}, "unmatched_receipts": {}, "reorder_suggestions": {},
        "movements": [], "reservations": {}, "processed_event_ids": set(),
        "next_res_id": 1, "next_mov_id": 1,
        "locations": dict(LOCATIONS), "catalog_parts": CATALOG,
    }


def compra(part_number, quantity, purpose="restock", line_id=1, work_order_code=None):
    """El campo data de un purchase.item_received, como lo manda el taller."""
    return {
        "purchase_request_id": 88, "request_number": "2044", "line_id": line_id,
        "part_number": part_number, "description": "Compra de prueba",
        "quantity": quantity, "unit_price": "15.00", "currency": "MXN",
        "purpose": purpose, "received_at": T, "work_order_code": work_order_code,
    }


def nuevo_id():
    return str(uuid.uuid4())


def tipos(events):
    return [e["event_type"] for e in events]


def run_tests():
    print("=" * 50)
    print("PRUEBAS BACKEND 3 (COMPRAS)")
    print("=" * 50)

    # TEST 1: matching exacto
    assert match_part("7014 CTA/P4", CATALOG) == 1, "❌ Test 1"
    print("✔ Test 1: matching exacto.")

    # TEST 2: minúsculas y espacios (orillas y en medio)
    assert match_part("   tornillo 6x12   ", CATALOG) == 2, "❌ Test 2a"
    assert match_part("7014cta/p4", CATALOG) == 1, "❌ Test 2b"
    print("✔ Test 2: matching tolerante.")

    # TEST 3: pieza que no existe
    assert match_part("PIEZA_FANTASMA_99", CATALOG) is None, "❌ Test 3"
    print("✔ Test 3: pieza desconocida.")

    # TEST 4: SKU ambiguo
    assert match_part("SKU_DUPLICADO", CATALOG) is None, "❌ Test 4"
    print("✔ Test 4: SKU ambiguo regresa None.")

    # TEST 5: compra para cliente (Regla 9)
    s = nuevo_state()
    events = handle_purchase_received(nuevo_id(), compra("7014 CTA/P4", 5, purpose="customer_order"), T, CATALOG, s)
    assert events == [] and s["movements"] == [], "❌ Test 5: customer_order no debe tocar inventario"
    print("✔ Test 5: customer_order ignorada.")

    # TEST 6: entrada en U-100, movimiento, reserva y faltante resuelto (Reglas 11 y 12)
    s = nuevo_state()
    s["shortages"][2] = [{"work_order_id": 501, "work_order_code": "R061026012", "quantity": 2, "inspection_item_id": 99}]
    events = handle_purchase_received(nuevo_id(), compra("tornillo 6x12", 5, line_id=201, work_order_code="R061026012"), T, CATALOG, s)
    t = tipos(events)
    assert "stock.received" in t and "stock.reserved" in t and "stock.shortage_resolved" in t, f"❌ Test 6: eventos {t}"
    mov = s["movements"][0]
    assert mov["location_id"] == 100 and mov["quantity"] == 5 and mov["movement_id"] == 1, f"❌ Test 6: movimiento {mov}"
    assert s["shortages"][2] == [], "❌ Test 6: el faltante debe quedar resuelto"
    res = s["reservations"][1]
    assert res["quantity"] == 2 and res["status"] == "active" and res["location_id"] == 100, f"❌ Test 6: reserva {res}"
    reserved = next(e for e in events if e["event_type"] == "stock.reserved")
    assert reserved["key"] == "R061026012", "❌ Test 6: la key de stock.reserved debe ser el código de la orden"
    print("✔ Test 6: entrada en U-100, reserva guardada y faltante resuelto.")

    # TEST 7: idempotencia (Regla 19)
    s = nuevo_state()
    eid = nuevo_id()
    handle_purchase_received(eid, compra("7014 CTA/P4", 4), T, CATALOG, s)
    otra_vez = handle_purchase_received(eid, compra("7014 CTA/P4", 4), T, CATALOG, s)
    assert otra_vez == [] and len(s["movements"]) == 1, "❌ Test 7: un evento repetido no debe cambiar nada"
    print("✔ Test 7: evento repetido ignorado.")

    # TEST 8: sin U-100 no se pierde la compra (Regla 20)
    s = nuevo_state()
    s["locations"] = {}
    eid = nuevo_id()
    try:
        handle_purchase_received(eid, compra("7014 CTA/P4", 2), T, CATALOG, s)
        assert False, "❌ Test 8: esperaba LookupError"
    except LookupError:
        pass
    assert eid not in s["processed_event_ids"] and s["movements"] == [], "❌ Test 8: no debe marcarse procesado"
    s["locations"] = dict(LOCATIONS)
    handle_purchase_received(eid, compra("7014 CTA/P4", 2), T, CATALOG, s)
    assert len(s["movements"]) == 1, "❌ Test 8: el reintento debe registrar la entrada"
    print("✔ Test 8: sin U-100 la compra espera y luego entra.")

    # TEST 9: sugerencia de reorden (Regla 18)
    s = nuevo_state()
    s["shortages"][3] = [
        {"work_order_id": 601, "work_order_code": "R01", "quantity": 2, "inspection_item_id": 101},
        {"work_order_id": 602, "work_order_code": "R02", "quantity": 3, "inspection_item_id": 102},
    ]
    events = handle_purchase_received(nuevo_id(), compra("ARANDELA CON SELLOS 8 MM", 2, work_order_code="R01"), T, CATALOG, s)
    sug = next(e for e in events if e["event_type"] == "stock.reorder_suggested")
    assert sug["data"]["suggested_quantity"] == 3 and sug["data"]["work_order_ids"] == [602], f"❌ Test 9: {sug['data']}"
    tablero = list_reorder_suggestions(s, CATALOG)
    assert tablero[0]["suggested_quantity"] == 3 and tablero[0]["sku"] == "ARANDELA CON SELLOS 8 MM", f"❌ Test 9: {tablero}"
    print("✔ Test 9: sugerencia de reorden.")

    # TEST 10: recepción sin relacionar, resolver, 409 y 404
    s = nuevo_state()
    events = handle_purchase_received(nuevo_id(), compra("PROV-XYZ", 3, line_id=777), T, CATALOG, s)
    assert tipos(events) == ["stock.unmatched_receipt"] and events[0]["key"] == "purchase_line:777", f"❌ Test 10: {events}"
    pendientes = list_unmatched_receipts(s)
    assert len(pendientes) == 1 and pendientes[0]["id"] == 777, f"❌ Test 10: {pendientes}"
    result = resolve_unmatched(777, 1, s)
    assert result["movement"]["type"] == "receipt" and result["movement"]["quantity"] == 3, f"❌ Test 10: {result['movement']}"
    assert "stock.received" in tipos(result["events"]), "❌ Test 10: resolver debe emitir stock.received"
    assert list_unmatched_receipts(s) == [], "❌ Test 10: ya no debe estar pendiente"
    try:
        resolve_unmatched(777, 1, s)
        assert False, "❌ Test 10: resolver dos veces debe dar ValueError (409)"
    except ValueError:
        pass
    try:
        resolve_unmatched(999, 1, s)
        assert False, "❌ Test 10: un id que no existe debe dar KeyError (404)"
    except KeyError:
        pass
    print("✔ Test 10: sin relacionar, resolver, 409 y 404.")

    # TEST 11: SKU ambiguo no entra solo
    s = nuevo_state()
    events = handle_purchase_received(nuevo_id(), compra("SKU_DUPLICADO", 1, line_id=888), T, CATALOG, s)
    assert tipos(events) == ["stock.unmatched_receipt"] and s["movements"] == [], "❌ Test 11"
    print("✔ Test 11: SKU ambiguo va a revisión manual.")

    print("=" * 50)
    print("🎉 TODAS LAS PRUEBAS PASARON (11)")
    print("=" * 50)


if __name__ == "__main__":
    run_tests()