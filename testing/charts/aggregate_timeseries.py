import argparse
import csv
import os
import re
import datetime as dt
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import requests
from matplotlib.ticker import MultipleLocator, MaxNLocator

from plot_prometheus import parse_memory_limit_mb

warnings.filterwarnings("ignore", message="All-NaN slice encountered",
                        category=RuntimeWarning)
warnings.filterwarnings("ignore", message="Mean of empty slice",
                        category=RuntimeWarning)

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
    return [(dt.datetime.fromtimestamp(float(t), dt.UTC), float(v))
            for t, v in data[0]["values"]]

def try_queries(base_url, queries, start, end, step):
    for q in queries:
        s = query_range(base_url, q, start, end, step)
        if s:
            return s
    return []

def to_relative(series, started_at):
    return [((t - started_at).total_seconds(), v) for t, v in series]

def resample_to_grid(rel_series, grid_seconds):
    if not rel_series:
        return np.full(len(grid_seconds), np.nan)
    xs = np.array([p[0] for p in rel_series])
    ys = np.array([p[1] for p in rel_series])
    out = np.interp(grid_seconds, xs, ys, left=np.nan, right=np.nan)
    return out

def aggregate_runs(per_run_series_list, grid_seconds):
    rows = [resample_to_grid(s, grid_seconds) for s in per_run_series_list]
    arr = np.array(rows)
    if arr.size == 0:
        return None, None, None
    with np.errstate(all="ignore"):
        n = np.sum(~np.isnan(arr), axis=0)
    return arr, None, n

def detect_breakpoints(y,
                       reversal_threshold=0.015,
                       step_threshold=0.06):
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n == 0:
        return []

    valid_mask = ~np.isnan(y)
    if not valid_mask.any():
        return []

    valid_idx = np.where(valid_mask)[0]
    first, last = int(valid_idx[0]), int(valid_idx[-1])
    breakpoints = {first, last}

    if last - first < 2:
        return sorted(breakpoints)

    y_range = float(np.nanmax(y) - np.nanmin(y))
    if y_range <= 0:
        return sorted(breakpoints)

    reversal_cutoff = y_range * reversal_threshold
    step_cutoff     = y_range * step_threshold

    valid_y = y[valid_idx]
    diffs = np.diff(valid_y)

    for j in range(1, len(valid_y) - 1):
        prev_d, next_d = diffs[j - 1], diffs[j]

        if prev_d * next_d < 0 and (
            abs(prev_d) > reversal_cutoff or abs(next_d) > reversal_cutoff
        ):
            breakpoints.add(int(valid_idx[j]))
            continue

        if abs(prev_d) > step_cutoff or abs(next_d) > step_cutoff:
            breakpoints.add(int(valid_idx[j]))

    return sorted(breakpoints)

def apply_minute_ticks(ax):
    ax.set_xlim(left=0)
    ax.xaxis.set_major_locator(MultipleLocator(1))
    ax.xaxis.set_minor_locator(MultipleLocator(0.5))
    ax.tick_params(axis="x", which="major", length=4, labelbottom=True)
    ax.tick_params(axis="x", which="minor", length=2)

def plot_metric(ax, grid, agg, label, color, scale=1.0):
    if agg is None or agg[0] is None:
        return
    arr = agg[0]
    minutes = grid / 60.0
    arr_s = arr * scale

    with np.errstate(all="ignore"):
        median = np.nanmedian(arr_s, axis=0)

    breakpoints = detect_breakpoints(median)

    ax.plot(
        minutes, median,
        color=color, linewidth=1.4, zorder=3,
        marker="o", markersize=6.0,
        markerfacecolor=color,
        markeredgecolor="white", markeredgewidth=0.9,
        markevery=breakpoints if breakpoints else None,
        label=f"{label} (median)",
    )

def plot_band(ax, grid, agg, label, color, show_band=True, scale=1.0):
    plot_metric(ax, grid, agg, label, color, scale=scale)

def overlay_vus_background(ax, grid, vus_agg, color="#999999", offset_pt=0):
    if vus_agg is None or vus_agg[0] is None:
        return None
    arr = vus_agg[0]
    minutes = grid / 60.0
    with np.errstate(all="ignore"):
        vu_line = np.nanmean(arr, axis=0)

    ax_v = ax.twinx()
    ax_v.fill_between(minutes, 0, vu_line, color=color, alpha=0.12,
                      linewidth=0, zorder=0)
    ax_v.plot(minutes, vu_line, color=color, alpha=0.55,
              linewidth=1.0, linestyle="--", zorder=0, label="Virtual users")
    ax_v.set_ylabel("VUs", color=color, fontsize=9)
    ax_v.tick_params(axis="y", labelcolor=color, labelsize=8)
    ax_v.set_ylim(bottom=0)
    ax_v.set_zorder(ax.get_zorder() - 1)
    ax.patch.set_visible(False)
    if offset_pt > 0:
        ax_v.spines["right"].set_position(("outward", offset_pt))
    return ax_v

def q_throughput(testid):
    return [f'sum(rate(k6_http_reqs_total{{testid="{testid}"}}[1m]))']

def derive_success_rps(total_series, error_series):
    if not total_series:
        return []
    err_lookup = {ts: pct for ts, pct in error_series}
    out = []
    for ts, total in total_series:
        err_pct = err_lookup.get(ts)
        if err_pct is None:
            success = total
        else:
            success = total * max(0.0, 1.0 - err_pct / 100.0)
        out.append((ts, success))
    return out

def q_error_rate(testid):
    return [
        f'100 * sum(rate(k6_http_reqs_total{{expected_response="false",testid="{testid}"}}[1m])) / sum(rate(k6_http_reqs_total{{testid="{testid}"}}[1m]))',
        f'100 * avg_over_time(k6_http_req_failed{{testid="{testid}"}}[2m])',
    ]

def q_latency(testid, pct):
    return [f'avg(k6_http_req_duration_{pct}{{testid="{testid}"}})']

def q_vus(testid):
    return [f'max(k6_vus{{testid="{testid}"}})']

def _q_memory_per_pod_mb(pod_regex, container, aggregator):
    primary = (
        f'{aggregator}(container_memory_working_set_bytes{{namespace="canvas",pod=~"{pod_regex}",container="{container}"}} '
        'unless on(id) '
        f'(time() - container_last_seen{{namespace="canvas",pod=~"{pod_regex}",container="{container}"}} > 30)) '
        '/ 1048576'
    )
    fallback1 = (
        f'{aggregator}(container_memory_working_set_bytes{{namespace="canvas",pod=~"{pod_regex}",container!="",container!="POD"}} '
        f'* on(pod) group_left() kube_pod_status_phase{{namespace="canvas",phase="Running"}}) / 1048576'
    )
    fallback2 = (
        f'{aggregator}(container_memory_working_set_bytes{{container_label_io_kubernetes_pod_namespace="canvas",'
        f'container_label_io_kubernetes_pod_name=~"{pod_regex}",container!="",container!="POD"}}) / 1048576'
    )
    return [primary, fallback1, fallback2]

def q_web_memory_max_mb():
    return _q_memory_per_pod_mb("canvas-web-.*", "web", "max")

def q_web_memory_avg_mb():
    return _q_memory_per_pod_mb("canvas-web-.*", "web", "avg")

def q_jobs_memory_max_mb():
    return _q_memory_per_pod_mb("canvas-jobs-.*", "jobs", "max")

def q_jobs_memory_avg_mb():
    return _q_memory_per_pod_mb("canvas-jobs-.*", "jobs", "avg")

def q_web_cpu_percent_of_request():
    return [
        'kube_horizontalpodautoscaler_status_target_metric{namespace="canvas",horizontalpodautoscaler="canvas-web",metric_target_type="utilization"}',
        '100 * sum(rate(container_cpu_usage_seconds_total{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"}[1m])) / sum(kube_pod_container_resource_requests{namespace="canvas",resource="cpu",pod=~"canvas-web-.*",container!="",container!="POD"})',
    ]

def read_snapshots_csv(path: Path, started_at, column):
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

def read_jobs_queue_csv(path: Path, started_at):
    pending, age, jpm = [], [], []
    if not path.exists():
        return pending, age, jpm

    rows = []
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_raw = row.get("timestamp", "")
            if not ts_raw:
                continue
            try:
                rows.append({
                    "ts": parse_ts(ts_raw),
                    "pending": float(row.get("pending", 0) or 0),
                    "age":     float(row.get("oldest_pending_age_sec", 0) or 0),
                    "cum":     float(row.get("total_processed_cumulative", 0) or 0),
                })
            except Exception:
                continue

    prev_cum, prev_ts = None, None
    last_nonzero = 0.0
    for r in rows:
        rel = (r["ts"] - started_at).total_seconds()
        pending.append((rel, r["pending"]))
        age.append((rel, r["age"]))
        if prev_cum is None:
            jpm.append((rel, 0.0))
        else:
            dt_seconds = (r["ts"] - prev_ts).total_seconds()
            d_cum = r["cum"] - prev_cum
            reset_recovery = prev_cum == 0 and r["cum"] > 0 and last_nonzero > 0
            big_drop = r["cum"] < last_nonzero / 2 and last_nonzero > 100
            if dt_seconds <= 0 or d_cum < 0 or reset_recovery or big_drop:
                rate = 0.0
            else:
                rate = (d_cum / dt_seconds) * 60.0
            jpm.append((rel, rate))
        prev_cum, prev_ts = r["cum"], r["ts"]
        if r["cum"] > 0:
            last_nonzero = r["cum"]
    return pending, age, jpm

def _plot_stacked_rps(ax, grid, tput, err):
    if (tput is None or tput[0] is None
            or err is None or err[0] is None):
        return

    with np.errstate(all="ignore"):
        total_med = np.nanmedian(tput[0], axis=0)
        err_med   = np.nanmedian(err[0],  axis=0)
    err_med = np.where(np.isnan(err_med), 0.0, err_med)
    err_med = np.clip(err_med, 0.0, 100.0)

    failed_med  = total_med * err_med / 100.0
    success_med = total_med - failed_med

    minutes = grid / 60.0

    ax.fill_between(minutes, 0, success_med,
                    color="#2ca02c", alpha=0.55, linewidth=0,
                    label="Successful RPS (median)")
    ax.fill_between(minutes, success_med, success_med + failed_med,
                    color="#d62728", alpha=0.55, linewidth=0,
                    label="Failed RPS (median)")

    top_edge = success_med + failed_med
    breakpoints = detect_breakpoints(top_edge)
    ax.plot(
        minutes, top_edge,
        color="#1f3a5f", linewidth=1.0, zorder=3,
        marker="o", markersize=4.0,
        markerfacecolor="#1f3a5f",
        markeredgecolor="white", markeredgewidth=0.6,
        markevery=breakpoints if breakpoints else None,
        label="Total RPS (median)",
    )

def _median_curve(agg):
    if agg is None or agg[0] is None:
        return None
    with np.errstate(all="ignore"):
        return np.nanmedian(agg[0], axis=0)

def _find_throughput_saturation_grid_idx(grid_seconds, median_tput,
                                         peak_tolerance=0.05,
                                         future_overshoot=0.02):
    if median_tput is None or len(median_tput) == 0:
        return None
    valid_mask = np.isfinite(median_tput)
    if not valid_mask.any():
        return None
    peak = float(np.nanmax(median_tput[valid_mask]))
    if peak <= 0:
        return None
    threshold = peak * (1.0 - peak_tolerance)
    for i in range(len(median_tput)):
        v = median_tput[i]
        if not np.isfinite(v) or v < threshold:
            continue
        future_slice = median_tput[i:]
        future_max = float(np.nanmax(future_slice)) if np.isfinite(future_slice).any() else v
        if future_max <= v * (1.0 + future_overshoot):
            return i
    return None

def _find_slo_breach_grid_idx(median_p95, threshold_seconds=3.0):
    if median_p95 is None or len(median_p95) == 0:
        return None
    for i, v in enumerate(median_p95):
        if np.isfinite(v) and v >= threshold_seconds:
            return i
    return None

def plot_throughput_error(grid, tput, tput_success, err, vus,
                          output, experiment, n_runs,
                          p95=None,
                          draw_saturation_markers=False):
    fig, axes = plt.subplots(2, 1, figsize=(11, 9), sharex=True)
    ax_rps, ax_err = axes

    overlay_vus_background(ax_rps, grid, vus)
    _plot_stacked_rps(ax_rps, grid, tput, err)
    ax_rps.set_ylabel("Requests / sec")
    ax_rps.set_ylim(bottom=0)
    ax_rps.grid(True, alpha=0.3)
    ax_rps.legend(loc="upper left", fontsize=9)

    overlay_vus_background(ax_err, grid, vus)
    plot_metric(ax_err, grid, err, "Error rate", "#d62728")
    ax_err.axhline(1.0, color="#888", linestyle=":", linewidth=1, alpha=0.7)
    ax_err.set_ylabel("Error rate (%)", color="#d62728")
    ax_err.tick_params(axis="y", labelcolor="#d62728")
    ax_err.set_xlabel("Minutes from test start")
    ax_err.grid(True, alpha=0.3)
    ax_err.legend(loc="upper left", fontsize=9)

    fig.suptitle(f"{experiment} — Throughput & Error Rate "
                 f"(median across runs, n={n_runs})")
    apply_minute_ticks(ax_rps)
    apply_minute_ticks(ax_err)
    fig.tight_layout()

    if draw_saturation_markers:
        median_tput = _median_curve(tput)
        median_p95 = _median_curve(p95) if p95 is not None else None
        sat_idx = _find_throughput_saturation_grid_idx(grid, median_tput)
        slo_idx = _find_slo_breach_grid_idx(median_p95) if median_p95 is not None else None
        end_min = grid[-1] / 60.0 if len(grid) else None

        if sat_idx is not None and end_min is not None:
            sat_min = grid[sat_idx] / 60.0
            for axis in (ax_rps, ax_err):
                axis.axvspan(sat_min, end_min,
                             color="#d62728", alpha=0.08, zorder=0)
                axis.axvline(sat_min, color="#ff6f00", linestyle="--",
                             linewidth=1.4, alpha=0.85, zorder=2)
            ax_rps.text(
                sat_min, ax_rps.get_ylim()[1] * 0.96,
                f"Throughput saturation\n(peak RPS @ {sat_min:.1f} min)",
                ha="left", va="top", fontsize=8.5,
                color="#bf360c", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.25",
                          facecolor="white", edgecolor="#ff6f00",
                          alpha=0.92),
                zorder=4,
            )

        if slo_idx is not None:
            slo_min = grid[slo_idx] / 60.0
            for axis in (ax_rps, ax_err):
                axis.axvline(slo_min, color="#b71c1c", linestyle="--",
                             linewidth=1.4, alpha=0.85, zorder=2)
            ax_rps.text(
                slo_min, ax_rps.get_ylim()[1] * 0.78,
                f"SLO breach\n(P95 ≥ 3 s @ {slo_min:.1f} min)",
                ha="left", va="top", fontsize=8.5,
                color="#7f0000", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.25",
                          facecolor="white", edgecolor="#b71c1c",
                          alpha=0.92),
                zorder=4,
            )

    fig.savefig(output, dpi=130)
    plt.close(fig)
    print(f"  → {output}")

def plot_latency(grid, p50, p95, p99, vus, output, experiment, n_runs):
    fig, ax = plt.subplots(figsize=(11, 5))
    overlay_vus_background(ax, grid, vus)
    plot_band(ax, grid, p50, "p50",  "#2ca02c", scale=1000)
    plot_band(ax, grid, p95, "p95",  "#ff7f0e", scale=1000)
    plot_band(ax, grid, p99, "p99",  "#d62728", scale=1000)
    ax.set_xlabel("Minutes from test start")
    ax.set_ylabel("Latency (ms)")
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")
    fig.suptitle(f"{experiment} — Response Time Percentiles (median across runs, n={n_runs})")
    apply_minute_ticks(ax)
    fig.tight_layout()
    fig.savefig(output, dpi=130)
    plt.close(fig)
    print(f"  → {output}")

def plot_cpu_replicas(grid, replicas, cpu_pct, vus, output, experiment, n_runs,
                      hpa_target_pct=None, jobs_replicas=None):
    fig, ax1 = plt.subplots(figsize=(11, 5))
    plot_band(ax1, grid, replicas, "Web replicas", "#1f77b4")
    if jobs_replicas is not None:
        plot_band(ax1, grid, jobs_replicas, "Jobs replicas", "#ff7f0e")
    ax1.set_xlabel("Minutes from test start")
    ax1.set_ylabel("Replica count")
    ax1.set_ylim(bottom=0)
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left")

    ax2 = ax1.twinx()
    plot_band(ax2, grid, cpu_pct, "Web CPU % of request", "#d62728")
    if hpa_target_pct is not None:
        ax2.axhline(hpa_target_pct, color="#d62728", linestyle="--",
                    linewidth=1, alpha=0.5,
                    label=f"HPA target {hpa_target_pct:.0f}%")
    ax2.set_ylabel("Web CPU %", color="#d62728")
    ax2.tick_params(axis="y", labelcolor="#d62728")
    ax2.legend(loc="upper right")

    overlay_vus_background(ax1, grid, vus, offset_pt=55)

    fig.suptitle(f"{experiment} — Replicas & Web CPU% (median across runs, n={n_runs})")
    apply_minute_ticks(ax1)
    fig.tight_layout()
    fig.savefig(output, dpi=130)
    plt.close(fig)
    print(f"  → {output}")

def plot_replicas_vs_vus(grid, web_replicas, jobs_replicas, vus,
                         output, experiment, n_runs):
    fig, ax1 = plt.subplots(figsize=(11, 5))
    plot_band(ax1, grid, web_replicas, "Web replicas", "#1f77b4")
    plot_band(ax1, grid, jobs_replicas, "Jobs replicas", "#ff7f0e")
    ax1.set_xlabel("Minutes from test start")
    ax1.set_ylabel("Replica count")
    ax1.set_ylim(bottom=0)
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left")

    overlay_vus_background(ax1, grid, vus)

    fig.suptitle(f"{experiment} — Elasticity Profile: Replicas vs VUs "
                 f"(median ± 1σ across runs, n={n_runs})")
    apply_minute_ticks(ax1)
    fig.tight_layout()
    fig.savefig(output, dpi=130)
    plt.close(fig)
    print(f"  → {output}")

def plot_jobs_queue(grid, queue_depth, job_age, jobs_per_min, vus,
                    output, experiment, n_runs):
    has_data = any(agg is not None and agg[0] is not None
                   for agg in (queue_depth, job_age, jobs_per_min))
    if not has_data:
        print(f"  → (skip) no jobs-queue.csv data found for {experiment}")
        return

    fig, axes = plt.subplots(3, 1, figsize=(11, 11), sharex=True)
    ax_q, ax_age, ax_tput = axes

    overlay_vus_background(ax_q, grid, vus)
    plot_metric(ax_q, grid, queue_depth, "Pending jobs", "#d62728")
    ax_q.set_ylabel("Jobs in queue", color="#d62728")
    ax_q.tick_params(axis="y", labelcolor="#d62728")
    ax_q.grid(True, alpha=0.3)
    ax_q.legend(loc="upper left", fontsize=9)

    overlay_vus_background(ax_age, grid, vus)
    plot_metric(ax_age, grid, job_age, "Oldest pending age", "#ff7f0e")
    ax_age.axhline(10, color="#888", linestyle=":", linewidth=1, alpha=0.7)
    ax_age.set_ylabel("Oldest pending age (s)", color="#ff7f0e")
    ax_age.tick_params(axis="y", labelcolor="#ff7f0e")
    ax_age.grid(True, alpha=0.3)
    ax_age.legend(loc="upper left", fontsize=9)

    overlay_vus_background(ax_tput, grid, vus)
    plot_metric(ax_tput, grid, jobs_per_min, "Jobs / min", "#1f77b4")
    ax_tput.set_ylabel("Jobs / minute", color="#1f77b4")
    ax_tput.tick_params(axis="y", labelcolor="#1f77b4")
    ax_tput.set_xlabel("Minutes from test start")
    ax_tput.grid(True, alpha=0.3)
    ax_tput.legend(loc="upper left", fontsize=9)

    fig.suptitle(f"{experiment} — Jobs Queue, Age, Throughput "
                 f"(median across runs, n={n_runs})")
    apply_minute_ticks(ax_q)
    apply_minute_ticks(ax_age)
    apply_minute_ticks(ax_tput)
    fig.tight_layout()
    fig.savefig(output, dpi=130)
    plt.close(fig)
    print(f"  → {output}")

def _plot_memory_line(ax, grid, agg, label, color, linestyle="-"):
    if agg is None or agg[0] is None:
        return
    arr = agg[0]
    minutes = grid / 60.0
    with np.errstate(all="ignore"):
        median = np.nanmedian(arr, axis=0)
    breakpoints = detect_breakpoints(median)
    ax.plot(
        minutes, median,
        color=color, linewidth=1.4, linestyle=linestyle, zorder=3,
        marker="o", markersize=5.0,
        markerfacecolor=color,
        markeredgecolor="white", markeredgewidth=0.8,
        markevery=breakpoints if breakpoints else None,
        label=label,
    )

def plot_memory(grid, web_mem_max, web_mem_avg, jobs_mem_max, jobs_mem_avg,
                vus, output, experiment, n_runs,
                web_limit_mb=None, jobs_limit_mb=None):
    fig, ax = plt.subplots(figsize=(11, 5))
    overlay_vus_background(ax, grid, vus)

    _plot_memory_line(ax, grid, web_mem_max,  "Web max per pod (MB)",   "#1f77b4", "-")
    _plot_memory_line(ax, grid, web_mem_avg,  "Web avg per pod (MB)",   "#1f77b4", "--")
    _plot_memory_line(ax, grid, jobs_mem_max, "Jobs max per pod (MB)",  "#ff7f0e", "-")
    _plot_memory_line(ax, grid, jobs_mem_avg, "Jobs avg per pod (MB)",  "#ff7f0e", "--")

    if web_limit_mb is not None:
        ax.axhline(web_limit_mb, color="#1f77b4", linestyle=":",
                   linewidth=1, alpha=0.7,
                   label=f"Web pod limit ({web_limit_mb:.0f} MB)")
    if jobs_limit_mb is not None:
        ax.axhline(jobs_limit_mb, color="#ff7f0e", linestyle=":",
                   linewidth=1, alpha=0.7,
                   label=f"Jobs pod limit ({jobs_limit_mb:.0f} MB)")

    ax.set_xlabel("Minutes from test start")
    ax.set_ylabel("Per-pod memory working set (MB)")
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=9, ncol=2)
    fig.suptitle(f"{experiment} — Per-Pod Memory (max & avg, median across runs, n={n_runs})")
    apply_minute_ticks(ax)
    fig.tight_layout()
    fig.savefig(output, dpi=130)
    plt.close(fig)
    print(f"  → {output}")

def discover_runs(results_dir: Path, experiment: str):
    runs = []
    for d in sorted(results_dir.iterdir()):
        if not d.is_dir():
            continue
        if not d.name.startswith(experiment + "-"):
            continue
        if not re.search(r"-run\d+(-\d{8}-\d{6})?$", d.name):
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
    parser.add_argument("--saturation-markers", action="store_true",
                        default=(os.environ.get("SATURATION_MARKERS", "off").lower() == "on"),
                        help="Draw throughput-saturation and SLO-breach "
                             "markers on the aggregate throughput chart. "
                             "Defaults off; recommended for breakpoint "
                             "experiments only.")
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

    max_duration = max((r["ended_at"] - r["started_at"]).total_seconds() for r in runs)
    grid = np.arange(0, max_duration + args.step_seconds, args.step_seconds)
    print(f"Time grid: 0 to {max_duration:.0f} s in {args.step_seconds} s steps "
          f"({len(grid)} bins)\n")

    metrics = {
        "throughput": [], "throughput_success": [],
        "error_rate": [], "vus": [],
        "p50": [], "p95": [], "p99": [],
        "replicas": [], "jobs_replicas": [], "cpu_pct": [],
        "web_memory_max": [], "web_memory_avg": [],
        "jobs_memory_max": [], "jobs_memory_avg": [],
        "jobs_queue_depth": [], "jobs_age": [], "jobs_per_min": [],
    }

    step_str = f"{args.step_seconds}s"

    for r in runs:
        tid = r["test_id"]
        s, e = r["started_at"], r["ended_at"]
        print(f"Querying metrics for {tid}...")

        thr = try_queries(args.prometheus_url, q_throughput(tid),  s, e, step_str)
        err = try_queries(args.prometheus_url, q_error_rate(tid),  s, e, step_str)
        vus = try_queries(args.prometheus_url, q_vus(tid),         s, e, step_str)
        thr_ok = derive_success_rps(thr, err)
        p50 = try_queries(args.prometheus_url, q_latency(tid, "p50"), s, e, step_str)
        p95 = try_queries(args.prometheus_url, q_latency(tid, "p95"), s, e, step_str)
        p99 = try_queries(args.prometheus_url, q_latency(tid, "p99"), s, e, step_str)

        wmem_max = try_queries(args.prometheus_url, q_web_memory_max_mb(),  s, e, step_str)
        wmem_avg = try_queries(args.prometheus_url, q_web_memory_avg_mb(),  s, e, step_str)
        jmem_max = try_queries(args.prometheus_url, q_jobs_memory_max_mb(), s, e, step_str)
        jmem_avg = try_queries(args.prometheus_url, q_jobs_memory_avg_mb(), s, e, step_str)
        cpu  = try_queries(args.prometheus_url, q_web_cpu_percent_of_request(), s, e, step_str)

        snap_csv = r["dir"] / "k8s-snapshots.csv"
        rep = read_snapshots_csv(snap_csv, s, "web_ready_replicas")
        jobs_rep = read_snapshots_csv(snap_csv, s, "jobs_ready_replicas")

        q_pending, q_age, q_jpm = read_jobs_queue_csv(r["dir"] / "jobs-queue.csv", s)

        metrics["throughput"].append(to_relative(thr, s))
        metrics["throughput_success"].append(to_relative(thr_ok, s))
        metrics["error_rate"].append(to_relative(err, s))
        metrics["vus"].append(to_relative(vus, s))
        metrics["p50"].append(to_relative(p50, s))
        metrics["p95"].append(to_relative(p95, s))
        metrics["p99"].append(to_relative(p99, s))
        metrics["replicas"].append(rep)
        metrics["jobs_replicas"].append(jobs_rep)
        metrics["cpu_pct"].append(to_relative(cpu, s))
        metrics["web_memory_max"].append(to_relative(wmem_max, s))
        metrics["web_memory_avg"].append(to_relative(wmem_avg, s))
        metrics["jobs_memory_max"].append(to_relative(jmem_max, s))
        metrics["jobs_memory_avg"].append(to_relative(jmem_avg, s))
        metrics["jobs_queue_depth"].append(q_pending)
        metrics["jobs_age"].append(q_age)
        metrics["jobs_per_min"].append(q_jpm)

    hpa_targets = set()
    for r in runs:
        env = load_env_file(r["dir"] / "environment.env")
        val = env.get("web_hpa_target_cpu_percent", "").strip()
        if val:
            try:
                hpa_targets.add(float(val))
            except ValueError:
                pass
    hpa_target_pct = hpa_targets.pop() if len(hpa_targets) == 1 else None

    web_limits, jobs_limits = set(), set()
    for r in runs:
        env = load_env_file(r["dir"] / "environment.env")
        wl = parse_memory_limit_mb(env.get("web_memory_limit", "")
                                   or env.get("web_memory_limit_spec", ""))
        jl = parse_memory_limit_mb(env.get("jobs_memory_limit", "")
                                   or env.get("jobs_memory_limit_spec", ""))
        if wl is not None:
            web_limits.add(wl)
        if jl is not None:
            jobs_limits.add(jl)
    web_limit_mb  = web_limits.pop()  if len(web_limits)  == 1 else None
    jobs_limit_mb = jobs_limits.pop() if len(jobs_limits) == 1 else None

    print("\nAggregating across runs...")
    agg = {k: aggregate_runs(v, grid) for k, v in metrics.items()}

    print("\nGenerating charts...")
    n = len(runs)
    plot_throughput_error(grid,
                          agg["throughput"], agg["throughput_success"],
                          agg["error_rate"], agg["vus"],
                          output_dir / "timeseries_throughput_error.png",
                          args.experiment, n,
                          p95=agg.get("p95"),
                          draw_saturation_markers=args.saturation_markers)
    plot_latency(grid, agg["p50"], agg["p95"], agg["p99"],
                 agg["vus"],
                 output_dir / "timeseries_latency.png",
                 args.experiment, n)
    plot_cpu_replicas(grid, agg["replicas"], agg["cpu_pct"],
                      agg["vus"],
                      output_dir / "timeseries_cpu_replicas.png",
                      args.experiment, n, hpa_target_pct=hpa_target_pct,
                      jobs_replicas=agg["jobs_replicas"])
    plot_replicas_vs_vus(grid, agg["replicas"], agg["jobs_replicas"],
                         agg["vus"],
                         output_dir / "timeseries_replicas_vs_vus.png",
                         args.experiment, n)
    plot_memory(grid,
                agg["web_memory_max"], agg["web_memory_avg"],
                agg["jobs_memory_max"], agg["jobs_memory_avg"],
                agg["vus"],
                output_dir / "timeseries_memory.png",
                args.experiment, n,
                web_limit_mb=web_limit_mb,
                jobs_limit_mb=jobs_limit_mb)
    plot_jobs_queue(grid,
                    agg["jobs_queue_depth"], agg["jobs_age"],
                    agg["jobs_per_min"],   agg["vus"],
                    output_dir / "timeseries_jobs_queue.png",
                    args.experiment, n)

    print(f"\nDone. Charts written to {output_dir}/timeseries_*.png")

if __name__ == "__main__":
    main()
