"""
seed.py - Deterministic demo database seeder (Backend Dev 1)

Populates the SQLite database with fixed, reproducible demo data:
  - Locations: U-100 (reception) and U-101 to U-103
  - 4 parts
  - 2 work orders
  - Deterministic ledger movements and reservations
"""
import os
import db

def seed():
    print("Initializing database schema...")
    db.init_db()

    with db.tx() as c:
        # Clear existing data to guarantee determinism
        tables_to_clear = [
            "ledger_movements",
            "stock_balances",
            "reservations",
            "shortages",
            "unmatched_receipts",
            "reorder_suggestions",
            "part_policies",
            "work_orders",
            "bom_lines",
            "locations",
            "parts",
            "outbox",
            "processed_events",
        ]
        for tbl in tables_to_clear:
            c.execute(f"DELETE FROM {tbl}")

        # Reset autoincrement counters so IDs are 100% deterministic between runs
        seq_exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'").fetchone()
        if seq_exists:
            c.execute("DELETE FROM sqlite_sequence")

        # 1. Locations
        locations = [
            (100, "U-100", "Recepción de material", 0, 1),
            (101, "U-101", "Estante principal A", 0, 1),
            (102, "U-102", "Estante principal B", 0, 1),
            (103, "U-103", "Mesa de ensamble 1", 1, 1),
        ]
        c.executemany(
            "INSERT INTO locations (location_id, code, name, is_workbench, active) VALUES (?,?,?,?,?)",
            locations,
        )

        # 2. Parts (4 parts)
        parts = [
            (1001, "ROD-6204", "ROD-6204", "Rodamiento rígido 6204-2RS", "TRANSMISION", "RODAMIENTOS", "RIGIDOS", "PIEZA", 1),
            (1002, "RET-3552", "RET-3552", "Retén radial 35x52x7 NBR", "SELLADO", "RETENES", "RADIALES", "PIEZA", 1),
            (1003, "OR-028", "OR-028", "O-Ring Viton 28x2.5", "SELLADO", "O-RINGS", "VITON", "PIEZA", 1),
            (1004, "TOR-M8-25", "TOR-M8-25", "Tornillo Allen M8x25 Grado 12.9", "TORNILLERIA", "TORNILLOS", "ALLEN", "PIEZA", 1),
        ]
        c.executemany(
            "INSERT INTO parts (part_id, sku, sku_norm, name, family, grp, subgroup, unit_name, active) VALUES (?,?,?,?,?,?,?,?,?)",
            parts,
        )

        # 3. Work Orders
        work_orders = [
            (2001, "R061026001", 301, "spindles", "assembly", "2026-10-01T08:00:00Z", 0),
            (2002, "R061026002", 302, "motors", "cleaning", "2026-10-02T09:30:00Z", 0),
        ]
        c.executemany(
            "INSERT INTO work_orders (work_order_id, code, model_id, department, stage, received_at, deleted) VALUES (?,?,?,?,?,?,?)",
            work_orders,
        )

        # 4. Movements (recorded via ledger helper: updates ledger_movements and stock_balances)
        # Part 1001: Receipt of 20 in U-100
        db.record_movement(c, "receipt", 1001, 100, 20, "2026-10-03T10:00:00Z", reference="PR-1001", unit_cost="450.00", currency="MXN")
        # Part 1001: Transfer 10 from U-100 to U-101
        db.record_movement(c, "transfer_out", 1001, 100, -10, "2026-10-03T11:00:00Z", reference="TR-001")
        db.record_movement(c, "transfer_in", 1001, 101, 10, "2026-10-03T11:00:00Z", reference="TR-001")
        # Part 1001: Issue 2 from U-101 for work order R061026001
        db.record_movement(c, "issue", 1001, 101, -2, "2026-10-05T14:00:00Z", reference="R061026001")

        # Part 1002: Receipt of 15 in U-100
        db.record_movement(c, "receipt", 1002, 100, 15, "2026-10-04T09:00:00Z", reference="PR-1002", unit_cost="120.00", currency="MXN")
        # Part 1002: Adjustment +1 in U-100
        db.record_movement(c, "adjustment", 1002, 100, 1, "2026-10-06T16:00:00Z", reference="conteo ciclico")

        # Part 1003: Receipt of 5 in U-100
        db.record_movement(c, "receipt", 1003, 100, 5, "2026-10-05T10:00:00Z", reference="PR-1003", unit_cost="35.00", currency="MXN")

        # Part 1004 has 0 movements -> 0 on_hand (awkward case)

        # 5. Reservations
        # Work order 2001 reserved 5 of Part 1001, 2 already issued, 3 remain active
        c.execute("""
            INSERT INTO reservations (work_order_id, bom_line_id, inspection_item_id, source_inspection_id, part_id, quantity, issued_quantity, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (2001, 501, 701, 901, 1001, 5, 2, "active"))

        # 6. Shortages
        # Work order 2002 has an open shortage on Part 1004 (zero stock)
        c.execute("""
            INSERT INTO shortages (work_order_id, work_order_code, bom_line_id, inspection_item_id, source_inspection_id, part_id, name, missing_quantity, status, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (2002, "R061026002", 502, 702, 902, 1004, "Tornillo Allen M8x25 Grado 12.9", 4, "open", "2026-10-06T11:00:00Z"))

        # Awkward case: shortage with part_id null and missing_quantity null
        c.execute("""
            INSERT INTO shortages (work_order_id, work_order_code, bom_line_id, inspection_item_id, source_inspection_id, part_id, name, missing_quantity, status, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (2002, "R061026002", 503, 703, 902, None, "Kit especial sin identificar", None, "open", "2026-10-06T11:05:00Z"))

        # 7. Unmatched receipt
        c.execute("""
            INSERT INTO unmatched_receipts (purchase_line_id, part_number, description, quantity, unit_price, currency, received_at, work_order_code, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (801, "UNKNOWN-BEARING-44", "Rodamiento desconocido recibido", 3, "250.00", "MXN", "2026-10-07T12:00:00Z", "R061026001", "open"))

        # 8. Reorder suggestion for part 1004 (zero stock)
        c.execute("""
            INSERT INTO reorder_suggestions (part_id, suggested_quantity, work_order_ids)
            VALUES (?, ?, ?)
        """, (1004, 10, "[2002]"))

    print("Database seeded successfully and deterministically.")

if __name__ == "__main__":
    seed()
