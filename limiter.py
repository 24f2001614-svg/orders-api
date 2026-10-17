"""Per-client sliding-window rate limiter (ASGI), keyed on X-Client-Id."""
import time
from collections import deque

from starlette.responses import JSONResponse

RATE_LIMIT = 17        # R
RATE_WINDOW_S = 10.0   # seconds


class RateLimitMiddleware:
    """R requests per window per X-Client-Id; the (R+1)th gets 429 + Retry-After.
    Requests with no X-Client-Id are not limited, so a client that omits the header
    (e.g. a plain pagination scan) can't be locked out by sharing an IP bucket.
    CORS preflights (OPTIONS) are never counted."""

    def __init__(self, app, limit=RATE_LIMIT, window=RATE_WINDOW_S, clock=time.monotonic):
        self.app, self.limit, self.window, self.clock = app, limit, window, clock
        self.hits = {}

    @staticmethod
    def _client_id(scope):
        for k, v in scope.get("headers", []):
            if k == b"x-client-id":
                return v.decode("latin-1").strip() or None
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        cid = self._client_id(scope)
        if cid is None:
            return await self.app(scope, receive, send)
        now = self.clock()
        q = self.hits.setdefault(cid, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            retry = max(1, int(self.window - (now - q[0]) + 0.999))
            resp = JSONResponse({"detail": "rate limit exceeded"}, status_code=429,
                                headers={"Retry-After": str(retry)})
            return await resp(scope, receive, send)
        q.append(now)
        if len(self.hits) > 10000:
            for k in [k for k, d in self.hits.items() if not d or now - d[-1] >= self.window]:
                del self.hits[k]
        await self.app(scope, receive, send)
