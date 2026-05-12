import argparse
import csv
import datetime as dt
from pathlib import Path
import re

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import requests


def slugify(value):
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value)
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "run"


def load_env_file(path):
    values = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def parse_timestamp(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_numeric(value):
    if value in (None, ""):
        return 0.0
    return float(value)


def parse_duration_to_seconds(value):
    if not value:
        return 0.0

    text = value.strip()
    if text in {"0", "0.0", "0s", "0ms"}:
        return 0.0

    total = 0.0
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)(µs|us|ms|s|m|h)", text):
        number = float(amount)
        if unit in ("µs", "us"):
            total += number / 1_000_000.0
        elif unit == "ms":
            total += number / 1000.0
        elif unit == "s":
            total += number
        elif unit == "m":
            total += number * 60.0
        elif unit == "h":
            total += number * 3600.0
    return total


def parse_k6_summary_metrics(path):
    if not path.exists():
        return {}

    text = path.read_text(encoding="utf-8", errors="ignore")
    metrics = {}

    # k6 summary line format (with summaryTrendStats including p(99)):
    # http_req_duration...: avg=96ms min=1ms med=60ms max=35s p(90)=127ms p(95)=168ms p(99)=300ms
    duration_match = re.search(
        r"http_req_duration\.*:\s+avg=(\S+)\s+min=\S+\s+med=(\S+).*?p\(90\)=(\S+)\s+p\(95\)=(\S+)",
        text,
        re.DOTALL,
    )
    if duration_match:
        metrics["avg"]  = parse_duration_to_seconds(duration_match.group(1))
        metrics["p50"]  = parse_duration_to_seconds(duration_match.group(2))  # median = p50
        metrics["p95"]  = parse_duration_to_seconds(duration_match.group(4))

    # p(99) is captured separately so older runs (which lacked p(99) in their
    # summary text) still parse the rest of the metrics. New runs include
    # p(99) because summaryTrendStats in the k6 options now requests it.
    # When present, p99 is computed by k6 over the *entire* request population
    # — matching the methodology used for p95 and avoiding the apples-to-
    # oranges comparison that resulted from time-averaging Prometheus values.
    p99_match = re.search(
        r"http_req_duration\.*:.*?p\(99\)=(\S+)",
        text,
        re.DOTALL,
    )
    if p99_match:
        metrics["p99"] = parse_duration_to_seconds(p99_match.group(1))

    expected_match = re.search(
        r"\{\s*expected_response:true\s*\}\.*:\s+avg=(\S+).*?p\(90\)=(\S+)\s+p\(95\)=(\S+)",
        text,
        re.DOTALL,
    )
    if expected_match:
        metrics["expected_avg"] = parse_duration_to_seconds(expected_match.group(1))
        metrics["expected_p95"] = parse_duration_to_seconds(expected_match.group(3))

    failed_match = re.search(r"http_req_failed\.*:\s+(\d+(?:\.\d+)?)%", text)
    if failed_match:
        metrics["error_rate_percent"] = float(failed_match.group(1))

    reqs_match = re.search(r"http_reqs\.*:\s+\d+\s+(\d+(?:\.\d+)?)\/s", text)
    if reqs_match:
        metrics["throughput_rps"] = float(reqs_match.group(1))

    vus_match = re.search(r"vus_max\.*:\s+(\d+(?:\.\d+)?)", text)
    if vus_match:
        metrics["max_vus"] = float(vus_match.group(1))

    return metrics


def query_range(base_url, query, start, end, step):
    response = requests.get(
        f"{base_url}/api/v1/query_range",
        params={
            "query": query,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "step": step,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["data"]["result"]


def parse_series(result):
    series = []
    for item in result:
        label = item["metric"]
        values = [(dt.datetime.fromtimestamp(float(ts), dt.UTC), float(val)) for ts, val in item["values"]]
        series.append((label, values))
    return series


def select_first_series(result):
    series = parse_series(result)
    return series[0][1] if series else []


def _parse_per_pod_memory(result):
    """Build a dict {pod_name: [(timestamp, value_MiB), ...]} from a
    Prometheus query that returns one series per pod (no aggregation).

    Each cAdvisor sample is in bytes; we divide by 1 048 576 here so the
    chart can plot directly in MiB (binary, displayed as "MB") without
    any further scaling. This matches the per-pod-limit reference line
    which is also derived in MiB by parse_memory_limit_mb.

    During a pod restart the SAME pod name may appear with two different
    cgroup `id` labels for ~30s. The upstream `unless on(id)` freshness
    filter usually trims the dead one before it lands here, but as
    belt-and-braces we keep only the highest sample per (pod, timestamp)
    so a transient overlap cannot create a phantom doubled line.
    """
    series = parse_series(result)
    pod_to_samples = {}  # pod -> {ts -> max_value_MiB}
    for label, values in series:
        pod = label.get("pod") or label.get("container_label_io_kubernetes_pod_name") or "unknown"
        bucket = pod_to_samples.setdefault(pod, {})
        for ts, raw_bytes in values:
            value_mib = raw_bytes / 1048576.0
            prior = bucket.get(ts)
            if prior is None or value_mib > prior:
                bucket[ts] = value_mib
    return {
        pod: sorted(samples.items())
        for pod, samples in pod_to_samples.items()
    }


def try_queries(base_url, queries, start, end, step):
    last_error = None
    for query in queries:
        try:
            result = query_range(base_url, query, start, end, step)
        except requests.HTTPError as exc:
            last_error = exc
            continue
        if result:
            return result, query
    if last_error:
        raise last_error
    return [], ""


def apply_time_axis(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.tick_params(axis="x", rotation=20)


def apply_minute_axis(ax, test_start):
    """Switch x-axis from absolute datetime to minutes-from-test-start, with
    a major tick at every minute and minor ticks at 30-second intervals."""
    from matplotlib.ticker import FuncFormatter, MultipleLocator
    if test_start is None:
        return
    def fmt(value, _pos):
        # value is matplotlib's internal numeric for datetime (days since epoch)
        try:
            ts = mdates.num2date(value)
            seconds = (ts - test_start).total_seconds() if hasattr(test_start, 'tzinfo') else 0
            return f"{int(seconds / 60)}"
        except Exception:
            return ""
    # Simpler approach: callers should already plot in seconds-from-start
    # (numeric) instead of datetime. This helper assumes that, and just sets
    # locators + xlim.
    ax.set_xlim(left=0)
    ax.xaxis.set_major_locator(MultipleLocator(1))
    ax.xaxis.set_minor_locator(MultipleLocator(0.5))
    ax.tick_params(axis="x", which="major", length=4, labelbottom=True)
    ax.tick_params(axis="x", which="minor", length=2)


def to_minutes_from_start(series_with_datetime, test_start):
    """Convert [(datetime, value), ...] -> ([minutes, ...], [values, ...]).

    Used to render per-run charts on a relative-minute x-axis aligned
    with the aggregate charts.
    """
    if not series_with_datetime or test_start is None:
        return [], []
    xs, ys = [], []
    for t, v in series_with_datetime:
        xs.append((t - test_start).total_seconds() / 60.0)
        ys.append(v)
    return xs, ys


def overlay_vus_per_run(ax, vus_values, test_start, color="#999999"):
    """Faint shaded VU profile overlay on twin y-axis, matching the style
    used by overlay_vus_background in aggregate_timeseries.py. Returns the
    twin axis so callers can offset its spine when another twin already
    occupies the right edge."""
    if not vus_values or test_start is None:
        return None
    xs, ys = to_minutes_from_start(vus_values, test_start)
    if not xs:
        return None
    ax_v = ax.twinx()
    ax_v.fill_between(xs, 0, ys, color=color, alpha=0.12, linewidth=0, zorder=0)
    ax_v.plot(xs, ys, color=color, alpha=0.55, linewidth=1.0,
              linestyle="--", zorder=0, label="Virtual users")
    ax_v.set_ylabel("VUs", color=color, fontsize=9)
    ax_v.tick_params(axis="y", labelcolor=color, labelsize=8)
    ax_v.set_ylim(bottom=0)
    ax_v.set_zorder(ax.get_zorder() - 1)
    ax.patch.set_visible(False)
    return ax_v


def overlay_rps_per_run(ax, throughput_values, test_start, color="#9ec5d8"):
    """Faint shaded RPS profile overlay, same style as VU overlay but for
    successful-rate-context on charts like the latency timeline."""
    if not throughput_values or test_start is None:
        return None
    xs, ys = to_minutes_from_start(throughput_values, test_start)
    if not xs:
        return None
    ax_r = ax.twinx()
    ax_r.fill_between(xs, 0, ys, color=color, alpha=0.15, linewidth=0, zorder=0)
    ax_r.plot(xs, ys, color=color, alpha=0.6, linewidth=1.0,
              linestyle="--", zorder=0, label="RPS")
    ax_r.set_ylabel("Requests / sec", color=color, fontsize=9)
    ax_r.tick_params(axis="y", labelcolor=color, labelsize=8)
    ax_r.set_ylim(bottom=0)
    ax_r.set_zorder(ax.get_zorder() - 1)
    ax.patch.set_visible(False)
    return ax_r


def restart_delta_series(snapshots, field):
    """Return (minutes_from_start, restart_count_during_test) — counter is
    rebased to 0 at the first snapshot so the line shows how many restarts
    happened DURING the test window, not the cumulative pod-lifetime value
    that may include events from before the test started.
    """
    if not snapshots:
        return [], []
    test_start = snapshots[0]["timestamp"]
    base = snapshots[0].get(field, 0) or 0
    xs, ys = [], []
    for row in snapshots:
        minutes = (row["timestamp"] - test_start).total_seconds() / 60.0
        delta = max(0, (row.get(field, 0) or 0) - base)
        xs.append(minutes)
        ys.append(delta)
    return xs, ys


def annotate_saturation(axes, saturation_time, saturation_vu, end_time=None):
    """Disabled by request — the inferred saturation point was deemed
    misleading on charts where the system degrades gracefully without an
    obvious threshold crossing. Calls are kept in the codebase so the
    surrounding chart wiring still works, but no red dashed line, no
    shaded collapse region, and no text label are drawn.

    Re-enable by restoring the previous body if a future stage wants
    the marker back.
    """
    return


def detect_saturation_point(snapshots, vus_values,
                            error_rate=None, latency_p95=None,
                            error_threshold_pct=1.0,
                            latency_threshold_seconds=5.0):
    """Return (saturation_datetime, vu_at_saturation) for breakpoint tests.

    Saturation is the EARLIEST of:
      (A) First sample where web_restart_total increments from 0 — i.e. the
          first OOMKill (the original definition).
      (B) First sample where error_rate (% of failed requests in the
          Prometheus rolling window) crosses `error_threshold_pct` (default 1%).
      (C) First sample where p95 latency (seconds) crosses
          `latency_threshold_seconds` (default 5s).

    With well-resourced pods on a max-packed node the system can survive
    100 VUs without an OOMKill, but error rate or tail latency will degrade
    well before that — so cases (B) and (C) catch threshold-based
    saturation that case (A) alone misses. VU count is interpolated from
    the k6 VU time-series at the nearest timestamp to whichever case fires
    first.

    Returns (None, None) when no signal is observed.
    """
    candidates = []

    # (A) OOMKill
    for i, row in enumerate(snapshots[1:], start=1):
        if row["web_restart_total"] > snapshots[i - 1]["web_restart_total"]:
            candidates.append((row["timestamp"], "oomkill"))
            break

    # (B) error rate threshold — error_rate values are already percent (Prometheus
    # series multiplies by 100 server-side or in our query construction).
    if error_rate:
        for ts, val in error_rate:
            if val is not None and val >= error_threshold_pct:
                candidates.append((ts, f"error_rate>={error_threshold_pct:g}%"))
                break

    # (C) p95 latency threshold — Prometheus emits p95 in seconds.
    if latency_p95:
        for ts, val in latency_p95:
            if val is not None and val >= latency_threshold_seconds:
                candidates.append((ts, f"p95>={latency_threshold_seconds:g}s"))
                break

    if not candidates:
        return None, None

    # Earliest signal wins.
    sat_time, _reason = min(candidates, key=lambda c: c[0])

    # Nearest VU sample to the saturation timestamp
    sat_vu = None
    if vus_values:
        closest = min(vus_values, key=lambda tv: abs((tv[0] - sat_time).total_seconds()))
        sat_vu = closest[1]

    return sat_time, sat_vu


def plot_breakpoint_saturation(output_dir, label, throughput, error_rate,
                               vus, snapshots, saturation_time, saturation_vu):
    """Composite chart for breakpoint tests, matching the throughput_error_rate
    chart style:
        Panel 1 — RPS stacked area: green = successful, red = failed (on top)
                  with a thin dark line tracing the total at the top edge.
        Panel 2 — Error rate %, plotted as a line with a 1 % reference line.
    VU profile overlaid on each panel as a faint shaded twin axis. X-axis:
    minutes from test start, major tick per minute. A vertical red dashed
    line marks the saturation point in both panels, with a shaded collapse
    region — this is the breakpoint-specific addition.
    """
    if not throughput and not error_rate and not vus:
        return

    # Anchor x-axis at the first available sample so minute ticks line up.
    test_start = None
    if throughput:
        test_start = throughput[0][0]
    elif error_rate:
        test_start = error_rate[0][0]
    elif vus:
        test_start = vus[0][0]
    if test_start is None:
        return

    fig, axes = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
    ax_rps, ax_err = axes

    # ── Panel 1: RPS stacked area (success at bottom, failed on top) ─────────
    overlay_vus_per_run(ax_rps, vus, test_start)

    if throughput:
        err_map = {ts: pct for ts, pct in (error_rate or [])}
        xs_min, total_ys, success_ys, failed_ys = [], [], [], []
        for ts, total in throughput:
            minutes = (ts - test_start).total_seconds() / 60.0
            err_pct = err_map.get(ts)
            if err_pct is None:
                success = total
                failed = 0.0
            else:
                err_pct = max(0.0, min(100.0, err_pct))
                failed = total * err_pct / 100.0
                success = max(0.0, total - failed)
            xs_min.append(minutes)
            total_ys.append(total)
            success_ys.append(success)
            failed_ys.append(failed)

        ax_rps.fill_between(xs_min, 0, success_ys,
                            color="#2ca02c", alpha=0.55, linewidth=0,
                            label="Successful RPS")
        ax_rps.fill_between(xs_min, success_ys,
                            [s + f for s, f in zip(success_ys, failed_ys)],
                            color="#d62728", alpha=0.55, linewidth=0,
                            label="Failed RPS")
        ax_rps.plot(xs_min, total_ys,
                    color="#1f3a5f", linewidth=1.0, zorder=3,
                    marker="o", markersize=3.0,
                    markerfacecolor="#1f3a5f",
                    markeredgecolor="white", markeredgewidth=0.6,
                    label="Total RPS")

    ax_rps.set_ylabel("Requests / sec")
    ax_rps.set_ylim(bottom=0)
    ax_rps.grid(True, alpha=0.3)
    ax_rps.legend(loc="upper left", fontsize=9)

    # ── Panel 2: Error rate ──────────────────────────────────────────────────
    overlay_vus_per_run(ax_err, vus, test_start)
    if error_rate:
        xs_err, ys_err = to_minutes_from_start(error_rate, test_start)
        ax_err.plot(xs_err, ys_err, color="#d62728", linewidth=1.5,
                    label="Error rate (1-min rolling, %)")
    ax_err.axhline(1.0, color="#888", linestyle=":", linewidth=1, alpha=0.7)
    ax_err.set_ylim(0, 110)
    ax_err.set_ylabel("Error rate (%)")
    ax_err.set_xlabel("Minutes from test start")
    ax_err.grid(True, alpha=0.3)
    ax_err.legend(loc="upper left", fontsize=9)

    fig.suptitle(f"Breakpoint Test — Load Profile & System Response ({label})")
    apply_minute_axis(ax_rps, test_start)
    apply_minute_axis(ax_err, test_start)
    fig.tight_layout()

    # Saturation marker hook — annotate_saturation is currently a no-op (the
    # inferred saturation point was deemed misleading on graceful-degradation
    # charts) but the wiring is preserved so it can be re-enabled per stage.
    end_time = None
    if throughput:
        end_time = throughput[-1][0]
    elif vus:
        end_time = vus[-1][0]
    annotate_saturation([ax_rps, ax_err], saturation_time, saturation_vu,
                        end_time)

    out = output_dir / f"breakpoint_saturation_{slugify(label)}.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out


def plot_latency_timeline(output_dir, metrics_by_label,
                          throughput_values=None, test_start=None):
    """Response-time percentiles (P50/P95/P99) over time.

    When called for a single run, an RPS overlay is drawn as a faint
    shaded twin-axis background so the reader can correlate latency
    spikes with throughput dips during crash windows.
    """
    fig, ax = plt.subplots(figsize=(12, 5))
    colors = {"p50": "#1f77b4", "p95": "#ff7f0e", "p99": "#d62728"}

    # Determine test_start for minute axis (use first available series)
    if test_start is None:
        for _label, series in metrics_by_label.items():
            for metric_name in ("p50", "p95", "p99"):
                values = series.get(metric_name, [])
                if values:
                    test_start = values[0][0]
                    break
            if test_start is not None:
                break

    # RPS overlay (only meaningful for single-run mode)
    if test_start is not None and throughput_values and len(metrics_by_label) == 1:
        overlay_rps_per_run(ax, throughput_values, test_start)

    for label, metric_series in metrics_by_label.items():
        for metric_name in ("p50", "p95", "p99"):
            values = metric_series.get(metric_name, [])
            if not values:
                continue
            if test_start is not None:
                xs, ys_raw = to_minutes_from_start(values, test_start)
                ys = [y * 1000 for y in ys_raw]
            else:
                xs = [x for x, _ in values]
                ys = [y * 1000 for _, y in values]
            legend = metric_name.upper() if len(metrics_by_label) == 1 else f"{label} {metric_name.upper()}"
            ax.plot(xs, ys, label=legend, linewidth=2, color=colors[metric_name],
                    alpha=0.9 if len(metrics_by_label) == 1 else 0.7)

    ax.set_title("Response Time Timeline")
    ax.set_xlabel("Minutes from test start" if test_start is not None else "Time")
    ax.set_ylabel("Latency (ms)")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.25)
    if test_start is not None:
        apply_minute_axis(ax, test_start)
    else:
        apply_time_axis(ax)
    fig.tight_layout()
    fig.savefig(output_dir / "response_time_timeline.png")
    plt.close(fig)


def plot_throughput_error(output_dir, label, throughput_values, error_values,
                          k6_error_rate_percent=None, vus_values=None,
                          saturation_time=None, saturation_vu=None,
                          test_start=None):
    """Per-run throughput + error chart redesigned to match the aggregate
    chart style:
        Panel 1 — RPS stacked area: green = successful, red = failed (on top)
                  with a thin dark line tracing the total at the top edge.
        Panel 2 — Error rate %, plotted as a line with 1 % reference line.
    VU profile overlaid on each panel as a faint shaded twin axis.
    X-axis: minutes from test start, major tick per minute.
    """
    if not throughput_values and not error_values:
        return

    if test_start is None and throughput_values:
        test_start = throughput_values[0][0]
    if test_start is None and error_values:
        test_start = error_values[0][0]
    if test_start is None:
        return

    fig, axes = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
    ax_rps, ax_err = axes

    # ── Panel 1: RPS stacked area (success at bottom, failed on top) ─────────
    overlay_vus_per_run(ax_rps, vus_values, test_start)

    # Align throughput and error timestamps so we can compute failed RPS
    # per tick = total × (error / 100). Use a dict lookup keyed on timestamp
    # to avoid relying on identical sample positions.
    if throughput_values:
        err_map = {ts: pct for ts, pct in (error_values or [])}
        xs_min, total_ys, success_ys, failed_ys = [], [], [], []
        for ts, total in throughput_values:
            minutes = (ts - test_start).total_seconds() / 60.0
            err_pct = err_map.get(ts)
            if err_pct is None:
                # No error sample at this tick -> assume 0% error
                success = total
                failed = 0.0
            else:
                err_pct = max(0.0, min(100.0, err_pct))
                failed = total * err_pct / 100.0
                success = max(0.0, total - failed)
            xs_min.append(minutes)
            total_ys.append(total)
            success_ys.append(success)
            failed_ys.append(failed)

        # Green band: successful at bottom
        ax_rps.fill_between(xs_min, 0, success_ys,
                            color="#2ca02c", alpha=0.55, linewidth=0,
                            label="Successful RPS")
        # Red band: failed stacked on top
        ax_rps.fill_between(xs_min, success_ys,
                            [s + f for s, f in zip(success_ys, failed_ys)],
                            color="#d62728", alpha=0.55, linewidth=0,
                            label="Failed RPS")
        # Thin marker line tracing top edge
        ax_rps.plot(xs_min, total_ys,
                    color="#1f3a5f", linewidth=1.0, zorder=3,
                    marker="o", markersize=3.0,
                    markerfacecolor="#1f3a5f",
                    markeredgecolor="white", markeredgewidth=0.6,
                    label="Total RPS")

    ax_rps.set_ylabel("Requests / sec")
    ax_rps.set_ylim(bottom=0)
    ax_rps.grid(True, alpha=0.3)
    ax_rps.legend(loc="upper left", fontsize=9)

    # ── Panel 2: Error rate ──────────────────────────────────────────────────
    overlay_vus_per_run(ax_err, vus_values, test_start)
    if error_values:
        xs_err, ys_err = to_minutes_from_start(error_values, test_start)
        ax_err.plot(xs_err, ys_err, color="#d62728", linewidth=1.5,
                    label="Error rate (1-min rolling, %)")
    if k6_error_rate_percent is not None:
        ax_err.axhline(
            y=k6_error_rate_percent, color="#d62728", linewidth=1.5,
            linestyle="--", alpha=0.7,
            label=f"Error rate (k6 summary: {k6_error_rate_percent:.2f}%)",
        )
    ax_err.axhline(1.0, color="#888", linestyle=":", linewidth=1, alpha=0.7)
    ax_err.set_ylim(0, 110)
    ax_err.set_ylabel("Error rate (%)")
    ax_err.set_xlabel("Minutes from test start")
    ax_err.grid(True, alpha=0.3)
    ax_err.legend(loc="upper left", fontsize=9)

    fig.suptitle(f"Throughput & Error Rate ({label})")
    apply_minute_axis(ax_rps, test_start)
    apply_minute_axis(ax_err, test_start)
    fig.tight_layout()
    fig.savefig(output_dir / f"throughput_error_rate_{slugify(label)}.png",
                bbox_inches="tight")
    plt.close(fig)


def plot_vu_profile(output_dir, label, vus_values):
    if not vus_values:
        return

    fig, ax = plt.subplots(figsize=(12, 5))
    xs = [x for x, _ in vus_values]
    ys = [y for _, y in vus_values]
    ax.fill_between(xs, ys, color="#7db7e8", alpha=0.7)
    ax.plot(xs, ys, color="#1f77b4", linewidth=2)
    ax.set_title(f"Load Profile (VU over Time) - {label}")
    ax.set_xlabel("Time")
    ax.set_ylabel("Virtual Users")
    ax.grid(alpha=0.25)
    apply_time_axis(ax)
    fig.tight_layout()
    fig.savefig(output_dir / f"load_profile_vus_{slugify(label)}.png")
    plt.close(fig)


def load_snapshots(path):
    if not path.exists():
        return []

    rows = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row["timestamp"] = parse_timestamp(row["timestamp"])
            for key, value in row.items():
                if key == "timestamp":
                    continue
                row[key] = parse_numeric(value)
            rows.append(row)
    return rows


def load_jobs_queue(path):
    """Load jobs-queue.csv produced by collect-jobs-metrics.sh.

    Returns a list of dicts sorted by timestamp. Throughput (jobs/min) is
    derived post-hoc from the cumulative `total_processed_cumulative` counter
    using the time delta between consecutive samples — delayed_job removes
    rows on success, so n_tup_del is monotonic.
    """
    if not path.exists():
        return []

    rows = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row["timestamp"] = parse_timestamp(row["timestamp"])
            for key, value in row.items():
                if key == "timestamp":
                    continue
                row[key] = parse_numeric(value)
            rows.append(row)

    # Compute jobs-per-minute from cumulative counter diff. First sample has
    # no predecessor, so set rate to 0 — chart will start flat for one tick.
    #
    # Counter-reset detection (3 cases — all → set rate to 0):
    #   (1) d_count < 0       counter went down (clear reset)
    #   (2) prev == 0 and cur > 0 with prev_was_nonzero_recently
    #                          n_tup_del was 0 mid-run (Postgres stats
    #                          reset or transient query failure that
    #                          fell back to the COALESCE default), then
    #                          the next sample sees the recovered value
    #                          → fake "burst" of e.g. 100k jobs in 5s.
    #   (3) cur < prev / 2     same family — large drop suggests reset.
    # Without these guards, peak_jobs_per_minute can report > 1M/min on
    # a workload that physically maxes out around a few thousand/min.
    prev_count = None
    prev_ts = None
    last_nonzero_count = 0
    for row in rows:
        cur_count = row.get("total_processed_cumulative", 0) or 0
        cur_ts = row["timestamp"]
        if prev_count is None or prev_ts is None:
            row["jobs_per_minute"] = 0.0
        else:
            dt_seconds = (cur_ts - prev_ts).total_seconds()
            d_count = cur_count - prev_count
            reset_recovery = (prev_count == 0 and cur_count > 0
                              and last_nonzero_count > 0)
            big_drop = (cur_count < last_nonzero_count / 2 and last_nonzero_count > 100)
            if dt_seconds <= 0 or d_count < 0 or reset_recovery or big_drop:
                row["jobs_per_minute"] = 0.0
            else:
                row["jobs_per_minute"] = (d_count / dt_seconds) * 60.0
        prev_count = cur_count
        prev_ts = cur_ts
        if cur_count > 0:
            last_nonzero_count = cur_count
    return rows


def plot_jobs_queue(output_dir, label, jobs_rows, snapshots=None):
    """Three-panel jobs-tier chart: queue depth + replicas, age, throughput.

    queue_depth: pending jobs over time, with jobs replicas overlay if
                 snapshots provided. Direct visual of "is HPA scaling enough?"
    age:         oldest pending job age in seconds — proxy for end-user-
                 perceived latency-to-start. SLO line drawn at 10s.
    throughput:  jobs processed per minute (derived from delayed_jobs n_tup_del
                 counter). Counterpart of HTTP RPS for the async tier.
    """
    if not jobs_rows:
        return

    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    ax_q, ax_age, ax_tput = axes

    xs = [row["timestamp"] for row in jobs_rows]

    # Panel 1 — queue depth + jobs replica overlay
    ax_q.plot(xs, [row["pending"] for row in jobs_rows],
              color="#d62728", linewidth=2, label="Pending (queued)")
    ax_q.plot(xs, [row["running"] for row in jobs_rows],
              color="#2ca02c", linewidth=1.5, alpha=0.8, label="Running")
    ax_q.set_ylabel("Jobs in queue", color="#d62728")
    ax_q.tick_params(axis="y", labelcolor="#d62728")
    ax_q.set_title(f"Jobs Queue Depth and Worker Replicas ({label})")
    ax_q.grid(alpha=0.25)

    if snapshots:
        replica_xs = [row["timestamp"] for row in snapshots]
        replica_ys = [row.get("jobs_ready_replicas") or row.get("jobs_spec_replicas") or 0
                      for row in snapshots]
        if len(set(replica_ys)) > 1:
            ax_q_r = ax_q.twinx()
            ax_q_r.step(replica_xs, replica_ys, where="post",
                        color="#9467bd", linewidth=2, label="Jobs replicas")
            ax_q_r.set_ylabel("Replica count", color="#9467bd")
            ax_q_r.tick_params(axis="y", labelcolor="#9467bd")
            handles_l = ax_q.get_lines()
            handles_r = ax_q_r.get_lines()
            ax_q.legend(handles_l + handles_r,
                        [h.get_label() for h in handles_l + handles_r],
                        loc="upper left")
        else:
            ax_q.legend(loc="upper left")
    else:
        ax_q.legend(loc="upper left")

    # Panel 2 — oldest pending age (latency to start)
    ages = [row["oldest_pending_age_sec"] for row in jobs_rows]
    ax_age.plot(xs, ages, color="#ff7f0e", linewidth=2, label="Oldest pending age")
    # SLO reference line — 10s is a common default for user-perceived async lag
    ax_age.axhline(10, color="#888", linestyle="--", linewidth=1, alpha=0.7,
                   label="10s SLO reference")
    ax_age.set_ylabel("Seconds")
    ax_age.set_title("Job Age (latency-to-start)")
    ax_age.grid(alpha=0.25)
    ax_age.legend(loc="upper left")

    # Panel 3 — throughput
    ax_tput.plot(xs, [row["jobs_per_minute"] for row in jobs_rows],
                 color="#1f77b4", linewidth=2, label="Jobs/min")
    ax_tput.set_ylabel("Jobs / minute")
    ax_tput.set_xlabel("Time")
    ax_tput.set_title("Jobs Throughput")
    ax_tput.grid(alpha=0.25)
    ax_tput.legend(loc="upper left")

    apply_time_axis(ax_tput)

    fig.tight_layout()
    fig.savefig(output_dir / f"jobs_queue_{slugify(label)}.png")
    plt.close(fig)


def load_postgres_health(path):
    """Load postgres-health.csv produced by collect-postgres-metrics.sh.
    Returns list of dicts sorted by timestamp."""
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row["timestamp"] = parse_timestamp(row["timestamp"])
            for key, value in row.items():
                if key == "timestamp":
                    continue
                row[key] = parse_numeric(value)
            rows.append(row)
    return rows


def load_redis_health(path):
    """Load redis-health.csv. Cumulative counters from INFO stats are kept as-is
    in the CSV; here we derive a *rolling-window* hit ratio per sample (delta
    hits / (delta hits + delta misses) since the previous sample), which
    reflects the current workload — the cumulative ratio drifts toward the
    pod-lifetime average and lags the current behavior. First sample has no
    predecessor so it falls back to the cumulative value.
    """
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row["timestamp"] = parse_timestamp(row["timestamp"])
            for key, value in row.items():
                if key == "timestamp":
                    continue
                row[key] = parse_numeric(value)
            rows.append(row)

    prev_hits = None
    prev_misses = None
    for row in rows:
        h = row.get("keyspace_hits_cumulative", 0) or 0
        m = row.get("keyspace_misses_cumulative", 0) or 0
        if prev_hits is None:
            # First sample — fall back to cumulative ratio
            ratio = 100.0 * h / (h + m) if (h + m) > 0 else 100.0
        else:
            dh = h - prev_hits
            dm = m - prev_misses
            # Counter reset (e.g. Redis restart) → window collapses; report
            # cumulative for that sample to avoid a misleading 0/0 spike.
            if dh < 0 or dm < 0:
                ratio = 100.0 * h / (h + m) if (h + m) > 0 else 100.0
            elif (dh + dm) == 0:
                # No keyspace activity in this window — keep the previous
                # rolling ratio rather than reporting 100% on a quiet bucket.
                ratio = row.get("hit_ratio_percent", 100.0)
            else:
                ratio = 100.0 * dh / (dh + dm)
        row["hit_ratio_percent"] = round(ratio, 2)
        prev_hits, prev_misses = h, m

    # Also expose the cumulative ratio for diagnostics — useful to compare
    # workload drift vs pod-lifetime average.
    for row in rows:
        h = row.get("keyspace_hits_cumulative", 0) or 0
        m = row.get("keyspace_misses_cumulative", 0) or 0
        row["hit_ratio_cumulative_percent"] = round(
            100.0 * h / (h + m) if (h + m) > 0 else 100.0, 2)
    return rows


def plot_db_health(output_dir, label, pg_rows, web_mem_limit_mb=None):
    """4-panel Postgres health chart for "DB is not the bottleneck" defense.

    Panel 1: CPU% (vs 70% threshold) and memory MiB
    Panel 2: Active connections / max_connections (% utilization)
    Panel 3: Cache hit ratio (% — should stay > 99%) and slow queries (>1s)
    Panel 4: Lock waits + idle-in-transaction (both should be 0)
    """
    if not pg_rows:
        return

    fig, axes = plt.subplots(4, 1, figsize=(12, 13), sharex=True)
    ax_cpu, ax_conn, ax_cache, ax_lock = axes
    xs = [row["timestamp"] for row in pg_rows]

    # Panel 1 — CPU + memory
    cpu_ms = [row.get("postgres_cpu_millicores", 0) or 0 for row in pg_rows]
    mem_mb = [row.get("postgres_memory_mib", 0) or 0 for row in pg_rows]
    ax_cpu.plot(xs, cpu_ms, color="#d62728", linewidth=2, label="CPU (millicores)")
    ax_cpu.set_ylabel("CPU millicores", color="#d62728")
    ax_cpu.tick_params(axis="y", labelcolor="#d62728")
    ax_cpu_r = ax_cpu.twinx()
    ax_cpu_r.plot(xs, mem_mb, color="#2ca02c", linewidth=1.5, alpha=0.8,
                  label="Memory (MiB)")
    ax_cpu_r.set_ylabel("Memory MiB", color="#2ca02c")
    ax_cpu_r.tick_params(axis="y", labelcolor="#2ca02c")
    ax_cpu.set_title(f"Postgres CPU and Memory ({label})")
    ax_cpu.grid(alpha=0.25)
    handles = ax_cpu.get_lines() + ax_cpu_r.get_lines()
    ax_cpu.legend(handles, [h.get_label() for h in handles], loc="upper left")

    # Panel 2 — connection utilization
    active = [row.get("active_conns", 0) or 0 for row in pg_rows]
    max_conn_series = [row.get("max_connections", 0) or 0 for row in pg_rows]
    max_conn = max(max_conn_series, default=100)
    util_pct = [(a / max_conn * 100.0) if max_conn > 0 else 0 for a in active]
    ax_conn.plot(xs, util_pct, color="#1f77b4", linewidth=2,
                 label=f"Active conns / max ({max_conn})")
    ax_conn.axhline(50, color="#888", linestyle="--", linewidth=1, alpha=0.7,
                    label="50% safety threshold")
    ax_conn.set_ylabel("% of max_connections")
    ax_conn.set_title("Postgres Connection Pool Utilization")
    ax_conn.grid(alpha=0.25)
    ax_conn.legend(loc="upper left")

    # Panel 3 — cache hit ratio + slow queries
    hit_ratio = [row.get("cache_hit_ratio_percent", 100) or 100 for row in pg_rows]
    slow = [row.get("slow_queries_over_1s", 0) or 0 for row in pg_rows]
    ax_cache.plot(xs, hit_ratio, color="#2ca02c", linewidth=2, label="Cache hit ratio %")
    ax_cache.axhline(99, color="#888", linestyle="--", linewidth=1, alpha=0.7,
                     label="99% threshold")
    ax_cache.set_ylabel("Hit ratio %", color="#2ca02c")
    ax_cache.tick_params(axis="y", labelcolor="#2ca02c")
    ax_cache.set_ylim(95, 100.5)
    ax_cache_r = ax_cache.twinx()
    ax_cache_r.plot(xs, slow, color="#d62728", linewidth=1.5, alpha=0.8,
                    label="Slow queries (>1s)")
    ax_cache_r.set_ylabel("Slow query count", color="#d62728")
    ax_cache_r.tick_params(axis="y", labelcolor="#d62728")
    ax_cache.set_title("Postgres Cache Hit Ratio and Slow Queries")
    ax_cache.grid(alpha=0.25)
    handles = ax_cache.get_lines() + ax_cache_r.get_lines()
    ax_cache.legend(handles, [h.get_label() for h in handles], loc="lower left")

    # Panel 4 — lock waits + idle in tx (contention indicators)
    locks = [row.get("waiting_on_locks", 0) or 0 for row in pg_rows]
    idle_tx = [row.get("idle_in_tx_conns", 0) or 0 for row in pg_rows]
    ax_lock.plot(xs, locks, color="#d62728", linewidth=2, label="Waiting on locks")
    ax_lock.plot(xs, idle_tx, color="#ff7f0e", linewidth=1.5, alpha=0.8,
                 label="Idle in transaction")
    ax_lock.set_ylabel("Connection count")
    ax_lock.set_xlabel("Time")
    ax_lock.set_title("Postgres Contention Indicators (both should stay at 0)")
    ax_lock.grid(alpha=0.25)
    ax_lock.legend(loc="upper left")

    apply_time_axis(ax_lock)

    fig.tight_layout()
    fig.savefig(output_dir / f"db_health_{slugify(label)}.png")
    plt.close(fig)


def plot_redis_health(output_dir, label, redis_rows):
    """2-panel Redis health chart.

    Panel 1: CPU + memory used vs maxmemory
    Panel 2: Hit ratio % + ops/sec + cumulative evictions
    """
    if not redis_rows:
        return

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    ax_res, ax_perf = axes
    xs = [row["timestamp"] for row in redis_rows]

    # Panel 1 — CPU + memory
    cpu = [row.get("redis_cpu_millicores", 0) or 0 for row in redis_rows]
    used = [row.get("redis_memory_used_mb", 0) or 0 for row in redis_rows]
    ax_res.plot(xs, cpu, color="#d62728", linewidth=2, label="CPU (millicores)")
    ax_res.set_ylabel("CPU millicores", color="#d62728")
    ax_res.tick_params(axis="y", labelcolor="#d62728")
    ax_res_r = ax_res.twinx()
    ax_res_r.plot(xs, used, color="#2ca02c", linewidth=1.5, label="Memory used (MB)")
    max_mb = max((row.get("redis_memory_max_mb", 0) or 0 for row in redis_rows),
                 default=0)
    if max_mb > 0:
        ax_res_r.axhline(max_mb, color="#888", linestyle="--", linewidth=1,
                         alpha=0.7, label=f"maxmemory ({max_mb} MB)")
    ax_res_r.set_ylabel("Memory MB", color="#2ca02c")
    ax_res_r.tick_params(axis="y", labelcolor="#2ca02c")
    ax_res.set_title(f"Redis CPU and Memory ({label})")
    ax_res.grid(alpha=0.25)
    handles = ax_res.get_lines() + ax_res_r.get_lines()
    ax_res.legend(handles, [h.get_label() for h in handles], loc="upper left")

    # Panel 2 — hit ratio + ops/sec + evictions
    hit_ratio = [row.get("hit_ratio_percent", 100) or 100 for row in redis_rows]
    ops = [row.get("ops_per_sec", 0) or 0 for row in redis_rows]
    evictions = [row.get("evicted_keys_cumulative", 0) or 0 for row in redis_rows]

    ax_perf.plot(xs, hit_ratio, color="#2ca02c", linewidth=2, label="Hit ratio %")
    ax_perf.axhline(95, color="#888", linestyle="--", linewidth=1, alpha=0.7,
                    label="95% threshold")
    ax_perf.set_ylabel("Hit ratio %", color="#2ca02c")
    ax_perf.tick_params(axis="y", labelcolor="#2ca02c")
    ax_perf.set_ylim(0, 105)

    ax_perf_r = ax_perf.twinx()
    ax_perf_r.plot(xs, ops, color="#1f77b4", linewidth=1.5, alpha=0.8,
                   label="Ops/sec")
    if max(evictions, default=0) > 0:
        ax_perf_r.plot(xs, evictions, color="#d62728", linewidth=1.5,
                       label="Evictions (cumulative)")
    ax_perf_r.set_ylabel("Ops/sec or evictions")
    ax_perf.set_title("Redis Performance and Cache Effectiveness")
    ax_perf.set_xlabel("Time")
    ax_perf.grid(alpha=0.25)
    handles = ax_perf.get_lines() + ax_perf_r.get_lines()
    ax_perf.legend(handles, [h.get_label() for h in handles], loc="lower left")

    apply_time_axis(ax_perf)

    fig.tight_layout()
    fig.savefig(output_dir / f"redis_health_{slugify(label)}.png")
    plt.close(fig)


def compute_db_summary(pg_rows, redis_rows):
    """Reduce Postgres + Redis health CSVs to scalar invariants. Returns 0
    for everything when no data so summary CSV stays consistent across runs.
    """
    out = {
        "peak_postgres_cpu_millicores": 0,
        "peak_postgres_memory_mib": 0,
        "peak_active_conns": 0,
        "max_db_lock_waits": 0,
        "max_db_idle_in_tx": 0,
        "total_slow_queries_over_1s": 0,
        "min_cache_hit_ratio_percent": 100.0,
        "peak_redis_cpu_millicores": 0,
        "peak_redis_memory_mb": 0,
        "peak_redis_memory_percent": None,
        "min_redis_hit_ratio_percent": 100.0,
        "redis_evictions_total": 0,
    }
    if pg_rows:
        out["peak_postgres_cpu_millicores"] = int(max((r.get("postgres_cpu_millicores", 0) or 0 for r in pg_rows), default=0))
        out["peak_postgres_memory_mib"]    = int(max((r.get("postgres_memory_mib", 0) or 0 for r in pg_rows), default=0))
        out["peak_active_conns"]           = int(max((r.get("active_conns", 0) or 0 for r in pg_rows), default=0))
        out["max_db_lock_waits"]           = int(max((r.get("waiting_on_locks", 0) or 0 for r in pg_rows), default=0))
        out["max_db_idle_in_tx"]           = int(max((r.get("idle_in_tx_conns", 0) or 0 for r in pg_rows), default=0))
        out["total_slow_queries_over_1s"]  = int(max((r.get("slow_queries_over_1s", 0) or 0 for r in pg_rows), default=0))
        ratios = [r.get("cache_hit_ratio_percent", 100) or 100 for r in pg_rows]
        out["min_cache_hit_ratio_percent"] = round(min(ratios, default=100.0), 2)
    if redis_rows:
        out["peak_redis_cpu_millicores"]   = int(max((r.get("redis_cpu_millicores", 0) or 0 for r in redis_rows), default=0))
        out["peak_redis_memory_mb"]        = int(max((r.get("redis_memory_used_mb", 0) or 0 for r in redis_rows), default=0))
        # Percentage-of-maxmemory invariant — only meaningful when Redis is
        # configured with an explicit cap. Bare `redis:alpine` defaults to
        # maxmemory=0 (unlimited); the manifest now sets --maxmemory 256mb so
        # this column is computable for runs from Stage 3 onward. Older runs
        # (Stages 1–2) still have redis_memory_max_mb=0 in their CSVs, in
        # which case we leave the percent column as None rather than dividing
        # by zero, and rely on the absolute peak_redis_memory_mb instead.
        max_mb_samples = [r.get("redis_memory_max_mb", 0) or 0 for r in redis_rows]
        max_mb = max(max_mb_samples, default=0)
        if max_mb > 0:
            pcts = [
                100.0 * (r.get("redis_memory_used_mb", 0) or 0) / max_mb
                for r in redis_rows
            ]
            out["peak_redis_memory_percent"] = round(max(pcts, default=0.0), 2)
        ratios = [r.get("hit_ratio_percent", 100) or 100 for r in redis_rows]
        out["min_redis_hit_ratio_percent"] = round(min(ratios, default=100.0), 2)
        out["redis_evictions_total"]       = int(max((r.get("evicted_keys_cumulative", 0) or 0 for r in redis_rows), default=0))
    return out


def compute_jobs_summary(jobs_rows):
    """Reduce jobs-queue.csv to scalar metrics for the summary CSV row.

    Returns 0 for every metric when no data present so summary CSV stays
    consistent across runs (some old runs predate the collector).
    """
    if not jobs_rows:
        return {
            "peak_queue_depth": 0,
            "avg_queue_depth": 0.0,
            "peak_job_age_sec": 0.0,
            "avg_job_age_sec": 0.0,
            "peak_jobs_per_minute": 0.0,
            "avg_jobs_per_minute": 0.0,
            "total_jobs_processed": 0,
            "peak_failed_jobs": 0,
        }

    pending = [row["pending"] for row in jobs_rows]
    ages = [row["oldest_pending_age_sec"] for row in jobs_rows]
    rates = [row["jobs_per_minute"] for row in jobs_rows]
    failed = [row["failed"] for row in jobs_rows]
    processed_first = jobs_rows[0].get("total_processed_cumulative", 0) or 0
    processed_last = jobs_rows[-1].get("total_processed_cumulative", 0) or 0

    return {
        "peak_queue_depth":       int(max(pending, default=0)),
        "avg_queue_depth":        round(sum(pending) / len(pending), 2) if pending else 0.0,
        "peak_job_age_sec":       round(max(ages, default=0), 2),
        "avg_job_age_sec":        round(sum(ages) / len(ages), 2) if ages else 0.0,
        "peak_jobs_per_minute":   round(max(rates, default=0), 2),
        "avg_jobs_per_minute":    round(sum(rates) / len(rates), 2) if rates else 0.0,
        "total_jobs_processed":   int(max(0, processed_last - processed_first)),
        "peak_failed_jobs":       int(max(failed, default=0)),
    }


def plot_cpu_replicas(output_dir, label, cpu_values, snapshots,
                      vus_values=None, test_start=None):
    if not cpu_values and not snapshots:
        return

    if test_start is None and cpu_values:
        test_start = cpu_values[0][0]
    if test_start is None and snapshots:
        test_start = snapshots[0]["timestamp"]
    if test_start is None:
        return

    fig, ax1 = plt.subplots(figsize=(12, 5))

    # VU overlay first so foreground series stack on top
    overlay_vus_per_run(ax1, vus_values, test_start)

    if cpu_values:
        xs, ys = to_minutes_from_start(cpu_values, test_start)
        ax1.plot(xs, ys, color="#2ca02c", linewidth=2, label="Web CPU")

    ax1.set_title(f"Canvas Web CPU and Replica Count ({label})")
    ax1.set_xlabel("Minutes from test start")
    ax1.set_ylabel("CPU cores", color="#2ca02c")
    ax1.tick_params(axis="y", labelcolor="#2ca02c")
    ax1.grid(alpha=0.25)

    ax2 = ax1.twinx()
    replica_line_drawn = False
    if snapshots:
        rep_series = [(row["timestamp"], row["web_ready_replicas"] or row["web_spec_replicas"])
                      for row in snapshots]
        xs_rep, ys_rep = to_minutes_from_start(rep_series, test_start)
        # Only draw replica line if it actually changes — flat lines (baseline=1,
        # prescaled=5) carry no information and clutter the chart.
        if len(set(ys_rep)) > 1:
            # Push spine outward so it doesn't overlap the VU twin axis
            ax2.spines["right"].set_position(("outward", 55))
            ax2.step(xs_rep, ys_rep, where="post", color="#9467bd",
                     linewidth=2, label="Ready replicas")
            replica_line_drawn = True
    if replica_line_drawn:
        ax2.set_ylabel("Replica count", color="#9467bd")
        ax2.tick_params(axis="y", labelcolor="#9467bd")
    else:
        ax2.set_yticks([])
        ax2.set_ylabel("")

    handles = ax1.get_lines() + ax2.get_lines()
    if handles:
        ax1.legend(handles, [line.get_label() for line in handles], loc="upper left")

    apply_minute_axis(ax1, test_start)
    fig.tight_layout()
    fig.savefig(output_dir / f"cpu_replicas_{slugify(label)}.png")
    plt.close(fig)


def parse_memory_limit_mb(limit_str):
    """Convert a Kubernetes memory limit string (e.g. '3Gi', '3500Mi') to MiB.

    Returns binary MiB (1 MiB = 1024² bytes) to match the Prometheus query
    that divides container_memory_working_set_bytes by 1024² (1_048_576).
    The legacy field name `_mb` is kept for backward compatibility; the
    unit is binary MiB throughout the chart pipeline. Users commonly call
    this "MB" in everyday usage.
    """
    if not limit_str:
        return None
    limit_str = limit_str.strip()
    try:
        if limit_str.endswith("Gi"):
            return int(limit_str[:-2]) * 1024          # 3Gi → 3072 MiB
        if limit_str.endswith("Mi"):
            return int(limit_str[:-2])                  # 3072Mi → 3072 MiB
        if limit_str.endswith("Ki"):
            return int(limit_str[:-2]) / 1024
        return int(limit_str) / 1024 ** 2              # bare bytes → MiB
    except ValueError:
        return None


def _short_pod_label(pod_name, deployment_prefix):
    """Return a compact line label for a per-pod memory series.

    K8s pod names follow ``<deployment>-<rs_hash>-<5char_random>``; the
    final 5-char suffix is unique within a deployment and stays stable
    for the lifetime of that pod instance, so it's the right anchor for
    a chart legend. Falls back to the raw name if the convention is not
    followed (e.g. statefulset, bare pod).
    """
    if not pod_name:
        return deployment_prefix
    suffix = pod_name.rsplit("-", 1)[-1]
    if 3 <= len(suffix) <= 10:
        return f"{deployment_prefix}-{suffix}"
    return pod_name


def plot_memory(output_dir, label,
                web_memory_per_pod, jobs_memory_per_pod,
                web_memory_limit_mb=None, jobs_memory_limit_mb=None,
                saturation_time=None, saturation_vu=None,
                vus_values=None, test_start=None,
                split_threshold=4):
    """One line per pod so OOM risk is directly visible against the
    per-pod limit reference. Each line is comparable to the limit line
    because there is no cross-pod aggregation.

    Layout adapts to pod count:
      - ≤ split_threshold total pods (web + jobs combined): single panel
        with both deployments on the same axes, so the eye can compare
        memory pressure side-by-side.
      - > split_threshold total pods: two stacked panels (web on top,
        jobs on bottom, sharex) so the legend stays readable when
        Stage 2/3/4 have 5+ pods.

    The aggregated web_memory / jobs_memory series (sum across pods) are
    NOT used here — that path remains intact upstream because the
    summary CSV's avg_*_memory_mb fields still consume them.

    Limit reference lines are only drawn when the caller passes explicit
    values (via metadata.env or CLI flag). They are per-pod limits, which
    now correctly compare against the per-pod data lines.
    """
    web_pods = sorted((web_memory_per_pod or {}).keys())
    jobs_pods = sorted((jobs_memory_per_pod or {}).keys())
    if not web_pods and not jobs_pods:
        return

    if test_start is None:
        # First sample of any pod establishes the minute-zero anchor.
        for pod_dict in (web_memory_per_pod, jobs_memory_per_pod):
            for pod in sorted((pod_dict or {}).keys()):
                series = pod_dict[pod]
                if series:
                    test_start = series[0][0]
                    break
            if test_start is not None:
                break
    if test_start is None:
        return

    total_pods = len(web_pods) + len(jobs_pods)
    split = total_pods > split_threshold

    if split:
        # Two stacked panels — sharex so VU profile aligns across them.
        fig, (ax_web, ax_jobs) = plt.subplots(
            2, 1, figsize=(12, 8), sharex=True,
        )
        axes_for_vus = [ax_web, ax_jobs]
    else:
        fig, ax = plt.subplots(figsize=(12, 5))
        ax_web = ax
        ax_jobs = ax
        axes_for_vus = [ax]

    # VU overlay first so foreground lines stack above. On split mode each
    # panel gets its own overlay so the right-hand VU axis is labelled
    # consistently on both.
    for axis in axes_for_vus:
        overlay_vus_per_run(axis, vus_values, test_start)

    # Distinct hues — not just shades — so adjacent pod lines never bleed
    # into one another at a glance. Deployment grouping is preserved via
    # cool tones (web) vs warm tones (jobs); within each group every entry
    # is a different hue rather than a different brightness. Pod-instance
    # identity is also reinforced by a per-pod marker shape so the legend
    # works even when printed in greyscale.
    web_palette = ["#1f77b4", "#17becf", "#2ca02c", "#9467bd", "#1a55a3",
                   "#5e35b1", "#00838f", "#558b2f", "#0d47a1", "#4527a0"]
    jobs_palette = ["#ff7f0e", "#d62728", "#8c564b", "#e377c2", "#bcbd22",
                    "#c2185b", "#f57f17", "#5d4037", "#bf360c", "#827717"]
    marker_cycle = ["o", "s", "D", "^", "v", "P", "X", "h", "*", ">"]

    def _plot_deployment(ax_target, pods, pod_dict, palette, prefix):
        for i, pod in enumerate(pods):
            series = pod_dict.get(pod) or []
            if not series:
                continue
            xs, ys = to_minutes_from_start(series, test_start)
            ax_target.plot(
                xs, ys,
                color=palette[i % len(palette)],
                linewidth=1.8, alpha=0.95,
                marker=marker_cycle[i % len(marker_cycle)],
                markersize=4.0,
                markerfacecolor=palette[i % len(palette)],
                markeredgecolor="white", markeredgewidth=0.6,
                # Markers every ~12 ticks keep the line readable at long
                # durations (18 min × 4 samples/min = 72 ticks → 6 markers)
                # while still acting as a per-pod glyph for the legend.
                markevery=max(1, len(xs) // 12),
                label=_short_pod_label(pod, prefix) + " (MB)",
            )

    _plot_deployment(ax_web, web_pods, web_memory_per_pod, web_palette, "web")
    _plot_deployment(ax_jobs, jobs_pods, jobs_memory_per_pod, jobs_palette, "jobs")

    # Limit reference lines drawn on the panel that contains the data.
    # In single-panel mode that's the shared `ax`; in split mode the web
    # limit goes on ax_web only, jobs limit on ax_jobs only.
    if web_memory_limit_mb is not None and web_pods:
        ax_web.axhline(
            y=web_memory_limit_mb, color="#1f77b4",
            linewidth=1.5, linestyle="--", alpha=0.7,
            label=f"Web limit ({web_memory_limit_mb/1024:.0f} GB = {web_memory_limit_mb:.0f} MB)",
        )
    if jobs_memory_limit_mb is not None and jobs_pods:
        ax_jobs.axhline(
            y=jobs_memory_limit_mb, color="#ff7f0e",
            linewidth=1.5, linestyle="--", alpha=0.7,
            label=f"Jobs limit ({jobs_memory_limit_mb/1024:.0f} GB = {jobs_memory_limit_mb:.0f} MB)",
        )

    if split:
        ax_web.set_title(f"Memory Working Set — per pod ({label})", fontsize=12)
        ax_web.set_ylabel("Web memory (MB)")
        ax_jobs.set_ylabel("Jobs memory (MB)")
        ax_jobs.set_xlabel("Minutes from test start")
        for axis in (ax_web, ax_jobs):
            axis.set_ylim(bottom=0)
            axis.legend(loc="upper left", fontsize=9)
            axis.grid(alpha=0.25)
            apply_minute_axis(axis, test_start)
    else:
        ax.set_title(f"Memory Working Set — per pod ({label})")
        ax.set_xlabel("Minutes from test start")
        ax.set_ylabel("Memory (MB)")
        ax.set_ylim(bottom=0)
        ax.legend(loc="upper left", fontsize=9)
        ax.grid(alpha=0.25)
        apply_minute_axis(ax, test_start)

    fig.tight_layout()
    fig.savefig(output_dir / f"memory_{slugify(label)}.png", bbox_inches="tight")
    plt.close(fig)


def plot_hpa_cpu(output_dir, label, hpa_cpu_values):
    """HPA CPU utilisation % with 70 % scale-out threshold line.

    Matches Grafana panel 14 exactly — same metric
    (kube_horizontalpodautoscaler_status_current_metrics_average_utilization),
    same 70 % reference line, same y-axis range 0–150 %.
    """
    if not hpa_cpu_values:
        return

    fig, ax = plt.subplots(figsize=(12, 5))
    xs = [x for x, _ in hpa_cpu_values]
    ys = [y for _, y in hpa_cpu_values]
    ax.plot(xs, ys, color="#2ca02c", label="canvas-web CPU % (HPA view)", linewidth=2)
    ax.axhline(y=70, color="#d62728", linewidth=2, linestyle="--", label="Scale-out threshold (70%)")
    ax.set_title(f"HPA CPU Utilisation % ({label})")
    ax.set_xlabel("Time")
    ax.set_ylabel("CPU utilisation (%)")
    ax.set_ylim(0, 150)
    ax.legend()
    ax.grid(alpha=0.25)
    apply_time_axis(ax)
    fig.tight_layout()
    fig.savefig(output_dir / f"hpa_cpu_{slugify(label)}.png")
    plt.close(fig)


def plot_restart_counts(output_dir, label, snapshots,
                        saturation_time=None, saturation_vu=None,
                        vus_values=None):
    """Pod restart count DURING the test, rebased to zero at test start.

    `web_restart_total` and `jobs_restart_total` in the snapshot CSV are
    Kubernetes container restartCount values — lifetime counters that do
    not reset when the test begins and can be non-zero from prior incidents.
    Subtracting the first sample isolates restarts that happened in the
    test window only.
    """
    if not snapshots:
        return

    test_start = snapshots[0]["timestamp"]

    fig, ax = plt.subplots(figsize=(12, 5))
    overlay_vus_per_run(ax, vus_values, test_start)

    xs_web,  ys_web  = restart_delta_series(snapshots, "web_restart_total")
    xs_jobs, ys_jobs = restart_delta_series(snapshots, "jobs_restart_total")
    if xs_web:
        ax.step(xs_web,  ys_web,  where="post",
                color="#d62728", linewidth=2, label="Web restarts (in-test)")
    if xs_jobs:
        ax.step(xs_jobs, ys_jobs, where="post",
                color="#ff7f0e", linewidth=2, label="Jobs restarts (in-test)")

    ax.set_title(f"Pod Restart Count ({label})")
    ax.set_xlabel("Minutes from test start")
    ax.set_ylabel("Restart count (from test start)")
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.25)
    apply_minute_axis(ax, test_start)
    fig.tight_layout()
    fig.savefig(output_dir / f"pod_restart_count_{slugify(label)}.png", bbox_inches="tight")
    plt.close(fig)


def compute_scale_events(snapshots):
    if not snapshots:
        return [], [], 0

    scale_out = []
    scale_in = []
    pending_out = None
    pending_in = None
    direction_changes = 0
    previous_direction = 0
    previous_desired = snapshots[0]["web_hpa_desired_replicas"] or snapshots[0]["web_spec_replicas"]

    for row in snapshots[1:]:
        desired = row["web_hpa_desired_replicas"] or row["web_spec_replicas"]
        ready = row["web_ready_replicas"] or row["web_spec_replicas"]
        direction = 0
        if desired > previous_desired:
            direction = 1
        elif desired < previous_desired:
            direction = -1

        if direction and previous_direction and direction != previous_direction:
            direction_changes += 1
        if direction:
            previous_direction = direction

        if desired > previous_desired:
            pending_out = {"start": row["timestamp"], "target": desired}
        elif desired < previous_desired:
            pending_in = {"start": row["timestamp"], "target": desired}

        if pending_out and ready >= pending_out["target"]:
            scale_out.append((pending_out["start"], (row["timestamp"] - pending_out["start"]).total_seconds()))
            pending_out = None

        if pending_in and ready <= pending_in["target"]:
            scale_in.append((pending_in["start"], (row["timestamp"] - pending_in["start"]).total_seconds()))
            pending_in = None

        previous_desired = desired

    return scale_out, scale_in, direction_changes


def plot_scale_latency(output_dir, label, snapshots):
    scale_out, scale_in, direction_changes = compute_scale_events(snapshots)
    if not scale_out and not scale_in:
        return None

    fig, ax = plt.subplots(figsize=(10, 5))
    names = []
    values = []
    colors = []

    for index, (_, seconds) in enumerate(scale_out, start=1):
        names.append(f"Out {index}")
        values.append(seconds)
        colors.append("#2ca02c")

    for index, (_, seconds) in enumerate(scale_in, start=1):
        names.append(f"In {index}")
        values.append(seconds)
        colors.append("#ff7f0e")

    ax.bar(names, values, color=colors)
    ax.set_title(f"Scale Latency ({label})")
    ax.set_ylabel("Seconds")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_dir / f"scale_latency_{slugify(label)}.png")
    plt.close(fig)

    return {
        "scale_out_events": len(scale_out),
        "scale_in_events": len(scale_in),
        "avg_scale_out_latency_seconds": sum(item[1] for item in scale_out) / len(scale_out) if scale_out else 0,
        "avg_scale_in_latency_seconds": sum(item[1] for item in scale_in) / len(scale_in) if scale_in else 0,
        "oscillation_count": direction_changes,
    }


def average_value(values):
    if not values:
        return 0.0
    return sum(value for _, value in values) / len(values)


def infer_scaling_mode(snapshots):
    """Infer baseline / hpa / prescaled from k8s snapshot data.

    The previous version hard-coded prescaled=5 replicas, which broke when
    Stage 2 was max-packed at 3 web pods on m6a.2xlarge — the run was
    classified as "unknown". Detect prescaled by "no HPA active AND replica
    count is constant > 1" instead, accepting any fixed pod count.
    """
    if not snapshots:
        return "unknown"
    web_specs = [row["web_spec_replicas"] for row in snapshots]
    min_spec = int(min(web_specs))
    max_spec = int(max(web_specs))
    has_hpa = any(row.get("web_hpa_desired_replicas", 0) > 0 for row in snapshots)
    if min_spec == 1 and max_spec == 1 and not has_hpa:
        return "baseline"
    if has_hpa or (min_spec < max_spec):
        return "hpa"
    # Constant >1 replicas with no HPA → prescaled (any size, e.g. 3 or 5).
    if min_spec == max_spec and min_spec > 1 and not has_hpa:
        return "prescaled"
    return "unknown"


def k6_or_prom(k6_summary, k6_key, prom_value, scale=1.0):
    """Prefer k6 final-summary value over Prometheus time-average.

    k6 summary metrics (error rate, p50, p95, throughput) are computed
    over every request in the test and are unaffected by the setup() phase
    or equal-weight time-averaging that Prometheus applies.

    The `scale` factor (e.g. 1000 to convert seconds → milliseconds) is
    applied to BOTH paths so callers can rely on a consistent unit
    regardless of whether k6 supplied the value or Prometheus did.
    """
    v = k6_summary.get(k6_key)
    if v is not None:
        return round(v * scale, 3)
    return round(prom_value * scale, 3)


def plot_comparison_p95(output_dir, comparison_rows):
    if not comparison_rows:
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    labels = [row["label"] for row in comparison_rows]
    values = [row["avg_p95_ms"] for row in comparison_rows]
    ax.bar(labels, values, color="#4c78a8")
    ax.set_title("Average P95 Latency Comparison")
    ax.set_ylabel("P95 latency (ms)")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_dir / "comparison_p95_latency.png")
    plt.close(fig)


def write_summary(output_dir, label, metrics):
    summary_path = output_dir / f"summary_{slugify(label)}.csv"
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value"])
        for key, value in metrics.items():
            writer.writerow([key, value])


def series_for_metric(base_url, metric_name, selector, start, end, step):
    query = f"{metric_name}{selector}"
    return select_first_series(query_range(base_url, query, start, end, step))


def fallback_latency_series(start, end, step, seconds_value):
    if seconds_value <= 0:
        return []

    step_delta = dt.timedelta(seconds=int(step.rstrip("s")) if str(step).endswith("s") else 15)
    current = start
    values = []
    while current <= end:
        values.append((current, seconds_value))
        current += step_delta
    return values


def apply_k6_summary_fallbacks(latency, throughput, error_rate, vus, start, end, step, k6_summary_metrics):
    fallback_used = False

    if not latency["p95"] and k6_summary_metrics.get("p95", 0.0) > 0:
        latency["p95"] = fallback_latency_series(start, end, step, k6_summary_metrics["p95"])
        fallback_used = True
    if not latency["p50"] and k6_summary_metrics.get("avg", 0.0) > 0:
        latency["p50"] = fallback_latency_series(start, end, step, k6_summary_metrics["avg"])
        fallback_used = True
    if not latency["p99"] and k6_summary_metrics.get("p95", 0.0) > 0:
        latency["p99"] = fallback_latency_series(start, end, step, k6_summary_metrics["p95"])
        fallback_used = True
    if not throughput and k6_summary_metrics.get("throughput_rps", 0.0) > 0:
        throughput = fallback_latency_series(start, end, step, k6_summary_metrics["throughput_rps"])
        fallback_used = True
    if not error_rate and k6_summary_metrics.get("error_rate_percent", 0.0) > 0:
        error_rate = fallback_latency_series(start, end, step, k6_summary_metrics["error_rate_percent"])
        fallback_used = True
    if not vus and k6_summary_metrics.get("max_vus", 0.0) > 0:
        vus = fallback_latency_series(start, end, step, k6_summary_metrics["max_vus"])
        fallback_used = True

    return latency, throughput, error_rate, vus, fallback_used


MAX_PROMETHEUS_POINTS = 10000


def safe_step(start, end, requested_step_str):
    """Return a step string that keeps data points under MAX_PROMETHEUS_POINTS."""
    try:
        step_seconds = int(requested_step_str.rstrip("s")) if requested_step_str.endswith("s") else 15
    except ValueError:
        step_seconds = 15
    range_seconds = (end - start).total_seconds()
    min_step = max(step_seconds, int(range_seconds / MAX_PROMETHEUS_POINTS) + 1)
    return f"{min_step}s"


def run_window(args, run_dir):
    metadata = load_env_file(run_dir / "metadata.env") if run_dir else {}
    if metadata.get("started_at"):
        start = parse_timestamp(metadata["started_at"])
        if metadata.get("ended_at"):
            end = parse_timestamp(metadata["ended_at"])
        else:
            # No ended_at — cap to 2 hours after start to avoid Prometheus resolution errors
            end = min(start + dt.timedelta(hours=2), dt.datetime.now(dt.UTC))
        return start.astimezone(dt.UTC), end.astimezone(dt.UTC), metadata

    end = dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(minutes=args.minutes)
    return start, end, metadata


def collect_run_metrics(base_url, selector, start, end, step):
    latency = {}
    for pct in ("p50", "p95", "p99"):
        result, _ = try_queries(
            base_url,
            [f"avg(k6_http_req_duration_{pct}{selector})"],
            start,
            end,
            step,
        )
        latency[pct] = select_first_series(result)

    throughput_result, _ = try_queries(
        base_url,
        [f"sum(rate(k6_http_reqs_total{selector}[1m]))"],
        start,
        end,
        step,
    )
    throughput = select_first_series(throughput_result)

    # Counter-based rate: k6_http_reqs_total is a proper Prometheus counter so
    # rate() gives genuine per-window resolution and shows real spikes when
    # errors cluster (e.g. during pod crash windows or HPA scale-in).
    # The gauge-based avg_over_time query is kept as a fallback — it always has
    # data but produces a near-flat line because k6 pre-aggregates the value
    # before shipping it to Prometheus.
    testid_val = selector.strip("{}").split('"')[1] if selector else ""
    error_result, _ = try_queries(
        base_url,
        [
            # Primary: counter-based — shows real variation over time
            f'100 * sum(rate(k6_http_reqs_total{{expected_response="false",testid="{testid_val}"}}[1m])) / sum(rate(k6_http_reqs_total{{testid="{testid_val}"}}[1m]))',
            # Fallback: gauge-based (flat but always populated)
            f"100 * avg_over_time(k6_http_req_failed{selector}[2m])",
            f"100 * avg_over_time(k6_http_req_failed_rate{selector}[2m])",
        ],
        start,
        end,
        step,
    )
    error_rate = select_first_series(error_result)

    vus_result, _ = try_queries(
        base_url,
        [f"max(k6_vus{selector})"],
        start,
        end,
        step,
    )
    vus = select_first_series(vus_result)

    # CPU query: filter to Running pods only (matches Grafana panel exactly).
    # Without the phase join, Terminating / CrashLoopBackOff pods are included,
    # which inflates the CPU reading during pod-crash windows.
    cpu_result, _ = try_queries(
        base_url,
        [
            'sum(rate(container_cpu_usage_seconds_total{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"}[1m]) * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"})',
            # Fallback for older k3s/cAdvisor label schemes (pre-namespace label)
            'sum(rate(container_cpu_usage_seconds_total{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-web-.*"}[1m]))',
        ],
        start,
        end,
        step,
    )
    web_cpu = select_first_series(cpu_result)

    # Memory — working set bytes (excludes file cache, matches kubectl top).
    # Divide by 1 000 000 → MB (decimal, matches Grafana unit "decmbytes").
    #
    # IMPORTANT: cAdvisor continues to report container_memory_working_set_bytes
    # for dead container cgroups for ~30-60s after a container exits, until the
    # kernel garbage-collects them. During a crash-loop (e.g. Stage 1 baseline
    # with 6 OOMKills in 23 minutes), up to 3-4 ghost containers can co-exist
    # with the active container, all reporting their last frozen working_set
    # (~limit value just before OOMKill). The naive `sum()` then inflates the
    # reading to 6-12 GiB even though kernel-enforced per-container limit is
    # 3 GiB. The primary query below filters to only currently-Running
    # containers via kube_pod_container_status_running == 1.
    # Primary query uses `unless on(id) (time() - container_last_seen > 30)` to
    # remove ghost cgroup series whose data is stale. Each container instance
    # (live or dead) has a unique `id` label (cgroup path); cAdvisor exports
    # container_last_seen indicating when each cgroup was last observed alive.
    # Filtering by id+freshness was empirically verified to eliminate the
    # 6 GiB+ spikes seen in Stage 1 baseline runs during OOMKill transitions
    # (e.g. 12:45:50 in Stage 1 run01: 6867 MB without filter → 492 MB with).
    web_memory_result, _ = try_queries(
        base_url,
        [
            # Primary: drop cgroup series whose last_seen is older than 30s.
            'sum(container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-web-.*",container="web"} '
            'unless on(id) '
            '(time() - container_last_seen{namespace="canvas",pod=~"canvas-web-.*",container="web"} > 30)) '
            '/ 1048576',
            # Fallback 1: older namespace-aware schema without freshness filter.
            'sum(container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"} * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}) / 1048576',
            # Fallback 2: legacy cAdvisor label scheme (pre-namespace label).
            'sum(container_memory_working_set_bytes{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-web-.*",container!="",container!="POD"}) / 1048576',
        ],
        start,
        end,
        step,
    )
    web_memory = select_first_series(web_memory_result)

    jobs_memory_result, _ = try_queries(
        base_url,
        [
            # Primary: drop stale cgroup series (see web_memory comment above).
            'sum(container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-jobs-.*",container="jobs"} '
            'unless on(id) '
            '(time() - container_last_seen{namespace="canvas",pod=~"canvas-jobs-.*",container="jobs"} > 30)) '
            '/ 1048576',
            # Fallback 1: older namespace-aware schema without freshness filter.
            'sum(container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-jobs-.*",container!="",container!="POD"} * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}) / 1048576',
            # Fallback 2: legacy cAdvisor label scheme.
            'sum(container_memory_working_set_bytes{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-jobs-.*",container!="",container!="POD"}) / 1048576',
        ],
        start,
        end,
        step,
    )
    jobs_memory = select_first_series(jobs_memory_result)

    # ── Per-pod memory (chart only — summary CSV continues to use sum() above)
    # The chart draws one line per pod so OOM risk is directly visible: each
    # line is comparable to the per-pod memory limit reference line. The
    # underlying metric is the same container_memory_working_set_bytes; we
    # just skip the aggregation. parse_series preserves the `pod` label
    # which is then used as the line identifier.
    # NOTE: this is an additional fetch, NOT a replacement. The aggregated
    # web_memory / jobs_memory series above remain unchanged and continue
    # to feed avg_web_memory_mb / avg_jobs_memory_mb in the summary CSV.
    web_memory_per_pod_result, _ = try_queries(
        base_url,
        [
            'container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-web-.*",container="web"} '
            'unless on(id) '
            '(time() - container_last_seen{namespace="canvas",pod=~"canvas-web-.*",container="web"} > 30)',
            'container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"} * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}',
            'container_memory_working_set_bytes{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-web-.*",container!="",container!="POD"}',
        ],
        start, end, step,
    )
    web_memory_per_pod = _parse_per_pod_memory(web_memory_per_pod_result)

    jobs_memory_per_pod_result, _ = try_queries(
        base_url,
        [
            'container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-jobs-.*",container="jobs"} '
            'unless on(id) '
            '(time() - container_last_seen{namespace="canvas",pod=~"canvas-jobs-.*",container="jobs"} > 30)',
            'container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-jobs-.*",container!="",container!="POD"} * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}',
            'container_memory_working_set_bytes{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-jobs-.*",container!="",container!="POD"}',
        ],
        start, end, step,
    )
    jobs_memory_per_pod = _parse_per_pod_memory(jobs_memory_per_pod_result)

    # HPA CPU utilisation % — calculated directly from cAdvisor.
    # Formula: sum(actualCPU) / sum(cpuRequest) * 100
    # This is mathematically identical to what the HPA controller uses and
    # produces a continuous time-series (unlike the kube-state-metrics metric
    # which is only emitted when the HPA controller is actively sampling).
    # The KSM metric is kept as a cross-check fallback only.
    hpa_cpu_result, _ = try_queries(
        base_url,
        [
            # Primary: cAdvisor-based calculation — always has data
            '100 * sum(rate(container_cpu_usage_seconds_total{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"}[2m]) * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}) / sum(kube_pod_container_resource_requests{namespace="canvas",resource="cpu",pod=~"canvas-web-.*",container!="",container!="POD"})',
            # Fallback: KSM official HPA metric (sparse — only emitted when HPA is sampling)
            'kube_horizontalpodautoscaler_status_current_metrics_average_utilization{namespace="canvas",horizontalpodautoscaler="canvas-web"}',
        ],
        start,
        end,
        step,
    )
    hpa_cpu = select_first_series(hpa_cpu_result)

    return (latency, throughput, error_rate, vus, web_cpu,
            web_memory, jobs_memory,
            web_memory_per_pod, jobs_memory_per_pod,
            hpa_cpu)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:30090")
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--step", default="15s")
    parser.add_argument("--output-dir", default="testing/charts/output")
    parser.add_argument("--testid", default="")
    parser.add_argument("--runs-dir", default="testing/results")
    parser.add_argument("--run-dir", default="",
                        help="Explicit path to the run directory. "
                             "Use when the folder has been renamed (e.g. "
                             "stage1-baseline-vpa-run01-…) but the test_id "
                             "in metadata.env is still the original "
                             "canvas-<ts>. Overrides runs_dir/testid lookup.")
    parser.add_argument("--compare-testids", default="")
    parser.add_argument("--compare-labels", default="")
    parser.add_argument("--web-memory-limit", default="",
                        help="Web container memory limit (e.g. '8Gi', '1Gi'). "
                             "Drawn as a horizontal reference line on memory_*.png. "
                             "Overrides metadata.env web_memory_limit. Omit to "
                             "render no limit line.")
    parser.add_argument("--jobs-memory-limit", default="",
                        help="Jobs container memory limit (e.g. '4Gi'). "
                             "Same semantics as --web-memory-limit.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    compare_testids = [item.strip() for item in args.compare_testids.split(",") if item.strip()]
    compare_labels = [item.strip() for item in args.compare_labels.split(",") if item.strip()]
    comparison_rows = []
    latency_overlays = {}

    if args.testid:
        # --run-dir overrides the default runs_dir/testid lookup so the
        # script keeps working when a folder has been renamed for
        # readability but the in-metadata test_id (used as the
        # Prometheus label) still points to the original canvas-<ts>.
        run_dir = Path(args.run_dir) if args.run_dir else Path(args.runs_dir) / args.testid
        start, end, metadata = run_window(args, run_dir)
        step = safe_step(start, end, args.step)
        selector = f'{{testid="{args.testid}"}}'
        label = metadata.get("test_type", args.testid)
        snapshots = load_snapshots(run_dir / "k8s-snapshots.csv")
        k6_summary_metrics = parse_k6_summary_metrics(run_dir / "k6-summary.txt")
        jobs_rows = load_jobs_queue(run_dir / "jobs-queue.csv")
        pg_rows = load_postgres_health(run_dir / "postgres-health.csv")
        redis_rows = load_redis_health(run_dir / "redis-health.csv")

        (latency, throughput, error_rate, vus, web_cpu,
         web_memory, jobs_memory,
         web_memory_per_pod, jobs_memory_per_pod,
         hpa_cpu) = collect_run_metrics(
            args.prometheus_url, selector, start, end, step
        )
        latency, throughput, error_rate, vus, fallback_used = apply_k6_summary_fallbacks(
            latency, throughput, error_rate, vus, start, end, step, k6_summary_metrics
        )

        # Memory limit reference lines come from one of three sources, in
        # decreasing precedence:
        #   1. --web-memory-limit / --jobs-memory-limit CLI flags
        #   2. metadata.env (web_memory_limit, jobs_memory_limit keys)
        #   3. environment.env snapshot captured at run time
        # If none provide a value, no limit line is drawn (the deployment
        # manifest at chart-rendering time may not match the historical run).
        env_snapshot = load_env_file(run_dir / "environment.env")
        web_mem_limit_mb = (
            parse_memory_limit_mb(args.web_memory_limit)
            or parse_memory_limit_mb(metadata.get("web_memory_limit", ""))
            or parse_memory_limit_mb(env_snapshot.get("web_memory_limit", ""))
        )
        jobs_mem_limit_mb = (
            parse_memory_limit_mb(args.jobs_memory_limit)
            or parse_memory_limit_mb(metadata.get("jobs_memory_limit", ""))
            or parse_memory_limit_mb(env_snapshot.get("jobs_memory_limit", ""))
        )

        scaling_mode = infer_scaling_mode(snapshots)
        is_breakpoint = (label == "breakpoint")

        # For breakpoint tests, detect the saturation point (earliest of:
        # first OOMKill, first error_rate >=1%, or first p95 >=5s) and the
        # VU count at that moment — used to annotate all charts.
        saturation_time, saturation_vu = (None, None)
        if is_breakpoint:
            saturation_time, saturation_vu = detect_saturation_point(
                snapshots, vus,
                error_rate=error_rate,
                latency_p95=latency.get("p95"),
            )

        plot_latency_timeline(output_dir, {label: latency},
                              throughput_values=throughput, test_start=start)
        plot_throughput_error(
            output_dir, label, throughput, error_rate,
            k6_error_rate_percent=k6_summary_metrics.get("error_rate_percent"),
            vus_values=vus,            # always show VU profile, not just breakpoint
            saturation_time=saturation_time,
            saturation_vu=saturation_vu,
            test_start=start,
        )
        # For breakpoint: also generate the dedicated composite saturation chart
        if is_breakpoint:
            plot_breakpoint_saturation(
                output_dir, label, throughput, error_rate, vus,
                snapshots, saturation_time, saturation_vu,
            )
        else:
            # VU profile is identical for all long-stress runs (same stages every time)
            # so it is omitted from per-run output. Generate once for thesis methodology.
            pass  # plot_vu_profile(output_dir, label, vus)
        plot_cpu_replicas(output_dir, label, web_cpu, snapshots,
                          vus_values=vus, test_start=start)
        plot_memory(
            output_dir, label,
            web_memory_per_pod, jobs_memory_per_pod,
            web_memory_limit_mb=web_mem_limit_mb,
            jobs_memory_limit_mb=jobs_mem_limit_mb,
            saturation_time=saturation_time,
            saturation_vu=saturation_vu,
            vus_values=vus, test_start=start,
        )
        # HPA CPU chart is only meaningful when an HPA is actually active.
        # For baseline (1 pod fixed) and prescaled (N pods fixed) the metric is
        # still computable via cAdvisor, but the 70 % threshold line is
        # meaningless and the chart would mislead readers into thinking HPA was
        # operating. Suppress it for non-HPA modes.
        if scaling_mode == "hpa":
            plot_hpa_cpu(output_dir, label, hpa_cpu)
        plot_restart_counts(
            output_dir, label, snapshots,
            saturation_time=saturation_time,
            saturation_vu=saturation_vu,
            vus_values=vus,
        )
        scaling_summary = plot_scale_latency(output_dir, label, snapshots) or {}
        # Per-run jobs-queue, db-health, and redis-health charts disabled
        # by request — these aren't cited in the thesis text. Aggregate
        # versions are still produced via aggregate_timeseries.py. Summary
        # CSV values are still computed below so they remain available
        # for the per-run summary_*.csv tables.
        # plot_jobs_queue(output_dir, label, jobs_rows, snapshots)
        # plot_db_health(output_dir, label, pg_rows)
        # plot_redis_health(output_dir, label, redis_rows)
        jobs_summary = compute_jobs_summary(jobs_rows)
        db_summary = compute_db_summary(pg_rows, redis_rows)

        # For summary CSV values prefer the k6 final-summary numbers when
        # available. They are computed over every request in the test
        # (failed/total, global percentile) and are unaffected by the setup()
        # phase or the equal-weight time-averaging that Prometheus applies.
        # Prometheus data is still used for all time-series charts.
        # p99 is not present in the k6 summary output so always comes from
        # Prometheus (noted in the CSV as a limitation).
        _thr = k6_or_prom(k6_summary_metrics, "throughput_rps",    average_value(throughput))
        _err = k6_or_prom(k6_summary_metrics, "error_rate_percent", average_value(error_rate))
        # Success Throughput — RPS that returned an expected 2xx response.
        # Performance-engineering convention: failed requests do not count
        # as useful work delivered by the system, so capacity comparisons
        # use this derived figure alongside the gross RPS.
        #     successful_rps = total_rps × (1 − error_rate_percent / 100)
        _success_rps = round(_thr * max(0.0, 1.0 - _err / 100.0), 3)
        summary_metrics = {
            "test_id":               args.testid,
            "label":                 label,
            "scaling_mode":          scaling_mode,
            "avg_throughput_rps":    _thr,
            "avg_successful_rps":    _success_rps,
            "avg_error_rate_percent":_err,
            "avg_p50_ms":            k6_or_prom(k6_summary_metrics, "p50",  average_value(latency["p50"]), scale=1000),
            "avg_p95_ms":            k6_or_prom(k6_summary_metrics, "p95",  average_value(latency["p95"]), scale=1000),
            # p99 now uses k6's true population p99 when available (post-fix runs
            # with summaryTrendStats including p(99)). Older runs fall back to
            # max-over-time of windowed p99 — guaranteed >= p95 and a defensible
            # worst-case-tail aggregation, unlike the original time-average.
            "avg_p99_ms":            k6_or_prom(k6_summary_metrics, "p99", max((v for _, v in latency["p99"]), default=0), scale=1000),
            "max_vus":               round(max((value for _, value in vus), default=0), 3),
            # Container restartCount is a lifetime counter that does not
            # reset at test start; report the delta (last - first) so the
            # number reflects restart events DURING the test window only.
            "max_web_restart_total": int(max(0, snapshots[-1]["web_restart_total"]  - snapshots[0]["web_restart_total"]))  if snapshots else 0,
            "max_jobs_restart_total":int(max(0, snapshots[-1]["jobs_restart_total"] - snapshots[0]["jobs_restart_total"])) if snapshots else 0,
            "avg_web_memory_mb":     round(average_value(web_memory), 3),
            "avg_jobs_memory_mb":    round(average_value(jobs_memory), 3),
            "max_hpa_cpu_percent":   round(max((v for _, v in hpa_cpu), default=0), 3),
            "prom_fallback_used":    int(fallback_used),
        }
        summary_metrics.update({key: round(value, 3) if isinstance(value, float) else value for key, value in scaling_summary.items()})
        summary_metrics.update(jobs_summary)
        summary_metrics.update(db_summary)
        write_summary(output_dir, label, summary_metrics)
        comparison_rows.append(summary_metrics)
        latency_overlays[label] = latency

    for index, testid in enumerate(compare_testids):
        run_dir = Path(args.runs_dir) / testid
        start, end, metadata = run_window(args, run_dir)
        selector = f'{{testid="{testid}"}}'
        label = compare_labels[index] if index < len(compare_labels) else metadata.get("test_type", testid)
        snapshots = load_snapshots(run_dir / "k8s-snapshots.csv")
        k6_summary_metrics = parse_k6_summary_metrics(run_dir / "k6-summary.txt")
        (latency, throughput, error_rate, vus, web_cpu,
         web_memory, jobs_memory,
         web_memory_per_pod, jobs_memory_per_pod,
         hpa_cpu) = collect_run_metrics(
            args.prometheus_url, selector, start, end, args.step
        )
        latency, throughput, error_rate, vus, _fallback_used = apply_k6_summary_fallbacks(
            latency, throughput, error_rate, vus, start, end, args.step, k6_summary_metrics
        )
        comparison_rows.append(
            {
                "test_id":               testid,
                "label":                 label,
                "scaling_mode":          infer_scaling_mode(snapshots),
                "avg_throughput_rps":    k6_or_prom(k6_summary_metrics, "throughput_rps",    average_value(throughput)),
                "avg_error_rate_percent":k6_or_prom(k6_summary_metrics, "error_rate_percent", average_value(error_rate)),
                "avg_p50_ms":            k6_or_prom(k6_summary_metrics, "p50",  average_value(latency["p50"]), scale=1000),
                "avg_p95_ms":            k6_or_prom(k6_summary_metrics, "p95",  average_value(latency["p95"]), scale=1000),
                "avg_p99_ms":            k6_or_prom(k6_summary_metrics, "p99", max((v for _, v in latency["p99"]), default=0), scale=1000),
                "max_vus":               round(max((value for _, value in vus), default=0), 3),
                # Delta over the test window (counter is lifetime-cumulative).
                "max_web_restart_total": int(max(0, snapshots[-1]["web_restart_total"]  - snapshots[0]["web_restart_total"]))  if snapshots else 0,
                "max_jobs_restart_total":int(max(0, snapshots[-1]["jobs_restart_total"] - snapshots[0]["jobs_restart_total"])) if snapshots else 0,
            }
        )
        latency_overlays[label] = latency

    if len(latency_overlays) > 1:
        plot_latency_timeline(output_dir, latency_overlays)
    # Comparison bar charts only make sense when there are 2+ runs to compare.
    # A single-run bar chart has no reference point and just wastes a figure.
    if len(comparison_rows) > 1:
        plot_comparison_p95(output_dir, comparison_rows)
        write_summary(output_dir, "comparison", {row["label"]: row["avg_p95_ms"] for row in comparison_rows})


if __name__ == "__main__":
    main()
