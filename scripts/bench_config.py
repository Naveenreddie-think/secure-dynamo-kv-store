"""Phase 8 benchmark config -- os.environ-driven, same style as
src/dynamokv/config.py, but deliberately kept out of src/dynamokv entirely:
this file (and everything downstream of it under scripts/) is the only
place in the repo allowed to import boto3. Production node code must never
gain that dependency just because Phase 8 exists.
"""
import os

# -- our cluster (client vantage point: this host machine, real public HTTPS API)
NODE_PUBLIC_URL = os.environ.get("BENCH_NODE_URL", "https://localhost:8001")
AUTH_TOKEN = os.environ.get("BENCH_AUTH_TOKEN", "tok_demo_admin")
NAMESPACE = os.environ.get("BENCH_NAMESPACE", "default")

# -- AWS / DynamoDB
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "dynamokv-benchmark")
DYNAMODB_ENDPOINT_HOST = f"dynamodb.{AWS_REGION}.amazonaws.com"

# Provisioned, not on-demand -- AWS's indefinite Always-Free tier (25 RCU /
# 25 WCU) is what keeps this benchmark genuinely $0 regardless of how many
# ops run. NEVER attach an Application Auto Scaling policy to this table --
# doing so is what would break the $0 guarantee. Exceeding 25/25 just
# throttles (ProvisionedThroughputExceededException); it does not bill for
# overage.
TABLE_RCU = int(os.environ.get("BENCH_TABLE_RCU", "25"))
TABLE_WCU = int(os.environ.get("BENCH_TABLE_WCU", "25"))

# -- value size tiers (bytes) -- same plain-string payload on both systems
VALUE_SIZES = [100, 1024, 10240]

# -- op-count targets, time-budget-driven (see dynamodb_bench_conditions.py):
# each condition runs until EITHER the target op count OR the time budget is
# reached, whichever comes first, but never fewer than the floor. Only the
# 10KB tier is expected to be throughput-capped by the free-tier ceiling
# (~2.5 writes/sec, ~8.3 consistent-reads/sec, ~16.7 eventual-reads/sec) --
# the smaller tiers should comfortably hit their target op count well before
# the time budget expires.
OPS_TARGET_UNCONSTRAINED = int(os.environ.get("BENCH_OPS_TARGET_UNCONSTRAINED", "4000"))
OPS_TARGET_10KB = int(os.environ.get("BENCH_OPS_TARGET_10KB", "750"))
OPS_FLOOR = int(os.environ.get("BENCH_OPS_FLOOR", "50"))
TIME_BUDGET_SECONDS = float(os.environ.get("BENCH_TIME_BUDGET_SECONDS", "120"))

WARMUP_OPS_UNCONSTRAINED = int(os.environ.get("BENCH_WARMUP_OPS_UNCONSTRAINED", "20"))
WARMUP_OPS_10KB = int(os.environ.get("BENCH_WARMUP_OPS_10KB", "5"))

# An idle provisioned table accumulates up to ~5 min of unused capacity as
# burst credit, which can push observed throughput above the nominal 25/25
# ceiling right after table creation or a gap. Run a small throwaway load
# against the 10KB tier before measuring it, so burst credit doesn't get
# misread as contradicting the ceiling math.
BURST_CREDIT_DRAIN_OPS = int(os.environ.get("BENCH_BURST_CREDIT_DRAIN_OPS", "30"))

# Retries pinned off on the AWS side to match our own httpx client, which
# does not auto-retry on a 503 -- keeps both sides "first attempt only" so
# neither system's tail latency is silently inflated by SDK backoff sleep.
AWS_MAX_ATTEMPTS = int(os.environ.get("BENCH_AWS_MAX_ATTEMPTS", "1"))
CONNECTION_POOL_SIZE = int(os.environ.get("BENCH_CONNECTION_POOL_SIZE", "10"))

REPORTS_DIR_NAME = "reports"


def op_count_target(size_bytes: int) -> int:
    return OPS_TARGET_10KB if size_bytes >= 10240 else OPS_TARGET_UNCONSTRAINED


def warmup_ops(size_bytes: int) -> int:
    return WARMUP_OPS_10KB if size_bytes >= 10240 else WARMUP_OPS_UNCONSTRAINED
