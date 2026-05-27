import argparse
import os
import csv
import datetime as dt
import math
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
        return float("nan")
    return float(value)

def _isnan(value):
    return isinstance(value, float) and math.isnan(value)

def _finite(values):
    return [v for v in values if v is not None and not _isnan(v)]

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

    duration_match = re.search(
        r"http_req_duration\.*:\s+avg=(\S+)\s+min=\S+\s+med=(\S+).*?p\(90\)=(\S+)\s+p\(95\)=(\S+)",
        text,
        re.DOTALL,
    )
    if duration_match:
        metrics["avg"]  = parse_duration_to_seconds(duration_match.group(1))
        metrics["p50"]  = parse_duration_to_seconds(duration_match.group(2))
        metrics["p95"]  = parse_duration_to_seconds(duration_match.group(4))

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

    metrics.update(parse_k6_error_breakdown(text))

    return metrics

def parse_k6_error_breakdown(text):
    err_lines = [ln for ln in text.splitlines() if "level=error" in ln]

    throttle = server_5xx = infra = other = 0
    for ln in err_lines:
        low = ln.lower()
        m = re.search(r"returned (\d+)", ln)
        status = int(m.group(1)) if m else None
        if status == 0:
            infra += 1
        elif status in (403, 429) or "rate limit exceeded" in low:
            throttle += 1
        elif status is not None and 500 <= status <= 599:
            server_5xx += 1
        elif re.search(
                r"timeout|connection refused|eof|dial tcp|no such host|"
                r"reset by peer|context deadline|broken pipe", low):
            infra += 1
        else:
            other += 1

    total_logged = len(err_lines)
    real_errors = server_5xx + infra + other

    out = {
        "error_logged_total":     total_logged,
        "error_throttle_count":   throttle,
        "error_server_5xx_count": server_5xx,
        "error_infra_count":      infra,
        "error_other_count":      other,
        "real_error_count":       real_errors,
    }

    rm = re.search(r"http_reqs\.*:\s+(\d+)\s+\d", text)
    if rm:
        total_reqs = int(rm.group(1))
        if total_reqs > 0:
            out["real_error_rate_percent"] = round(100.0 * real_errors / total_reqs, 4)
            out["throttle_rate_percent"]   = round(100.0 * throttle / total_reqs, 4)

    return out

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

def _parse_per_pod_series(result, scale=1.0):
    series = parse_series(result)
    pod_to_samples = {}
    for label, values in series:
        pod = label.get("pod") or label.get("container_label_io_kubernetes_pod_name") or "unknown"
        bucket = pod_to_samples.setdefault(pod, {})
        for ts, raw in values:
            v = raw * scale
            prior = bucket.get(ts)
            if prior is None or v > prior:
                bucket[ts] = v
    return {
        pod: sorted(samples.items())
        for pod, samples in pod_to_samples.items()
    }

def _parse_per_pod_memory(result):
    return _parse_per_pod_series(result, scale=1.0 / 1048576.0)

def _parse_per_pod_cpu(result):
    return _parse_per_pod_series(result, scale=1.0)

def parse_cpu_limit_cores(limit_str):
    if not limit_str:
        return None
    s = str(limit_str).strip()
    if not s:
        return None
    try:
        if s.endswith("m"):
            return float(s[:-1]) / 1000.0
        return float(s)
    except ValueError:
        return None

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
    from matplotlib.ticker import FuncFormatter, MultipleLocator
    if test_start is None:
        return
    def fmt(value, _pos):
        try:
            ts = mdates.num2date(value)
            seconds = (ts - test_start).total_seconds() if hasattr(test_start, 'tzinfo') else 0
            return f"{int(seconds / 60)}"
        except Exception:
            return ""
    ax.set_xlim(left=0)
    ax.xaxis.set_major_locator(MultipleLocator(1))
    ax.xaxis.set_minor_locator(MultipleLocator(0.5))
    ax.tick_params(axis="x", which="major", length=4, labelbottom=True)
    ax.tick_params(axis="x", which="minor", length=2)

def to_minutes_from_start(series_with_datetime, test_start):
    if not series_with_datetime or test_start is None:
        return [], []
    xs, ys = [], []
    for t, v in series_with_datetime:
        xs.append((t - test_start).total_seconds() / 60.0)
        ys.append(v)
    return xs, ys

def overlay_vus_per_run(ax, vus_values, test_start, color="#999999"):
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

def plot_latency_timeline(output_dir, metrics_by_label,
                          throughput_values=None, test_start=None):
    fig, ax = plt.subplots(figsize=(12, 5))
    colors = {"p50": "#1f77b4", "p95": "#ff7f0e", "p99": "#d62728"}

    if test_start is None:
        for _label, series in metrics_by_label.items():
            for metric_name in ("p50", "p95", "p99"):
                values = series.get(metric_name, [])
                if values:
                    test_start = values[0][0]
                    break
            if test_start is not None:
                break

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
                          test_start=None, latency_p95_values=None,
                          draw_saturation_markers=True):
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

    overlay_vus_per_run(ax_rps, vus_values, test_start)

    if throughput_values:
        err_map = {ts: pct for ts, pct in (error_values or [])}
        xs_min, total_ys, success_ys, failed_ys = [], [], [], []
        for ts, total in throughput_values:
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

    if draw_saturation_markers and label == "breakpoint" and throughput_values:
        sat_min = _find_throughput_saturation_minute(throughput_values, test_start)
        slo_min = _find_slo_breach_minute(latency_p95_values, test_start) if latency_p95_values else None
        end_min = (throughput_values[-1][0] - test_start).total_seconds() / 60.0

        if sat_min is not None and end_min > sat_min:
            for axis in (ax_rps, ax_err):
                axis.axvspan(sat_min, end_min,
                             color="#d62728", alpha=0.08, zorder=0)
            for axis in (ax_rps, ax_err):
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

        if slo_min is not None:
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

    fig.savefig(output_dir / f"throughput_error_rate_{slugify(label)}.png",
                bbox_inches="tight")
    plt.close(fig)

def _find_throughput_saturation_minute(throughput_values, test_start,
                                       peak_tolerance=0.05,
                                       future_overshoot=0.02):
    if not throughput_values:
        return None
    peak = max(v for _, v in throughput_values)
    if peak <= 0:
        return None
    threshold = peak * (1.0 - peak_tolerance)
    for i, (ts, v) in enumerate(throughput_values):
        if v < threshold:
            continue
        future = throughput_values[i:]
        future_max = max(vv for _, vv in future)
        if future_max <= v * (1.0 + future_overshoot):
            return (ts - test_start).total_seconds() / 60.0
    return None

def _find_slo_breach_minute(p95_values, test_start, threshold_seconds=3.0):
    if not p95_values:
        return None
    for ts, v in p95_values:
        if v is not None and v >= threshold_seconds:
            return (ts - test_start).total_seconds() / 60.0
    return None

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

def compute_pod_load_distribution(base_url, start, end, pod_regex,
                                  deployment_label):
    try:
        duration = int((end - start).total_seconds())
        if duration <= 0:
            return {}
        query = (
            'sum by (pod) (increase(container_network_receive_packets_total{'
            f'namespace="canvas",pod=~"{pod_regex}"}}[{duration}s]))'
        )
        response = requests.get(
            f"{base_url}/api/v1/query",
            params={"query": query, "time": end.isoformat()},
            timeout=10,
        )
        response.raise_for_status()
        series = response.json().get("data", {}).get("result", [])
    except Exception:
        return {}

    counts = {item["metric"].get("pod", "?"): float(item["value"][1])
              for item in series if item.get("value")}
    counts = {p: c for p, c in counts.items() if c > 0}
    if not counts:
        return {}
    total = sum(counts.values())
    shares = sorted(v / total * 100 for v in counts.values())
    return {
        f"{deployment_label}_pod_count":                 len(counts),
        f"{deployment_label}_min_pod_share_percent":     round(shares[0],  2),
        f"{deployment_label}_max_pod_share_percent":     round(shares[-1], 2),
        f"{deployment_label}_pod_share_spread_percent":  round(shares[-1] - shares[0], 2),
    }

def compute_resource_area(snapshots, env_snapshot):
    if not snapshots or len(snapshots) < 2:
        return {}

    web_cpu_req_per_pod  = parse_cpu_limit_cores(env_snapshot.get("web_cpu_request", "")) or 0.0
    web_mem_req_per_pod  = (parse_memory_limit_mb(env_snapshot.get("web_memory_request", "")) or 0.0) / 1024.0
    jobs_cpu_req_per_pod = parse_cpu_limit_cores(env_snapshot.get("jobs_cpu_request", "")) or 0.0
    jobs_mem_req_per_pod = (parse_memory_limit_mb(env_snapshot.get("jobs_memory_request", "")) or 0.0) / 1024.0

    web_cpu_core_seconds  = 0.0
    web_mem_gib_seconds   = 0.0
    jobs_cpu_core_seconds = 0.0
    jobs_mem_gib_seconds  = 0.0
    web_replicas_samples  = []
    jobs_replicas_samples = []

    for i in range(1, len(snapshots)):
        prev = snapshots[i - 1]
        curr = snapshots[i]
        dt_seconds = (curr["timestamp"] - prev["timestamp"]).total_seconds()
        if dt_seconds <= 0:
            continue
        web_avg = ((prev.get("web_ready_replicas") or 0)
                   + (curr.get("web_ready_replicas") or 0)) / 2.0
        jobs_avg = ((prev.get("jobs_ready_replicas") or 0)
                    + (curr.get("jobs_ready_replicas") or 0)) / 2.0
        web_cpu_core_seconds  += web_avg  * web_cpu_req_per_pod  * dt_seconds
        web_mem_gib_seconds   += web_avg  * web_mem_req_per_pod  * dt_seconds
        jobs_cpu_core_seconds += jobs_avg * jobs_cpu_req_per_pod * dt_seconds
        jobs_mem_gib_seconds  += jobs_avg * jobs_mem_req_per_pod * dt_seconds
        web_replicas_samples.append(prev.get("web_ready_replicas") or 0)
        jobs_replicas_samples.append(prev.get("jobs_ready_replicas") or 0)

    web_replicas_samples.append(snapshots[-1].get("web_ready_replicas") or 0)
    jobs_replicas_samples.append(snapshots[-1].get("jobs_ready_replicas") or 0)

    test_duration_s = (snapshots[-1]["timestamp"] - snapshots[0]["timestamp"]).total_seconds()
    test_duration_min = test_duration_s / 60.0

    total_web_cpu_core_min  = web_cpu_core_seconds  / 60.0
    total_web_mem_gib_min   = web_mem_gib_seconds   / 60.0
    total_jobs_cpu_core_min = jobs_cpu_core_seconds / 60.0
    total_jobs_mem_gib_min  = jobs_mem_gib_seconds  / 60.0

    return {
        "test_duration_minutes":         round(test_duration_min, 2),
        "total_web_cpu_core_minutes":    round(total_web_cpu_core_min,  3),
        "total_web_memory_gib_minutes":  round(total_web_mem_gib_min,   3),
        "total_jobs_cpu_core_minutes":   round(total_jobs_cpu_core_min, 3),
        "total_jobs_memory_gib_minutes": round(total_jobs_mem_gib_min,  3),
        "total_cpu_core_minutes":        round(total_web_cpu_core_min + total_jobs_cpu_core_min, 3),
        "total_memory_gib_minutes":      round(total_web_mem_gib_min  + total_jobs_mem_gib_min,  3),
        "peak_web_replicas":             max(web_replicas_samples),
        "peak_jobs_replicas":            max(jobs_replicas_samples),
        "avg_web_replicas":              round(sum(web_replicas_samples)  / len(web_replicas_samples),  2),
        "avg_jobs_replicas":             round(sum(jobs_replicas_samples) / len(jobs_replicas_samples), 2),
    }

def load_jobs_queue(path):
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
    if not jobs_rows:
        return

    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    ax_q, ax_age, ax_tput = axes

    xs = [row["timestamp"] for row in jobs_rows]

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

    ages = [row["oldest_pending_age_sec"] for row in jobs_rows]
    ax_age.plot(xs, ages, color="#ff7f0e", linewidth=2, label="Oldest pending age")
    ax_age.axhline(10, color="#888", linestyle="--", linewidth=1, alpha=0.7,
                   label="10s SLO reference")
    ax_age.set_ylabel("Seconds")
    ax_age.set_title("Job Age (latency-to-start)")
    ax_age.grid(alpha=0.25)
    ax_age.legend(loc="upper left")

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
            ratio = 100.0 * h / (h + m) if (h + m) > 0 else 100.0
        else:
            dh = h - prev_hits
            dm = m - prev_misses
            if dh < 0 or dm < 0:
                ratio = 100.0 * h / (h + m) if (h + m) > 0 else 100.0
            elif (dh + dm) == 0:
                ratio = row.get("hit_ratio_percent", 100.0)
            else:
                ratio = 100.0 * dh / (dh + dm)
        row["hit_ratio_percent"] = round(ratio, 2)
        prev_hits, prev_misses = h, m

    for row in rows:
        h = row.get("keyspace_hits_cumulative", 0) or 0
        m = row.get("keyspace_misses_cumulative", 0) or 0
        row["hit_ratio_cumulative_percent"] = round(
            100.0 * h / (h + m) if (h + m) > 0 else 100.0, 2)
    return rows

def plot_db_health(output_dir, label, pg_rows, web_mem_limit_mb=None):
    if not pg_rows:
        return

    fig, axes = plt.subplots(4, 1, figsize=(12, 13), sharex=True)
    ax_cpu, ax_conn, ax_cache, ax_lock = axes
    xs = [row["timestamp"] for row in pg_rows]

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
    if not redis_rows:
        return

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    ax_res, ax_perf = axes
    xs = [row["timestamp"] for row in redis_rows]

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
        out["peak_postgres_cpu_millicores"] = int(max(_finite(r.get("postgres_cpu_millicores") for r in pg_rows), default=0))
        out["peak_postgres_memory_mib"]    = int(max(_finite(r.get("postgres_memory_mib") for r in pg_rows), default=0))
        out["peak_active_conns"]           = int(max(_finite(r.get("active_conns") for r in pg_rows), default=0))
        out["max_db_lock_waits"]           = int(max(_finite(r.get("waiting_on_locks") for r in pg_rows), default=0))
        out["max_db_idle_in_tx"]           = int(max(_finite(r.get("idle_in_tx_conns") for r in pg_rows), default=0))
        out["total_slow_queries_over_1s"]  = int(max(_finite(r.get("slow_queries_over_1s") for r in pg_rows), default=0))
        ratios = _finite(r.get("cache_hit_ratio_percent") for r in pg_rows)
        out["min_cache_hit_ratio_percent"] = round(min(ratios), 2) if ratios else 100.0
    if redis_rows:
        out["peak_redis_cpu_millicores"]   = int(max(_finite(r.get("redis_cpu_millicores") for r in redis_rows), default=0))
        out["peak_redis_memory_mb"]        = int(max(_finite(r.get("redis_memory_used_mb") for r in redis_rows), default=0))
        max_mb = max(_finite(r.get("redis_memory_max_mb") for r in redis_rows), default=0)
        if max_mb > 0:
            used_vals = _finite(r.get("redis_memory_used_mb") for r in redis_rows)
            pcts = [100.0 * v / max_mb for v in used_vals]
            out["peak_redis_memory_percent"] = round(max(pcts), 2) if pcts else None
        ratios = _finite(r.get("hit_ratio_percent") for r in redis_rows)
        out["min_redis_hit_ratio_percent"] = round(min(ratios), 2) if ratios else 100.0
        out["redis_evictions_total"]       = int(max(_finite(r.get("evicted_keys_cumulative") for r in redis_rows), default=0))
    return out

def compute_jobs_summary(jobs_rows):
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

    pending = _finite(row["pending"] for row in jobs_rows)
    ages = _finite(row["oldest_pending_age_sec"] for row in jobs_rows)
    rates = _finite(row["jobs_per_minute"] for row in jobs_rows)
    failed = _finite(row["failed"] for row in jobs_rows)
    processed_vals = _finite(row.get("total_processed_cumulative") for row in jobs_rows)
    processed_first = processed_vals[0] if processed_vals else 0
    processed_last = processed_vals[-1] if processed_vals else 0

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

def plot_cpu_replicas(output_dir, label,
                      web_cpu_per_pod, jobs_cpu_per_pod, snapshots,
                      web_cpu_limit_cores=None, jobs_cpu_limit_cores=None,
                      vus_values=None, test_start=None,
                      split_threshold=4):
    web_has_data = bool(web_cpu_per_pod)
    jobs_has_data = bool(jobs_cpu_per_pod)
    if not web_has_data and not jobs_has_data and not snapshots:
        return

    def _spawn_order(pod_dict):
        def _key(pod):
            series = pod_dict.get(pod) or []
            first_ts = series[0][0] if series else None
            return (first_ts is None, first_ts, pod)
        return sorted((pod_dict or {}).keys(), key=_key)

    web_pods = _spawn_order(web_cpu_per_pod)
    jobs_pods = _spawn_order(jobs_cpu_per_pod)

    if test_start is None:
        for pod_dict in (web_cpu_per_pod, jobs_cpu_per_pod):
            for pod in sorted((pod_dict or {}).keys()):
                series = pod_dict[pod]
                if series:
                    test_start = series[0][0]
                    break
            if test_start is not None:
                break
    if test_start is None and snapshots:
        test_start = snapshots[0]["timestamp"]
    if test_start is None:
        return

    total_pods = len(web_pods) + len(jobs_pods)
    split = total_pods > split_threshold

    if split:
        fig, (ax_web, ax_jobs) = plt.subplots(
            2, 1, figsize=(12, 8), sharex=True,
        )
        axes_for_vus = [ax_web, ax_jobs]
    else:
        fig, ax = plt.subplots(figsize=(12, 5))
        ax_web = ax
        ax_jobs = ax
        axes_for_vus = [ax]

    for axis in axes_for_vus:
        overlay_vus_per_run(axis, vus_values, test_start)

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
                markevery=max(1, len(xs) // 12),
                label=_short_pod_label(pod, prefix) + " (cores)",
            )

    _plot_deployment(ax_web, web_pods, web_cpu_per_pod, web_palette, "web")
    _plot_deployment(ax_jobs, jobs_pods, jobs_cpu_per_pod, jobs_palette, "jobs")

    if web_cpu_limit_cores is not None and web_has_data:
        ax_web.axhline(
            y=web_cpu_limit_cores, color="#1f77b4",
            linewidth=1.5, linestyle="--", alpha=0.7,
            label=f"Web limit ({web_cpu_limit_cores:g} cores)",
        )
    if jobs_cpu_limit_cores is not None and jobs_has_data:
        ax_jobs.axhline(
            y=jobs_cpu_limit_cores, color="#ff7f0e",
            linewidth=1.5, linestyle="--", alpha=0.7,
            label=f"Jobs limit ({jobs_cpu_limit_cores:g} cores)",
        )

    def _maybe_replica(ax_target, key, color, deployment_label):
        if not snapshots:
            return
        rep_series = [
            (row["timestamp"], row[key] or row[key.replace("ready", "spec")])
            for row in snapshots
        ]
        xs_rep, ys_rep = to_minutes_from_start(rep_series, test_start)
        if len(set(ys_rep)) <= 1:
            return
        ax2 = ax_target.twinx()
        ax2.spines["right"].set_position(("outward", 55))
        ax2.step(xs_rep, ys_rep, where="post", color=color,
                 linewidth=2, linestyle="-.",
                 label=f"{deployment_label} replicas")
        ax2.set_ylabel(f"{deployment_label} replicas", color=color, fontsize=9)
        ax2.tick_params(axis="y", labelcolor=color)

    if web_has_data:
        _maybe_replica(ax_web, "web_ready_replicas", "#9467bd", "Web")
    if jobs_has_data:
        if snapshots and "jobs_ready_replicas" in snapshots[0]:
            _maybe_replica(ax_jobs, "jobs_ready_replicas", "#9467bd", "Jobs")

    if split:
        ax_web.set_title(f"Canvas CPU — per pod ({label})", fontsize=12)
        ax_web.set_ylabel("Web CPU (cores)")
        ax_jobs.set_ylabel("Jobs CPU (cores)")
        ax_jobs.set_xlabel("Minutes from test start")
        for axis in (ax_web, ax_jobs):
            axis.set_ylim(bottom=0)
            axis.legend(loc="upper left", fontsize=9)
            axis.grid(alpha=0.25)
            apply_minute_axis(axis, test_start)
    else:
        ax.set_title(f"Canvas CPU — per pod ({label})")
        ax.set_xlabel("Minutes from test start")
        ax.set_ylabel("CPU (cores)")
        ax.set_ylim(bottom=0)
        ax.legend(loc="upper left", fontsize=9)
        ax.grid(alpha=0.25)
        apply_minute_axis(ax, test_start)

    fig.tight_layout()
    fig.savefig(output_dir / f"cpu_replicas_{slugify(label)}.png",
                bbox_inches="tight")
    plt.close(fig)

def parse_memory_limit_mb(limit_str):
    if not limit_str:
        return None
    limit_str = limit_str.strip()
    try:
        if limit_str.endswith("Gi"):
            return int(limit_str[:-2]) * 1024
        if limit_str.endswith("Mi"):
            return int(limit_str[:-2])
        if limit_str.endswith("Ki"):
            return int(limit_str[:-2]) / 1024
        return int(limit_str) / 1024 ** 2
    except ValueError:
        return None

def _short_pod_label(pod_name, deployment_prefix):
    if not pod_name:
        return deployment_prefix
    suffix = pod_name.rsplit("-", 1)[-1]
    if 3 <= len(suffix) <= 10:
        return f"{deployment_prefix}-{suffix}"
    return pod_name

def plot_replicas_vs_vus(output_dir, label, snapshots, vus_values,
                         test_start=None):
    if not snapshots:
        return
    if test_start is None and snapshots:
        test_start = snapshots[0]["timestamp"]
    if test_start is None:
        return

    fig, ax_vu = plt.subplots(figsize=(12, 5))

    if vus_values:
        xs_vu, ys_vu = to_minutes_from_start(vus_values, test_start)
        ax_vu.fill_between(xs_vu, 0, ys_vu, color="#9e9e9e", alpha=0.22, linewidth=0)
        ax_vu.plot(xs_vu, ys_vu, color="#555555", linewidth=1.4,
                   linestyle="--", label="Virtual Users (VUs)")
    ax_vu.set_xlabel("Minutes from test start")
    ax_vu.set_ylabel("Virtual Users", color="#555555")
    ax_vu.tick_params(axis="y", labelcolor="#555555")
    ax_vu.set_ylim(bottom=0)
    ax_vu.grid(alpha=0.25)

    ax_rep = ax_vu.twinx()
    ys_all_rep = []
    web_series = [
        (row["timestamp"],
         row.get("web_ready_replicas") if row.get("web_ready_replicas") is not None
         else row.get("web_spec_replicas") or 0)
        for row in snapshots
    ]
    xs_w, ys_w = to_minutes_from_start(web_series, test_start)
    ax_rep.step(xs_w, ys_w, where="post", color="#1f77b4",
                linewidth=2.4, label="Web replicas", zorder=3)
    ys_all_rep.extend(ys_w)

    jobs_have_data = any(row.get("jobs_ready_replicas") is not None
                        or row.get("jobs_spec_replicas") is not None
                        for row in snapshots)
    if jobs_have_data:
        jobs_series = [
            (row["timestamp"],
             row.get("jobs_ready_replicas") if row.get("jobs_ready_replicas") is not None
             else row.get("jobs_spec_replicas") or 0)
            for row in snapshots
        ]
        xs_j, ys_j = to_minutes_from_start(jobs_series, test_start)
        ax_rep.step(xs_j, ys_j, where="post", color="#ff7f0e",
                    linewidth=2.4, label="Jobs replicas", zorder=3)
        ys_all_rep.extend(ys_j)

    max_rep = max(ys_all_rep) if ys_all_rep else 1
    ax_rep.set_ylim(bottom=0, top=max(max_rep + 1, 3))
    ax_rep.set_ylabel("Replica count", color="#1f77b4")
    ax_rep.tick_params(axis="y", labelcolor="#1f77b4")
    from matplotlib.ticker import MaxNLocator
    ax_rep.yaxis.set_major_locator(MaxNLocator(integer=True))

    handles_l, labels_l = ax_vu.get_legend_handles_labels()
    handles_r, labels_r = ax_rep.get_legend_handles_labels()
    ax_vu.legend(handles_l + handles_r, labels_l + labels_r,
                 loc="upper left", fontsize=9)

    scaling_mode = infer_scaling_mode(snapshots)
    ax_vu.set_title(
        f"Elasticity Profile — VUs vs Replicas ({label}, {scaling_mode})"
    )
    apply_minute_axis(ax_vu, test_start)
    fig.tight_layout()
    fig.savefig(output_dir / f"replicas_vs_vus_{slugify(label)}.png",
                bbox_inches="tight")
    plt.close(fig)

def plot_memory(output_dir, label,
                web_memory_per_pod, jobs_memory_per_pod,
                web_memory_limit_mb=None, jobs_memory_limit_mb=None,
                vus_values=None, test_start=None,
                split_threshold=4):
    def _spawn_order(pod_dict):
        def _key(pod):
            series = pod_dict.get(pod) or []
            first_ts = series[0][0] if series else None
            return (first_ts is None, first_ts, pod)
        return sorted((pod_dict or {}).keys(), key=_key)

    web_pods = _spawn_order(web_memory_per_pod)
    jobs_pods = _spawn_order(jobs_memory_per_pod)
    if not web_pods and not jobs_pods:
        return

    if test_start is None:
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
        fig, (ax_web, ax_jobs) = plt.subplots(
            2, 1, figsize=(12, 8), sharex=True,
        )
        axes_for_vus = [ax_web, ax_jobs]
    else:
        fig, ax = plt.subplots(figsize=(12, 5))
        ax_web = ax
        ax_jobs = ax
        axes_for_vus = [ax]

    for axis in axes_for_vus:
        overlay_vus_per_run(axis, vus_values, test_start)

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
                markevery=max(1, len(xs) // 12),
                label=_short_pod_label(pod, prefix) + " (MB)",
            )

    _plot_deployment(ax_web, web_pods, web_memory_per_pod, web_palette, "web")
    _plot_deployment(ax_jobs, jobs_pods, jobs_memory_per_pod, jobs_palette, "jobs")

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

def plot_hpa_cpu(output_dir, label, hpa_cpu_values, target_percent=None,
                 test_start=None):
    if not hpa_cpu_values:
        return

    threshold = float(target_percent) if target_percent else 70.0
    if test_start is None:
        test_start = hpa_cpu_values[0][0]

    fig, ax = plt.subplots(figsize=(12, 5))
    xs, ys = to_minutes_from_start(hpa_cpu_values, test_start)
    ax.plot(xs, ys, color="#2ca02c", label="canvas-web CPU % (HPA view)", linewidth=2)
    ax.axhline(y=threshold, color="#d62728", linewidth=2, linestyle="--",
               label=f"Scale-out threshold ({threshold:g}%)")
    ax.set_title(f"HPA CPU Utilisation % ({label})")
    ax.set_xlabel("Minutes from test start")
    ax.set_ylabel("CPU utilisation (%)")
    ax.set_ylim(0, max(150, threshold + 30))
    ax.legend()
    ax.grid(alpha=0.25)
    apply_minute_axis(ax, test_start)
    fig.tight_layout()
    fig.savefig(output_dir / f"hpa_cpu_{slugify(label)}.png")
    plt.close(fig)

def plot_restart_counts(output_dir, label, snapshots,
                        vus_values=None):
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
        if _isnan(desired) or _isnan(ready):
            continue
        if _isnan(previous_desired):
            previous_desired = desired
            continue
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
    if not snapshots:
        return "unknown"
    web_specs = _finite(row["web_spec_replicas"] for row in snapshots)
    if not web_specs:
        return "unknown"
    min_spec = int(min(web_specs))
    max_spec = int(max(web_specs))
    has_hpa = any(row.get("web_hpa_desired_replicas", 0) > 0 for row in snapshots)
    if min_spec == 1 and max_spec == 1 and not has_hpa:
        return "baseline"
    if has_hpa or (min_spec < max_spec):
        return "hpa"
    if min_spec == max_spec and min_spec > 1 and not has_hpa:
        return "prescaled"
    return "unknown"

def k6_or_prom(k6_summary, k6_key, prom_value, scale=1.0):
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

    testid_val = selector.strip("{}").split('"')[1] if selector else ""
    error_result, _ = try_queries(
        base_url,
        [
            f'100 * sum(rate(k6_http_reqs_total{{expected_response="false",testid="{testid_val}"}}[1m])) / sum(rate(k6_http_reqs_total{{testid="{testid_val}"}}[1m]))',
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

    cpu_result, _ = try_queries(
        base_url,
        [
            'sum(rate(container_cpu_usage_seconds_total{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"}[1m]) * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"})',
            'sum(rate(container_cpu_usage_seconds_total{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-web-.*"}[1m]))',
        ],
        start,
        end,
        step,
    )
    web_cpu = select_first_series(cpu_result)

    web_cpu_per_pod_result, _ = try_queries(
        base_url,
        [
            'rate(container_cpu_usage_seconds_total{namespace="canvas",pod=~"canvas-web-.*",container="web"}[1m])',
            'rate(container_cpu_usage_seconds_total{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"}[1m]) * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}',
            'rate(container_cpu_usage_seconds_total{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-web-.*"}[1m])',
        ],
        start, end, step,
    )
    web_cpu_per_pod = _parse_per_pod_cpu(web_cpu_per_pod_result)

    jobs_cpu_per_pod_result, _ = try_queries(
        base_url,
        [
            'rate(container_cpu_usage_seconds_total{namespace="canvas",pod=~"canvas-jobs-.*",container="jobs"}[1m])',
            'rate(container_cpu_usage_seconds_total{namespace="canvas",pod=~"canvas-jobs-.*",container!="",container!="POD"}[1m]) * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}',
            'rate(container_cpu_usage_seconds_total{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-jobs-.*"}[1m])',
        ],
        start, end, step,
    )
    jobs_cpu_per_pod = _parse_per_pod_cpu(jobs_cpu_per_pod_result)

    web_memory_result, _ = try_queries(
        base_url,
        [
            'sum(container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-web-.*",container="web"} '
            'unless on(id) '
            '(time() - container_last_seen{namespace="canvas",pod=~"canvas-web-.*",container="web"} > 30)) '
            '/ 1048576',
            'sum(container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"} * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}) / 1048576',
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
            'sum(container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-jobs-.*",container="jobs"} '
            'unless on(id) '
            '(time() - container_last_seen{namespace="canvas",pod=~"canvas-jobs-.*",container="jobs"} > 30)) '
            '/ 1048576',
            'sum(container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-jobs-.*",container!="",container!="POD"} * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}) / 1048576',
            'sum(container_memory_working_set_bytes{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-jobs-.*",container!="",container!="POD"}) / 1048576',
        ],
        start,
        end,
        step,
    )
    jobs_memory = select_first_series(jobs_memory_result)

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

    hpa_cpu_result, _ = try_queries(
        base_url,
        [
            'kube_horizontalpodautoscaler_status_target_metric{namespace="canvas",horizontalpodautoscaler="canvas-web",metric_target_type="utilization"}',
            '100 * sum(rate(container_cpu_usage_seconds_total{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"}[1m]) * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}) / sum(kube_pod_container_resource_requests{namespace="canvas",resource="cpu",pod=~"canvas-web-.*",container!="",container!="POD"})',
        ],
        start,
        end,
        step,
    )
    hpa_cpu = select_first_series(hpa_cpu_result)

    return (latency, throughput, error_rate, vus,
            web_cpu, web_cpu_per_pod, jobs_cpu_per_pod,
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
    parser.add_argument("--no-saturation-markers", action="store_true",
                        default=(os.environ.get("SATURATION_MARKERS", "on").lower() == "off"),
                        help="Suppress the throughput-saturation and SLO-breach "
                             "marker lines + saturated-zone shading on the "
                             "breakpoint throughput chart. Default: markers drawn.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    compare_testids = [item.strip() for item in args.compare_testids.split(",") if item.strip()]
    compare_labels = [item.strip() for item in args.compare_labels.split(",") if item.strip()]
    comparison_rows = []
    latency_overlays = {}

    if args.testid:
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

        (latency, throughput, error_rate, vus,
         web_cpu, web_cpu_per_pod, jobs_cpu_per_pod,
         web_memory, jobs_memory,
         web_memory_per_pod, jobs_memory_per_pod,
         hpa_cpu) = collect_run_metrics(
            args.prometheus_url, selector, start, end, step
        )
        latency, throughput, error_rate, vus, fallback_used = apply_k6_summary_fallbacks(
            latency, throughput, error_rate, vus, start, end, step, k6_summary_metrics
        )

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

        web_cpu_limit_cores = (
            parse_cpu_limit_cores(metadata.get("web_cpu_limit", ""))
            or parse_cpu_limit_cores(env_snapshot.get("web_cpu_limit", ""))
        )
        jobs_cpu_limit_cores = (
            parse_cpu_limit_cores(metadata.get("jobs_cpu_limit", ""))
            or parse_cpu_limit_cores(env_snapshot.get("jobs_cpu_limit", ""))
        )

        scaling_mode = infer_scaling_mode(snapshots)

        plot_latency_timeline(output_dir, {label: latency},
                              throughput_values=throughput, test_start=start)
        plot_throughput_error(
            output_dir, label, throughput, error_rate,
            k6_error_rate_percent=k6_summary_metrics.get("error_rate_percent"),
            vus_values=vus,
            test_start=start,
            latency_p95_values=latency.get("p95"),
            draw_saturation_markers=not args.no_saturation_markers,
        )
        plot_cpu_replicas(
            output_dir, label,
            web_cpu_per_pod, jobs_cpu_per_pod, snapshots,
            web_cpu_limit_cores=web_cpu_limit_cores,
            jobs_cpu_limit_cores=jobs_cpu_limit_cores,
            vus_values=vus, test_start=start,
        )
        plot_memory(
            output_dir, label,
            web_memory_per_pod, jobs_memory_per_pod,
            web_memory_limit_mb=web_mem_limit_mb,
            jobs_memory_limit_mb=jobs_mem_limit_mb,
            vus_values=vus, test_start=start,
        )
        if scaling_mode == "hpa":
            hpa_target_pct = env_snapshot.get("web_hpa_target_cpu_percent", "")
            try:
                hpa_target_pct = float(hpa_target_pct) if hpa_target_pct else None
            except ValueError:
                hpa_target_pct = None
            plot_hpa_cpu(output_dir, label, hpa_cpu,
                         target_percent=hpa_target_pct,
                         test_start=start)
        plot_restart_counts(
            output_dir, label, snapshots,
            vus_values=vus,
        )
        plot_replicas_vs_vus(
            output_dir, label, snapshots, vus,
            test_start=start,
        )
        scaling_summary = plot_scale_latency(output_dir, label, snapshots) or {}
        resource_area = compute_resource_area(snapshots, env_snapshot)

        load_dist_web  = compute_pod_load_distribution(
            args.prometheus_url, start, end, "canvas-web-.*", "web"
        )
        load_dist_jobs = compute_pod_load_distribution(
            args.prometheus_url, start, end, "canvas-jobs-.*", "jobs"
        )
        jobs_summary = compute_jobs_summary(jobs_rows)
        db_summary = compute_db_summary(pg_rows, redis_rows)

        _thr = k6_or_prom(k6_summary_metrics, "throughput_rps",    average_value(throughput))
        _err = k6_or_prom(k6_summary_metrics, "error_rate_percent", average_value(error_rate))
        _success_rps = round(_thr * max(0.0, 1.0 - _err / 100.0), 3)
        summary_metrics = {
            "test_id":               args.testid,
            "label":                 label,
            "scaling_mode":          scaling_mode,
            "avg_throughput_rps":    _thr,
            "avg_successful_rps":    _success_rps,
            "avg_error_rate_percent":_err,
            "error_logged_total":      k6_summary_metrics.get("error_logged_total", 0),
            "error_throttle_count":    k6_summary_metrics.get("error_throttle_count", 0),
            "error_server_5xx_count":  k6_summary_metrics.get("error_server_5xx_count", 0),
            "error_infra_count":       k6_summary_metrics.get("error_infra_count", 0),
            "error_other_count":       k6_summary_metrics.get("error_other_count", 0),
            "real_error_count":        k6_summary_metrics.get("real_error_count", 0),
            "real_error_rate_percent": k6_summary_metrics.get("real_error_rate_percent", 0.0),
            "throttle_rate_percent":   k6_summary_metrics.get("throttle_rate_percent", 0.0),
            "avg_p50_ms":            k6_or_prom(k6_summary_metrics, "p50",  average_value(latency["p50"]), scale=1000),
            "avg_p95_ms":            k6_or_prom(k6_summary_metrics, "p95",  average_value(latency["p95"]), scale=1000),
            "avg_p99_ms":            k6_or_prom(k6_summary_metrics, "p99", max((v for _, v in latency["p99"]), default=0), scale=1000),
            "max_vus":               round(max((value for _, value in vus), default=0), 3),
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
        summary_metrics.update(resource_area)
        summary_metrics.update(load_dist_web)
        summary_metrics.update(load_dist_jobs)
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
        (latency, throughput, error_rate, vus,
         web_cpu, web_cpu_per_pod, jobs_cpu_per_pod,
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
                "max_web_restart_total": int(max(0, snapshots[-1]["web_restart_total"]  - snapshots[0]["web_restart_total"]))  if snapshots else 0,
                "max_jobs_restart_total":int(max(0, snapshots[-1]["jobs_restart_total"] - snapshots[0]["jobs_restart_total"])) if snapshots else 0,
            }
        )
        latency_overlays[label] = latency

    if len(latency_overlays) > 1:
        plot_latency_timeline(output_dir, latency_overlays)
    if len(comparison_rows) > 1:
        plot_comparison_p95(output_dir, comparison_rows)
        write_summary(output_dir, "comparison", {row["label"]: row["avg_p95_ms"] for row in comparison_rows})

if __name__ == "__main__":
    main()
