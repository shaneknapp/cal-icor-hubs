#!/usr/bin/env python3
"""
Query Prometheus for concurrent JupyterHub user statistics across all prod hubs.

Requires an active port-forward to the Prometheus server:
    kubectl -n support port-forward deployment/support-prometheus-server 9090

Then run:
    python3 scripts/query_concurrent_users.py

Optional arguments:
    --days                Number of days to look back (default: 90)
    --step                Query resolution step for instant queries (default: 5m)
    --url                 Prometheus URL (default: http://localhost:9090)
    --threshold           User count threshold for "above N users" stats (default: 80). This is roughly the total users that a single node with ~64GB total ram can support.
    --timezone            IANA timezone for local time display, should match hub users' location
                          (default: America/Los_Angeles)
    --namespace-pattern   Prometheus regex to match hub namespaces (default: .*-prod)
    --save-report         Optionally save a report to scripts/reports/: text, markdown (or md), or html
    --config              Path to a YAML config file. Any key matching a CLI arg sets its default;
                          explicit CLI args always win.

Example config file (my-deployment.yaml):
    namespace_pattern: ".*-staging"
    timezone: "America/New_York"
    threshold: 60
"""

import argparse
import calendar
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from ruamel.yaml import YAML

DAYS_OF_WEEK = list(calendar.day_abbr)


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


def get_range_samples(url, days, tz_name, namespace_pattern):
    """
    Fetch total running servers at 30m resolution over the given number of days.
    Returns a list of (datetime_local, value) tuples, converted to tz_name.
    """
    end = int(time.time())
    start = end - days * 86400
    tz = ZoneInfo(tz_name)

    # 90 days at 30m = ~4320 samples, well within the 11000-point limit
    values = query_range(
        url,
        f'sum(jupyterhub_running_servers{{namespace=~"{namespace_pattern}"}})',
        start,
        end,
        step=1800,
    )

    samples = []
    for ts, val in values:
        v = max(int(float(val)), 0)  # clamp negatives from counter resets
        dt = datetime.fromtimestamp(int(ts), tz=tz)
        samples.append((dt, v))
    return samples


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def format_text(report):
    """Render report data as plain text (mirrors stdout output)."""
    days = report["days"]
    T = report["threshold"]
    T2 = report["threshold2"]
    lines = []

    lines.append("Cal-ICOR JupyterHub Concurrent User Report")
    lines.append(f"Generated: {report['generated']}")
    lines.append(
        f"Range: last {days} days  |  Threshold: {T} users per node"
        f"  |  Timezone: {report['timezone']}"
    )

    lines.append(f"\n{'=' * 60}")
    lines.append(f"  Overall statistics (last {days} days)")
    lines.append(f"{'=' * 60}")
    lines.append(f"  Peak concurrent users (all hubs): {report['overall']['peak']}")
    for a in report["overall"]["above"]:
        lines.append(
            f"  Fraction of time above {a['n']:>3} users: "
            f"{a['pct']:5.2f}%  (~{a['hours']:.1f} hours total)"
        )

    lines.append(f"\n{'=' * 60}")
    lines.append(f"  Per-hub peak concurrent users (last {days} days)")
    lines.append(f"{'=' * 60}")
    lines.append(f"  {'Hub':<28} {'Peak':>5}  Chart")
    lines.append(f"  {'-' * 28} {'-' * 5}  -----")
    for hub, val in report["hubs"]:
        bar = "#" * (val // 5)
        lines.append(f"  {hub:<28} {val:>5}  {bar}")

    hrs_t_col = f"Hrs>{T}"
    hrs_t2_col = f"Hrs>{T2}"
    lines.append(f"\n{'=' * 60}")
    lines.append("  Week-by-week breakdown (Mon-Sun, local time)")
    lines.append(f"{'=' * 60}")
    lines.append(
        f"  {'Week of':<12} {'Peak':>5}  {'Avg':>5}  "
        f"{hrs_t_col:>7}  {hrs_t2_col:>8}  Chart"
    )
    lines.append(f"  {'-' * 12} {'-' * 5}  {'-' * 5}  {'-' * 7}  {'-' * 8}  -----")
    for w in report["weeks"]:
        bar = "#" * (w["peak"] // 5)
        lines.append(
            f"  {w['week']:<12} {w['peak']:>5}  {w['avg']:>5.1f}  "
            f"{w['hrs_t']:>6.1f}h  {w['hrs_t2']:>7.1f}h  {bar}"
        )

    pct_col = f"Pct>{T}"
    lines.append(f"\n{'=' * 60}")
    lines.append("  Concurrent users by hour of day (local time)")
    lines.append(f"{'=' * 60}")
    lines.append(f"  {'Hour':<10} {'Peak':>5}  {'Avg':>5}  {pct_col:>8}  Chart")
    lines.append(f"  {'-' * 10} {'-' * 5}  {'-' * 5}  {'-' * 8}  -----")
    for h in report["hours"]:
        bar = "#" * (h["peak"] // 5)
        lines.append(
            f"  {h['label']:<10} {h['peak']:>5}  {h['avg']:>5.1f}"
            f"  {h['pct']:>7.1f}%  {bar}"
        )

    lines.append(f"\n{'=' * 60}")
    lines.append("  Concurrent users by day of week (local time)")
    lines.append(f"{'=' * 60}")
    lines.append(f"  {'Day':<5} {'Peak':>5}  {'Avg':>5}  {pct_col:>8}  Chart")
    lines.append(f"  {'-' * 5} {'-' * 5}  {'-' * 5}  {'-' * 8}  -----")
    for dow in report["days_of_week"]:
        bar = "#" * (dow["peak"] // 5)
        lines.append(
            f"  {dow['label']:<5} {dow['peak']:>5}  {dow['avg']:>5.1f}"
            f"  {dow['pct']:>7.1f}%  {bar}"
        )

    return "\n".join(lines)


def format_markdown(report):
    """Render report data as Markdown."""
    days = report["days"]
    T = report["threshold"]
    T2 = report["threshold2"]
    lines = []

    lines.append("# Cal-ICOR JupyterHub Concurrent User Report")
    lines.append("")
    lines.append(f"**Generated:** {report['generated']}  ")
    lines.append(
        f"**Range:** last {days} days  |  "
        f"**Threshold:** {T} users per node  |  "
        f"**Timezone:** {report['timezone']}"
    )
    lines.append("")

    lines.append(f"## Overall statistics (last {days} days)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Peak concurrent users | **{report['overall']['peak']}** |")
    for a in report["overall"]["above"]:
        lines.append(
            f"| Fraction of time above {a['n']} users"
            f" | {a['pct']:.2f}% (~{a['hours']:.1f} hours) |"
        )
    lines.append("")

    lines.append(f"## Per-hub peak concurrent users (last {days} days)")
    lines.append("")
    lines.append("| Hub | Peak |")
    lines.append("|---|---|")
    for hub, val in report["hubs"]:
        lines.append(f"| {hub} | {val} |")
    lines.append("")

    lines.append("## Week-by-week breakdown (Mon-Sun, local time)")
    lines.append("")
    lines.append(f"| Week of | Peak | Avg | Hrs > {T} | Hrs > {T2} |")
    lines.append("|---|---|---|---|---|")
    for w in report["weeks"]:
        lines.append(
            f"| {w['week']} | {w['peak']} | {w['avg']:.1f}"
            f" | {w['hrs_t']:.1f}h | {w['hrs_t2']:.1f}h |"
        )
    lines.append("")

    lines.append("## Concurrent users by hour of day (local time)")
    lines.append("")
    lines.append(f"| Hour | Peak | Avg | % > {T} |")
    lines.append("|---|---|---|---|")
    for h in report["hours"]:
        lines.append(
            f"| {h['label']} | {h['peak']} | {h['avg']:.1f} | {h['pct']:.1f}% |"
        )
    lines.append("")

    lines.append("## Concurrent users by day of week (local time)")
    lines.append("")
    lines.append(f"| Day | Peak | Avg | % > {T} |")
    lines.append("|---|---|---|---|")
    for dow in report["days_of_week"]:
        lines.append(
            f"| {dow['label']} | {dow['peak']} | {dow['avg']:.1f} | {dow['pct']:.1f}% |"
        )

    return "\n".join(lines)


def format_html(report):
    """Render report data as a self-contained HTML document."""
    days = report["days"]
    T = report["threshold"]
    T2 = report["threshold2"]

    def th(*cells):
        return "<tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>"

    def td(*cells):
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

    parts = []
    parts.append(
        f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Cal-ICOR Concurrent Users Report</title>
  <style>
    body {{ font-family: sans-serif; max-width: 960px; margin: 2em auto; padding: 0 1em; color: #222; }}
    h1 {{ font-size: 1.4em; }}
    h2 {{ font-size: 1.1em; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-top: 2em; }}
    p.meta {{ color: #666; font-size: 0.9em; }}
    table {{ border-collapse: collapse; margin: 1em 0; font-size: 0.9em; }}
    th, td {{ border: 1px solid #ddd; padding: 4px 12px; }}
    th {{ background: #f0f0f0; }}
    td {{ text-align: right; }}
    td:first-child {{ text-align: left; }}
    .bar {{ font-family: monospace; color: #999; }}
  </style>
</head>
<body>
<h1>Cal-ICOR JupyterHub Concurrent User Report</h1>
<p class="meta">
  Generated: {report["generated"]} &nbsp;|&nbsp;
  Range: last {days} days &nbsp;|&nbsp;
  Threshold: {T} users per node &nbsp;|&nbsp;
  Timezone: {report["timezone"]}
</p>"""
    )

    parts.append(f"<h2>Overall statistics (last {days} days)</h2><table>")
    parts.append(th("Metric", "Value"))
    parts.append(
        td("Peak concurrent users", f"<strong>{report['overall']['peak']}</strong>")
    )
    for a in report["overall"]["above"]:
        parts.append(
            td(
                f"Fraction of time above {a['n']} users",
                f"{a['pct']:.2f}% (~{a['hours']:.1f} hours)",
            )
        )
    parts.append("</table>")

    parts.append(f"<h2>Per-hub peak concurrent users (last {days} days)</h2><table>")
    parts.append(th("Hub", "Peak", "Chart"))
    for hub, val in report["hubs"]:
        bar = "#" * (val // 5)
        parts.append(td(hub, val, f'<span class="bar">{bar}</span>'))
    parts.append("</table>")

    parts.append("<h2>Week-by-week breakdown (Mon-Sun, local time)</h2><table>")
    parts.append(th("Week of", "Peak", "Avg", f"Hrs &gt; {T}", f"Hrs &gt; {T2}"))
    for w in report["weeks"]:
        parts.append(
            td(
                w["week"],
                w["peak"],
                f"{w['avg']:.1f}",
                f"{w['hrs_t']:.1f}h",
                f"{w['hrs_t2']:.1f}h",
            )
        )
    parts.append("</table>")

    parts.append("<h2>Concurrent users by hour of day (local time)</h2><table>")
    parts.append(th("Hour", "Peak", "Avg", f"% &gt; {T}"))
    for h in report["hours"]:
        parts.append(td(h["label"], h["peak"], f"{h['avg']:.1f}", f"{h['pct']:.1f}%"))
    parts.append("</table>")

    parts.append("<h2>Concurrent users by day of week (local time)</h2><table>")
    parts.append(th("Day", "Peak", "Avg", f"% &gt; {T}"))
    for dow in report["days_of_week"]:
        parts.append(
            td(dow["label"], dow["peak"], f"{dow['avg']:.1f}", f"{dow['pct']:.1f}%")
        )
    parts.append("</table>")

    parts.append("</body></html>")
    return "\n".join(parts)


def write_report(report, fmt, script_dir):
    """Write a formatted report to scripts/reports/."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    ext = {"text": "txt", "markdown": "md", "md": "md", "html": "html"}[fmt]
    path = script_dir / "reports" / f"concurrent-users-{date_str}.{ext}"

    formatters = {
        "text": format_text,
        "markdown": format_markdown,
        "md": format_markdown,
        "html": format_html,
    }
    path.write_text(formatters[fmt](report), encoding="utf-8")
    print(f"\nReport written to {path}")


def main(args):
    window = f"{args.days}d"
    subquery = f"[{window}:{args.step}]"
    ns_filter = f'namespace=~"{args.namespace_pattern}"'
    T = args.threshold
    T2 = T + 40

    print(f"Querying {args.url} over the last {args.days} days")
    print(f"Threshold: {T} users per node  |  Timezone: {args.timezone}\n")

    report = {
        "generated": datetime.now(ZoneInfo(args.timezone)).strftime(
            "%Y-%m-%d %H:%M %Z"
        ),
        "days": args.days,
        "threshold": T,
        "threshold2": T2,
        "timezone": args.timezone,
        "overall": {"peak": None, "above": []},
        "hubs": [],
        "weeks": [],
        "hours": [],
        "days_of_week": [],
    }

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
    report["overall"]["peak"] = peak

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
        report["overall"]["above"].append(
            {"n": n, "pct": frac * 100, "hours": hours_above}
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
        report["hubs"].append((hub, val))

    # -------------------------------------------------------------------------
    # Fetch range data once for the remaining analyses (30m resolution)
    # -------------------------------------------------------------------------
    samples = get_range_samples(
        args.url, args.days, args.timezone, args.namespace_pattern
    )

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
        report["weeks"].append(
            {"week": week, "peak": wpeak, "avg": wavg, "hrs_t": hrs_t, "hrs_t2": hrs_t2}
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
        report["hours"].append({"label": label, "peak": hpeak, "avg": havg, "pct": pct})

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
        report["days_of_week"].append(
            {"label": DAYS_OF_WEEK[d], "peak": dpeak, "avg": davg, "pct": pct}
        )

    # -------------------------------------------------------------------------
    # Optional: write report to scripts/reports/
    # -------------------------------------------------------------------------
    if args.save_report:
        write_report(report, args.save_report, Path(__file__).parent)


if __name__ == "__main__":
    # Pre-parse to get --config before setting up the full parser, so config
    # file values can be applied as defaults before argparse sees the rest.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("-c", "--config")
    pre_args, _ = pre.parse_known_args()

    config_defaults = {}
    if pre_args.config:
        _yaml = YAML(typ="safe")
        with open(pre_args.config) as f:
            config_defaults = _yaml.load(f) or {}

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-c", "--config", metavar="FILE", help="Path to a YAML config file"
    )
    parser.add_argument(
        "-d", "--days", type=int, default=90, help="Days to look back (default: 90)"
    )
    parser.add_argument(
        "--step",
        default="5m",
        help="Query resolution step for instant queries (default: 5m)",
    )
    parser.add_argument(
        "-u",
        "--url",
        default="http://localhost:9090",
        help="Prometheus URL (default: http://localhost:9090)",
    )
    parser.add_argument(
        "-t",
        "--threshold",
        type=int,
        default=80,
        help="User count threshold for percentage/hours stats (default: 80). This is roughly the total users that a single node with ~64GB total ram can support.",
    )
    parser.add_argument(
        "-z",
        "--timezone",
        default="America/Los_Angeles",
        help="IANA timezone for local time display, should match hub users' location (default: America/Los_Angeles)",
    )
    parser.add_argument(
        "-n",
        "--namespace-pattern",
        default=".*-prod",
        help="Prometheus regex to match hub namespaces (default: .*-prod)",
    )
    parser.add_argument(
        "-r",
        "--save-report",
        choices=["text", "markdown", "md", "html"],
        help="Save a report to scripts/reports/ in the specified format (md and markdown are equivalent)",
    )

    if config_defaults:
        parser.set_defaults(**config_defaults)

    main(parser.parse_args())
