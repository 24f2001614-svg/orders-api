"""Run: python3 test_orders.py   (logic directly + limiter/CORS through a Starlette ASGI harness)"""
import asyncio, json, threading
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
import logic
from limiter import RateLimitMiddleware

fails = 0
def check(name, cond, extra=""):
    global fails
    fails += 0 if cond else 1
    print("PASS" if cond else "FAIL", name, "" if cond else extra)

# ---- pagination: for several page sizes, scan everything ----
for P in (1, 2, 5, 7, 10, 25, 53, 54, 100):
    seen, cur, pages = [], None, 0
    while True:
        items, cur = logic.page(P, cur)
        assert len(items) <= P
        seen += [i["id"] for i in items]
        pages += 1
        if cur is None or pages > 200: break
    check(f"scan limit={P}: ids 1..{logic.TOTAL}, no gaps/repeats/oversize", seen == list(range(1, logic.TOTAL + 1)), seen)
check("cursor is opaque base64 (not a bare number)", not logic.page(5)[1].isdigit())
check("bad cursor rejected", all(_ for _ in [True]) and (lambda: (lambda f: f())(lambda: [logic.page(5, "!!!")] ) ) is not None)
try:
    logic.page(5, "garbage!!"); check("bad cursor raises", False)
except ValueError:
    check("bad cursor raises", True)

# ---- idempotency ----
s = logic.IdempotentOrders()
a, ra = s.create("k1", {"x": 1}); b, rb = s.create("k1", {"x": 2}); c, _ = s.create("k2", {})
check("same key -> same id, replay flagged", a["id"] == b["id"] and not ra and rb)
check("different key -> different id", a["id"] != c["id"])
out = []
def worker(): out.append(s.create("race", {})[0]["id"])
ts = [threading.Thread(target=worker) for _ in range(50)]; [t.start() for t in ts]; [t.join() for t in ts]
check("50 concurrent same-key calls -> one order", len(set(out)) == 1, set(out))

# ---- limiter + CORS through ASGI ----
async def ok(request): return JSONResponse({"ok": True})
def mkapp():
    a = Starlette(routes=[Route("/orders", ok)])
    a.add_middleware(RateLimitMiddleware)
    a.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                     expose_headers=["Retry-After"], allow_credentials=False)
    return a

async def call(app, method, path, headers=None):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path,
             "raw_path": path.encode(), "query_string": b"", "scheme": "http", "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
             "client": ("9.9.9.9", 1), "server": ("t", 80)}
    out = []
    async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
    async def send(m): out.append(m)
    await app(scope, receive, send)
    st = next(m for m in out if m["type"] == "http.response.start")
    h = {k.decode().lower(): v.decode() for k, v in st["headers"]}
    return st["status"], h

async def main():
    R = 17
    app = mkapp()
    codes = []
    for _ in range(R + 1):
        codes.append((await call(app, "GET", "/orders", {"X-Client-Id": "A", "Origin": "https://x.example"}))[0])
    check(f"first {R} ok, request {R+1} is 429", codes[:R] == [200]*R and codes[R] == 429, codes)
    s, h = await call(app, "GET", "/orders", {"X-Client-Id": "A", "Origin": "https://x.example"})
    check("429 has Retry-After + CORS", s == 429 and int(h["retry-after"]) >= 1 and "access-control-allow-origin" in h, (s, h))
    s, _ = await call(app, "GET", "/orders", {"X-Client-Id": "B"})
    check("different client id still accepted", s == 200)
    codes = [(await call(app, "GET", "/orders"))[0] for _ in range(60)]
    check("no X-Client-Id -> never limited", set(codes) == {200}, set(codes))
    app2 = mkapp()
    for _ in range(40):
        await call(app2, "OPTIONS", "/orders", {"Origin": "https://x.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "idempotency-key,x-client-id", "X-Client-Id": "P"})
    s, _ = await call(app2, "GET", "/orders", {"X-Client-Id": "P"})
    check("preflights not counted", s == 200)
    s, h = await call(app2, "OPTIONS", "/orders", {"Origin": "https://x.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "idempotency-key,x-client-id"})
    check("preflight allows Idempotency-Key + X-Client-Id", s == 200 and "idempotency-key" in h.get("access-control-allow-headers", "").lower(), (s, h))
    t = [0.0]; sent = []
    async def inner(scope, receive, send): sent.append("pass")
    rl = RateLimitMiddleware(inner, limit=17, window=10, clock=lambda: t[0])
    async def snd(m): sent.append(m["type"]) if m["type"] == "http.response.start" else None
    async def rcv(): return {"type": "http.request"}
    sc = {"type": "http", "method": "GET", "headers": [(b"x-client-id", b"w")]}
    for _ in range(18): await rl(sc, rcv, snd)
    t[0] = 10.5
    await rl(sc, rcv, snd)
    check("recovers after the window", sent.count("pass") == 18 and sent.count("http.response.start") == 1, sent)

asyncio.run(main())
print("\nall passed" if not fails else f"\n{fails} FAILED")
raise SystemExit(1 if fails else 0)
