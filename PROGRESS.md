# Progress

## Phase 1 — Single-node KV store (done)

Built a single-process FastAPI service exposing PUT/GET/DELETE over HTTP, backed
by pluggable storage.

**What was built:**
- `StorageBackend` — a `typing.Protocol` interface (`get`/`put`/`delete`/`exists`),
  not an ABC, so future backends don't need inheritance.
- `MemoryStorage` — dict-backed, used by tests.
- `SqliteStorage` — file-backed, used by the running app by default. Opens a
  fresh connection per call to sidestep sqlite's single-thread-per-connection
  restriction under FastAPI's threaded request handling.
- `Node` — owns one storage instance, exposes `get`/`put`/`delete`/`exists`.
  Currently a thin pass-through; this is the seam Phase 2 will extend with
  hash-ring-aware routing/forwarding, without routes or storage changing.
- `api/routes.py` — `PUT/GET/DELETE /keys/{key}` + `GET /healthz`, all
  depending only on `Node` (never on storage directly).
- `config.py` — three env vars (`NODE_ID`, `STORAGE_BACKEND`, `DB_PATH`), added
  now (slightly ahead of Phase 9's formal "config via env vars" scope) because
  Phase 2 needs to run N copies of the same app image via Docker Compose,
  distinguished only by env vars, with no code changes.
- `main.py` — `create_app()` factory: config → storage → node → router.
  Production builds storage from env vars; tests inject `MemoryStorage`
  directly for isolation and speed.

**Key decisions:**
- Both `MemoryStorage` and `SqliteStorage` were built in Phase 1 itself (per
  PLAN.md's "start with in-memory dict, then persist to disk/SQLite"), proven
  identical via one shared parametrized contract test suite
  (`tests/test_storage_contract.py`).
- SQLite (not memory) is the default for the *running* app — confirmed via a
  manual smoke test that a value written before a `uvicorn` restart is still
  readable after. This matters because Phase 3/5's kill-and-restart tests
  would be meaningless against pure in-memory storage.
- No `/v1/` prefix, no value-version envelope, no auth/logging/metrics — all
  explicitly deferred to their respective later phases.

**Test results:** 22/22 passing (`pytest`) — 7 storage-contract cases × 2
backends + 1 sqlite-restart test + 7 API tests. Manual smoke test via `curl`
against a running `uvicorn` process confirmed PUT/GET/DELETE/healthz status
codes and a restart-survives-persistence check.

**Deferred to later phases:** versioned routes, 409/503 semantics, structured
logging, `/metrics`, full env-var config system, TLS/N-R-W config, README/CI
(Phase 9) · consistent hashing, hash ring, Docker Compose (Phase 2) ·
replication/quorum (Phase 3) · vector clocks (Phase 4) · gossip/hinted
handoff (Phase 5) · mTLS/AES/auth/ACLs/audit log (Phase 6) · adversarial
testing (Phase 7) · DynamoDB benchmarking (Phase 8) · dashboard (Phase 10).
