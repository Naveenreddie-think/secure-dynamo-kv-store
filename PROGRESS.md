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

## Phase 3 — Replication with quorum R/W (done)

Generalized Phase 2's "who owns this key" into "who are this key's N
replicas," and gave `Node` quorum-aware reads/writes that tolerate replica
failure. Confirmed live on the 3-node Docker Compose cluster: killed one of
three replicas, writes and reads both still succeeded via the remaining
quorum — `PLAN.md`'s exact Phase 3 test criterion.

**What was built:**
- `ring.py` — `get_preference_list(key, n)`: walks the same starting point
  as `get_node`, collecting up to `n` distinct real node owners (fewer,
  not an error, if the cluster itself is smaller than `n`).
- `node.py` — gained `get_local`/`put_local`/`delete_local`/`exists_local`
  (unconditional local storage access, no ring lookup) plus quorum fan-out:
  `get`/`put`/`delete` now compute the key's preference list, fan the
  operation out concurrently via a `ThreadPoolExecutor` to all N replicas
  (local call for itself, HTTP call to `/internal/keys/{key}` for peers),
  and return as soon as a W (write) or R (read) threshold succeeds.
  Un-met threshold raises `HTTPException(503)` — the same code Phase 2
  established for "quorum/peer unavailable." `Node.exists()` (the Phase 2
  peer-forwarding method) was removed — confirmed via grep it was unused
  outside the old GET handler and its own tests.
- `api/routes.py` — new internal-only routes (`/internal/keys/{key}`,
  hidden from the OpenAPI schema) that touch local storage unconditionally.
  These exist because replica writes going through the *public* route would
  make every replica re-derive the preference list on arrival and
  re-fan-out all over again — the public route is for client-facing
  coordination, the internal one is for "just store your own copy." The
  public GET handler also collapsed from two `Node` calls to one, since
  quorum fan-out would otherwise multiply the double-call inefficiency
  Phase 2 had already flagged and accepted.
- `config.py`/`main.py` — `N`/`R`/`W` env vars (defaults 3/2/2, matching
  `PLAN.md`'s own example), threaded into `Node`. A `warnings.warn(...)` at
  startup if `W + R <= N` (breaks the read/write overlap guarantee) — no
  logging framework, since structured logging is explicitly Phase 9 scope.

**Key decisions:**
- Fan-out stayed **sync + `ThreadPoolExecutor`**, not an async rewrite —
  FastAPI already runs sync routes in its own threadpool, so this adds no
  new concurrency model, just makes explicit what's already implicit; an
  async rewrite would have touched every layer (routes, `Node`, test
  infrastructure) for a phase whose actual ask was quorum logic, not an I/O
  model change.
- **"Presence beats absence"** read reconciliation: among the R collected
  responses, any "found" wins over "not found." Not value-freshness
  comparison (arbitrary among multiple *found* results — that's Phase 4's
  vector clocks) — it fixes a real in-scope gap: a write can return success
  once W replicas ack while the remaining N-W are still catching up, so a
  concurrent read can otherwise see a nondeterministic found/not-found
  split with zero node failures involved.
- **Threshold clamping** (`min(configured_w_or_r, len(preference_list))`),
  computed in `Node`. Without it, the existing single-node test fixture
  (`W=2` configured, 1 real node) would be permanently unsatisfiable — this
  is what kept `test_api.py`/`conftest.py` passing with zero changes.
- Stale-replica repair (a node that missed a write while down stays stale
  indefinitely) and multi-value conflict resolution are explicitly **not**
  attempted here — those are Phase 5 (hinted handoff) and Phase 4 (vector
  clocks) respectively. Confirmed this gap is real and stays masked by
  design: since *every* node's public GET does quorum fan-out (not just a
  designated coordinator), hitting the stale replica directly through its
  public API still returns the correct value. Only the internal, local-only
  endpoint (or inspecting the replica's raw DB) reveals it never actually
  received the write.

**Test results:** 42/42 passing (`pytest`) — all 34 Phase 1/2 tests
unmodified except two obsolete `Node.exists()` assertions removed from
`test_node_forwarding.py` (that method no longer exists); the rest of that
file keeps passing unmodified since `Node`'s new `n=r=w=1` defaults
degenerate to exactly Phase 2's single-owner-forward behavior. Plus 5 new
`get_preference_list` cases in `test_ring.py`, and a new
`test_node_quorum.py` covering: all 3 replicas up, 1 of 3 down (write+read
still succeed), 2 of 3 down (both correctly 503), and a synthetic 5-node/N=3
case proving a coordinator that isn't itself one of the key's 3 replicas
still works correctly.

Manual Docker Compose smoke test (3 real containers, `N=3`/`R=2`/`W=2`):
confirmed a key PUT while all 3 were up is physically stored on all 3 (since
cluster size equals N); `docker compose stop node-3`, PUT and GET via
`node-1` both succeeded (200) via the remaining 2-node quorum; restarted
`node-3` and confirmed via its raw SQLite file and its internal-only
endpoint (both bypassing quorum) that it never actually received the write
during its downtime — while its own *public* GET still returned the correct
value, since it too fans out to the healthy replicas. This is precisely the
gap Phase 5's hinted handoff exists to close.

**Deferred to later phases:** vector clocks, multi-value conflict detection
(Phase 4) · gossip-based failure detection, hinted handoff, stale-replica
repair (Phase 5) · mTLS/AES/auth/ACLs/audit log (Phase 6) · adversarial
testing (Phase 7) · DynamoDB benchmarking (Phase 8) · versioned routes,
409 semantics, structured logging, `/metrics`, README/CI (Phase 9) ·
dashboard (Phase 10).

## Phase 4 — Vector clocks + conflict handling (done)

Closed the gap Phase 3's "presence beats absence" couldn't: two replicas
both *having* a value that genuinely disagrees, because of truly concurrent
writes. Built a deterministic test that partitions a 5-node cluster,
writes different values from two coordinators that provably can't see each
other, heals the partition, and proves both versions survive and are
correctly flagged as conflicting — not silently resolved to whichever
response happened to arrive first.

**What was built:**
- `vector_clock.py` — `VectorClock` (dict-of-counters, `compare()` returning
  `equal`/`dominates`/`dominated`/`concurrent`, union of keys with missing
  entries treated as 0), `Version` (value + clock pair), and `reconcile()` —
  a single function reused in two places: merging one replica's incoming
  write against its own stored siblings, and merging multiple replicas'
  responses together at read time. Proven order-independent (processing
  versions in any order converges to the same maximal antichain) via a
  property test cross-checking against an O(n²) brute-force computation
  across shuffled inputs.
- `node.py` — each replica now stores a **list** of sibling versions per key
  (usually length 1), not a bare value. `get_local`/`put_local` carry
  `List[Version]`/`Version`; `get()` merges every queried replica's sibling
  list via `reconcile()` and returns the single survivor, or raises
  `HTTPException(409)` with all surviving versions when more than one
  remains. `put()` became **read-then-increment-then-write**: gather the
  currently-visible version set via a quorum read, take its elementwise-max
  clock, increment only the coordinating node's own entry, then write —
  this is what makes ordinary sequential writes never spuriously conflict
  (the new clock provably dominates whatever it read), while two
  coordinators isolated from each other's replicas independently increment
  from different bases, producing genuinely incomparable clocks.
- `models.py`/`routes.py` — `KeyValueResponse` gained a `clock` field
  (informational, no client round-trip required); new `InternalPutRequest`/
  `InternalVersionsResponse` for the internal route's versioned contract.
  `Node` raises the `409` itself (same established pattern as its existing
  404/503), so `routes.py` needed no new branching.

**Key decisions:**
- **Sibling-list storage, not single-overwrite-per-replica.** The
  alternative (each replica just overwrites its one stored value, detect
  conflicts only by luck at read time) would make the partition test's
  outcome depend on write-arrival timing — not a demonstration, a coin
  flip. Confirmed via 5 repeated runs of the new conflict test that the
  chosen design is actually deterministic.
- **`R + W > N` (this project's default `N=3/R=2/W=2`) makes an undetected
  conflict structurally impossible** — any two W-sized write sets on a key
  must overlap, and PUT's own R-sized pre-read is guaranteed to overlap any
  prior write, by the same pigeonhole logic behind `config.py`'s existing
  `W + R <= N` warning from Phase 3. The conflict test therefore
  *deliberately* uses a 5-node cluster with `N=5/R=2/W=2` (`R+W <= N`) — the
  only way to construct a genuine conflict on purpose. The live 3-node
  Docker Compose cluster, at its default N/R/W, cannot reproduce an
  unresolved conflict without a temporary env override — a real
  consequence of the guarantee working as designed, not a limitation of
  the phase's implementation.
- **HTTP 409 introduced now, not deferred to Phase 9.** Matches this
  project's own precedent — Phase 2/3 already introduced 503 as soon as a
  scenario needing it existed, rather than waiting for a later formal
  status-code pass.
- Delete stays entirely clock-naive, exactly as Phase 3 left it — no
  tombstones, no reconciliation on delete. Structurally free to keep this
  scope boundary: since the whole sibling list is one opaque blob per key,
  a plain delete already atomically clears every sibling.

**Test results:** 58/58 passing (`pytest`) — 44 from Phases 1-3 (three
required updates, all mechanical: `test_api.py`'s two exact-dict-equality
assertions needed the new `clock` field accounted for;
`test_node_forwarding.py`'s `Node.get()` assertion needed a `.value`
accessor since `get()` now returns a `Version`; `test_node_quorum.py` had
one direct `put_local(key, "bar")` call that needed updating to pass a
proper `Version` — found by re-reading the file directly, not caught by
the initial design pass). Plus 14 new `test_vector_clock.py` cases and a
new `test_partitioned_concurrent_writes_produce_a_detected_conflict` in
`test_node_conflicts.py`, run 5 times in a row to confirm determinism.
Manual smoke test against a running single-node instance confirmed the
`clock` field appears on PUT/GET responses and its counter strictly
increases across sequential writes to the same key.

**Deferred to later phases:** gossip-based failure detection, hinted
handoff, stale-replica repair (Phase 5) · mTLS/AES/auth/ACLs/audit log
(Phase 6) · adversarial testing (Phase 7) · DynamoDB benchmarking (Phase 8)
· versioned routes, structured logging, `/metrics`, README/CI (Phase 9) ·
dashboard (Phase 10).
