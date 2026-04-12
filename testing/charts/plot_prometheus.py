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

    duration_match = re.search(
        r"http_req_duration\.*:\s+avg=(\S+).*?p\(90\)=(\S+)\s+p\(95\)=(\S+)",
        text,
        re.DOTALL,
    )
    if duration_match:
        metrics["avg"] = parse_duration_to_seconds(duration_match.group(1))
        metrics["p95"] = parse_duration_to_seconds(duration_match.group(3))

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


def plot_throughput_error(output_dir, label, throughput_values, error_values):
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
        ax2.plot(xs, ys, color="#d62728", label="Error rate", linewidth=2)
    ax2.set_ylabel("Error rate (%)", color="#d62728")
    ax2.tick_params(axis="y", labelcolor="#d62728")

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
        ax2.step(xs, ys, where="post", color="#9467bd", linewidth=2, label="Ready replicas")
    ax2.set_ylabel("Replica count", color="#9467bd")
    ax2.tick_params(axis="y", labelcolor="#9467bd")

    handles = ax1.get_lines() + ax2.get_lines()
    if handles:
        ax1.legend(handles, [line.get_label() for line in handles], loc="upper left")

    fig.tight_layout()
    fig.savefig(output_dir / f"cpu_replicas_{slugify(label)}.png")
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

    error_result, _ = try_queries(
        base_url,
        [
            f"100 * (avg(k6_http_req_failed{selector}) or avg(k6_http_req_failed_rate{selector}))",
            f"100 * (max(k6_http_req_failed{selector}) or max(k6_http_req_failed_rate{selector}))",
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
            'sum(rate(container_cpu_usage_seconds_total{namespace="canvas",pod=~"canvas-web-.*",container!="",container!="POD"}[1m]))',
            'sum(rate(container_cpu_usage_seconds_total{container_label_io_kubernetes_pod_namespace="canvas",container_label_io_kubernetes_pod_name=~"canvas-web-.*"}[1m]))',
        ],
        start,
        end,
        step,
    )
    web_cpu = select_first_series(cpu_result)

    return latency, throughput, error_rate, vus, web_cpu


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

        latency, throughput, error_rate, vus, web_cpu = collect_run_metrics(
            args.prometheus_url, selector, start, end, step
        )
        latency, throughput, error_rate, vus, fallback_used = apply_k6_summary_fallbacks(
            latency, throughput, error_rate, vus, start, end, step, k6_summary_metrics
        )

        plot_latency_timeline(output_dir, {label: latency})
        plot_throughput_error(output_dir, label, throughput, error_rate)
        plot_vu_profile(output_dir, label, vus)
        plot_cpu_replicas(output_dir, label, web_cpu, snapshots)
        plot_restart_counts(output_dir, label, snapshots)
        scaling_summary = plot_scale_latency(output_dir, label, snapshots) or {}

        summary_metrics = {
            "test_id": args.testid,
            "label": label,
            "avg_throughput_rps": round(average_value(throughput), 3),
            "avg_error_rate_percent": round(average_value(error_rate), 3),
            "avg_p50_ms": round(average_value(latency["p50"]) * 1000, 3),
            "avg_p95_ms": round(average_value(latency["p95"]) * 1000, 3),
            "avg_p99_ms": round(average_value(latency["p99"]) * 1000, 3),
            "max_vus": round(max((value for _, value in vus), default=0), 3),
            "max_web_restart_total": round(max((row["web_restart_total"] for row in snapshots), default=0), 3),
            "max_jobs_restart_total": round(max((row["jobs_restart_total"] for row in snapshots), default=0), 3),
            "k6_summary_fallback": int(fallback_used),
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
        latency, throughput, error_rate, vus, web_cpu = collect_run_metrics(
            args.prometheus_url, selector, start, end, args.step
        )
        latency, throughput, error_rate, vus, _fallback_used = apply_k6_summary_fallbacks(
            latency, throughput, error_rate, vus, start, end, args.step, k6_summary_metrics
        )
        comparison_rows.append(
            {
                "test_id": testid,
                "label": label,
                "avg_throughput_rps": round(average_value(throughput), 3),
                "avg_error_rate_percent": round(average_value(error_rate), 3),
                "avg_p50_ms": round(average_value(latency["p50"]) * 1000, 3),
                "avg_p95_ms": round(average_value(latency["p95"]) * 1000, 3),
                "avg_p99_ms": round(average_value(latency["p99"]) * 1000, 3),
                "max_vus": round(max((value for _, value in vus), default=0), 3),
                "max_web_restart_total": round(max((row["web_restart_total"] for row in snapshots), default=0), 3),
                "max_jobs_restart_total": round(max((row["jobs_restart_total"] for row in snapshots), default=0), 3),
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
