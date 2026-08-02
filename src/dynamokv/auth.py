import json
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel

_METHOD_TO_VERB = {"PUT": "write", "GET": "read", "DELETE": "delete"}


class AuthContext(BaseModel):
    client_id: str
    namespaces: Dict[str, List[str]]


def load_auth_tokens(path: str) -> Dict[str, dict]:
    if not path:
        return {}
    return json.loads(Path(path).read_text())


def namespace_for_key(key: str) -> str:
    if ":" in key:
        return key.split(":", 1)[0]
    return "default"


def get_auth_context(request: Request, key: str) -> AuthContext:
    """Dependency for the public /keys/{key} routes. If the app has no
    auth_tokens configured (test mode -- see create_app's auth_tokens
    param), auth is a no-op so existing tests never need an Authorization
    header. In production, a missing/unrecognized token is always 401; a
    recognized token lacking the namespace+verb is 403.

    Stashes the resolved client id on request.state.client_id as it goes
    -- even on the failure paths, wherever it's knowable -- so the audit
    middleware can log who made the attempt regardless of outcome.
    """
    auth_tokens: Optional[Dict[str, dict]] = getattr(request.app.state, "auth_tokens", None)
    if auth_tokens is None:
        request.state.client_id = "anonymous"
        return AuthContext(client_id="anonymous", namespaces={})

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        request.state.client_id = "anonymous"
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")

    token = auth_header[len("Bearer "):]
    entry = auth_tokens.get(token)
    if entry is None:
        request.state.client_id = "invalid"
        raise HTTPException(status_code=401, detail="invalid token")

    request.state.client_id = entry["id"]
    namespace = namespace_for_key(key)
    verb = _METHOD_TO_VERB[request.method]
    allowed_verbs = entry.get("namespaces", {}).get(namespace, [])
    if verb not in allowed_verbs:
        raise HTTPException(status_code=403, detail=f"token not permitted to {verb} namespace '{namespace}'")

    return AuthContext(client_id=entry["id"], namespaces=entry["namespaces"])
