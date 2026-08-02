Project 2: Secure Dynamo-Inspired Distributed Key-Value Store (RECOMMENDED PRIMARY PROJECT)
One-line pitch

A scaled-down reimplementation of Amazon's own Dynamo architecture (the paper behind DynamoDB) — highly available, partition-tolerant key-value storage — with an added security layer (encryption, access control, adversarial node testing) that showcases your actual specialization.

Why this project (strategic fit)
Directly inspired by an Amazon-authored paper — this is the single most "matches Amazon infrastructure" project possible for a student, short of literally working there. Citing that you studied and reimplemented core ideas from Amazon's own 2007 Dynamo paper is a strong, authentic interview story.
Hits JD language almost word-for-word: "distributed storage, index, and query systems that are scalable, fault-tolerant, low cost, and easy to manage/use."
Combines core distributed systems (JD requirement) with your genuine security background (differentiator), without faking novelty.
Scoped realistically as a single deep project rather than three shallow ones.
Background: what Dynamo actually is

Dynamo is Amazon's internal highly-available key-value store, designed for its e-commerce infrastructure where availability matters more than strict consistency (e.g., shopping cart should always accept a write, even during a network partition). Key ideas from the paper:

Consistent hashing — data partitioned across nodes using a hash ring, so adding/removing nodes only reshuffles a small fraction of data
Vector clocks — track causal history of a value instead of a single timestamp, allowing conflict detection when concurrent writes happen
Quorum reads/writes (N, R, W) — configurable durability/consistency trade-off (e.g., N=3 replicas, W=2 writes must succeed, R=2 reads must agree)
Gossip protocol — nodes exchange membership/health info peer-to-peer instead of relying on a central coordinator
Hinted handoff — if a target node is down, a neighboring node temporarily holds the write and hands it off once the target recovers
Merkle trees — used for efficient anti-entropy (detecting and repairing replica divergence)

You do not need to implement every single mechanism at full paper fidelity — pick the core 4-5 (consistent hashing, quorum R/W, vector clocks, gossip-based failure detection, hinted handoff) and implement them properly rather than doing all 7 shallowly.

Architecture
Components
Node — a process holding a shard of the key space, exposing a simple API (PUT/GET/DELETE)
Consistent hash ring — maps keys to nodes, supports virtual nodes to balance load
Coordinator logic — any node can coordinate a request, forwarding to the correct replica set
Vector clock module — attached to every value, used to detect and surface conflicting concurrent writes
Gossip layer — periodic heartbeat exchange between nodes for membership and failure detection
Security layer (your differentiation):
Mutual TLS between all inter-node connections
Encryption at rest for stored values (AES-256)
Access control layer: per-key or per-namespace ACLs, API-key or token-based auth for clients
Audit log of all access attempts (successful and denied)
Adversarial test suite simulating a compromised/malicious node attempting to read data it shouldn't, forge gossip messages, or replay old writes
Data flow example (a PUT request)
Client sends PUT key=X value=Y (authenticated request) to any node
Coordinator hashes key X to find the N nodes responsible (via consistent hash ring)
Coordinator sends write to all N replicas, waits for W acknowledgments
Each replica stores value with an updated vector clock and writes to encrypted local storage
If a replica is down, a healthy neighbor accepts a "hinted" write and forwards it later
Client gets success once W acks received
Data flow example (a GET request)
Client sends authenticated GET key=X
Coordinator queries R replicas, compares vector clocks
If clocks conflict (concurrent writes), return both versions to client (or apply a resolution policy) — this is the actual "eventual consistency" trade-off Dynamo made famous
Build phases (suggested milestones)

Phase 0 — Foundations

Read the Dynamo paper (DeCandia et al., 2007) — focus on sections 4 (system architecture) and 5 (implementation)
Decide your N/R/W quorum defaults (e.g., N=3, R=2, W=2) and be ready to explain the trade-off

Phase 1 — Single-node KV store

Basic PUT/GET/DELETE with local storage (start with in-memory dict, then persist to disk/SQLite)
Get the API layer working (FastAPI or gRPC) before adding any distribution

Phase 2 — Partitioning across nodes

Implement consistent hashing with virtual nodes
Spin up multiple node processes (Docker Compose), route keys to the correct node(s)
Test: add/remove a node, confirm only ~1/N of keys need to move

Phase 3 — Replication with quorum R/W

Replicate each key to N nodes
Implement quorum-based reads and writes
Test: kill one replica out of three, confirm writes/reads still succeed via remaining quorum

Phase 4 — Vector clocks + conflict handling

Attach vector clocks to values
Simulate concurrent writes from two clients to the same key during a partition, show the system detects and surfaces the conflict correctly

Phase 5 — Gossip-based failure detection + hinted handoff

Nodes periodically gossip health/membership
When a target replica is down, a neighbor holds a "hint" and forwards once the node recovers
Test: partition a node from the cluster for 60 seconds, confirm hinted handoff delivers the missed writes on recovery

Phase 6 — Security layer

mTLS between all node-to-node connections (self-signed CA is fine for a student project)
AES-256 encryption at rest for stored values
Token-based client auth + per-namespace ACLs
Audit logging of all access attempts

Phase 7 — Adversarial testing (reuse your Agent Security Testbed methodology)

Simulate a compromised node: attempt to read encrypted data without proper key, forge gossip heartbeats claiming a healthy node is dead, replay an old write to overwrite a newer one
Build a small taxonomy of attack scenarios (aim for 6-10 categories, similar structure to your existing testbed) and measure detection/prevention rate

Phase 8 — Benchmarking against real DynamoDB

If budget allows, spin up an actual small DynamoDB table (AWS free tier) and compare your system's latency/throughput at small scale — even an honest "our system is X times slower than production DynamoDB, here's why" is a strong, mature engineering observation for an interview

Phase 9 — Engineering polish (backend professionalism)

Versioned REST API (/v1/put, /v1/get) with OpenAPI/Swagger docs auto-generated via FastAPI
Pydantic models validating every request/response; proper HTTP status codes (409 for conflicts, 503 when quorum unavailable, etc.)
Config via environment variables (N/R/W values, node list, ports, TLS cert paths) — no hardcoded values
Structured JSON logging for every request (node, operation, latency, outcome)
Expose a /metrics endpoint (Prometheus format) for request count, latency, error rate, current cluster membership
Full test pyramid: unit tests (hash ring math, vector clock comparison logic), integration tests (multi-node quorum behavior via Docker Compose), your existing adversarial suite as the chaos layer
GitHub Actions CI: lint + unit tests + integration tests on every push (mirror your SecEx pipeline)
README with architecture diagram, one-command docker-compose up setup, API reference, and a "Design Decisions" section explaining your N/R/W choice and CAP trade-off

Phase 10 — Live cluster dashboard (frontend)

React app served alongside the API, polling or WebSocket-connected to a /v1/cluster-state endpoint
Ring visualization: circular diagram showing all nodes on the consistent hash ring, which node owns which key range, color-coded health (green=up, red=down/partitioned)
Live operation log: scrolling feed of recent PUT/GET requests, highlighting any vector-clock conflicts in a distinct color when they occur
Chaos panel: buttons to kill/restart a node or trigger a network partition directly from the UI, with the ring visualization updating in real time as hinted handoff and recovery happen
Tech: React + Tailwind, recharts or a custom SVG ring for the hash-ring visual, simple WebSocket or 1-2s polling for live state
What to measure (for your eventual resume bullet)
Throughput (ops/sec) at various N/R/W configurations
Recovery time after node failure (via hinted handoff)
Percentage of keys relocated when adding/removing a node (should be ~1/N, proving consistent hashing works)
Number of adversarial scenarios tested and prevention/detection rate
Latency comparison vs. real AWS DynamoDB (optional but powerful)
Tech stack suggestion
Backend: Python (fast iteration, you're already fluent) or Go if you want a resume boost in a new language
API: FastAPI (you already have this in SecEx), OpenAPI docs, Pydantic validation
Storage: SQLite or RocksDB per node for local persistence
Crypto: cryptography library for AES + mTLS certs
Observability: structured logging (structlog or JSON logging), Prometheus-format /metrics
Orchestration: Docker Compose for multi-node local cluster
Frontend: React + Tailwind, WebSocket or polling client, SVG/recharts for the hash-ring visualization
CI/CD: GitHub Actions (lint, unit tests, integration tests)
Optional cloud: deploy across a few small EC2 instances instead of local Docker, for real network latency
Honest scope warning

This is a large but very achievable project if built in the phased order above. Do not skip straight to the security layer — the underlying distributed correctness (Phases 1-5) needs to work first, or the security additions have nothing solid to sit on top of.

Draft resume bullet (fill in real numbers once built)

"Built a Dynamo-inspired distributed key-value store (consistent hashing, quorum-based R/W, vector clocks, gossip-based failure detection, hinted handoff) in [language], hardened with mTLS, AES-256 encryption at rest, and token-based ACLs; validated via 8+ adversarial scenarios (forged gossip, compromised-node reads) achieving [X]% detection rate, with [Y]ms write latency at N=3/W=2 quorum"

"Engineered a production-style REST API (FastAPI, OpenAPI docs, structured logging, Prometheus metrics) with full unit/integration/chaos test coverage and CI/CD, plus a React dashboard for live cluster visualization (hash-ring topology, real-time health, chaos-injection controls)"

Interview talking points this unlocks
CAP theorem trade-offs in practice (why Dynamo chose availability over strict consistency, and when that's the right call)
How consistent hashing avoids full data reshuffle on cluster resize
How you extended a classic distributed systems design with a security threat model — genuinely differentiated story most other candidates won't have