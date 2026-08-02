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

## Phase 2 — Consistent hashing & multi-node partitioning (done)

Gave `Node` awareness of a consistent-hash ring and the ability to forward a
request to whichever node actually owns a key, exactly as its Phase 1
docstring promised. Ran a real 3-node cluster via Docker Compose and proved
routing works end-to-end.

**What was built:**
- `ring.py` — `HashRing`: 150 virtual nodes per real node by default, hashed
  with `hashlib.blake2b` (not Python's built-in `hash()`, which is
  per-process-randomized and would make separate node processes disagree on
  key ownership), stored as a sorted list + dict for `bisect`-based O(log V)
  lookup. `get_node()` uses `bisect_left` so an exact hash match lands on
  that virtual node's own owner rather than the next one on the ring.
- `Node` (extended, not replaced by a separate `Coordinator`) — every method
  now asks the ring who owns the key first. Local key: behaves exactly as
  Phase 1. Remote key: forwards an equivalent HTTP call (sync `httpx.Client`)
  to the owning peer's own `/keys/{key}` route and translates the response;
  an unreachable peer raises `HTTPException(503)`. Method signatures
  (`get`/`put`/`delete`/`exists`) are unchanged.
- `config.py` — added `CLUSTER_NODES` (comma-separated node ids),
  `PORT`, `VIRTUAL_NODES`. Peer URLs are derived by convention
  (`http://{node_id}:{PORT}`) via Docker Compose's service-name DNS, so no
  peer address needs to be configured by hand.
- `main.py` — `create_app()` now also builds the `HashRing` and the peers
  dict and wires them into `Node`. Cluster membership defaults from env vars
  only in production mode (`storage is None`); test-injected storage
  defaults to an isolated single-node cluster so existing tests don't need
  to know about clustering at all.
- `Dockerfile` + `docker-compose.yml` — 3 node services (`node-1/2/3`),
  matching Phase 3's later 3-replica test, each with distinct `NODE_ID`/
  `DB_PATH`, identical `CLUSTER_NODES`, host ports 8001-8003.

**Key decisions:**
- Hash-ring/routing logic lives inside `Node` itself, not a separate
  `Coordinator` class — `routes.py` and `storage/*` needed **zero changes**.
  Verified this holds: `routes.py`'s diff is empty; `storage/base.py`,
  `storage/memory.py`, `storage/sqlite.py` are all untouched.
- Cluster membership is static/env-var-driven, not gossip (gossip is
  explicitly Phase 5's job). No retry/recovery on an unreachable peer either
  (Phase 5's hinted handoff will add that) — just a clean 503.
- Accepted a known inefficiency: `routes.py`'s GET handler calls
  `node.exists()` then `node.get()` separately, so a remote read means two
  forwarded HTTP calls. Not worth fixing now — Phase 3's replication/quorum
  fan-out will replace this single-owner-forward logic wholesale anyway.

**Test results:** 34/34 passing (`pytest`) — the original 22 Phase 1 tests
unmodified, plus 5 new `test_ring.py` cases (including the literal proof of
PLAN.md's success criterion: adding an 11th node to a 10-node/150-vnode ring
moved ~1/11 of a 10,000-key sample, all to the new node; removing a node
left every other key's owner untouched) and 7 new `test_node_forwarding.py`
cases (GET/PUT/DELETE/exists forwarding verified in-process via `TestClient`
standing in as a peer, plus the unreachable-peer → 503 path).

Manual Docker Compose smoke test (`docker compose up --build`, 3 real
containers): PUT via `node-1` for a key that the ring assigns to `node-3`;
GET via all three nodes returned the identical value; inspecting each
node's own SQLite file directly confirmed the row physically exists only on
`node-3` (nodes 1 and 2 forwarded, didn't store a copy); DELETE issued via
`node-2` correctly propagated so all three nodes returned 404 afterward.

**Deferred to later phases:** replication to N nodes, quorum R/W (Phase 3) ·
vector clocks (Phase 4) · gossip-based failure detection, hinted handoff
(Phase 5) · mTLS/AES/auth/ACLs/audit log (Phase 6) · adversarial testing
(Phase 7) · DynamoDB benchmarking (Phase 8) · versioned routes, 409/503
polish, structured logging, `/metrics`, README/CI (Phase 9) · dashboard
(Phase 10).
