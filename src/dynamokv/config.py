import os

# Deliberately minimal: just enough for Phase 2 to run N copies of this app
# as separate processes/containers, distinguished only by env vars. The full
# config system (Pydantic Settings, N/R/W, TLS paths) is Phase 9 scope.

NODE_ID = os.environ.get("NODE_ID", "node-1")
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "sqlite")
DB_PATH = os.environ.get("DB_PATH", f"data/{NODE_ID}.db")
