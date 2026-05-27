#!/usr/bin/env python3
"""
Query Prometheus for concurrent JupyterHub user statistics across all prod hubs.

Requires an active port-forward to the Prometheus server:
    kubectl -n support port-forward deployment/support-prometheus-server 9090

Then run:
    python3 scripts/query_concurrent_users.py

Optional arguments:
    --days      Number of days to look back (default: 148)
    --step      Query step interval (default: 5m)
    --url       Prometheus URL (default: http://localhost:9090)
    --threshold User count threshold for "above N users" stats (default: 80)
"""

import argparse
import json
import sys
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen


def query(url, promql):
    """Run an instant Prometheus query and return parsed JSON."""
    params = urlencode({"query": promql})
    try:
        with urlopen(f"{url}/api/v1/query?{params}") as resp:
            return json.load(resp)
    except URLError as e:
        print(f"Error connecting to Prometheus at {url}: {e}")
        print(
            "Is the port-forward running?\n"
            "    kubectl -n support port-forward deployment/support-prometheus-server 9090"
        )
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--days", type=int, default=148, help="Days to look back (default: 148)"
    )
    parser.add_argument(
        "--step", default="5m", help="Query resolution step (default: 5m)"
    )
    parser.add_argument(
        "--url",
        default="http://localhost:9090",
        help="Prometheus URL (default: http://localhost:9090)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=80,
        help="User count threshold for percentage stats (default: 80)",
    )
    args = parser.parse_args()

    window = f"{args.days}d"
    subquery = f"[{window}:{args.step}]"
    ns_filter = 'namespace=~".*-prod"'

    print(f"Querying {args.url} over the last {args.days} days (step={args.step})\n")

    # --- 1. Overall peak ---
    data = query(
        args.url,
        f"max_over_time(sum(jupyterhub_running_servers{{{ns_filter}}}){subquery})",
    )
    peak = int(data["data"]["result"][0]["value"][1])
    print(f"Peak concurrent users (all hubs): {peak}")

    # --- 2. Fraction of time above threshold ---
    for n in [args.threshold, args.threshold + 40]:
        data = query(
            args.url,
            f"sum_over_time((sum(jupyterhub_running_servers{{{ns_filter}}}) > bool {n}){subquery})"
            f" / count_over_time(sum(jupyterhub_running_servers{{{ns_filter}}}){subquery})",
        )
        frac = float(data["data"]["result"][0]["value"][1])
        total_minutes = args.days * 24 * 60
        minutes_above = frac * total_minutes
        hours_above = minutes_above / 60
        print(
            f"Fraction of time above {n} users: {frac:.4f} ({frac * 100:.2f}%) ~= {hours_above:.1f} hours"
        )

    # --- 3. Per-hub peak (deduplicated) ---
    data = query(
        args.url, f"max_over_time(jupyterhub_running_servers{{{ns_filter}}}{subquery})"
    )
    hubs = {}
    for r in data["data"]["result"]:
        ns = r["metric"].get("namespace", "unknown")
        val = int(r["value"][1])
        if val > hubs.get(ns, 0):
            hubs[ns] = val

    print(f"\nPer-hub peak concurrent users (top hubs, last {args.days} days):")
    print(f"  {'Hub':<28} {'Peak':>6}  Chart")
    print(f"  {'-' * 28} {'-' * 6}  -----")
    for hub, val in sorted(hubs.items(), key=lambda x: -x[1]):
        if val == 0:
            continue
        bar = "#" * (val // 5)
        print(f"  {hub:<28} {val:>6}  {bar}")


if __name__ == "__main__":
    main()
