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

## Phase 5 — Gossip-based failure detection + hinted handoff (done)

Closed the exact gap Phase 3 named and Phase 4 confirmed: a node that
missed a write while down used to stay stale indefinitely. Nodes now
maintain a liveness view of the cluster via periodic gossip, and a write
whose target replica is down gets handed to a spare "neighbor" node as a
hint, delivered once gossip reports the target is back.

**What was built:**
- `gossip.py` — `GossipState`: per-node heartbeat counter + locally-stamped
  timestamp. Only integer counters ever cross the wire (`to_wire`); a
  timestamp is only ever set by the node doing the observing
  (`merge_wire`), never trusted from a peer — sidesteps clock skew across
  containers entirely. A peer never gossiped about defaults to believed
  *up* until first mentioned, and a node can never mark itself down.
- `hint_store.py` — `HintStore`: one blob per *intended recipient* (not per
  holder) in a dedicated `StorageBackend`, so two different down nodes
  parking hints on the same neighbor never collide. Reuses `reconcile()`
  verbatim to compact repeated writes to the same key during one outage.
- `node.py` — `gossip_round()` (tick → push-pull with one random peer →
  merge → flush any hints for now-live targets) and `handle_gossip()`.
  `put()` now partitions its preference list by liveness: a presumed-down
  target's write goes straight to a hint instead of a doomed attempt
  (proactive path); a live attempt that fails right now falls back to the
  same hint mechanism reactively. A hint is created via `_deliver_hint()`,
  which — this took a real bug to get right (see below) — must carry the
  hint's *target* separately from its *holder*.
- `api/routes.py`/`models.py` — new internal-only `POST /internal/gossip`
  (push-pull heartbeat exchange) and `PUT /internal/hints/{key}` (accepts a
  hint on behalf of a named target). Hint *delivery* needed no new route at
  all — it reuses the existing `/internal/keys/{key}` PUT verbatim; from
  the recovered node's side, a handed-off write is indistinguishable from
  an ordinary replica write.
- `gossip_worker.py` — a small `threading.Thread` loop calling
  `Node.gossip_round()` on a fixed interval, with clean `start()`/`stop()`.
- `main.py` — a new `lifespan` context manager starts/stops the
  `GossipWorker`, gated so it only ever runs when a real ASGI server drives
  the app.

**Key decisions:**
- **A stored hint never counts toward the write quorum.** It's created
  as a side effect inside the same exception path `_quorum_op` already
  discards failures through — architecturally incapable of counting. This
  keeps the `R+W>N` guarantee Phase 4's conflict-freedom proof leans on
  completely untouched.
- **Hints persist in a second, dedicated `StorageBackend` instance**
  (`{DB_PATH}.hints` in production), never sharing a table with real
  application data — a client PUTting a key that happened to match an
  internal naming scheme could otherwise silently collide with hint
  bookkeeping.
- **The gossip thread is lifespan-gated, not construction-gated.**
  Verified directly: `main.py`'s `app = create_app()` runs at *module
  import time*, and `conftest.py` imports `create_app` from `dynamokv.main`
  — so every single pytest run already constructs one production-mode
  `Node` as an import side effect. Confirmed empirically (see Test results)
  that gating on `production_mode` alone would have leaked a live
  background thread into every test run in this repo, not just Phase 5's
  own tests.
- **Fallback tries exactly one extra ring candidate.** Matches PLAN.md's
  singular "a neighbor" and its single-node-down test scenario. With a
  cluster no bigger than N, there's no spare node at all — `hint_holder` is
  `None` and the replica slot just doesn't count that round, falling back
  to Phase 3's original behavior. No crash, no further search.
- **A real bug found while implementing, not just during design review:**
  the first draft of `_deliver_hint` only took `hint_holder` (who to send
  the hint to) and had no separate parameter for *who the hint is actually
  for*. Every hint got mislabeled with the holder's own id as its target,
  so nothing ever got delivered anywhere. Caught by the first hinted-handoff
  test actually failing, not by inspection — fixed by adding
  `target_node_id` as an explicit, distinct argument.
- Hinted handoff covers `put()` only, not `delete()`, matching Phase 4's
  precedent of keeping delete deliberately unsophisticated. `get()` is
  untouched this phase — no proactive skip of presumed-down replicas on
  reads, keeping the diff focused on what PLAN.md actually asked for.

**Test results:** 85/85 passing (`pytest`) — all Phase 1-4 tests
unmodified. New: `test_gossip.py` (10 cases — merge semantics, timeout
correctness against an injected fake clock, self never marked down),
`test_hint_store.py` (8 cases), `test_gossip_worker.py` (4 cases, real
threads with short intervals — start/stop correctness, survives a raising
round, idempotent start), `test_node_hinted_handoff.py` (5 cases on a
4-node/N=3 cluster reusing `test_node_quorum.py`'s mounted-transport
infrastructure — reactive hint creation on the correct holder, proactive
skip proven by the presumed-down replica's local storage staying empty,
gossip-gated flush via an injected clock, the no-spare-node edge case, and
gossip propagating third-party liveness knowledge between two nodes that
never talked to the dead one directly). Also directly verified, outside
pytest, that importing `dynamokv.main` leaves zero background threads
running, and that `with TestClient(app) as c:` correctly starts and then
cleanly stops the gossip thread.

Manual Docker Compose smoke test, matching PLAN.md's literal scenario:
temporarily overrode `N=2/R=1/W=1` via a `docker-compose.override.yml`
(deleted afterward) so the 3-node cluster would have a spare node to serve
as hint-holder — the base `N=3` config has no room for one, since cluster
size equals N. PUT `probe` via `node-1` (replicas: `node-1`/`node-3`,
spare: `node-2`); stopped `node-3`; PUT a new value for `probe` (200, W=1
met locally); waited the full 60 seconds PLAN.md specifies; confirmed via
`node-2`'s raw hint-database file that a hint for `node-3` was holding
exactly that write. Restarted `node-3`; within ~15s (a few gossip
intervals), `node-3`'s raw data file showed the missed write had arrived,
and `node-2`'s hint file was empty. Quorum GETs from all three nodes
afterward agreed.

**Deferred to later phases:** mTLS/AES/auth/ACLs/audit log (Phase 6) ·
adversarial testing (Phase 7) · DynamoDB benchmarking (Phase 8) · versioned
routes, structured logging, `/metrics`, README/CI (Phase 9) · dashboard
(Phase 10). Not attempted in this phase, by design: reconciling a delete
against a concurrent write (tombstones), anti-entropy/Merkle-tree repair
beyond what a single hint flush provides, and dynamic cluster membership
(nodes joining/leaving the roster at runtime) — gossip here only tracks
liveness within the static `CLUSTER_NODES` roster, it doesn't grow or
shrink it.

## Phase 6 — Security layer (done)

Built all four mechanisms PLAN.md asks for — mTLS between nodes, AES-256
encryption at rest, token-based client auth with per-namespace ACLs, and
audit logging — and proved each works functionally. Phase 7 (adversarial
testing) is next; this phase deliberately built the mechanisms, not the
red-team suite that will try to break them.

**What was built:**
- `crypto.py` — `EncryptedStorage`: wraps any `StorageBackend` with
  AES-256-GCM (authenticated encryption), fresh random nonce per write,
  `{"nonce","ciphertext"}` envelope. Satisfies the exact same `StorageBackend`
  Protocol untouched across all 6 phases now — slotted straight into the
  existing `test_storage_contract.py` parametrized suite as a third backend.
  `load_or_create_encryption_key()` generates a 256-bit key once per node on
  first boot and persists it to `data/{node_id}.key` — one key per node
  (never shared cluster-wide, since no node ever decrypts another node's
  disk), derived from `DB_PATH`'s directory with no new env var, mirroring
  how the hint store's path is already derived rather than configured.
- `auth.py`/`audit.py` — `get_auth_context` dependency (bearer token →
  namespace/verb permissions, `key.split(":", 1)[0]` namespace convention,
  no default/bypass token) added only to the public `/keys/{key}` routes;
  `AuditLogMiddleware` logs every public request — allowed or denied — as
  one JSON-lines entry to `data/{node_id}.audit.log`, capturing the token's
  *id*, never the raw token itself.
- `api/routes.py` split into `public_router` (client-facing, token-gated)
  and `internal_router` (node-to-node, mTLS-gated instead) — `create_app()`
  still includes both on one app so every existing multi-node test fixture
  (`test_node_quorum.py` etc.) needed zero changes.
- `run.py` (new production entrypoint) — runs two independent
  `uvicorn.Server` instances concurrently in one process via
  `asyncio.gather`: the public app on `PORT` (server-only TLS, no client
  cert required) and the internal app on `INTERNAL_PORT` (`8443`,
  `ssl_cert_reqs=CERT_REQUIRED` against the cluster CA). The internal port
  is never published to the Docker host, so it's unreachable from outside
  the compose network by construction, not just by the handshake.
- `scripts/gen_certs.py` — generates a self-signed CA and per-node
  certificates using the `cryptography` library directly (no `openssl` CLI
  dependency), signature verified independently after generation.

**Key decisions:**
- **Two ports, not one.** A single mTLS-required listener would have forced
  real clients to present a cluster certificate just to connect, on top of
  their bearer token. Researched first, not assumed: standard ASGI/uvicorn
  don't expose the negotiated peer certificate to the application layer (no
  `client_cert` in `scope`), so "mTLS only on `/internal/*`, optional
  elsewhere, same port" isn't a reliable option — confirmed via uvicorn's
  own open issues on the subject, not guessed.
- **Auth defaults to disabled, audit log defaults to off, unless a test
  explicitly opts in** — `create_app()` gained `auth_tokens`/`audit_log_path`
  params following the exact `storage is None` production-mode-detection
  pattern already used for `cluster_nodes`/`n`/`r`/`w`. This is what kept
  every one of the ~85 pre-Phase-6 tests passing with zero modification —
  the same discipline Phase 5 used to gate the gossip thread.
- **A genuine `httpx` bug found and worked around, not just a config typo:**
  `httpx.Client(cert=(...), verify="<ca path>")` looked correct and passed
  code review, but silently dropped the client certificate. Traced
  `httpx.create_ssl_context()`'s source directly: the string-`verify`
  branch returns immediately with `ssl.create_default_context(cafile=...)`,
  before the function ever reaches the code that would call
  `ctx.load_cert_chain()` for `cert`. Confirmed by reproducing the exact
  failure with a hand-built `ssl.SSLContext` using httpx's own
  `create_ssl_context()` output against a raw socket (still failed), then
  confirming a manually-built context with both `load_verify_locations()`
  and `load_cert_chain()` succeeded. Fixed by building the `SSLContext`
  directly and passing it as `verify=<SSLContext>` — which is also, it
  turns out, exactly what httpx's own deprecation warnings on `cert=` and
  string `verify=` already recommend. Every internal call was returning 503
  (quorum unreachable) until this was found; the fix was one function.
- Wrapped only `SqliteStorage` instances (main store + hint store) in
  `EncryptedStorage`; `MemoryStorage` (tests only) stays unwrapped — "at
  rest" means on-disk exposure, encrypting RAM buys nothing.

**Test results:** 108/108 passing (`pytest`) — all 85 pre-Phase-6 tests
unmodified. New: `test_storage_contract.py` gained a third parametrized
backend (`EncryptedStorage`); `test_storage_encrypted.py` (4 cases —
raw SQLite bytes provably don't contain the plaintext value, wrong key
fails to decrypt, key persistence and per-path distinctness);
`test_auth.py` (9 cases — 401/403/200 paths, default-namespace handling,
`/healthz` unauthenticated, auth-disabled-when-not-configured);
`test_audit_log.py` (3 cases — allowed and denied entries logged
correctly, raw token never appears in the log file).

Manual Docker Compose smoke test (3 real containers, real mTLS, real
encrypted SQLite files) — every check below passed only *after* finding
and fixing the httpx bug above, since every internal call failed with it in
place: valid token + correct namespace → 200; missing/wrong/unknown token →
401; wrong namespace or verb → 403; write via `node-1` replicates correctly
to `node-2`/`node-3` over mTLS; internal port genuinely rejects a
connection presenting no client certificate (`ReadError`/broken pipe at the
TLS layer, confirmed as the TLS 1.3-deferred-certificate-check behavior
Python's `ssl` module documents, not an application-level rejection) and
accepts one signed by the cluster CA; stopping `node-3` and writing through
`node-1` still succeeds via the remaining quorum with mTLS on, proving the
transport change didn't silently break replication; `data/node-1.audit.log`
showed both allowed and denied entries with no raw token ever present;
`data/node-1.db`'s raw `value` column held a `{"nonce","ciphertext"}`
envelope, not the literal stored value.

**Deferred to later phases:** the adversarial red-team suite this phase's
mechanisms exist to be tested against (Phase 7) · DynamoDB benchmarking
(Phase 8) · versioned routes, structured logging, `/metrics`, README/CI
(Phase 9) · dashboard (Phase 10). Not attempted in this phase, by design:
certificate rotation (self-signed CA, ~10yr validity, no rotation story —
matches PLAN.md's own "fine for a student project"), token revocation
beyond editing the JSON file and restarting, and any audit log
rotation/retention policy (Phase 9's structured-logging territory, not this
narrow purpose-built log).

## Phase 7 — Adversarial testing (done)

Red-teamed Phase 6's four security mechanisms together, as an integrated
live system, from an insider-threat model matching PLAN.md's own framing
("simulate a compromised node") — a party who already holds *some*
legitimate credential (a real node cert, a real client token) rather than
an external attacker with none. Built a 10-category taxonomy, executed
every scenario against the real 3-node Docker Compose cluster (not just
unit-level assertions), and measured prevention/detection independently
rather than as a single pass/fail.

**What was built:**
- `mtls.py` (new) — `build_mtls_client_context()`/`build_no_client_cert_context()`,
  extracted from `run.py`'s existing TLS context builder with zero behavior
  change, so both production code and the attack harness build TLS contexts
  through the one place that already paid the cost of finding Phase 6's
  `httpx` client-cert bug.
- `tests/test_adversarial_mechanisms.py` (new) + `test_gossip.py` (extended)
  — fast, deterministic, mounted-transport proofs of the mechanisms behind
  categories 2 (partial)/3/4/5/6, run before ever touching Docker so the
  claims in the live report rest on something proven independently of
  container timing.
- `scripts/adversarial_scenarios.py` + `scripts/adversarial_testbed.py`
  (new) — 10 scenario implementations against a shared `Context` (real
  certs/tokens read straight off the host filesystem, `docker compose exec`
  for on-disk inspection since `docker-compose.yml` uses named volumes, not
  host bind mounts), a CLI that preflights the cluster, runs every scenario,
  computes rates, and writes `reports/phase7_adversarial_report.md` +
  `reports/phase7_adversarial_results.json`.
- `main.py` — the one remediation this phase makes: `create_internal_app()`
  now accepts an `audit_log_path` and attaches `add_audit_middleware()` when
  given one; `run.py` passes a new, separate `default_internal_audit_log_path()`
  (`{node_id}.internal-audit.log`, distinct from the public audit log — two
  different trust domains worth inspecting independently).

**Taxonomy and results** (prevented/detected measured independently — a
scenario can be either, both, or neither):

| # | Category | Result |
|---|---|---|
| 1 | Unauthorized internal join (no cert / rogue CA cert) | PREVENTED (TLS handshake rejected) |
| 2 | Node identity spoofing (`sender`/`target` fields never cross-checked against the mTLS peer cert) | UNDEFENDED |
| 3 | Gossip forgery — compromised relay suppresses a healthy node's liveness | mechanism proven deterministically in `test_gossip.py`; live reproduction inconclusive (see below) |
| 4 | Vector-clock replay of an old write | PREVENTED |
| 5 | Vector-clock forgery — fabricated clock hijacks a key cluster-wide | UNDEFENDED (immediate hijack); self-heals on the impersonated node's next real write |
| 6 | Unauthorized/forged delete (`delete_local` is unconditional) | UNDEFENDED |
| 7 | Decrypt on-disk ciphertext with the wrong/no key | PREVENTED |
| 8 | Tamper with ciphertext at rest (GCM auth tag) | PREVENTED |
| 9 | Client auth bypass (5 sub-cases: no/malformed/unknown token, wrong verb, valid) | PREVENTED + DETECTED, all 5 |
| 10 | Audit blind spot — (a) internal-port attacks left zero record; (b) local audit log has no integrity protection | (a) FIXED this phase; (b) UNDEFENDED |

**Measured rates (final run):** Prevention 11/18 = 61%, Detection 6/18 = 33%.

**Key decisions:**
- **Categories 2, 5(hijack), 6, and 10b were measured and reported, not
  fixed** — the one decision confirmed with the user up front. Category
  10a's audit-wiring gap was the sole exception, fixed because leaving it
  would have made the detection-rate number misleadingly near-zero for a
  one-line wiring gap rather than an unsolved design problem.
- **Category 2 is a structural transport-stack limitation, not a forgotten
  check** — standard ASGI/uvicorn doesn't expose the negotiated mTLS peer
  certificate to the application layer at all (the same fact Phase 6's own
  two-port decision was already based on), so `sender`/`target` fields
  genuinely cannot be cross-checked today without a reverse proxy or
  protocol change.
- **Category 3's live reproduction is honestly inconclusive, not a false
  "defended."** The chosen topology (separate Docker Compose `networks:`
  isolating node-1 from node-3) causes near-instant connection/DNS failures
  rather than a hung TCP timeout, so a reactive failed attempt and a
  proactive gossip-driven skip are latency-indistinguishable under this
  specific technique — independent of whether the forged relay actually
  worked. Rather than force a different observable to get a clean pass/fail,
  the harness reports "inconclusive" as a real third outcome and points to
  the deterministic proof already sitting in `test_gossip.py`. Both
  `prevented` and `detected` are `False` for this scenario, and it's
  excluded from neither count's denominator — the 11/18 and 6/18 rates
  already reflect this.
- **Category 5's hijack is immediate but not eternal.** `Node.put()`'s
  `merge(...).incremented(self.node_id)` always dominates whatever it read,
  so the impersonated node's next real coordinated write supersedes the
  poison — measured as a separate line (self-heal: PREVENTED) rather than
  glossed into the hijack's UNDEFENDED verdict.
- Four real bugs were found and fixed *while building the harness itself*,
  not in production code: (1) two scenarios execed into the wrong
  container, trying to load a cert path that only exists in a *different*
  node's mounted `certs/` volume; (2) category 3 used `http://` against a
  TLS-only health-check port; (3) category 5's "legit" seed write used
  `localhost` (resolving to whichever container ran the snippet) while the
  poison claimed a fixed node id, so the two clocks landed under different
  node-id keys and were correctly judged `concurrent` rather than
  dominated — a bug in the scenario's design, not evidence of a defense;
  (4) category 8's first version read through the public quorum-routed
  endpoint, where `R=2` of 3 correctly masks one tampered replica (the
  system working as designed) — fixed by reading the tampered replica's own
  internal endpoint directly to actually exercise GCM's tamper detection.

**Test results:** 115/115 passing (`pytest`) — all Phase 1-6 tests
unmodified, plus the new fast adversarial-mechanism tests. Full live run
against a freshly rebuilt 3-node Docker Compose cluster (`docker compose
down -v && up --build -d`) produced the rates above, written to
`reports/phase7_adversarial_report.md` and
`reports/phase7_adversarial_results.json`.

**Deferred to later phases:** DynamoDB benchmarking (Phase 8) · versioned
routes, structured logging, `/metrics`, README/CI (Phase 9) · dashboard
(Phase 10). Not attempted in this phase, by design, with concrete future
costs: mTLS peer-identity binding for categories 2/5/6 needs a reverse
proxy or an ASGI/uvicorn protocol change to expose the peer cert; delete
needs a tombstone + GC mechanism to gain clock-awareness; tamper-evident
audit logging (hash chaining or signing) is Phase 9-adjacent
structured-logging territory.

## Phase 9 — Engineering polish (done)

Read through the actual code before planning anything, and found several
of PLAN.md's eight asks were already substantially done as an incidental
byproduct of earlier phases, not net-new work: `config.py` was already
100% env-var-driven with zero hardcoded values, and `models.py` already
had a Pydantic model for every route's request/response with 409/503
already raised correctly (Phase 3/4). What was genuinely new: route
versioning, structured operational logging (distinct from Phase 6/7's
security-focused audit log), a `/metrics` endpoint, GitHub Actions CI, and
a README — the last two didn't exist in the repo at all before this phase.

**What was built:**
- `api/routes.py` — `public_router` split into two: a data-plane router
  (`/keys/*`) and a small new `ops_router` for `/healthz`. Necessary
  because FastAPI's `include_router(prefix=...)` prefixes every route on
  that router with no per-route override, and `/healthz` needed to stay
  unversioned while `/keys/*` moved under `/v1`. `internal_router` is
  completely untouched — it's a private node-to-node wire protocol
  (`include_in_schema=False`, mTLS-gated), not a public contract, and
  `Node`'s own peer HTTP calls build `/internal/...` paths directly.
  Also fixed the one real gap `models.py` had: `put_hint` returned a bare
  `dict` with no `response_model` — added `InternalHintResponse`.
- `main.py` — `public_router` now mounted with `prefix="/v1"` on all three
  app constructors (`create_app`, `create_public_app`); `ops_router`
  mounted unprefixed alongside it.
- `logging_middleware.py` (new) — `RequestLogMiddleware`: structured JSON
  Lines to **stdout** via `logging.getLogger("dynamokv.access")`, capturing
  node_id/method/path/latency_ms/status_code/outcome for every request on
  every app, including high-frequency internal gossip/hint traffic the
  audit log was never designed to absorb. Deliberately separate from
  `AuditLogMiddleware` — audit is security-focused (client_id, source IP,
  allowed/denied, written to a per-node file for compliance-style
  retention, no timing); this is operational (perf-debugging, stdout,
  12-factor convention, captured by `docker compose logs`). Added *after*
  `add_audit_middleware()` everywhere both are wired, so reported latency
  includes the audit write's own cost rather than hiding it.
- `metrics.py` (new) — Prometheus `/metrics` via `prometheus_client`:
  `Counter` (requests by method/path/status), `Histogram` (request
  latency), `Gauge` (cluster membership, read fresh on each scrape from a
  new `Node.down_peers()`/`Node.peer_ids()` accessor pair — no background
  updater, no staleness window). Served on its **own dedicated,
  unpublished port** (`config.METRICS_PORT`, default `9090`) via a third
  `uvicorn.Server` in `run.py`'s `asyncio.gather(...)` — never listed in
  `docker-compose.yml`'s `ports:`, the same "unreachable by construction"
  pattern `INTERNAL_PORT` already uses, so a scraper needs neither a
  bearer token nor a cluster client certificate.
- `.github/workflows/ci.yml` (new) — two jobs: `lint-and-test` (required on
  every push — `ruff check .` + the full pytest suite, zero Docker/AWS/
  certs needed since every test injects `MemoryStorage`); `docker-smoke`
  (manual `workflow_dispatch` only, not required to merge — real
  `gen_certs.py` + `docker compose up` + a versioned-API PUT/GET smoke
  test). PLAN.md's text says integration tests run "via Docker Compose,"
  but making that a required merge gate would mean every push needs
  container startup to succeed — flakier than this project's actual
  integration coverage (in-process mounted transports).
- `README.md` (new) — Mermaid architecture diagram (three ports per node:
  public/internal/metrics), one-command `docker compose up --build` setup,
  an API reference pointing at FastAPI's auto-generated `/docs` plus a
  worked `curl` example, and a Design Decisions section distilling the
  N=3/R=2/W=2 + CAP trade-off reasoning already scattered across this
  file's earlier phase entries.
- `tests/test_observability.py` (new) — structured-log field/ordering
  proofs and `/metrics` content proofs, all fast/in-process.

**Key decisions:**
- **Clean break on route versioning, no permanent unversioned alias.**
  15 hardcoded `/keys/...`/`/healthz` calls in `tests/test_api.py` and
  several more test files, plus the live-system public-port URLs in
  `scripts/adversarial_scenarios.py` and `scripts/dynamodb_bench_conditions.py`
  (Phases 7/8), all got mechanically updated to `/v1/keys/...` — internal
  `/internal/keys/...` calls were left untouched throughout. A "versioned
  API" that keeps the old surface mounted forever isn't really versioned,
  and there's no external consumer here to protect.
- **`/metrics` on its own port, not the public app or the internal mTLS
  port.** Mounting it on the public app would mean anyone who can reach
  the client API can also see request counts/latencies/cluster membership;
  mounting it on the internal port would require a scraper to hold a
  cluster client certificate, since `ssl_cert_reqs=CERT_REQUIRED` applies
  to the whole handshake before any routing happens — there's no way to
  carve out one unauthenticated route on that port. A third plain-HTTP,
  unpublished port gets the same network-boundary protection as the
  internal port without either downside.
- **A real bug found while writing this phase's own tests, not in
  production logic already covered elsewhere:** `logging_middleware.py`'s
  first draft gated *both* handler-attachment *and* `logger.setLevel(INFO)`
  behind the same `if not logger.handlers` check. If anything else ever
  attached a handler to `"dynamokv.access"` before `RequestLogMiddleware`'s
  own lazy first-use (which happens on an app's first real request, not at
  construction — Starlette builds the middleware stack lazily), the level
  would silently stay at `NOTSET`/root-inherited (`WARNING`), filtering out
  every structured-log entry before any handler ever saw it. Caught by a
  test using a directly-attached collector handler, not by inspection —
  fixed by unconditionally setting level/`propagate` on every call and
  only guarding the handler-attachment itself.
- **`caplog` doesn't work for testing this logger, by design.**
  `RequestLogMiddleware`'s logger sets `propagate = False` deliberately (so
  structured logs never leak into the root logger/pytest's own capture
  ecosystem), which also means `caplog` — which relies on root-logger
  propagation — can't observe it even when told which logger to watch.
  Tests attach a plain `logging.Handler` directly to the named logger
  instead.
- **Route-versioning migration was verified live, not just by grep**: full
  `docker compose down -v` + `up --build -d` + a complete
  `scripts/adversarial_testbed.py` run against the new `/v1` paths
  reproduced the exact known-good Phase 7 baseline (11/18 prevented, 6/18
  detected) — confirming the chaos layer survives the versioning change
  unmodified in its own logic, only in its URL strings. (Two anomalies seen
  on an interim un-reset run — category 5's hijack briefly showing
  "DEFENDED" and category 8 briefly showing corrupted-data-accepted — were
  both traced to stale accumulated state in Docker's named volumes from
  prior harness runs, not the `/v1` migration itself; resolved by the same
  fresh-restart discipline Phase 7's own final verification already used.)
- **`ruff` line-length set to 200, not the default 88/120.** This
  codebase's established style (per `CLAUDE.md`: "explain design decisions
  in plain language") is long, dense prose comments/docstrings — running
  `ruff` at a default width first would have meant rewrapping dozens of
  carefully-written explanatory comments across 20+ files, a large,
  unrelated-to-content diff for no real readability gain. The two
  remaining outliers (a 310-char embedded shell snippet and a 206-char
  report string, both in `scripts/`, both single data strings rather than
  prose meant to be read at a normal width) got a targeted `# noqa: E501`
  instead of being force-wrapped.

**Test results:** 132/132 passing (`pytest`) — all 126 pre-Phase-9 tests
updated only mechanically (URL strings) where the versioning change
required it, plus 6 new `test_observability.py` cases. `ruff check .`
clean across the whole tree. Full live verification: `docker compose up
--build -d` (fresh volumes) + `scripts/adversarial_testbed.py` reproduced
the Phase 7 baseline exactly under the new `/v1` paths.

**Deferred to later phases:** dashboard (Phase 10). Not attempted in this
phase, by design: the `docker-smoke` CI job is manual-trigger-only, not a
required merge gate (see Key decisions); no log rotation/retention policy
for the new stdout structured logs (12-factor apps typically leave this to
the container runtime/log driver, not the application); no metrics
persistence (Prometheus itself, not this app, is where scrape history
would live if this were ever deployed against a real Prometheus server).

## Phase 10 — Live cluster dashboard (done, final phase)

Before writing any frontend code, checked what the dashboard actually
needed against what already existed: nothing exposed ring topology, no
HTTP endpoint surfaced Phase 9's `Node.down_peers()`/`peer_ids()` (and
`/metrics`' Prometheus gauge lives on a port deliberately never published
to the host — a browser can't reach it, by design), and Phase 9's
structured logging went to stdout only, never retained or queryable. One
new endpoint, `GET /v1/cluster-state`, plus a small in-memory buffer,
closed all three gaps — no WebSocket infrastructure needed, matching
PLAN.md's own "polling... 1-2s" as the simpler allowed option.

**What was built:**
- `ring.py` — `HashRing.sorted_points() -> List[Tuple[float, str]]`, each
  virtual point as `(hash / 2**64, owner)`. Never serializes the raw
  64-bit hash int: JS numbers are IEEE754 doubles with a 53-bit safe-integer
  range, so shipping the raw hash would silently lose precision in the
  browser (wrong sort order, apparent duplicates) — dividing by a fixed
  constant is monotonic, so the existing sort order carries over for free.
- `node.py` — two new accessors alongside Phase 9's `down_peers()`/
  `peer_ids()`: `ring_topology()` (wraps `sorted_points()`) and
  `pending_hints() -> Dict[str, int]` (per-peer hint-queue depth, a live
  signal of hinted handoff in progress).
- `recent_ops.py` (new) — a module-level `collections.deque(maxlen=200)`,
  guarded by one lock around **both** the append and the read-side
  snapshot copy. `routes.py`'s handlers are plain `def` (threadpool-
  dispatched) while `RequestLogMiddleware` appends from the event loop —
  an unguarded read can raise `RuntimeError: deque mutated during
  iteration` under real concurrency, not just return stale data.
- `logging_middleware.py` — `RequestLogMiddleware` gained one gated call to
  `record_operation()`, filtered to `request.url.path.startswith("/v1/keys/")`
  specifically (not just "key is not `None`" — `/internal/keys/{key}`
  shares that path param but is replica fan-out, not a client op).
  Without the filter, both internal traffic and the dashboard's own
  polling of `/v1/cluster-state` would drown real operations in noise.
- `api/routes.py` — `GET /cluster-state` on `public_router` (→
  `/v1/cluster-state` via the existing prefix), deliberately with **no**
  `auth: AuthContext` dependency, unlike every other `/v1` route. Returns
  one node's own-perspective payload: ring topology, its own gossip-derived
  peer up/down view, pending hint counts, and its own recent-ops feed.
- `config.py` — `PUBLIC_CLUSTER_URLS` (externally-reachable node addresses,
  surfaced in the cluster-state response so the same dashboard bundle
  knows every node to poll regardless of which one served it —
  `docker-compose.yml`'s existing peer-URL convention is Docker-internal
  DNS only, invisible to a browser), `FRONTEND_DIST_DIR`,
  `DASHBOARD_DEV_CORS_ORIGINS` (empty by default — production ships zero
  CORS surface).
- `main.py` — `_add_dashboard()`: conditional `CORSMiddleware` (only if
  `DASHBOARD_DEV_CORS_ORIGINS` is set) and a **guarded** `StaticFiles`
  mount at `/dashboard` (`if Path(config.FRONTEND_DIST_DIR).is_dir()`) —
  FastAPI's `StaticFiles` raises at construction if the directory is
  missing, which would otherwise break every test/CI run in a checkout
  that never ran `npm run build`.
- `Dockerfile` — multi-stage: a `node:20-slim` stage runs `npm ci && npm
  run build`, copied into the existing `python:3.11-slim` final stage,
  which never gets Node.js installed itself.
- `frontend/` (new) — Vite + plain React + Tailwind: `src/api.js` polls
  every node in `PUBLIC_CLUSTER_URLS` in parallel every 1.5s, each fetch
  wrapped in an `AbortController` with a ~2s timeout (a killed node's
  origin doesn't fail fast on its own — TCP/TLS connect timeouts run
  20-30s+ — so without an explicit deadline, requests to a dead node pile
  up across poll cycles); `RingVisualization.jsx` (SVG, one tick per
  virtual point); `HealthMatrix.jsx` (reporting-node × peer grid, not one
  boolean per node — gossip is eventually consistent, so a real partition
  showing up as *disagreement* between nodes is the honest, correct
  behavior, not a bug to hide); `OperationLog.jsx` (merged, sorted by
  timestamp, 409/conflict rows highlighted); `ChaosPanel.jsx` (advisory
  only — shows the exact `docker compose stop/start` command, no fetch
  calls at all).

**Key decisions (all three confirmed with the user up front):**
- **Chaos panel is advisory-only**, not a real control endpoint. A browser
  can't run Docker commands itself, so "real" execution would mean a new
  network-reachable endpoint that can stop cluster nodes or reshape the
  network — meaningful new attack surface for a security-focused project
  to take on for a dashboard button. The panel just shows the command;
  the ring/health/log views update live once you run it yourself.
- **`/v1/cluster-state` is unauthenticated.** Requiring the same bearer
  token as `/v1/keys` would mean embedding a real token in client-side JS,
  visible to anyone who opens dev tools — a false sense of security, not a
  real one. It never returns values or raw tokens, but `recent_ops` does
  include key names (the same extraction `audit.py` already does) — a
  genuinely new, if minor, disclosure to an unauthenticated caller, named
  explicitly in the route's docstring rather than left implicit.
- **Dashboard bundled into the existing public app**, not a new
  docker-compose service/port — matches this project's one-command
  `docker compose up --build` setup story; no new container, no new
  published port.
- **Client-side merge across all N nodes, not server-side aggregation.**
  Each client operation is coordinated and logged by exactly one node
  (`create_public_app()` never mounts `internal_router`), so merging needs
  no de-duplication. More importantly: if one node aggregated on the
  others' behalf, killing *that* node via the chaos panel would blind the
  whole dashboard — polling all N directly means killing any single node
  only ever removes that one node's row from the health matrix.
- **A real design gap caught only during validation, not in the original
  draft:** every existing peer-URL construction (`main.py`, `run.py`) uses
  Docker Compose's internal service-name DNS (`http://node-1:8000`), which
  a browser cannot resolve at all — it can only reach the host-mapped
  ports. Without `PUBLIC_CLUSTER_URLS`, the frontend would have had no way
  to discover the other two nodes' addresses at all.

**Test results:** 140/140 passing (`pytest`) — all 132 pre-Phase-10 tests
unmodified, plus 8 new `test_cluster_state.py` cases (including one that
required a test-only `recent_ops.clear()` reset fixture once discovered
that the recorded-ops buffer, correctly a process-global singleton in
production, was leaking entries between tests running in the same pytest
process). `ruff check .` clean. Frontend: `npm run build` succeeds,
produces correctly `/dashboard/`-prefixed asset URLs; manually confirmed
`main.py`'s guarded mount both serves the built dashboard correctly *and*
leaves `create_app()` construction unaffected when `frontend/dist` is
absent (the fresh-checkout/CI case).

Full live verification against a freshly rebuilt 3-node cluster
(multi-stage `docker compose up --build -d`): `/dashboard/` serves the
built SPA and its assets correctly over the public port; `/v1/cluster-state`
returned all 450 ring points, both peers correctly reported "up"; a real
`PUT`/`GET` through the token-gated `/v1/keys/dash-test` (plus a
deliberately-unauthenticated attempt that correctly 401'd) all appeared in
`recent_ops` — even the rejected attempt, which is honest and useful;
`docker compose stop node-3` was reflected as `"node-3": "down"` in
node-1's own `peers` view within a few gossip intervals, with node-1's own
`/v1/cluster-state` polling uninterrupted throughout.

**Deferred, by design:** real chaos-panel execution (see Key decisions);
WebSocket push (polling is simpler and sufficient at this scale, per
PLAN.md's own allowance); frontend automated tests (Playwright/etc. would
be a genuinely new testing tier for a backend-testing-focused project;
the backend contract it depends on is fully covered by
`test_cluster_state.py` instead); clock-skew handling in the merged,
sorted-by-timestamp operation log (fine on one Docker host sharing the
host clock; would need addressing if this ever ran across real,
independently-clocked EC2 instances, per PLAN.md's own optional cloud
phase). This was PLAN.md's final phase — all ten are now built.
