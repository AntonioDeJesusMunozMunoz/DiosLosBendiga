"""errors.py - exceptions shared by Dev 2 and Dev 3 handlers."""


class MissingDependency(Exception):
    """The event references something we have not seen yet (order, part, BOM line).

    Raise it from any handler. The processor rolls the transaction back, parks the
    event in pending_events and retries it later; after PARK_TIMEOUT_SECONDS it goes
    to inventory.dlq.
    """
