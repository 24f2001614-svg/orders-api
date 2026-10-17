"""Orders logic: fixed catalog 1..T, opaque cursors, idempotent creation. No web framework needed."""
import base64
import json
import threading
import uuid
from datetime import datetime, timezone

TOTAL = 53  # T: the fixed catalog is order ids 1..T

CATALOG = [
    {"id": i, "name": f"Order #{i}", "status": "confirmed", "amount": round(10 + (i * 7.31) % 90, 2)}
    for i in range(1, TOTAL + 1)
]


def encode_cursor(offset: int) -> str:
    raw = json.dumps({"o": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        offset = int(json.loads(raw)["o"])
    except Exception as exc:
        raise ValueError("invalid cursor") from exc
    if offset < 0:
        raise ValueError("invalid cursor")
    return offset


def page(limit: int, cursor=None):
    """Return (items, next_cursor). Never more than `limit` items; next_cursor is None at the end."""
    start = decode_cursor(cursor) if cursor else 0
    items = CATALOG[start:start + limit]
    end = start + len(items)
    return items, (encode_cursor(end) if end < len(CATALOG) else None)


class IdempotentOrders:
    """Creates each order once per Idempotency-Key (thread-safe, so concurrent retries can't duplicate)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._by_key = {}
        self._by_id = {}

    def create(self, key: str, data):
        with self._lock:
            existing = self._by_key.get(key)
            if existing is not None:
                return existing, True
            order = {
                "id": "ord_" + uuid.uuid4().hex[:16],
                "status": "created",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "data": data,
            }
            self._by_key[key] = order
            self._by_id[order["id"]] = order
            return order, False

    def get(self, order_id: str):
        with self._lock:
            return self._by_id.get(order_id)
