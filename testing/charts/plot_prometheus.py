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
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", text):
        number = float(amount)
        if unit == "ms":
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

    # k6 summary line format:
    # http_req_duration...: avg=96ms min=1ms med=60ms max=35s p(90)=127ms p(95)=168ms
    duration_match = re.search(
        r"http_req_duration\.*:\s+avg=(\S+)\s+min=\S+\s+med=(\S+).*?p\(90\)=(\S+)\s+p\(95\)=(\S+)",
        text,
        re.DOTALL,
    )
    if duration_match:
        metrics["avg"]  = parse_duration_to_seconds(duration_match.group(1))
        metrics["p50"]  = parse_duration_to_seconds(duration_match.group(2))  # median = p50
        metrics["p95"]  = parse_duration_to_seconds(duration_match.group(4))

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


def plot_latency_timeline(output_dir, metrics_by_label):
    fig, ax = plt.subplots(figsize=(12, 5))
    colors = {"p50": "#1f77b4", "p95": "#ff7f0e", "p99": "#d62728"}

    for label, metric_series in metrics_by_label.items():
        for metric_name in ("p50", "p95", "p99"):
            values = metric_series.get(metric_name, [])
            if not values:
                continue
            xs = [x for x, _ in values]
            ys = [y * 1000 for _, y in values]
            legend = metric_name.upper() if len(metrics_by_label) == 1 else f"{label} {metric_name.upper()}"
            ax.plot(xs, ys, label=legend, linewidth=2, color=colors[metric_name], alpha=0.9 if len(metrics_by_label) == 1 else 0.7)

    ax.set_title("Response Time Timeline")
    ax.set_xlabel("Time")
    ax.set_ylabel("Latency (ms)")
    ax.legend()
    ax.grid(alpha=0.25)
    apply_time_axis(ax)
    fig.tight_layout()
    fig.savefig(output_dir / "response_time_timeline.png")
    plt.close(fig)


def plot_throughput_error(output_dir, label, throughput_values, error_values, k6_error_rate_percent=None):
    if not throughput_values and not error_values:
        return

    fig, ax1 = plt.subplots(figsize=(12, 5))

    if throughput_values:
        xs = [x for x, _ in throughput_values]
        ys = [y for _, y in throughput_values]
        ax1.plot(xs, ys, color="#1f77b4", label="Throughput", linewidth=2)

    ax1.set_title(f"Throughput vs Error Rate ({label})")
    ax1.set_xlabel("Time")
    ax1.set_ylabel("Requests/sec", color="#1f77b4")
    ax1.tick_params(axis="y", labelcolor="#1f77b4")
    ax1.grid(alpha=0.25)
    apply_time_axis(ax1)

    ax2 = ax1.twinx()
    if error_values:
        xs = [x for x, _ in error_values]
        ys = [y for _, y in error_values]
        ax2.plot(xs, ys, color="#d62728", label="Error rate (1-min rolling, %)", linewidth=1.5, alpha=0.6)

    # Always fix the error rate axis to 0-100% so crash spikes are in context
    # and stable near-zero phases are visible. Without this matplotlib
    # autoscales to e.g. 96-104% when crash windows dominate, hiding the
    # stable baseline and making the chart unreadable.
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("Error rate (%)", color="#d62728")
    ax2.tick_params(axis="y", labelcolor="#d62728")

    # Overlay k6 final-summary error rate as a dashed horizontal line.
    # This is the ground-truth value (computed over every request in the test)
    # and should be used for reporting. The Prometheus time-series above shows
    # how error rate evolved during the test; this line anchors it to reality.
    if k6_error_rate_percent is not None:
        ax2.axhline(
            y=k6_error_rate_percent,
            color="#d62728",
            linewidth=2,
            linestyle="--",
            label=f"Error rate (k6 summary: {k6_error_rate_percent:.2f}%)",
        )

    handles = ax1.get_lines() + ax2.get_lines()
    if handles:
        ax1.legend(handles, [line.get_label() for line in handles], loc="upper left")

    fig.tight_layout()
    fig.savefig(output_dir / f"throughput_error_rate_{slugify(label)}.png")
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


def plot_cpu_replicas(output_dir, label, cpu_values, snapshots):
    if not cpu_values and not snapshots:
        return

    fig, ax1 = plt.subplots(figsize=(12, 5))
    if cpu_values:
        xs = [x for x, _ in cpu_values]
        ys = [y for _, y in cpu_values]
        ax1.plot(xs, ys, color="#2ca02c", linewidth=2, label="Web CPU")

    ax1.set_title(f"Canvas Web CPU and Replica Count ({label})")
    ax1.set_xlabel("Time")
    ax1.set_ylabel("CPU cores", color="#2ca02c")
    ax1.tick_params(axis="y", labelcolor="#2ca02c")
    ax1.grid(alpha=0.25)
    apply_time_axis(ax1)

    ax2 = ax1.twinx()
    if snapshots:
        xs = [row["timestamp"] for row in snapshots]
        ys = [row["web_ready_replicas"] or row["web_spec_replicas"] for row in snapshots]
        # Only draw replica line if it actually changes — flat lines (baseline=1,
        # prescaled=5) carry no information and clutter the chart.
        if len(set(ys)) > 1:
            ax2.step(xs, ys, where="post", color="#9467bd", linewidth=2, label="Ready replicas")
    ax2.set_ylabel("Replica count", color="#9467bd")
    ax2.tick_params(axis="y", labelcolor="#9467bd")

    handles = ax1.get_lines() + ax2.get_lines()
    if handles:
        ax1.legend(handles, [line.get_label() for line in handles], loc="upper left")

    fig.tight_layout()
    fig.savefig(output_dir / f"cpu_replicas_{slugify(label)}.png")
    plt.close(fig)


def parse_memory_limit_mb(limit_str):
    """Convert a Kubernetes memory limit string (e.g. '3Gi', '3500Mi', '2Gi') to decimal MB.

    Uses bytes / 1_000_000 to match the Prometheus query which divides by 1_000_000.
    """
    if not limit_str:
        return None
    limit_str = limit_str.strip()
    try:
        if limit_str.endswith("Gi"):
            return int(limit_str[:-2]) * 1024 ** 3 / 1_000_000
        if limit_str.endswith("Mi"):
            return int(limit_str[:-2]) * 1024 ** 2 / 1_000_000
        if limit_str.endswith("Ki"):
            return int(limit_str[:-2]) * 1024 / 1_000_000
        return int(limit_str) / 1_000_000
    except ValueError:
        return None


def plot_memory(output_dir, label, web_memory_values, jobs_memory_values, web_memory_limit_mb=None):
    """Memory working-set (MB) for canvas-web and canvas-jobs over time.

    Matches Grafana panels 6 and 7 — same metric, same unit (MB decimal),
    same Running-pod-only filter applied during collection.

    web_memory_limit_mb: if provided, draws a red dashed line showing the
    container memory limit so OOMKill risk is immediately visible.
    """
    if not web_memory_values and not jobs_memory_values:
        return

    fig, ax = plt.subplots(figsize=(12, 5))
    if web_memory_values:
        xs = [x for x, _ in web_memory_values]
        ys = [y for _, y in web_memory_values]
        ax.plot(xs, ys, color="#1f77b4", label="canvas-web (MB)", linewidth=2)
    if jobs_memory_values:
        xs = [x for x, _ in jobs_memory_values]
        ys = [y for _, y in jobs_memory_values]
        ax.plot(xs, ys, color="#ff7f0e", label="canvas-jobs (MB)", linewidth=2)

    if web_memory_limit_mb is not None:
        ax.axhline(
            y=web_memory_limit_mb,
            color="#d62728",
            linewidth=2,
            linestyle="--",
            label=f"Web memory limit ({web_memory_limit_mb/1024:.1f} GiB)",
        )

    ax.set_title(f"Memory Working Set ({label})")
    ax.set_xlabel("Time")
    ax.set_ylabel("Memory (MB)")
    ax.legend()
    ax.grid(alpha=0.25)
    apply_time_axis(ax)
    fig.tight_layout()
    fig.savefig(output_dir / f"memory_{slugify(label)}.png")
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


def plot_restart_counts(output_dir, label, snapshots):
    if not snapshots:
        return

    fig, ax = plt.subplots(figsize=(12, 5))
    xs = [row["timestamp"] for row in snapshots]
    ax.step(xs, [row["web_restart_total"] for row in snapshots], where="post", label="Web restarts", linewidth=2)
    ax.step(xs, [row["jobs_restart_total"] for row in snapshots], where="post", label="Jobs restarts", linewidth=2)
    ax.set_title(f"Pod Restart Count ({label})")
    ax.set_xlabel("Time")
    ax.set_ylabel("Restart count")
    ax.legend()
    ax.grid(alpha=0.25)
    apply_time_axis(ax)
    fig.tight_layout()
    fig.savefig(output_dir / f"pod_restart_count_{slugify(label)}.png")
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
    """Infer baseline / hpa / prescaled from k8s snapshot data."""
    if not snapshots:
        return "unknown"
    web_specs = [row["web_spec_replicas"] for row in snapshots]
    min_spec = int(min(web_specs))
    max_spec = int(max(web_specs))
    has_hpa = any(row.get("web_hpa_desired_replicas", 0) > 0 for row in snapshots)
    if min_spec == 1 and max_spec == 1:
        return "baseline"
    if min_spec == 5 and max_spec == 5 and not has_hpa:
        return "prescaled"
    if has_hpa or (min_spec < max_spec):
        return "hpa"
    return "unknown"


def k6_or_prom(k6_summary, k6_key, prom_value, scale=1.0):
    """Prefer k6 final-summary value over Prometheus time-average.

    k6 summary metrics (error rate, p50, p95, throughput) are computed
    over every request in the test and are unaffected by the setup() phase
    or equal-weight time-averaging that Prometheus applies.
    """
    v = k6_summary.get(k6_key)
    return round(v * scale, 3) if v is not None else round(prom_value, 3)


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
    web_memory_result, _ = try_queries(
        base_url,
        [
            'sum(container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"} * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}) / 1000000',
            # Fallback for older k3s/cAdvisor label schemes (no namespace label)
            'sum(container_memory_working_set_bytes{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-web-.*"}) / 1000000',
        ],
        start,
        end,
        step,
    )
    web_memory = select_first_series(web_memory_result)

    jobs_memory_result, _ = try_queries(
        base_url,
        [
            'sum(container_memory_working_set_bytes{namespace="canvas",pod=~"canvas-jobs-.*",container!="",container!="POD"} * on(pod) group_left() kube_pod_status_phase{namespace="canvas",phase="Running"}) / 1000000',
            'sum(container_memory_working_set_bytes{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-jobs-.*"}) / 1000000',
        ],
        start,
        end,
        step,
    )
    jobs_memory = select_first_series(jobs_memory_result)

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

    return latency, throughput, error_rate, vus, web_cpu, web_memory, jobs_memory, hpa_cpu


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:30090")
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--step", default="15s")
    parser.add_argument("--output-dir", default="testing/charts/output")
    parser.add_argument("--testid", default="")
    parser.add_argument("--runs-dir", default="testing/results")
    parser.add_argument("--compare-testids", default="")
    parser.add_argument("--compare-labels", default="")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    compare_testids = [item.strip() for item in args.compare_testids.split(",") if item.strip()]
    compare_labels = [item.strip() for item in args.compare_labels.split(",") if item.strip()]
    comparison_rows = []
    latency_overlays = {}

    if args.testid:
        run_dir = Path(args.runs_dir) / args.testid
        start, end, metadata = run_window(args, run_dir)
        step = safe_step(start, end, args.step)
        selector = f'{{testid="{args.testid}"}}'
        label = metadata.get("test_type", args.testid)
        snapshots = load_snapshots(run_dir / "k8s-snapshots.csv")
        k6_summary_metrics = parse_k6_summary_metrics(run_dir / "k6-summary.txt")

        latency, throughput, error_rate, vus, web_cpu, web_memory, jobs_memory, hpa_cpu = collect_run_metrics(
            args.prometheus_url, selector, start, end, step
        )
        latency, throughput, error_rate, vus, fallback_used = apply_k6_summary_fallbacks(
            latency, throughput, error_rate, vus, start, end, step, k6_summary_metrics
        )

        # Parse memory limit from environment snapshot so the memory chart can
        # draw a red limit line — makes OOMKill risk immediately visible.
        env_snapshot = load_env_file(run_dir / "environment.env")
        web_mem_limit_mb = parse_memory_limit_mb(env_snapshot.get("web_memory_limit", ""))

        plot_latency_timeline(output_dir, {label: latency})
        plot_throughput_error(output_dir, label, throughput, error_rate, k6_error_rate_percent=k6_summary_metrics.get("error_rate_percent"))
        # VU profile is identical for all long-stress runs (same stages every time)
        # so it is omitted from per-run output. Generate once for thesis methodology.
        # plot_vu_profile(output_dir, label, vus)
        plot_cpu_replicas(output_dir, label, web_cpu, snapshots)
        plot_memory(output_dir, label, web_memory, jobs_memory, web_memory_limit_mb=web_mem_limit_mb)
        plot_hpa_cpu(output_dir, label, hpa_cpu)
        plot_restart_counts(output_dir, label, snapshots)
        scaling_summary = plot_scale_latency(output_dir, label, snapshots) or {}

        # For summary CSV values prefer the k6 final-summary numbers when
        # available. They are computed over every request in the test
        # (failed/total, global percentile) and are unaffected by the setup()
        # phase or the equal-weight time-averaging that Prometheus applies.
        # Prometheus data is still used for all time-series charts.
        # p99 is not present in the k6 summary output so always comes from
        # Prometheus (noted in the CSV as a limitation).
        summary_metrics = {
            "test_id":               args.testid,
            "label":                 label,
            "scaling_mode":          infer_scaling_mode(snapshots),
            "avg_throughput_rps":    k6_or_prom(k6_summary_metrics, "throughput_rps",    average_value(throughput)),
            "avg_error_rate_percent":k6_or_prom(k6_summary_metrics, "error_rate_percent", average_value(error_rate)),
            "avg_p50_ms":            k6_or_prom(k6_summary_metrics, "p50",  average_value(latency["p50"]), scale=1000),
            "avg_p95_ms":            k6_or_prom(k6_summary_metrics, "p95",  average_value(latency["p95"]), scale=1000),
            # p99 is not in k6 summary — Prometheus time-average only (caveat: averages per-interval p99s)
            "avg_p99_ms":            round(average_value(latency["p99"]) * 1000, 3),
            "max_vus":               round(max((value for _, value in vus), default=0), 3),
            "max_web_restart_total": round(max((row["web_restart_total"]  for row in snapshots), default=0), 3),
            "max_jobs_restart_total":round(max((row["jobs_restart_total"] for row in snapshots), default=0), 3),
            "avg_web_memory_mb":     round(average_value(web_memory), 3),
            "avg_jobs_memory_mb":    round(average_value(jobs_memory), 3),
            "max_hpa_cpu_percent":   round(max((v for _, v in hpa_cpu), default=0), 3),
            "prom_fallback_used":    int(fallback_used),
        }
        summary_metrics.update({key: round(value, 3) if isinstance(value, float) else value for key, value in scaling_summary.items()})
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
        latency, throughput, error_rate, vus, web_cpu, web_memory, jobs_memory, hpa_cpu = collect_run_metrics(
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
                "avg_p99_ms":            round(average_value(latency["p99"]) * 1000, 3),
                "max_vus":               round(max((value for _, value in vus), default=0), 3),
                "max_web_restart_total": round(max((row["web_restart_total"] for row in snapshots), default=0), 3),
                "max_jobs_restart_total":round(max((row["jobs_restart_total"] for row in snapshots), default=0), 3),
            }
        )
        latency_overlays[label] = latency

    if len(latency_overlays) > 1:
        plot_latency_timeline(output_dir, latency_overlays)
    if comparison_rows:
        plot_comparison_p95(output_dir, comparison_rows)
        write_summary(output_dir, "comparison", {row["label"]: row["avg_p95_ms"] for row in comparison_rows})


if __name__ == "__main__":
    main()
