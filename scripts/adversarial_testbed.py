"""Phase 7 adversarial testbed CLI.

Usage:
    python scripts/gen_certs.py        # once, if certs/ doesn't exist yet
    docker compose up --build -d       # bring the cluster up
    python scripts/adversarial_testbed.py

Assumes the cluster is already running -- matches every Phase 2-6
precedent of manual, scripted verification rather than a self-managed
lifecycle. Never calls `docker compose up`/`down` itself, except scenario
3's own temporary network-topology override (applied and removed within
that scenario alone).

Writes reports/phase7_adversarial_report.md and
reports/phase7_adversarial_results.json, and prints a live summary plus
the final table to stdout.
"""
import json
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from adversarial_scenarios import ALL_SCENARIOS, Context, Result  # noqa: E402

PUBLIC_PORTS = {"node-1": 8001, "node-2": 8002, "node-3": 8003}
REPORTS_DIR = REPO_ROOT / "reports"


def preflight() -> Context:
    ca_cert = REPO_ROOT / "certs" / "ca.crt"
    if not ca_cert.exists():
        print(f"ERROR: {ca_cert} not found. Run `python scripts/gen_certs.py` first.")
        sys.exit(1)

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

    auth_tokens_path = REPO_ROOT / "auth_tokens.json"
    auth_tokens = json.loads(auth_tokens_path.read_text()) if auth_tokens_path.exists() else {}

    print("Preflight OK: all 3 nodes healthy, certs/ present.\n")
    return Context(node_ids=list(PUBLIC_PORTS), public_ports=PUBLIC_PORTS, auth_tokens=auth_tokens)


def run_all(ctx: Context) -> list:
    all_results = []
    for category, fn in ALL_SCENARIOS:
        print(f"--- Category {category}: {fn.__name__} ---")
        try:
            results = fn(ctx)
        except Exception as e:
            results = [
                Result(
                    category=category,
                    scenario=f"{fn.__name__} (harness error)",
                    expected="n/a",
                    observed=f"HARNESS ERROR: {type(e).__name__}: {e}",
                    prevented=False,
                    detected=False,
                )
            ]
        for r in results:
            status = "PASS" if r.prevented or r.category in ("4",) else "FINDING"
            print(f"  [{status}] {r.scenario}\n         expected={r.expected!r} observed={r.observed!r}")
            all_results.append(r)
        print()
    return all_results


def compute_rates(results: list) -> dict:
    total = len(results)
    prevented = sum(1 for r in results if r.prevented)
    detected = sum(1 for r in results if r.detected)
    return {
        "total_scenarios": total,
        "prevented": prevented,
        "detected": detected,
        "prevention_rate": prevented / total if total else 0.0,
        "detection_rate": detected / total if total else 0.0,
    }


def write_reports(results: list, rates: dict) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)

    json_path = REPORTS_DIR / "phase7_adversarial_results.json"
    json_path.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rates": rates,
        "results": [r.__dict__ for r in results],
    }, indent=2))

    lines = [
        "# Phase 7 Adversarial Testbed Report",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Headline rates",
        "",
        f"- **Prevention rate** = prevented / total_scenarios = {rates['prevented']}/{rates['total_scenarios']} = {rates['prevention_rate']:.0%}",
        f"- **Detection rate** = detected / total_scenarios = {rates['detected']}/{rates['total_scenarios']} = {rates['detection_rate']:.0%}",
        "",
        "Prevention and detection are measured independently (a scenario can be either, both, or neither) -- see PROGRESS.md's Phase 7 entry for why a single pass/fail column would understate the picture.",  # noqa: E501
        "",
        "## Results",
        "",
        "| Category | Scenario | Expected | Observed | Prevented? | Detected? |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.category} | {r.scenario} | {r.expected} | {r.observed} | "
            f"{'yes' if r.prevented else 'no'} | {'yes' if r.detected else 'no'} |"
        )

    lines += ["", "## Notes"]
    for r in results:
        if r.note:
            lines.append(f"- **{r.category} ({r.scenario})**: {r.note}")

    (REPORTS_DIR / "phase7_adversarial_report.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    ctx = preflight()
    results = run_all(ctx)
    rates = compute_rates(results)

    print("=" * 70)
    print(f"Prevention rate: {rates['prevented']}/{rates['total_scenarios']} = {rates['prevention_rate']:.0%}")
    print(f"Detection rate:  {rates['detected']}/{rates['total_scenarios']} = {rates['detection_rate']:.0%}")
    print("=" * 70)

    write_reports(results, rates)
    print(f"\nReports written to {REPORTS_DIR}/")


if __name__ == "__main__":
    main()
