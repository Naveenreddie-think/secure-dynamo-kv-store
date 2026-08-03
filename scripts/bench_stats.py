"""Pure percentile/throughput math + the generic condition-runner loop for
Phase 8 benchmarking. Deliberately has ZERO third-party imports (no boto3,
no httpx) so tests/test_bench_percentiles.py can exercise it without either
dependency installed -- dynamodb_bench_conditions.py imports this module
and adds the httpx/boto3-specific op functions on top.
"""
import math
import time
from typing import Any, Callable, Dict, List


def percentile(sorted_values: List[float], pct: float) -> float:
    """Linear-interpolation percentile (same convention as numpy's default),
    over an already-sorted list."""
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def compute_stats(latencies_ms: List[float], elapsed_s: float) -> Dict[str, float]:
    n = len(latencies_ms)
    s = sorted(latencies_ms)
    return {
        "n": n,
        "p50_ms": percentile(s, 50),
        "p95_ms": percentile(s, 95),
        "p99_ms": percentile(s, 99),
        "mean_ms": (sum(s) / n) if n else float("nan"),
        "throughput_ops_sec": (n / elapsed_s) if elapsed_s > 0 else float("nan"),
    }


def run_condition(
    op_fn: Callable[[], None],
    target_n: int,
    floor_n: int,
    time_budget_s: float,
    warmup_n: int,
) -> Dict[str, Any]:
    """Runs op_fn in a closed loop (request N+1 issued only after N
    returns) until EITHER target_n measured ops OR time_budget_s has
    elapsed, whichever first -- but never fewer than floor_n. Always
    reports the actual N achieved."""
    for _ in range(warmup_n):
        op_fn()

    latencies_ms: List[float] = []
    start = time.perf_counter()
    while True:
        t0 = time.perf_counter()
        op_fn()
        latencies_ms.append((time.perf_counter() - t0) * 1000)
        elapsed = time.perf_counter() - start
        if len(latencies_ms) >= target_n:
            break
        if elapsed >= time_budget_s and len(latencies_ms) >= floor_n:
            break

    elapsed = time.perf_counter() - start
    stats = compute_stats(latencies_ms, elapsed)
    stats["time_budget_hit"] = elapsed >= time_budget_s and stats["n"] < target_n
    stats["target_n"] = target_n
    return stats
