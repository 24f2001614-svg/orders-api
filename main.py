import json
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import logic
from limiter import RateLimitMiddleware

app = FastAPI(title="Orders API")
store = logic.IdempotentOrders()

# Last added = outermost: CORS wraps the limiter, so 429 responses carry CORS headers too
# (the browser must be able to read the status and Retry-After).
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "Idempotent-Replayed"],
    allow_credentials=False,
)


@app.get("/")
def root():
    return {"service": "orders", "total": logic.TOTAL, "endpoints": ["POST /orders", "GET /orders", "GET /orders/{id}"]}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/orders")
async def create_order(request: Request):
    key = (request.headers.get("idempotency-key") or "").strip()
    if not key:
        return JSONResponse({"detail": "Idempotency-Key header is required"}, status_code=400)
    raw = await request.body()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {"raw": raw.decode("utf-8", "replace")}
    order, replayed = store.create(key, data)
    headers = {"Idempotent-Replayed": "true"} if replayed else {}
    return JSONResponse(order, status_code=201, headers=headers)


@app.get("/orders")
def list_orders(limit: int = Query(10, ge=1, le=1000), cursor: Optional[str] = None):
    try:
        items, next_cursor = logic.page(limit, cursor)
    except ValueError:
        return JSONResponse({"detail": "invalid cursor"}, status_code=400)
    return {"items": items, "next_cursor": next_cursor}


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    if order_id.isdigit() and 1 <= int(order_id) <= logic.TOTAL:
        return logic.CATALOG[int(order_id) - 1]
    order = store.get(order_id)
    if order is None:
        return JSONResponse({"detail": "not found"}, status_code=404)
    return order
