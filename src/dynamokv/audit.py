import json
import time
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from dynamokv.auth import namespace_for_key


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Logs every request through the app it's attached to -- allowed,
    denied-by-auth, 404, 409, 503, whatever the outcome -- as one JSON-lines
    entry per request. Middleware rather than scattered logging calls so no
    code path (a route handler, the auth dependency) has to remember to log
    itself; call_next() here sees the final response even when a dependency
    raised an HTTPException, since FastAPI's exception handling already
    converted it to a Response before this middleware's call_next returns.
    """

    def __init__(self, app, log_path: str) -> None:
        super().__init__(app)
        self._log_path = log_path
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        self._write_entry(request, response.status_code)
        return response

    def _write_entry(self, request: Request, status_code: int) -> None:
        key = request.path_params.get("key")
        entry = {
            "timestamp": time.time(),
            "client_id": getattr(request.state, "client_id", "anonymous"),
            "source_ip": request.client.host if request.client else None,
            "method": request.method,
            "path": request.url.path,
            "key": key,
            "namespace": namespace_for_key(key) if key is not None else None,
            "status_code": status_code,
            "outcome": "allowed" if status_code < 400 else "denied",
        }
        with open(self._log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")


def add_audit_middleware(app, log_path: str) -> None:
    app.add_middleware(AuditLogMiddleware, log_path=log_path)
