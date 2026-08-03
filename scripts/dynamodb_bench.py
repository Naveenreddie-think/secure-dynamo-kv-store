"""Phase 8 benchmark CLI.

Usage:
    python scripts/dynamodb_bench.py --create-table   # first run only, provisions the table
    python scripts/dynamodb_bench.py                  # subsequent runs
    python scripts/dynamodb_bench.py --teardown        # also delete the table when done
    python scripts/dynamodb_bench.py --network-rtt-only  # just print the WAN RTT probe

Assumes `docker compose up --build -d` is already running -- matching every
prior phase's harness precedent (adversarial_testbed.py etc.): never
self-manages that lifecycle, with one narrow, explicit exception -- a
temporary docker-compose.override.yml setting node-1's R=1 for the
eventually-consistent read condition, applied and reverted within this
script alone (same technique Phase 3/5/7 already used).

AWS: never silently provisions the table -- preflight fails fast with
instructions unless --create-table is passed explicitly. Table creation
uses PROVISIONED 25/25 RCU/WCU (AWS's indefinite Always-Free tier) with no
autoscaling attached, so cost stays genuinely $0 regardless of how many ops
this script runs.

Writes reports/phase8_benchmark_report.md and
reports/phase8_benchmark_results.json.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bench_config as cfg  # noqa: E402
import bench_stats  # noqa: E402
import dynamodb_bench_aws as aws  # noqa: E402
import dynamodb_bench_conditions as cond  # noqa: E402

PUBLIC_PORTS = {"node-1": 8001, "node-2": 8002, "node-3": 8003}
REPORTS_DIR = REPO_ROOT / "reports"
OVERRIDE_PATH = REPO_ROOT / "docker-compose.override.yml"

# Only node-1 needs R=1 -- our benchmark's client always talks to node-1's
# public port (cfg.NODE_PUBLIC_URL), and R is a property of whichever node
# coordinates the read, not a cluster-wide setting.
_R1_OVERRIDE = """services:
  node-1:
    environment:
      R: "1"
"""


def preflight_cluster() -> None:
    for node_id, port in PUBLIC_PORTS.items():
        try:
            resp = httpx.get(f"https://localhost:{port}/healthz", verify=False, timeout=5.0)
            if resp.status_code != 200:
                print(f"ERROR: {node_id} responded {resp.status_code} on /healthz, expected 200.")
                sys.exit(1)
        except httpx.HTTPError as e:
            print(f"ERROR: could not reach {node_id} on https://localhost:{port} -- {type(e).__name__}: {e}")
            print("Is `docker compose up --build -d` running?")
            sys.exit(1)
    print("Cluster preflight OK: all 3 nodes healthy.")


def preflight_aws(client, create_table: bool) -> None:
    exists = aws.table_exists(client, cfg.DYNAMODB_TABLE_NAME)
    if not exists:
        if not create_table:
            print(f"ERROR: DynamoDB table {cfg.DYNAMODB_TABLE_NAME!r} does not exist in {cfg.AWS_REGION}.")
            print("Run with --create-table to provision it (PROVISIONED "
                  f"{cfg.TABLE_RCU}/{cfg.TABLE_WCU} RCU/WCU, AWS Always-Free tier).")
            sys.exit(1)
        print(f"Creating table {cfg.DYNAMODB_TABLE_NAME!r} in {cfg.AWS_REGION} "
              f"(PROVISIONED {cfg.TABLE_RCU}/{cfg.TABLE_WCU}, no autoscaling)...")
        aws.create_table_if_missing(client, cfg.DYNAMODB_TABLE_NAME, cfg.TABLE_RCU, cfg.TABLE_WCU)
        print("Table ACTIVE.")
    else:
        print(f"AWS preflight OK: table {cfg.DYNAMODB_TABLE_NAME!r} exists in {cfg.AWS_REGION}.")


def apply_r1_override() -> bool:
    if OVERRIDE_PATH.exists():
        print("WARNING: docker-compose.override.yml already exists -- leaving it untouched, "
              "skipping the R=1 (eventually-consistent) condition for our system.")
        return False
    OVERRIDE_PATH.write_text(_R1_OVERRIDE)
    subprocess.run(["docker", "compose", "up", "-d"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    time.sleep(3)
    return True


def revert_r1_override() -> None:
    if OVERRIDE_PATH.exists():
        OVERRIDE_PATH.unlink()
    subprocess.run(["docker", "compose", "up", "-d"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    time.sleep(3)


def run_all() -> list:
    results = []

    for size in cfg.VALUE_SIZES:
        print(f"-- PUT ({size}B) -- our system's PUT is measured once, under the cluster's "
              "default R=2/W=2, and never re-run under the R=1 override (see report notes)")
        ours = cond.our_put_condition(size)
        ddb = cond.dynamodb_put_condition(size)
        results.append({"system": "ours", "operation": "PUT", "size_bytes": size,
                         "consistency": "W=2, R=2 pre-read reconcile", **ours})
        results.append({"system": "dynamodb", "operation": "PutItem", "size_bytes": size,
                         "consistency": "default (undocumented durability mechanism)", **ddb})

    for size in cfg.VALUE_SIZES:
        print(f"-- GET quorum-majority ({size}B) --")
        ours = cond.our_get_condition(size)
        ddb = cond.dynamodb_get_condition(size, consistent_read=True)
        results.append({"system": "ours", "operation": "GET", "size_bytes": size,
                         "consistency": "R=2 (quorum majority, our default)", **ours})
        results.append({"system": "dynamodb", "operation": "GetItem", "size_bytes": size,
                         "consistency": "ConsistentRead=True", **ddb})

    applied = apply_r1_override()
    if applied:
        try:
            for size in cfg.VALUE_SIZES:
                print(f"-- GET eventual ({size}B) -- our system --")
                ours = cond.our_get_condition(size)
                results.append({"system": "ours", "operation": "GET", "size_bytes": size,
                                 "consistency": "R=1 (single replica, temporary override)", **ours})
        finally:
            revert_r1_override()

    for size in cfg.VALUE_SIZES:
        print(f"-- GET eventual ({size}B) -- DynamoDB --")
        ddb = cond.dynamodb_get_condition(size, consistent_read=False)
        results.append({"system": "dynamodb", "operation": "GetItem", "size_bytes": size,
                         "consistency": "ConsistentRead=False (eventual, default)", **ddb})

    return results


def run_network_rtt() -> dict:
    print(f"Measuring pure network RTT to {cfg.DYNAMODB_ENDPOINT_HOST} (TCP+TLS connect only, no DB call)...")
    return aws.run_network_rtt_probe(cfg.DYNAMODB_ENDPOINT_HOST, n_samples=30)


_NOTES = [
    "Closed-loop measurement: request N+1 is issued only after N's response returns (single "
    "outstanding request per condition). This under-represents tail latency during any slow "
    "period (coordinated omission) -- a stated limitation, not an open-loop-representative result.",
    "p99 is a noisy estimate at low N (roughly the k-th highest of n samples) -- treat it as "
    "less trustworthy than p50/p95, especially for the 10KB tier where N is smaller by design.",
    "Our system's calls go through the real public HTTPS API with the full stack live: mTLS-"
    "signed internal replica fan-out, AES-256-GCM encryption at rest, a synchronous blocking "
    "audit-log file write on every request (AuditLogMiddleware), and vector-clock reconcile. "
    "DynamoDB's PutItem/GetItem have no equivalent client-visible steps -- replication, "
    "encryption at rest, and durability happen server-side and are not separately timed here.",
    "Our PUT is not a pure W=2 operation: Node.put() performs an R-sized quorum READ before "
    "writing, to merge vector clocks, so its cost is 'read-quorum reconcile + write-quorum ack.' "
    "DynamoDB's PutItem has no analogous internal read -- a legitimate structural reason our "
    "PUT is slower, not an artifact of the benchmark.",
    "DynamoDB's own write durability mechanism is not publicly documented in quorum terms -- "
    "the comparison leans only on the observable property that a successful PutItem is "
    "immediately visible to a following ConsistentRead=True GetItem, not a specific quorum claim.",
    "Value payloads are plain strings on both sides, deliberately excluding our system's vector-"
    "clock/sibling JSON envelope from the value itself. DynamoDB's own attribute-value wire "
    "format ({\"pk\":{\"S\":...}}) is its own protocol overhead outside the raw value -- neither "
    "side is truly 'just the raw bytes.'",
    "This machine is Windows 11 + Docker Desktop, so our own cluster's 'loopback' already "
    "crosses a WSL2/Hyper-V VM boundary -- not bare-metal-zero-overhead either.",
    "Network RTT to the DynamoDB endpoint is measured separately (TCP connect + TLS handshake "
    "only, no DynamoDB API call) and reported alongside the operation latencies, so WAN transit "
    "time can be mentally separated from server-side processing time without needing a "
    "same-region EC2 instance.",
    "Only the 10KB tier is throughput-capped by the free-tier provisioned ceiling: ~2.5 "
    "writes/sec (10 WCU/write of 25 WCU) and ~8.3 ConsistentRead=True / ~16.7 eventual reads/sec "
    "(3 / 1.5 RCU per read of 25 RCU). 100B/1KB tiers round to the 1-unit minimum and are "
    "effectively unconstrained at this benchmark's scale. A throwaway warm-up load is run "
    "against the 10KB tier before measuring, to drain any accumulated burst credit so the "
    "reported ceiling reflects steady-state capacity, not a post-idle burst.",
    "AWS SDK retries are disabled (max_attempts=1) to match our own httpx client, which does "
    "not auto-retry on a 503 -- both sides are measured 'first attempt only' so neither system's "
    "tail latency is inflated by SDK backoff sleep.",
    "HTTP/1.1 on both sides -- confirmed, not assumed: botocore's DynamoDB default, and our "
    "stack has no HTTP/2 configured anywhere in mtls.py/run.py.",
    "The low-level boto3 client (not the resource() API) is used deliberately -- the resource "
    "API's TypeSerializer/TypeDeserializer marshaling layer would add client-side overhead with "
    "no equivalent on our thin-JSON side.",
]


def write_reports(results: list, rtt: dict) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)

    json_path = REPORTS_DIR / "phase8_benchmark_results.json"
    json_path.write_text(json.dumps({
        "aws_region": cfg.AWS_REGION,
        "table_name": cfg.DYNAMODB_TABLE_NAME,
        "results": results,
        "network_rtt_ms": rtt,
    }, indent=2))

    def fmt(v: float) -> str:
        return f"{v:.2f}" if v == v else "n/a"  # NaN check via self-inequality

    lines = [
        "# Phase 8 Benchmark Report — our system vs. real AWS DynamoDB",
        "",
        f"AWS region: `{cfg.AWS_REGION}` · Table: `{cfg.DYNAMODB_TABLE_NAME}` "
        f"(PROVISIONED {cfg.TABLE_RCU}/{cfg.TABLE_WCU} RCU/WCU, Always-Free tier)",
        "",
        "## Network RTT to DynamoDB endpoint (context, not a database operation)",
        "",
        f"- TCP connect: p50 {fmt(bench_stats.percentile(sorted(rtt['tcp_connect_ms']), 50))}ms "
        f"(n={len(rtt['tcp_connect_ms'])})",
        f"- TLS handshake: p50 {fmt(bench_stats.percentile(sorted(rtt['tls_handshake_ms']), 50))}ms",
        f"- Total (TCP+TLS, no DB call): p50 {fmt(bench_stats.percentile(sorted(rtt['total_ms']), 50))}ms",
        "",
        "## Results",
        "",
        "| System | Operation | Size (B) | Consistency | N | p50 (ms) | p95 (ms) | p99 (ms) | Throughput (ops/s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['system']} | {r['operation']} | {r['size_bytes']} | {r['consistency']} | "
            f"{r['n']} | {fmt(r['p50_ms'])} | {fmt(r['p95_ms'])} | {fmt(r['p99_ms'])} | "
            f"{fmt(r['throughput_ops_sec'])} |"
        )

    lines += ["", "## Methodology notes"]
    for note in _NOTES:
        lines.append(f"- {note}")

    (REPORTS_DIR / "phase8_benchmark_report.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--create-table", action="store_true")
    parser.add_argument("--teardown", action="store_true")
    parser.add_argument("--network-rtt-only", action="store_true")
    args = parser.parse_args()

    client = aws.build_client()

    if args.network_rtt_only:
        print(json.dumps(run_network_rtt(), indent=2))
        return

    preflight_cluster()
    preflight_aws(client, args.create_table)
    print(f"AWS region: {cfg.AWS_REGION}, table: {cfg.DYNAMODB_TABLE_NAME}\n")

    results = run_all()
    rtt = run_network_rtt()

    write_reports(results, rtt)

    if args.teardown:
        print("Tearing down DynamoDB table...")
        aws.teardown_table(client, cfg.DYNAMODB_TABLE_NAME)

    print(f"\nReports written to {REPORTS_DIR}/")


if __name__ == "__main__":
    main()
