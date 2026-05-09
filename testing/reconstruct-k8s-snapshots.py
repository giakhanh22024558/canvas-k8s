#!/usr/bin/env python3
"""Reconstruct testing/results/<run>/k8s-snapshots.csv from Prometheus history.

Use this when collect-k8s-snapshots.sh did not run during a test (e.g. earlier
two-host runs where the load gen had no kubectl access). All 13 columns of the
snapshot CSV correspond to metrics exposed by kube-state-metrics + cAdvisor,
which Prometheus has retained for the full test window — so a faithful
backfill is possible without re-running the test.

Usage:
    python3 testing/reconstruct-k8s-snapshots.py <run_folder> \\
        [--prometheus-url http://127.0.0.1:30090] [--step 5s]

The run folder must contain metadata.env with started_at and ended_at fields.
The reconstructed file is written to <run_folder>/k8s-snapshots.csv. Existing
file is overwritten.
"""
import argparse
import csv
import datetime as dt
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path


def parse_ts(s):
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return dt.datetime.fromisoformat(s)


def query_range(base_url, query, start, end, step):
    url = f"{base_url}/api/v1/query_range"
    params = urllib.parse.urlencode({
        "query": query,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "step": step,
    })
    req = urllib.request.Request(f"{url}?{params}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            import json
            data = json.load(resp)
        if data.get("status") != "success":
            return []
        result = data["data"]["result"]
        if not result:
            return []
        # Flatten the first series (we only ever query for a single aggregated
        # series like sum() or max()).
        return [(float(t), float(v) if v != "NaN" else 0.0)
                for t, v in result[0]["values"]]
    except (urllib.error.URLError, ValueError):
        return []


# Map column → PromQL. Each query is aggregated server-side so only a single
# (timestamp, value) series comes back per column.
QUERIES = {
    "web_ready_replicas":      'kube_deployment_status_replicas_ready{namespace="canvas",deployment="canvas-web"}',
    "web_available_replicas":  'kube_deployment_status_replicas_available{namespace="canvas",deployment="canvas-web"}',
    "web_spec_replicas":       'kube_deployment_spec_replicas{namespace="canvas",deployment="canvas-web"}',
    "jobs_ready_replicas":     'kube_deployment_status_replicas_ready{namespace="canvas",deployment="canvas-jobs"}',
    "jobs_available_replicas": 'kube_deployment_status_replicas_available{namespace="canvas",deployment="canvas-jobs"}',
    "jobs_spec_replicas":      'kube_deployment_spec_replicas{namespace="canvas",deployment="canvas-jobs"}',
    "web_hpa_current_replicas":  'kube_horizontalpodautoscaler_status_current_replicas{namespace="canvas",horizontalpodautoscaler="canvas-web"}',
    "web_hpa_desired_replicas":  'kube_horizontalpodautoscaler_status_desired_replicas{namespace="canvas",horizontalpodautoscaler="canvas-web"}',
    "jobs_hpa_current_replicas": 'kube_horizontalpodautoscaler_status_current_replicas{namespace="canvas",horizontalpodautoscaler="canvas-jobs"}',
    "jobs_hpa_desired_replicas": 'kube_horizontalpodautoscaler_status_desired_replicas{namespace="canvas",horizontalpodautoscaler="canvas-jobs"}',
    # Sum container restart counts across all pods for the deployment. The
    # snapshot script summed `restartCount` per pod via kubectl jsonpath; the
    # Prometheus equivalent sums across the matching pod regex.
    "web_restart_total":  'sum(kube_pod_container_status_restarts_total{namespace="canvas",pod=~"canvas-web-.*"}) or vector(0)',
    "jobs_restart_total": 'sum(kube_pod_container_status_restarts_total{namespace="canvas",pod=~"canvas-jobs-.*"}) or vector(0)',
}

COLUMNS = [
    "timestamp",
    "web_ready_replicas", "web_available_replicas", "web_spec_replicas",
    "jobs_ready_replicas", "jobs_available_replicas", "jobs_spec_replicas",
    "web_hpa_current_replicas", "web_hpa_desired_replicas",
    "jobs_hpa_current_replicas", "jobs_hpa_desired_replicas",
    "web_restart_total", "jobs_restart_total",
]


def reconstruct(run_dir, prometheus_url, step):
    metadata = {}
    meta_path = run_dir / "metadata.env"
    if not meta_path.exists():
        sys.exit(f"ERROR: {meta_path} not found")
    for line in meta_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            metadata[k.strip()] = v.strip()

    if "started_at" not in metadata or "ended_at" not in metadata:
        sys.exit(f"ERROR: metadata.env missing started_at or ended_at")

    start = parse_ts(metadata["started_at"])
    end = parse_ts(metadata["ended_at"])
    print(f"Run window: {start} → {end} ({(end - start).total_seconds():.0f}s)")

    # Query each metric. Series come back as (unix_ts, value) tuples sampled
    # at <step> intervals server-side.
    series = {}
    for col, query in QUERIES.items():
        data = query_range(prometheus_url, query, start, end, step)
        series[col] = dict(data)
        if not data:
            print(f"  WARN: no data for {col}")
        else:
            print(f"  {col}: {len(data)} samples")

    # Build a unified timestamp axis from any column that has data, so rows
    # align even when one metric has gaps.
    all_ts = set()
    for col_data in series.values():
        all_ts.update(col_data.keys())
    if not all_ts:
        sys.exit("ERROR: no Prometheus data found for the run window")
    all_ts = sorted(all_ts)
    print(f"Unified grid: {len(all_ts)} timestamps")

    out_path = run_dir / "k8s-snapshots.csv"
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        # Per-column running last-known value so missing samples carry the
        # previous value (matches the live collector's behaviour: it polls
        # every 5s, so values are step-functions held until the next change).
        last = {col: 0 for col in COLUMNS[1:]}
        for ts in all_ts:
            ts_iso = dt.datetime.utcfromtimestamp(ts).replace(
                tzinfo=dt.timezone.utc).isoformat()
            row = [ts_iso]
            for col in COLUMNS[1:]:
                if ts in series[col]:
                    last[col] = series[col][ts]
                # Replicas/restart counts are integers in the original CSV
                row.append(int(last[col]))
            w.writerow(row)

    print(f"\nWrote {out_path} ({len(all_ts)} rows)")
    print(f"Re-run charts: TEST_ID={run_dir.name} bash testing/publish-results.sh")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_folder", help="path to testing/results/canvas-...")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:30090")
    parser.add_argument("--step", default="5s",
                        help="Prometheus query_range step (default 5s, matches live collector)")
    args = parser.parse_args()

    run_dir = Path(args.run_folder).resolve()
    if not run_dir.is_dir():
        sys.exit(f"ERROR: not a directory: {run_dir}")

    reconstruct(run_dir, args.prometheus_url.rstrip("/"), args.step)


if __name__ == "__main__":
    main()
