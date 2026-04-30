"""
aggregate_timeseries.py — Cross-run time-series charts with mean ± std bands.

Produces the same chart layouts as plot_prometheus.py (per-run) but each line is
the mean across all runs of an experiment, with a shaded band showing ±1 std.

Usage:
    python3 aggregate_timeseries.py \
        --experiment stage5-hpa-tuned \
        --results-dir testing/results \
        --prometheus-url http://127.0.0.1:30090 \
        --output-dir testing/results/analysis-stage5-hpa-tuned

Charts generated:
    timeseries_throughput_error.png  — RPS + error rate over time
    timeseries_latency.png           — p50/p95/p99 over time
    timeseries_cpu_replicas.png      — replica count + CPU% over time
    timeseries_memory.png            — web/jobs memory over time
    timeseries_hpa_cpu.png           — HPA CPU target metric over time
"""

import argparse
import csv
import datetime as dt
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import requests


# ── helpers ───────────────────────────────────────────────────────────────────

def load_env_file(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def query_range(base_url, query, start, end, step):
    """Query Prometheus and return list of (timestamp, value) tuples."""
    try:
        r = requests.get(
            f"{base_url}/api/v1/query_range",
            params={
                "query": query,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "step": step,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()["data"]["result"]
    except Exception as e:
        print(f"    WARN: query failed: {e}")
        return []
    if not data:
        return []
    # take first series
    return [(dt.datetime.fromtimestamp(float(t), dt.UTC), float(v))
            for t, v in data[0]["values"]]


def try_queries(base_url, queries, start, end, step):
    """Try queries in order; return first non-empty series."""
    for q in queries:
        s = query_range(base_url, q, start, end, step)
        if s:
            return s
    return []


# ── core: align run series to a common grid ──────────────────────────────────

def to_relative(series, started_at):
    """Convert [(ts, val), ...] → [(seconds_from_start, val), ...]."""
    return [((t - started_at).total_seconds(), v) for t, v in series]


def resample_to_grid(rel_series, grid_seconds):
    """Linear-interpolate a (sec, val) series onto a regular time grid.

    Returns numpy array same length as grid; NaN where extrapolated.
    """
    if not rel_series:
        return np.full(len(grid_seconds), np.nan)
    xs = np.array([p[0] for p in rel_series])
    ys = np.array([p[1] for p in rel_series])
    out = np.interp(grid_seconds, xs, ys, left=np.nan, right=np.nan)
    return out


def aggregate_runs(per_run_series_list, grid_seconds):
    """Stack runs onto common grid, return (mean, std, n_valid_per_bin)."""
    rows = [resample_to_grid(s, grid_seconds) for s in per_run_series_list]
    arr = np.array(rows)  # shape (n_runs, n_bins)
    if arr.size == 0:
        return None, None, None
    with np.errstate(all="ignore"):
        mean = np.nanmean(arr, axis=0)
        std  = np.nanstd(arr, axis=0)
        n    = np.sum(~np.isnan(arr), axis=0)
    return mean, std, n


# ── per-metric query helpers ──────────────────────────────────────────────────

def q_throughput(testid):
    return [f'sum(rate(k6_http_reqs_total{{testid="{testid}"}}[1m]))']


def q_error_rate(testid):
    return [
        f'100 * sum(rate(k6_http_reqs_total{{expected_response="false",testid="{testid}"}}[1m])) / sum(rate(k6_http_reqs_total{{testid="{testid}"}}[1m]))',
        f'100 * avg_over_time(k6_http_req_failed{{testid="{testid}"}}[2m])',
    ]


def q_latency(testid, pct):
    """pct: 'p50', 'p95', 'p99' → returns avg over the percentile metric."""
    return [f'avg(k6_http_req_duration_{pct}{{testid="{testid}"}})']


def q_web_memory_mb():
    return [
        'sum(container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"} * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}) / 1000000',
        'sum(container_memory_working_set_bytes{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-web-.*",container!="",container!="POD"}) / 1000000',
    ]


def q_jobs_memory_mb():
    return [
        'sum(container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-jobs-.*",container!="",container!="POD"} * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}) / 1000000',
        'sum(container_memory_working_set_bytes{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-jobs-.*",container!="",container!="POD"}) / 1000000',
    ]


def q_web_cpu_percent_of_request():
    return [
        '100 * sum(rate(container_cpu_usage_seconds_total{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"}[2m])) / sum(kube_pod_container_resource_requests{namespace="canvas",resource="cpu",pod=~"canvas-web-.*",container!="",container!="POD"})',
    ]


# ── snapshot CSV: replica counts, restart counts ──────────────────────────────

def read_snapshots_csv(path: Path, started_at, column):
    """Return [(seconds_from_start, value)] from a column in k8s-snapshots.csv."""
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_raw = row.get("timestamp", "")
            if not ts_raw:
                continue
            try:
                t = parse_ts(ts_raw)
                v = float(row.get(column, 0))
                out.append(((t - started_at).total_seconds(), v))
            except Exception:
                continue
    return out


# ── plotting ──────────────────────────────────────────────────────────────────

def plot_band(ax, grid, agg, label, color, show_band=True):
    """agg is the (mean, std, n) tuple returned by aggregate_runs."""
    if agg is None or agg[0] is None:
        return
    mean, std, _ = agg
    minutes = grid / 60.0
    ax.plot(minutes, mean, label=label, color=color, linewidth=2)
    if show_band and std is not None:
        ax.fill_between(minutes, mean - std, mean + std,
                        alpha=0.20, color=color, linewidth=0)


def plot_throughput_error(grid, tput, err, output, experiment, n_runs):
    fig, ax1 = plt.subplots(figsize=(11, 5))
    plot_band(ax1, grid, tput, "Throughput (RPS)", "#1f77b4")
    ax1.set_xlabel("Minutes from test start")
    ax1.set_ylabel("Requests/sec", color="#1f77b4")
    ax1.tick_params(axis="y", labelcolor="#1f77b4")
    ax1.grid(True, alpha=0.3)

    ax2 = ax1.twinx()
    plot_band(ax2, grid, err, "Error rate (%)", "#d62728")
    ax2.set_ylabel("Error rate %", color="#d62728")
    ax2.tick_params(axis="y", labelcolor="#d62728")

    fig.suptitle(f"{experiment} — Throughput & Error Rate (mean ± std, n={n_runs})")
    fig.tight_layout()
    fig.savefig(output, dpi=130)
    plt.close(fig)
    print(f"  → {output}")


def plot_latency(grid, p50, p95, p99, output, experiment, n_runs):
    fig, ax = plt.subplots(figsize=(11, 5))
    plot_band(ax, grid, p50, "p50",  "#2ca02c")
    plot_band(ax, grid, p95, "p95",  "#ff7f0e")
    plot_band(ax, grid, p99, "p99",  "#d62728")
    ax.set_xlabel("Minutes from test start")
    ax.set_ylabel("Latency (ms)")
    ax.set_yscale("log")
    ax.grid(True, alpha=0.3, which="both")
    ax.legend(loc="upper left")
    fig.suptitle(f"{experiment} — Response Time Percentiles (mean ± std, n={n_runs})")
    fig.tight_layout()
    fig.savefig(output, dpi=130)
    plt.close(fig)
    print(f"  → {output}")


def plot_cpu_replicas(grid, replicas, cpu_pct, output, experiment, n_runs):
    fig, ax1 = plt.subplots(figsize=(11, 5))
    plot_band(ax1, grid, replicas, "Web replicas", "#1f77b4")
    ax1.set_xlabel("Minutes from test start")
    ax1.set_ylabel("Replicas", color="#1f77b4")
    ax1.tick_params(axis="y", labelcolor="#1f77b4")
    ax1.grid(True, alpha=0.3)

    ax2 = ax1.twinx()
    plot_band(ax2, grid, cpu_pct, "CPU % of request", "#d62728")
    ax2.axhline(70, color="#d62728", linestyle="--", linewidth=1, alpha=0.5,
                label="HPA target 70%")
    ax2.set_ylabel("CPU %", color="#d62728")
    ax2.tick_params(axis="y", labelcolor="#d62728")
    ax2.legend(loc="upper right")

    fig.suptitle(f"{experiment} — Replicas & CPU% (mean ± std, n={n_runs})")
    fig.tight_layout()
    fig.savefig(output, dpi=130)
    plt.close(fig)
    print(f"  → {output}")


def plot_memory(grid, web_mem, jobs_mem, output, experiment, n_runs):
    fig, ax = plt.subplots(figsize=(11, 5))
    plot_band(ax, grid, web_mem,  "Web memory (MB)",  "#1f77b4")
    plot_band(ax, grid, jobs_mem, "Jobs memory (MB)", "#2ca02c")
    ax.set_xlabel("Minutes from test start")
    ax.set_ylabel("Memory (MB)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")
    fig.suptitle(f"{experiment} — Memory Working Set (mean ± std, n={n_runs})")
    fig.tight_layout()
    fig.savefig(output, dpi=130)
    plt.close(fig)
    print(f"  → {output}")


# ── main ──────────────────────────────────────────────────────────────────────

def discover_runs(results_dir: Path, experiment: str):
    """Find all subdirectories matching <experiment>-*-run* with metadata.env."""
    runs = []
    for d in sorted(results_dir.iterdir()):
        if not d.is_dir():
            continue
        if not d.name.startswith(experiment + "-"):
            continue
        if "-run" not in d.name:
            continue
        meta = load_env_file(d / "metadata.env")
        if "started_at" not in meta or "ended_at" not in meta:
            continue
        runs.append({
            "dir": d,
            "test_id": meta["test_id"],
            "started_at": parse_ts(meta["started_at"]),
            "ended_at":   parse_ts(meta["ended_at"]),
        })
    return runs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True,
                        help="Experiment name prefix, e.g. stage5-hpa-tuned")
    parser.add_argument("--results-dir", default="testing/results")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:30090")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--step-seconds", type=int, default=15,
                        help="Time grid step in seconds (default 15)")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    output_dir = Path(args.output_dir or
                      results_dir / f"analysis-{args.experiment}")
    output_dir.mkdir(parents=True, exist_ok=True)

    runs = discover_runs(results_dir, args.experiment)
    if not runs:
        print(f"No runs found matching {args.experiment} in {results_dir}")
        return
    print(f"Found {len(runs)} runs for experiment '{args.experiment}':")
    for r in runs:
        print(f"  - {r['test_id']}")
    print()

    # Build a common time grid covering the longest run
    max_duration = max((r["ended_at"] - r["started_at"]).total_seconds() for r in runs)
    grid = np.arange(0, max_duration + args.step_seconds, args.step_seconds)
    print(f"Time grid: 0 to {max_duration:.0f} s in {args.step_seconds} s steps "
          f"({len(grid)} bins)\n")

    # Collect per-run series for each metric
    metrics = {
        "throughput": [], "error_rate": [],
        "p50": [], "p95": [], "p99": [],
        "replicas": [], "cpu_pct": [],
        "web_memory": [], "jobs_memory": [],
    }

    step_str = f"{args.step_seconds}s"

    for r in runs:
        tid = r["test_id"]
        s, e = r["started_at"], r["ended_at"]
        print(f"Querying metrics for {tid}...")

        # k6 metrics from Prometheus
        thr = try_queries(args.prometheus_url, q_throughput(tid),  s, e, step_str)
        err = try_queries(args.prometheus_url, q_error_rate(tid),  s, e, step_str)
        p50 = try_queries(args.prometheus_url, q_latency(tid, "p50"), s, e, step_str)
        p95 = try_queries(args.prometheus_url, q_latency(tid, "p95"), s, e, step_str)
        p99 = try_queries(args.prometheus_url, q_latency(tid, "p99"), s, e, step_str)

        # Cluster metrics from Prometheus
        wmem = try_queries(args.prometheus_url, q_web_memory_mb(),  s, e, step_str)
        jmem = try_queries(args.prometheus_url, q_jobs_memory_mb(), s, e, step_str)
        cpu  = try_queries(args.prometheus_url, q_web_cpu_percent_of_request(), s, e, step_str)

        # Replica count from local snapshots CSV (more reliable)
        snap_csv = r["dir"] / "k8s-snapshots.csv"
        rep = read_snapshots_csv(snap_csv, s, "web_ready_replicas")

        metrics["throughput"].append(to_relative(thr, s))
        metrics["error_rate"].append(to_relative(err, s))
        metrics["p50"].append(to_relative(p50, s))
        metrics["p95"].append(to_relative(p95, s))
        metrics["p99"].append(to_relative(p99, s))
        metrics["replicas"].append(rep)
        metrics["cpu_pct"].append(to_relative(cpu, s))
        metrics["web_memory"].append(to_relative(wmem, s))
        metrics["jobs_memory"].append(to_relative(jmem, s))

    # Aggregate
    print("\nAggregating across runs...")
    agg = {k: aggregate_runs(v, grid) for k, v in metrics.items()}

    # Plot
    print("\nGenerating charts...")
    n = len(runs)
    plot_throughput_error(grid, agg["throughput"], agg["error_rate"],
                          output_dir / "timeseries_throughput_error.png",
                          args.experiment, n)
    plot_latency(grid, agg["p50"], agg["p95"], agg["p99"],
                 output_dir / "timeseries_latency.png",
                 args.experiment, n)
    plot_cpu_replicas(grid, agg["replicas"], agg["cpu_pct"],
                      output_dir / "timeseries_cpu_replicas.png",
                      args.experiment, n)
    plot_memory(grid, agg["web_memory"], agg["jobs_memory"],
                output_dir / "timeseries_memory.png",
                args.experiment, n)

    print(f"\nDone. Charts written to {output_dir}/timeseries_*.png")


if __name__ == "__main__":
    main()
