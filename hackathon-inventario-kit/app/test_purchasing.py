"""
TEST RUNNER: test_purchasing.py
Ejecuta este archivo en tu terminal para probar tu código de Backend 3:
    python app/test_purchasing.py
"""

from purchasing import match_part, handle_purchase_received


def run_tests():
    print("==================================================")
    print("🧪 INICIANDO PRUEBAS DE TU MÓDULO (BACKEND 3)...")
    print("==================================================")

    # Catálogo simulado del taller
    catalog = [
        {"part_id": 1, "sku": "7014 CTA/P4", "name": "RODAMIENTO DE PRECISION"},
        {"part_id": 2, "sku": "TORNILLO 6X12", "name": "TORNILLO CABEZA ALLEN"},
        {"part_id": 3, "sku": "ARANDELA CON SELLOS 8 MM", "name": "ARANDELA SELLADA"},
        # Dos piezas con el mismo SKU a propósito (para probar ambigüedad):
        {"part_id": 4, "sku": "SKU_DUPLICADO", "name": "PIEZA A"},
        {"part_id": 5, "sku": "SKU_DUPLICADO", "name": "PIEZA B"},
    ]

    # ----------------------------------------------------
    # TEST 1: Matching normal (exacto)
    # ----------------------------------------------------
    res = match_part("7014 CTA/P4", catalog)
    assert res == 1, f"❌ Esperaba part_id 1, obtuve {res}"
    print("✔ Test 1: Matching exacto pasó correctamente.")

    # ----------------------------------------------------
    # TEST 2: Matching tolerante (minúsculas y espacios)
    # ----------------------------------------------------
    res = match_part("   tornillo 6x12   ", catalog)
    assert res == 2, f"❌ Esperaba part_id 2, obtuve {res}"
    print("✔ Test 2: Matching con minúsculas y espacios pasó correctamente.")

    # ----------------------------------------------------
    # TEST 3: Pieza que no existe en el catálogo
    # ----------------------------------------------------
    res = match_part("PIEZA_FANTASMA_99", catalog)
    assert res is None, f"❌ Esperaba None, obtuve {res}"
    print("✔ Test 3: Pieza desconocida rechazada correctamente.")

    # ----------------------------------------------------
    # TEST 4: SKU duplicado / ambiguo
    # ----------------------------------------------------
    res = match_part("SKU_DUPLICADO", catalog)
    assert res is None, f"❌ Esperaba None por ambigüedad, obtuve {res}"
    print("✔ Test 4: SKU ambiguo protegido correctamente (retorna None).")

    # ----------------------------------------------------
    # TEST 5: Compra para cliente (Regla 9)
    # ----------------------------------------------------
    state = {
        "stock_by_location": {},
        "shortages": {},
        "unmatched_receipts": {},
        "reorder_suggestions": {},
        "next_res_id": 1
    }
    customer_event = {
        "purchase_line_id": 100,
        "part_number": "7014 CTA/P4",
        "quantity": 5,
        "purpose": "customer_order"  # <--- REGLA 9
    }
    events = handle_purchase_received(customer_event, "2026-10-09T18:00:00Z", catalog, state)
    assert len(events) == 0, f"❌ No debió emitir eventos para customer_order, emitió {len(events)}"
    assert 1 not in state["stock_by_location"], "❌ No debió ingresar stock para customer_order"
    print("✔ Test 5: Regla 9 (customer_order ignorada) pasó al 100%.")

    # ----------------------------------------------------
    # TEST 6: Llegada de compra que surte un faltante (Reglas 11 y 12)
    # ----------------------------------------------------
    state["shortages"][2] = [
        {"work_order_id": 501, "work_order_code": "R061026012", "quantity": 2, "inspection_item_id": 99}
    ]

    restock_event = {
        "purchase_line_id": 201,
        "part_number": "tornillo 6x12",
        "quantity": 5,
        "unit_cost": "15.00",
        "currency": "MXN",
        "purpose": "restock",
        "work_order_code": "R061026012"
    }
    events = handle_purchase_received(restock_event, "2026-10-09T18:05:00Z", catalog, state)

    assert state["stock_by_location"][2][100] == 5, "❌ El stock en U-100 debe ser 5"
    types = [e["event_type"] for e in events]
    assert "stock.received" in types, "❌ Falta evento stock.received"
    assert "stock.reserved" in types, "❌ Falta evento stock.reserved"
    assert "stock.shortage_resolved" in types, "❌ Falta evento stock.shortage_resolved"
    assert len(state["shortages"][2]) == 0, "❌ El faltante debió quedar resuelto y limpio"
    print("✔ Test 6: Reglas 11 y 12 (Entrada a U-100 + Surtido prioritario) pasaron al 100%.")

    # ----------------------------------------------------
    # TEST 7: Sugerencias de Reorden / Compra (Regla 18)
    # ----------------------------------------------------
    # Supongamos que hay dos órdenes esperando la pieza 3 (ARANDELA):
    # Orden 601 necesita 2 arandelas, Orden 602 necesita 3 arandelas (Total: 5)
    state["shortages"][3] = [
        {"work_order_id": 601, "work_order_code": "R01", "quantity": 2, "inspection_item_id": 101},
        {"work_order_id": 602, "work_order_code": "R02", "quantity": 3, "inspection_item_id": 102}
    ]

    # Llega una compra de solo 2 arandelas para la orden R01:
    partial_purchase = {
        "purchase_line_id": 301,
        "part_number": "ARANDELA CON SELLOS 8 MM",
        "quantity": 2,
        "purpose": "restock",
        "work_order_code": "R01"
    }
    events_reorder = handle_purchase_received(partial_purchase, "2026-10-09T18:10:00Z", catalog, state)

    # Debe emitirse stock.reorder_suggested avisando que todavía faltan 3 arandelas para la orden 602
    types_reorder = [e["event_type"] for e in events_reorder]
    assert "stock.reorder_suggested" in types_reorder, "❌ Falta evento stock.reorder_suggested"

    reorder_event = next(e for e in events_reorder if e["event_type"] == "stock.reorder_suggested")
    assert reorder_event["data"]["suggested_quantity"] == 3, f"❌ Cantidad sugerida debe ser 3, obtuve {reorder_event['data']['suggested_quantity']}"
    assert reorder_event["data"]["work_order_ids"] == [602], f"❌ work_order_ids debe ser [602], obtuve {reorder_event['data']['work_order_ids']}"
    assert state["reorder_suggestions"][3]["suggested_quantity"] == 3, "❌ El estado de sugerencias debe guardar 3"

    print("✔ Test 7: Regla 18 (Pizarrón de compras actualizado) pasó al 100%.")

    print("==================================================")
    print("🎉 ¡TODAS TUS 7 PRUEBAS PASARON EXITOSAMENTE!")
    print("==================================================")


if __name__ == "__main__":
    run_tests()
