"""Phase 8 benchmark condition runner. One function per (system x operation
x size x consistency) condition. Each condition:
  1. opens exactly one persistent client (reused for every op in that
     condition -- no per-request reconnect on either side),
  2. seeds/rotates a small fixed pool of keys so storage stays bounded and
     GET conditions read real, pre-existing data rather than immediately
     re-reading what was just written,
  3. discards a warm-up batch (excludes cold TCP/TLS handshake skew
     symmetrically on both systems),
  4. times every measured op with time.perf_counter() (monotonic,
     client-side only -- no cross-machine timestamp diffing, so clock skew
     is a non-issue by construction),
  5. runs until EITHER the target op count OR the time budget is reached
     (whichever first, never below the floor), and always reports the
     actual N achieved next to its percentiles.

The pure percentile/throughput math and the generic run loop live in
bench_stats.py (zero third-party imports), so tests/test_bench_percentiles.py
can exercise them without boto3/httpx installed. This module adds the
httpx/boto3-specific op functions on top.

This is a closed-loop benchmark: request N+1 is issued only after N's
response returns (single outstanding request per condition). That
under-represents tail latency during any slow period (coordinated
omission) -- a stated limitation, not hidden.
"""
import itertools
import random
import string
from typing import Any, Dict, List

import bench_config as cfg
import dynamodb_bench_aws as aws
import httpx
from bench_stats import percentile, run_condition  # noqa: F401  (percentile re-exported for the report)

POOL_SIZE = 50


def _random_value(size_bytes: int) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=size_bytes))


# ---------------------------------------------------------------------------
# Our system -- real public HTTPS API, full stack overhead included (mTLS
# fan-out, AES-256-GCM at rest, synchronous audit-log write, vector-clock
# reconcile). Deliberate: this is "our deployed system as it actually
# runs" vs. DynamoDB, not a stripped-down microbenchmark.
# ---------------------------------------------------------------------------


def _our_client() -> httpx.Client:
    return httpx.Client(
        verify=False,
        timeout=10.0,
        headers={"Authorization": f"Bearer {cfg.AUTH_TOKEN}"},
        limits=httpx.Limits(
            max_connections=cfg.CONNECTION_POOL_SIZE,
            max_keepalive_connections=cfg.CONNECTION_POOL_SIZE,
        ),
    )


def our_put_condition(size_bytes: int) -> Dict[str, Any]:
    with _our_client() as client:
        keys = [f"{cfg.NAMESPACE}:bench-put-{i}" for i in range(POOL_SIZE)]
        key_cycle = itertools.cycle(keys)

        def op() -> None:
            key = next(key_cycle)
            resp = client.put(f"{cfg.NODE_PUBLIC_URL}/v1/keys/{key}", json={"value": _random_value(size_bytes)})
            resp.raise_for_status()

        target_n = cfg.op_count_target(size_bytes)
        return run_condition(op, target_n, cfg.OPS_FLOOR, cfg.TIME_BUDGET_SECONDS, cfg.warmup_ops(size_bytes))


def our_get_condition(size_bytes: int) -> Dict[str, Any]:
    with _our_client() as client:
        keys = [f"{cfg.NAMESPACE}:bench-get-{i}" for i in range(POOL_SIZE)]
        for key in keys:
            resp = client.put(f"{cfg.NODE_PUBLIC_URL}/v1/keys/{key}", json={"value": _random_value(size_bytes)})
            resp.raise_for_status()
        key_cycle = itertools.cycle(keys)

        def op() -> None:
            key = next(key_cycle)
            resp = client.get(f"{cfg.NODE_PUBLIC_URL}/v1/keys/{key}")
            resp.raise_for_status()

        target_n = cfg.op_count_target(size_bytes)
        return run_condition(op, target_n, cfg.OPS_FLOOR, cfg.TIME_BUDGET_SECONDS, cfg.warmup_ops(size_bytes))


# ---------------------------------------------------------------------------
# DynamoDB
# ---------------------------------------------------------------------------


def dynamodb_put_condition(size_bytes: int) -> Dict[str, Any]:
    client = aws.build_client()
    keys = [f"bench-put-{i}" for i in range(POOL_SIZE)]
    key_cycle = itertools.cycle(keys)

    if size_bytes >= 10240:
        _drain_burst_credit(client, keys, size_bytes)

    def op() -> None:
        key = next(key_cycle)
        aws.put_item(client, cfg.DYNAMODB_TABLE_NAME, key, _random_value(size_bytes))

    target_n = cfg.op_count_target(size_bytes)
    return run_condition(op, target_n, cfg.OPS_FLOOR, cfg.TIME_BUDGET_SECONDS, cfg.warmup_ops(size_bytes))


def dynamodb_get_condition(size_bytes: int, consistent_read: bool) -> Dict[str, Any]:
    client = aws.build_client()
    keys = [f"bench-get-{i}" for i in range(POOL_SIZE)]
    for key in keys:
        aws.put_item(client, cfg.DYNAMODB_TABLE_NAME, key, _random_value(size_bytes))
    key_cycle = itertools.cycle(keys)

    if size_bytes >= 10240:
        _drain_burst_credit(client, keys, size_bytes)

    def op() -> None:
        key = next(key_cycle)
        aws.get_item(client, cfg.DYNAMODB_TABLE_NAME, key, consistent_read=consistent_read)

    target_n = cfg.op_count_target(size_bytes)
    return run_condition(op, target_n, cfg.OPS_FLOOR, cfg.TIME_BUDGET_SECONDS, cfg.warmup_ops(size_bytes))


def _drain_burst_credit(client, keys: List[str], size_bytes: int) -> None:
    """An idle provisioned table accumulates up to ~5 min of unused capacity
    as burst credit, which can push observed throughput above the nominal
    25/25 ceiling right after table creation or a gap. Run a small
    throwaway load first so the 10KB tier's numbers reflect steady-state
    capacity, not burst credit."""
    key_cycle = itertools.cycle(keys)
    for _ in range(cfg.BURST_CREDIT_DRAIN_OPS):
        aws.put_item(client, cfg.DYNAMODB_TABLE_NAME, next(key_cycle), _random_value(size_bytes))
