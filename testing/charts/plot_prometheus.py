import argparse
import datetime as dt
from pathlib import Path

import matplotlib.pyplot as plt
import requests


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
    data = response.json()
    return data["data"]["result"]


def parse_series(result):
    series = []
    for item in result:
        label = item["metric"]
        values = [(dt.datetime.fromtimestamp(float(ts)), float(val)) for ts, val in item["values"]]
        series.append((label, values))
    return series


def plot_series(output_dir, filename, title, ylabel, series):
    plt.figure(figsize=(12, 5))
    for label, values in series:
        xs = [x for x, _ in values]
        ys = [y for _, y in values]
        legend = label.get("pod") or label.get("container") or label.get("testid") or "series"
        plt.plot(xs, ys, label=legend)

    plt.title(title)
    plt.ylabel(ylabel)
    plt.xlabel("Time")
    if len(series) <= 10:
        plt.legend()
    plt.tight_layout()
    plt.savefig(output_dir / filename)
    plt.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:30090")
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--step", default="15s")
    parser.add_argument("--output-dir", default="testing/charts/output")
    parser.add_argument("--testid", default="")
    args = parser.parse_args()

    end = dt.datetime.now(dt.UTC)
    start = end - dt.timedelta(minutes=args.minutes)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    k6_selector = f'{{testid="{args.testid}"}}' if args.testid else ""

    queries = {
        "k6_request_rate.png": (
            f"sum(rate(k6_http_reqs_total{k6_selector}[1m]))",
            "k6 request rate",
            "requests/sec",
        ),
        "k6_latency_p95.png": (
            f"k6_http_req_duration_seconds_p95{k6_selector}",
            "k6 p95 latency",
            "seconds",
        ),
        "canvas_pod_cpu.png": (
            'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="canvas",pod!=""}[1m]))',
            "canvas pod cpu",
            "cpu cores",
        ),
        "canvas_pod_memory.png": (
            'sum by (pod) (container_memory_working_set_bytes{namespace="canvas",pod!=""})',
            "canvas pod memory",
            "bytes",
        ),
    }

    for filename, (query, title, ylabel) in queries.items():
        result = query_range(args.prometheus_url, query, start, end, args.step)
        series = parse_series(result)
        if series:
            plot_series(output_dir, filename, title, ylabel, series)


if __name__ == "__main__":
    main()
