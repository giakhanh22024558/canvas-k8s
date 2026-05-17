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
import os
import re
import datetime as dt
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import requests
from matplotlib.ticker import MultipleLocator, MaxNLocator

# Reuse the K8s-resource-string parser from plot_prometheus to keep one
# canonical implementation of "3Gi → 3072 MiB", "4400Mi → 4400 MiB", etc.
from plot_prometheus import parse_memory_limit_mb

# Silence "All-NaN slice encountered" / "Mean of empty slice" warnings from
# nanmedian / nanmean — these fire at grid bins where every run has NaN
# (e.g. the tail of the time grid past a shorter run's end). The resulting
# NaN values are handled correctly by matplotlib (line gaps where no data),
# so the warning text is pure noise.
warnings.filterwarnings("ignore", message="All-NaN slice encountered",
                        category=RuntimeWarning)
warnings.filterwarnings("ignore", message="Mean of empty slice",
                        category=RuntimeWarning)


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
    """Stack runs onto common grid, return (stacked_array, _, n_valid_per_bin).

    Callers iterate the rows of `stacked_array` to draw each run individually
    with a distinct colour. No aggregate statistic (mean / median / std) is
    computed here — chart panels show raw per-run data only, which avoids
    parametric assumptions that do not hold for metrics bounded at zero
    (queue depth, error rate, evictions).
    """
    rows = [resample_to_grid(s, grid_seconds) for s in per_run_series_list]
    arr = np.array(rows)  # shape (n_runs, n_bins)
    if arr.size == 0:
        return None, None, None
    with np.errstate(all="ignore"):
        n = np.sum(~np.isnan(arr), axis=0)
    # Middle slot retained as None for backwards-compatible tuple shape.
    return arr, None, n


def detect_breakpoints(y,
                       reversal_threshold=0.015,
                       step_threshold=0.06):
    """Return indices of 'meaningful' points on a 1-D series.

    Selection rules:
        1. The first and last non-NaN samples (endpoints).
        2. Local extrema — interior points where the slope changes sign
           AND at least one adjacent slope exceeds `reversal_threshold`
           × (overall range). Catches peaks, valleys, knees.
        3. Step changes — points where the local first-difference itself
           exceeds `step_threshold` × (overall range). Catches large
           jumps within a monotonic stretch (e.g. saturation onset,
           recovery after a spike) that case (2) would miss because
           there is no sign reversal.

    The two thresholds let micro-jitter pass while still surfacing every
    visually meaningful change. Defaults are tuned for a 15 s grid over
    20–60-minute runs; lower the numbers for denser markers.
    """
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
        return sorted(breakpoints)  # flat line — only endpoints

    reversal_cutoff = y_range * reversal_threshold
    step_cutoff     = y_range * step_threshold

    valid_y = y[valid_idx]
    diffs = np.diff(valid_y)  # forward differences

    for j in range(1, len(valid_y) - 1):
        prev_d, next_d = diffs[j - 1], diffs[j]

        # Rule 2 — slope reversal (peak / valley)
        if prev_d * next_d < 0 and (
            abs(prev_d) > reversal_cutoff or abs(next_d) > reversal_cutoff
        ):
            breakpoints.add(int(valid_idx[j]))
            continue

        # Rule 3 — large step inside a monotonic stretch
        if abs(prev_d) > step_cutoff or abs(next_d) > step_cutoff:
            breakpoints.add(int(valid_idx[j]))

    return sorted(breakpoints)


def apply_minute_ticks(ax):
    """Place a major x-axis tick at every minute and a minor tick every
    30 seconds, clamp the visible time range to start at 0, AND force
    tick labels to render on this axis even when sharex=True would
    normally hide them on inner panels of a multi-panel figure.

    Use this on every panel of a multi-panel chart so readers can read
    the time directly under each curve instead of mentally projecting
    down to the bottom axis."""
    ax.set_xlim(left=0)
    ax.xaxis.set_major_locator(MultipleLocator(1))
    ax.xaxis.set_minor_locator(MultipleLocator(0.5))
    ax.tick_params(axis="x", which="major", length=4, labelbottom=True)
    ax.tick_params(axis="x", which="minor", length=2)


def plot_metric(ax, grid, agg, label, color, scale=1.0):
    """Render one metric across N runs as a single bold median line with
    dot markers placed only at significant break-points (endpoints and
    direction-reversals above a 4 % range threshold).

    With N=3 the median at each tick is literally the middle observed run,
    not a parametric aggregate — it is a value that was actually measured.
    Restricting markers to break-points keeps the curve readable on long
    runs while still flagging peaks, valleys and inflection ticks.
    """
    if agg is None or agg[0] is None:
        return
    arr = agg[0]
    minutes = grid / 60.0
    arr_s = arr * scale

    with np.errstate(all="ignore"):
        median = np.nanmedian(arr_s, axis=0)

    breakpoints = detect_breakpoints(median)

    # Line is intentionally thinner than the markers so each break-point
    # marker dominates visually. A thin white edge produces a "halo" that
    # separates the marker from the line underneath.
    ax.plot(
        minutes, median,
        color=color, linewidth=1.4, zorder=3,
        marker="o", markersize=6.0,
        markerfacecolor=color,
        markeredgecolor="white", markeredgewidth=0.9,
        markevery=breakpoints if breakpoints else None,
        label=f"{label} (median)",
    )


# Backwards-compatible alias for multi-metric overlay call sites
# (latency p50/p95/p99, memory web vs jobs). The same median-line +
# markers rendering applies; each metric is drawn in its own colour and
# the panels become a layered set of single curves rather than overlaid
# bands.
def plot_band(ax, grid, agg, label, color, show_band=True, scale=1.0):
    """Alias for plot_metric. `show_band` retained for call-site
    compatibility but is now ignored."""
    plot_metric(ax, grid, agg, label, color, scale=scale)


def overlay_vus_background(ax, grid, vus_agg, color="#999999", offset_pt=0):
    """Draw VUs as a faint shaded area + thin dashed line on a twin y-axis
    behind the foreground metric. VUs are identical across runs by design
    (same k6 scenario, same ramp), so one representative line is drawn —
    the per-bin mean across runs (NaN-safe), which equals any single run
    when all runs agree.
    """
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
    # Push twin axis behind the foreground axis so per-run lines stay on top.
    ax_v.set_zorder(ax.get_zorder() - 1)
    ax.patch.set_visible(False)
    # Caller may push this spine outward if the host axis already has
    # another twin (e.g. plot_cpu_replicas has a CPU% twin on the right).
    if offset_pt > 0:
        ax_v.spines["right"].set_position(("outward", offset_pt))
    return ax_v


# ── per-metric query helpers ──────────────────────────────────────────────────

def q_throughput(testid):
    """Total requests / sec — every HTTP call k6 fires is counted here,
    regardless of response status. This is what k6 PUSHED into the system."""
    return [f'sum(rate(k6_http_reqs_total{{testid="{testid}"}}[1m]))']


def derive_success_rps(total_series, error_series):
    """Derive per-run successful RPS as total × (1 - error_rate/100) at
    each timestamp.

    Previously we queried the successful RPS directly with
        sum(rate(k6_http_reqs_total{expected_response="true"}[1m]))
    but during crash windows the {expected_response="true"} series can
    cease to exist in Prometheus (no matching samples → empty result),
    leaving NaN gaps in the successful series while the total series
    remains populated. When the cross-run median is then taken
    independently for total and success, asymmetric NaN distribution
    can make median(success) > median(total) — impossible per-run, but
    a real artefact of medians over arrays with different NaN masks.

    Deriving success from total and error per-run instead guarantees:
        - identical NaN pattern between the two series within a run
          (success is NaN ⟺ total is NaN);
        - 0 ≤ success ≤ total at every timestamp within each run;
        - therefore median(success) ≤ median(total) at every grid tick.

    Inputs:
        total_series — list of (datetime, total_rps) from q_throughput
        error_series — list of (datetime, error_pct) from q_error_rate
    Output:
        list of (datetime, success_rps) aligned with total_series.
    """
    if not total_series:
        return []
    err_lookup = {ts: pct for ts, pct in error_series}
    out = []
    for ts, total in total_series:
        err_pct = err_lookup.get(ts)
        if err_pct is None:
            # No error-rate sample at this timestamp — Prometheus drops
            # the error-rate series when there are no failures (denominator
            # of the ratio query has no matching members). Treat as 0%
            # error, i.e. every request at this tick was successful.
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
    """pct: 'p50', 'p95', 'p99' → returns avg over the percentile metric."""
    return [f'avg(k6_http_req_duration_{pct}{{testid="{testid}"}})']


def q_vus(testid):
    """Virtual-user count gauge — k6 pushes the running VU total every 5 s."""
    return [f'max(k6_vus{{testid="{testid}"}})']


def _q_memory_per_pod_mb(pod_regex, container, aggregator):
    """Per-pod memory-working-set series, aggregated across pods with
    ``aggregator`` ∈ {"max", "avg"}.

    The ``unless on(id) (container_last_seen > 30)`` filter drops ghost
    cgroups from crash-looped or recently-terminated containers; the
    cgroup path persists in cAdvisor for 30–60 s after the kernel reaps
    the container, which inflated naive aggregates by 2–4× at OOMKill
    transitions in Stage 1 baseline. Two label-scheme fallbacks cover
    older cAdvisor versions.

    ``max()`` reflects the hottest pod (the one that will OOM-kill first
    since OOM is per-pod); ``avg()`` reflects the cluster mean, which
    smooths nicely but undercounts when one pod is under-load.
    """
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
    """Hottest web pod — the OOM-risk indicator."""
    return _q_memory_per_pod_mb("canvas-web-.*", "web", "max")


def q_web_memory_avg_mb():
    """Per-pod mean across the web fleet — smooths spawn-time RSS dips."""
    return _q_memory_per_pod_mb("canvas-web-.*", "web", "avg")


def q_jobs_memory_max_mb():
    return _q_memory_per_pod_mb("canvas-jobs-.*", "jobs", "max")


def q_jobs_memory_avg_mb():
    return _q_memory_per_pod_mb("canvas-jobs-.*", "jobs", "avg")


def q_web_cpu_percent_of_request():
    # try_queries() takes the first query that returns data.
    #
    # Primary: the CPU utilisation % the HPA controller actually observed,
    # straight from its status.currentMetrics. kube-state-metrics exposes it
    # as ..._status_target_metric with metric_target_type="utilization" — the
    # name is misleading (it is the *current* observed value, not the spec
    # target). This is exactly what the controller acted on, with no rate-
    # window or aggregation interpretation gap, so a scale-out event lines up
    # with the line crossing the target threshold. Empty for the non-HPA
    # stages (no HPA object), which fall through to the cAdvisor query.
    #
    # Fallback: cAdvisor reconstruction — sum(usage)/sum(request), the same
    # formula the controller uses. 1-minute rate window (≈ the controller's
    # sampling horizon); the previous 2-minute window damped the transients
    # HPA reacts to below the target line, hiding the very phenomenon the
    # chart exists to show.
    return [
        'kube_horizontalpodautoscaler_status_target_metric{namespace="canvas",horizontalpodautoscaler="canvas-web",metric_target_type="utilization"}',
        '100 * sum(rate(container_cpu_usage_seconds_total{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"}[1m])) / sum(kube_pod_container_resource_requests{namespace="canvas",resource="cpu",pod=~"canvas-web-.*",container!="",container!="POD"})',
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


def read_jobs_queue_csv(path: Path, started_at):
    """Return three series — pending, oldest_age, jobs_per_minute — each as
    [(seconds_from_start, value)]. Throughput is derived from the cumulative
    n_tup_del counter via diff, identical logic to load_jobs_queue() in
    plot_prometheus.py.
    """
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
            # Mirror the reset-recovery + big-drop guards in
            # load_jobs_queue() so an aggregate Stage 2 chart doesn't get
            # spiked by a single Postgres-stat reset window.
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


# ── plotting ──────────────────────────────────────────────────────────────────

def _plot_stacked_rps(ax, grid, tput, err):
    """Render the RPS panel as a stacked area chart.

    Layout (bottom-up):
        - Green filled band [0, success_band]                  — successful RPS
        - Red filled band   [success_band, total_band]         — failed RPS
        - Thin dark marker line on top at total_band           — chart top edge
          with break-point markers consistent with other charts

    The bands are derived from the SAME median(error_rate) used by the
    panel below, so the two panels are visually consistent at every
    tick by construction:

        failed_band  = median(total) × median(error_rate) / 100
        success_band = median(total) − failed_band

    Aggregating with two independent medians (median(total) and
    median(success)) would not preserve this consistency because median
    does not distribute over multiplication — at ticks where a subset of
    runs is in a crash cycle, median(error) on the bottom panel and the
    implied error from the RPS stack would disagree.
    """
    if (tput is None or tput[0] is None
            or err is None or err[0] is None):
        return

    with np.errstate(all="ignore"):
        total_med = np.nanmedian(tput[0], axis=0)
        err_med   = np.nanmedian(err[0],  axis=0)
    # Error rate query may return NaN at ticks where no failures exist
    # (denominator-of-ratio empty); treat as 0 %.
    err_med = np.where(np.isnan(err_med), 0.0, err_med)
    err_med = np.clip(err_med, 0.0, 100.0)

    failed_med  = total_med * err_med / 100.0
    success_med = total_med - failed_med

    minutes = grid / 60.0

    # Successful (green) at the bottom
    ax.fill_between(minutes, 0, success_med,
                    color="#2ca02c", alpha=0.55, linewidth=0,
                    label="Successful RPS (median)")
    # Failed (red) stacked on top, height = failed_med
    ax.fill_between(minutes, success_med, success_med + failed_med,
                    color="#d62728", alpha=0.55, linewidth=0,
                    label="Failed RPS (median)")

    # Thin marker line tracing the top edge so the chart still has the
    # break-point markers that every other panel uses for cross-chart
    # alignment.
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
    """Return 1-D median across runs for a stacked (n_runs, n_bins) array.
    Returns None when input is missing.
    """
    if agg is None or agg[0] is None:
        return None
    with np.errstate(all="ignore"):
        return np.nanmedian(agg[0], axis=0)


def _find_throughput_saturation_grid_idx(grid_seconds, median_tput,
                                         peak_tolerance=0.05,
                                         future_overshoot=0.02):
    """First grid index where median throughput reaches its peak AND
    stops growing. Mirror of the per-run helper in plot_prometheus.py
    but operates on a 1-D numpy array indexed by `grid_seconds`.

    See plot_prometheus._find_throughput_saturation_minute for the
    rationale on peak_tolerance and future_overshoot. Returns None
    when no valid sample meets the criteria.
    """
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
    """First grid index where median windowed P95 latency crosses
    `threshold_seconds`. Returns None when threshold is never crossed
    or when input is missing — both are valid "no marker" outcomes.
    """
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
    """Two-panel load-response chart sharing a time axis:
        Panel 1 (top)    — RPS shown as a stacked area chart:
                           bottom band = median successful RPS (green),
                           top band    = median failed RPS (red).
                           Total height = total RPS. When error rate is
                           low the red band is a thin strip; during a
                           crash window the green band collapses and red
                           fills the panel. A thin marker line traces
                           the top edge so the breakpoint markers from
                           the other charts remain consistent.
        Panel 2 (bottom) — Error rate (%) line.

    The VU profile is drawn on each panel as a faint shaded area on a
    twin y-axis (identical across runs by design).
    """
    fig, axes = plt.subplots(2, 1, figsize=(11, 9), sharex=True)
    ax_rps, ax_err = axes

    # Panel 1 — stacked area: successful (bottom) + failed (top).
    # The stack is derived from median(total) × median(error_rate) so
    # this panel and the error-rate panel below are visually consistent
    # tick-by-tick. The tput_success series is kept in the function
    # signature (and used by callers that may want it for tables) but
    # is no longer plotted here.
    overlay_vus_background(ax_rps, grid, vus)
    _plot_stacked_rps(ax_rps, grid, tput, err)
    ax_rps.set_ylabel("Requests / sec")
    ax_rps.set_ylim(bottom=0)
    ax_rps.grid(True, alpha=0.3)
    ax_rps.legend(loc="upper left", fontsize=9)

    # Panel 2 — Error rate (red)
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
    # Apply on every panel so each shows its own x-axis labels, even
    # though sharex=True would normally hide them on inner panels.
    apply_minute_ticks(ax_rps)
    apply_minute_ticks(ax_err)
    fig.tight_layout()

    # ── Saturation markers on aggregate chart (breakpoint experiments) ──────
    # Computed from the median curves rather than any single run, so the
    # marker positions reflect the consensus saturation behaviour. Same
    # visual treatment as per-run plot_prometheus.plot_throughput_error:
    #   - Orange dashed line: throughput-saturation onset (median peak RPS)
    #   - Red dashed line:    QoS saturation (median P95 first ≥ 3 s)
    #   - Red-tinted shading: from throughput-saturation to end of x-axis
    # Pre-peak region is intentionally left unshaded — ramp-up adaptation
    # errors are concurrency-growth artifacts, not saturation symptoms.
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
    # VU profile sits behind the percentile curves as a faint shaded area.
    overlay_vus_background(ax, grid, vus)
    # Prometheus k6_http_req_duration_pXX is in seconds — scale to ms so the
    # axis label "Latency (ms)" matches the displayed values.
    plot_band(ax, grid, p50, "p50",  "#2ca02c", scale=1000)
    plot_band(ax, grid, p95, "p95",  "#ff7f0e", scale=1000)
    plot_band(ax, grid, p99, "p99",  "#d62728", scale=1000)
    ax.set_xlabel("Minutes from test start")
    ax.set_ylabel("Latency (ms)")
    # Linear scale with auto-selected round-number ticks. A log scale would
    # compress the typical 60–300 ms band against the bottom whenever a
    # single tail spike pushes the upper bound; linear keeps the axis
    # evenly divided and makes p50/p95/p99 comparable at a glance for
    # readers who are not used to logarithmic axes.
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
    # Jobs replicas share the same integer replica axis. They are plotted here
    # deliberately AGAINST the web CPU% line: web replicas track web CPU%
    # closely, jobs replicas do not — that visible mismatch is the evidence
    # that CPU is the right HPA signal for the synchronous web tier but the
    # wrong one for the queue-driven jobs tier.
    if jobs_replicas is not None:
        plot_band(ax1, grid, jobs_replicas, "Jobs replicas", "#ff7f0e")
    ax1.set_xlabel("Minutes from test start")
    ax1.set_ylabel("Replica count")
    ax1.set_ylim(bottom=0)
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left")

    ax2 = ax1.twinx()
    # The CPU% series is the WEB tier's HPA signal (kube_horizontalpod-
    # autoscaler_status_target_metric for canvas-web). It is paired with the
    # web replica line; the jobs replica line is intentionally NOT explained
    # by it.
    plot_band(ax2, grid, cpu_pct, "Web CPU % of request", "#d62728")
    # HPA scale-out threshold line — only drawn when the runs actually had an
    # HPA, and at the threshold they were really configured with (read from
    # environment.env, not hard-coded). Previously this was a fixed 70% line,
    # which mislabelled Stage 3 (target 80%) and drew a meaningless HPA line
    # on the non-HPA Stage 1/2 charts.
    if hpa_target_pct is not None:
        ax2.axhline(hpa_target_pct, color="#d62728", linestyle="--",
                    linewidth=1, alpha=0.5,
                    label=f"HPA target {hpa_target_pct:.0f}%")
    ax2.set_ylabel("Web CPU %", color="#d62728")
    ax2.tick_params(axis="y", labelcolor="#d62728")
    ax2.legend(loc="upper right")

    # VU profile on a second twin axis, spine pushed outward by 55 pt
    # so it doesn't overlap the CPU% spine already at the right edge.
    overlay_vus_background(ax1, grid, vus, offset_pt=55)

    fig.suptitle(f"{experiment} — Replicas & Web CPU% (median across runs, n={n_runs})")
    apply_minute_ticks(ax1)
    fig.tight_layout()
    fig.savefig(output, dpi=130)
    plt.close(fig)
    print(f"  → {output}")


def plot_replicas_vs_vus(grid, web_replicas, jobs_replicas, vus,
                         output, experiment, n_runs):
    """Elasticity profile — web AND jobs replica counts (median ± 1σ band
    across runs) plotted against the VU profile. The cross-run aggregate
    of the per-run replicas_vs_vus chart: shows whether the scaling
    trajectory is reproducible across the n runs. Both replica series share
    one integer y-axis (counts are small); VUs are the faint background."""
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
    """Three-panel jobs-tier chart: queue depth, oldest pending age, jobs
    per minute. Each panel renders one coloured line per run and overlays
    the VU profile as a faint shaded area on a twin axis (identical input
    across runs). Skipped silently if no run has jobs-queue.csv data.
    """
    has_data = any(agg is not None and agg[0] is not None
                   for agg in (queue_depth, job_age, jobs_per_min))
    if not has_data:
        print(f"  → (skip) no jobs-queue.csv data found for {experiment}")
        return

    fig, axes = plt.subplots(3, 1, figsize=(11, 11), sharex=True)
    ax_q, ax_age, ax_tput = axes

    # Panel 1 — queue depth (red)
    overlay_vus_background(ax_q, grid, vus)
    plot_metric(ax_q, grid, queue_depth, "Pending jobs", "#d62728")
    ax_q.set_ylabel("Jobs in queue", color="#d62728")
    ax_q.tick_params(axis="y", labelcolor="#d62728")
    ax_q.grid(True, alpha=0.3)
    ax_q.legend(loc="upper left", fontsize=9)

    # Panel 2 — oldest pending age (orange)
    overlay_vus_background(ax_age, grid, vus)
    plot_metric(ax_age, grid, job_age, "Oldest pending age", "#ff7f0e")
    ax_age.axhline(10, color="#888", linestyle=":", linewidth=1, alpha=0.7)
    ax_age.set_ylabel("Oldest pending age (s)", color="#ff7f0e")
    ax_age.tick_params(axis="y", labelcolor="#ff7f0e")
    ax_age.grid(True, alpha=0.3)
    ax_age.legend(loc="upper left", fontsize=9)

    # Panel 3 — jobs per minute (blue)
    overlay_vus_background(ax_tput, grid, vus)
    plot_metric(ax_tput, grid, jobs_per_min, "Jobs / min", "#1f77b4")
    ax_tput.set_ylabel("Jobs / minute", color="#1f77b4")
    ax_tput.tick_params(axis="y", labelcolor="#1f77b4")
    ax_tput.set_xlabel("Minutes from test start")
    ax_tput.grid(True, alpha=0.3)
    ax_tput.legend(loc="upper left", fontsize=9)

    fig.suptitle(f"{experiment} — Jobs Queue, Age, Throughput "
                 f"(median across runs, n={n_runs})")
    # Apply on every panel so each shows its own x-axis labels.
    apply_minute_ticks(ax_q)
    apply_minute_ticks(ax_age)
    apply_minute_ticks(ax_tput)
    fig.tight_layout()
    fig.savefig(output, dpi=130)
    plt.close(fig)
    print(f"  → {output}")


def _plot_memory_line(ax, grid, agg, label, color, linestyle="-"):
    """Per-pod memory line (median across runs). Same break-point marker
    treatment as plot_metric, but linestyle is overridable so the dashed
    avg line sits visually under the solid max line of the same colour."""
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
    """Per-pod memory chart — 4 lines, all in MiB:

      • Web max  (solid blue)   — the hottest web pod  ← OOM-risk signal
      • Web avg  (dashed blue)  — mean across web pods (load distribution)
      • Jobs max (solid orange) — hottest jobs pod
      • Jobs avg (dashed orange)

    A wide gap between max and avg of the same colour reveals load
    imbalance across pods (e.g. sticky sessions, LB skew). When max
    approaches the horizontal limit reference line, OOMKill is imminent
    on that one pod regardless of how the rest of the fleet looks.

    sum() across pods was avoided deliberately: with HPA scaling 1→3
    pods during a run, a sum line jumps at scale-out events even when
    no individual pod's RSS changed, conflating "scaled out" with
    "memory grew". Per-pod aggregation matches how OOMKill actually
    fires (per-pod, not per-tier total)."""
    fig, ax = plt.subplots(figsize=(11, 5))
    overlay_vus_background(ax, grid, vus)

    _plot_memory_line(ax, grid, web_mem_max,  "Web max per pod (MB)",   "#1f77b4", "-")
    _plot_memory_line(ax, grid, web_mem_avg,  "Web avg per pod (MB)",   "#1f77b4", "--")
    _plot_memory_line(ax, grid, jobs_mem_max, "Jobs max per pod (MB)",  "#ff7f0e", "-")
    _plot_memory_line(ax, grid, jobs_mem_avg, "Jobs avg per pod (MB)",  "#ff7f0e", "--")

    # Per-pod memory-limit reference lines: OOMKill triggers when one
    # pod's working set exceeds its own limit, so the comparison must be
    # against per-pod values (which is exactly what max/avg here are).
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


# ── main ──────────────────────────────────────────────────────────────────────

def discover_runs(results_dir: Path, experiment: str):
    """Find all run subdirectories for an experiment that carry a valid
    metadata.env.

    A folder qualifies when it (a) starts with `<experiment>-` and (b) has a
    `run<NN>` segment, optionally followed by the `-YYYYMMDD-HHMMSS` timestamp
    run-load-test.sh appends. The run<NN> requirement keeps this consistent
    with aggregate_analysis.parse_run_dir: a folder that merely shares the
    prefix but is not part of the numbered run series — e.g. an ablation run
    renamed to `<experiment>-throttleoff-<ts>` — must NOT be averaged into the
    mean ± std bands, or the time-series and the stats table would disagree
    on n."""
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
    # Saturation marker toggle for breakpoint-style experiments. Off by
    # default because aggregate charts are commonly viewed for all
    # experiment types (smoke / load / staircase / soak / breakpoint),
    # and saturation markers only make sense for breakpoint. Enable via
    # --saturation-markers flag or SATURATION_MARKERS=on env var.
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

    # Build a common time grid covering the longest run
    max_duration = max((r["ended_at"] - r["started_at"]).total_seconds() for r in runs)
    grid = np.arange(0, max_duration + args.step_seconds, args.step_seconds)
    print(f"Time grid: 0 to {max_duration:.0f} s in {args.step_seconds} s steps "
          f"({len(grid)} bins)\n")

    # Collect per-run series for each metric
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

        # k6 metrics from Prometheus
        thr = try_queries(args.prometheus_url, q_throughput(tid),  s, e, step_str)
        err = try_queries(args.prometheus_url, q_error_rate(tid),  s, e, step_str)
        vus = try_queries(args.prometheus_url, q_vus(tid),         s, e, step_str)
        # Derive successful RPS per-run from total and error rate. This
        # preserves the median(success) ≤ median(total) invariant across
        # runs; see derive_success_rps() for the asymmetric-NaN rationale.
        thr_ok = derive_success_rps(thr, err)
        p50 = try_queries(args.prometheus_url, q_latency(tid, "p50"), s, e, step_str)
        p95 = try_queries(args.prometheus_url, q_latency(tid, "p95"), s, e, step_str)
        p99 = try_queries(args.prometheus_url, q_latency(tid, "p99"), s, e, step_str)

        # Cluster metrics from Prometheus
        wmem_max = try_queries(args.prometheus_url, q_web_memory_max_mb(),  s, e, step_str)
        wmem_avg = try_queries(args.prometheus_url, q_web_memory_avg_mb(),  s, e, step_str)
        jmem_max = try_queries(args.prometheus_url, q_jobs_memory_max_mb(), s, e, step_str)
        jmem_avg = try_queries(args.prometheus_url, q_jobs_memory_avg_mb(), s, e, step_str)
        cpu  = try_queries(args.prometheus_url, q_web_cpu_percent_of_request(), s, e, step_str)

        # Replica counts from local snapshots CSV (more reliable than Prom)
        snap_csv = r["dir"] / "k8s-snapshots.csv"
        rep = read_snapshots_csv(snap_csv, s, "web_ready_replicas")
        jobs_rep = read_snapshots_csv(snap_csv, s, "jobs_ready_replicas")

        # Jobs-queue series from local CSV (collect-jobs-metrics.sh output)
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

    # HPA scale-out threshold for the CPU% chart's reference line. Read from
    # each run's environment.env snapshot rather than hard-coded: Stage 3 uses
    # 80%, the non-HPA stages (1, 2) leave it empty so no line is drawn. If the
    # runs disagree, fall back to None and skip the line rather than mislead.
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

    # Per-pod memory limits for the memory-chart reference lines. Same
    # protocol as the HPA target: read from environment.env per run; if
    # all runs in the experiment agree, draw the line — if they disagree
    # (mixed VPA / unscaled), fall back to None and skip.
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

    # Aggregate
    print("\nAggregating across runs...")
    agg = {k: aggregate_runs(v, grid) for k, v in metrics.items()}

    # Plot
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
