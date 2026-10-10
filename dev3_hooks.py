"""
dev3_hooks.py - STUBS for Dev 3. Replace the bodies, keep the signatures.

Both functions run INSIDE a db.tx() opened by Dev 2's processor. `c` is the
connection: never commit, never open another tx(), just write rows and call
db.enqueue_event(...) so output events are saved atomically with the change.
"""
import logging

log = logging.getLogger(__name__)


def handle_purchasing(c, event):
    """Handle one `purchase.item_received` envelope (topic shop.purchasing).

    event["data"] has the purchase line. Typical flow (contract rules 9 to 12):
      - purpose == "customer_order": return without doing anything
      - purpose == "restock": match part_number against parts.sku_norm
          * 0 or 2+ matches -> insert unmatched_receipts row + stock.unmatched_receipt
          * 1 match -> db.record_movement(c, "receipt", part_id, 100, qty, occurred_at, ...)
                       + stock.received, then fill shortages (see below)

    To fill shortages (rule 12), shortages are rows in `shortages` with status 'open'
    (one per order + bom_line_id). Order: the order in work_order_code first (if
    work_orders.deleted = 0), then the rest by shortages.opened_at, id.
    For each one you fill:
      - raise reservations.quantity (row UNIQUE by work_order_id + bom_line_id; it may
        not exist yet, or may be 'cancelled'/'fulfilled': reactivate it with
        status='active')
      - lower shortages.missing_quantity, or set status='resolved' if it reaches 0
      - emit stock.reserved + stock.shortage_resolved
    Do NOT touch `needs` (Dev 2's table): the required quantity does not change.
    Use occurred_at from the event, never the current time.

    Raise errors.MissingDependency if something you need has not arrived yet.
    """
    log.warning("purchasing handler is a stub, ignoring event %s", event.get("event_id"))


def recompute_reorder_suggestions(c, part_ids, occurred_at):
    """Called by Dev 2 after shortages were created or closed for these part ids
    (a set of ints). Recompute reorder_suggestions rows and emit
    stock.reorder_suggested when a suggestion is born or changes (rule 18).
    """
    return None
