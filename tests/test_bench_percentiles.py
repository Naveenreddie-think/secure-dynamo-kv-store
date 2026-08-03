"""Fast, deterministic, no-network tests for the Phase 8 benchmark's
percentile/throughput math and run-loop bookkeeping (bench_stats.py).
Deliberately does NOT touch dynamodb_bench_conditions.py/dynamodb_bench_aws.py
(those require httpx/boto3, which are optional 'bench'-extras deps, not
installed by default) -- mirrors test_adversarial_mechanisms.py's role of
proving pure logic independently of any live run.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from bench_stats import compute_stats, percentile, run_condition  # noqa: E402


def test_percentile_empty_list_is_nan():
    assert percentile([], 50) != percentile([], 50)  # NaN != NaN


def test_percentile_single_value():
    assert percentile([42.0], 50) == 42.0
    assert percentile([42.0], 99) == 42.0


def test_percentile_p50_matches_median_odd_count():
    values = sorted([1.0, 2.0, 3.0, 4.0, 5.0])
    assert percentile(values, 50) == 3.0


def test_percentile_p100_is_max():
    values = sorted([5.0, 1.0, 3.0, 2.0, 4.0])
    assert percentile(values, 100) == 5.0


def test_percentile_p0_is_min():
    values = sorted([5.0, 1.0, 3.0, 2.0, 4.0])
    assert percentile(values, 0) == 1.0


def test_percentile_interpolates_between_neighbors():
    # 5 sorted values, indices 0-4. p75 -> k = 4 * 0.75 = 3.0 (exact index 3).
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert percentile(values, 75) == 40.0
    # p60 -> k = 4 * 0.6 = 2.4 -> interpolate between index 2 (30) and 3 (40)
    assert abs(percentile(values, 60) - 34.0) < 1e-9


def test_compute_stats_reports_n_and_throughput():
    latencies = [10.0, 20.0, 30.0, 40.0, 50.0]
    stats = compute_stats(latencies, elapsed_s=1.0)
    assert stats["n"] == 5
    assert stats["p50_ms"] == 30.0
    assert stats["mean_ms"] == 30.0
    assert stats["throughput_ops_sec"] == 5.0


def test_compute_stats_zero_elapsed_gives_nan_throughput():
    stats = compute_stats([1.0, 2.0], elapsed_s=0.0)
    assert stats["throughput_ops_sec"] != stats["throughput_ops_sec"]  # NaN


def test_run_condition_stops_at_target_n_when_fast():
    calls = {"n": 0}

    def op():
        calls["n"] += 1

    stats = run_condition(op, target_n=10, floor_n=1, time_budget_s=10.0, warmup_n=3)
    assert stats["n"] == 10
    assert calls["n"] == 13  # 3 warm-up + 10 measured
    assert stats["time_budget_hit"] is False


def test_run_condition_respects_floor_even_if_time_budget_expires_immediately():
    calls = {"n": 0}

    def op():
        calls["n"] += 1

    # time_budget_s=0 means the budget is already "expired" on the very
    # first check, but floor_n=5 forces at least 5 measured ops anyway.
    stats = run_condition(op, target_n=1000, floor_n=5, time_budget_s=0.0, warmup_n=0)
    assert stats["n"] >= 5
    assert stats["time_budget_hit"] is True


def test_run_condition_reports_actual_n_next_to_target():
    def op():
        pass

    stats = run_condition(op, target_n=7, floor_n=1, time_budget_s=10.0, warmup_n=0)
    assert stats["target_n"] == 7
    assert stats["n"] == 7
