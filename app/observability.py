"""Request IDs and JSON operational logs. Never log URLs, bodies or headers."""
import json
import logging
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

from fastapi.responses import JSONResponse

log = logging.getLogger("scena.requests")
counts = Counter()


def install(app):
    @app.middleware("http")
    async def request_diagnostics(request, call_next):
        request_id = uuid.uuid4().hex  # Do not trust arbitrary inbound IDs.
        request.state.request_id = request_id
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # Tracebacks may contain secrets; log exception type, not request data.
            log.error(json.dumps({"event": "unhandled_exception", "request_id": request_id}))
            response = JSONResponse({"detail": "Внутренняя ошибка. Попробуйте позже.",
                                     "request_id": request_id}, status_code=500)
        status = response.status_code
        counts[f"{status // 100}xx"] += 1
        route = request.scope.get("route")
        record = {"event": "http_request", "time": datetime.now(timezone.utc).isoformat(),
                  "request_id": request_id, "method": request.method,
                  "route": getattr(route, "path", "unmatched"), "status": status,
                  "duration_ms": round((time.monotonic() - started) * 1000, 2)}
        (log.error if status >= 500 else log.info)(json.dumps(record))
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
