#!/usr/bin/env python3
"""
Query Prometheus for concurrent JupyterHub user statistics across all prod hubs.

Requires an active port-forward to the Prometheus server:
    kubectl -n support port-forward deployment/support-prometheus-server 9090

Then run:
    python3 scripts/query_concurrent_users.py

Optional arguments:
    --days      Number of days to look back (default: 148)
    --step      Query resolution step for instant queries (default: 5m)
    --url       Prometheus URL (default: http://localhost:9090)
    --threshold User count threshold for "above N users" stats (default: 80). This is roughly the total users that a single node with ~64GB total ram can support.
    --tz-offset Hours offset from UTC for local time display (default: -7 for PDT)
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


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


def query_range(url, promql, start, end, step):
    """Run a Prometheus range query and return the list of (timestamp, value) pairs."""
    params = urlencode({"query": promql, "start": start, "end": end, "step": step})
    try:
        with urlopen(f"{url}/api/v1/query_range?{params}") as resp:
            data = json.load(resp)
    except URLError as e:
        print(f"Error connecting to Prometheus at {url}: {e}")
        print(
            "Is the port-forward running?\n"
            "    kubectl -n support port-forward deployment/support-prometheus-server 9090"
        )
        sys.exit(1)

    if data.get("status") != "success":
        print(f"Prometheus error: {data.get('error', 'unknown')}")
        sys.exit(1)

    return data["data"]["result"][0]["values"]


def get_range_samples(url, days, tz_offset_hours):
    """
    Fetch total running servers at 30m resolution over the given number of days.
    Returns a list of (datetime_local, value) tuples.
    """
    end = int(time.time())
    start = end - days * 86400
    tz_offset_secs = tz_offset_hours * 3600

    # 148 days at 30m = ~7104 samples, well within the 11000-point limit
    values = query_range(
        url,
        'sum(jupyterhub_running_servers{namespace=~".*-prod"})',
        start,
        end,
        step=1800,
    )

    samples = []
    for ts, val in values:
        v = max(int(float(val)), 0)  # clamp negatives from counter resets
        local_ts = int(ts) + tz_offset_secs
        dt = datetime.fromtimestamp(local_ts, tz=timezone.utc)
        samples.append((dt, v))
    return samples


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--days", type=int, default=148, help="Days to look back (default: 148)"
    )
    parser.add_argument(
        "--step",
        default="5m",
        help="Query resolution step for instant queries (default: 5m)",
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
        help="User count threshold for percentage/hours stats (default: 80). This is roughly the total users that a single node with ~64GB total ram can support.",
    )
    parser.add_argument(
        "--tz-offset",
        type=int,
        default=-7,
        help="Hours offset from UTC for local time display (default: -7 for PDT)",
    )
    args = parser.parse_args()

    window = f"{args.days}d"
    subquery = f"[{window}:{args.step}]"
    ns_filter = 'namespace=~".*-prod"'
    T = args.threshold
    T2 = T + 40

    print(f"Querying {args.url} over the last {args.days} days")
    print(f"Threshold: {T} users  |  UTC offset: {args.tz_offset:+d}h\n")

    # -------------------------------------------------------------------------
    # Section 1: Overall peak + time-above-threshold
    # -------------------------------------------------------------------------
    section(f"Overall statistics (last {args.days} days)")

    data = query(
        args.url,
        f"max_over_time(sum(jupyterhub_running_servers{{{ns_filter}}}){subquery})",
    )
    peak = int(data["data"]["result"][0]["value"][1])
    print(f"  Peak concurrent users (all hubs): {peak}")

    for n in [T, T2]:
        data = query(
            args.url,
            f"sum_over_time((sum(jupyterhub_running_servers{{{ns_filter}}}) > bool {n}){subquery})"
            f" / count_over_time(sum(jupyterhub_running_servers{{{ns_filter}}}){subquery})",
        )
        frac = float(data["data"]["result"][0]["value"][1])
        hours_above = frac * args.days * 24
        print(
            f"  Fraction of time above {n:>3} users: "
            f"{frac * 100:5.2f}%  (~{hours_above:.1f} hours total)"
        )

    # -------------------------------------------------------------------------
    # Section 2: Per-hub peak
    # -------------------------------------------------------------------------
    section(f"Per-hub peak concurrent users (last {args.days} days)")

    data = query(
        args.url, f"max_over_time(jupyterhub_running_servers{{{ns_filter}}}{subquery})"
    )
    hubs = {}
    for r in data["data"]["result"]:
        ns = r["metric"].get("namespace", "unknown")
        val = int(r["value"][1])
        if val > hubs.get(ns, 0):
            hubs[ns] = val

    print(f"  {'Hub':<28} {'Peak':>5}  Chart")
    print(f"  {'-' * 28} {'-' * 5}  -----")
    for hub, val in sorted(hubs.items(), key=lambda x: -x[1]):
        if val == 0:
            continue
        bar = "#" * (val // 5)
        print(f"  {hub:<28} {val:>5}  {bar}")

    # -------------------------------------------------------------------------
    # Fetch range data once for the remaining analyses (30m resolution)
    # -------------------------------------------------------------------------
    samples = get_range_samples(args.url, args.days, args.tz_offset)

    # -------------------------------------------------------------------------
    # Section 3: Week-by-week breakdown
    # -------------------------------------------------------------------------
    section("Week-by-week breakdown (Mon–Sun, local time)")

    week_samples = defaultdict(list)
    for dt, v in samples:
        week_start = dt - timedelta(days=dt.weekday())
        week_samples[week_start.strftime("%Y-%m-%d")].append(v)

    print(
        f"  {'Week of':<12} {'Peak':>5}  {'Avg':>5}  "
        f"{'Hrs>{T}':>7}  {'Hrs>{T2}':>8}  Chart"
    )
    print(f"  {'-' * 12} {'-' * 5}  {'-' * 5}  {'-' * 7}  {'-' * 8}  -----")
    for week in sorted(week_samples):
        s = week_samples[week]
        wpeak = max(s)
        wavg = sum(s) / len(s)
        # each sample = 30 min
        hrs_t = sum(1 for v in s if v > T) * 0.5
        hrs_t2 = sum(1 for v in s if v > T2) * 0.5
        bar = "#" * (wpeak // 5)
        print(
            f"  {week:<12} {wpeak:>5}  {wavg:>5.1f}  "
            f"{hrs_t:>6.1f}h  {hrs_t2:>7.1f}h  {bar}"
        )

    # -------------------------------------------------------------------------
    # Section 4: Hour-of-day breakdown
    # -------------------------------------------------------------------------
    section("Concurrent users by hour of day (local time)")

    hour_samples = defaultdict(list)
    for dt, v in samples:
        hour_samples[dt.hour].append(v)

    print(f"  {'Hour':<10} {'Peak':>5}  {'Avg':>5}  {'Pct>{T}':>8}  Chart")
    print(f"  {'-' * 10} {'-' * 5}  {'-' * 5}  {'-' * 8}  -----")
    for h in range(24):
        s = hour_samples[h]
        if not s:
            continue
        hpeak = max(s)
        havg = sum(s) / len(s)
        pct = sum(1 for v in s if v > T) / len(s) * 100
        bar = "#" * (hpeak // 5)
        label = f"{h:02d}:00-{h + 1:02d}:00"
        print(f"  {label:<10} {hpeak:>5}  {havg:>5.1f}  {pct:>7.1f}%  {bar}")

    # -------------------------------------------------------------------------
    # Section 5: Day-of-week breakdown
    # -------------------------------------------------------------------------
    section("Concurrent users by day of week (local time)")

    dow_samples = defaultdict(list)
    for dt, v in samples:
        dow_samples[dt.weekday()].append(v)

    print(f"  {'Day':<5} {'Peak':>5}  {'Avg':>5}  {'Pct>{T}':>8}  Chart")
    print(f"  {'-' * 5} {'-' * 5}  {'-' * 5}  {'-' * 8}  -----")
    for d in range(7):
        s = dow_samples[d]
        if not s:
            continue
        dpeak = max(s)
        davg = sum(s) / len(s)
        pct = sum(1 for v in s if v > T) / len(s) * 100
        bar = "#" * (dpeak // 5)
        print(f"  {DAYS_OF_WEEK[d]:<5} {dpeak:>5}  {davg:>5.1f}  {pct:>7.1f}%  {bar}")


if __name__ == "__main__":
    main()
