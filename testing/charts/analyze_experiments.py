import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

from scipy import stats


METRICS = [
    "avg_p50_ms",
    "avg_p95_ms",
    "avg_p99_ms",
    "avg_throughput_rps",
    "avg_error_rate_percent",
]


def parse_summary(path):
    values = {}
    if not path.exists():
        return values
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            values[row["metric"]] = row["value"]
    return values


def parse_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def confidence_interval(values, confidence=0.95):
    if len(values) < 2:
        value = values[0] if values else math.nan
        return value, value
    sample_mean = mean(values)
    sample_std = stdev(values)
    margin = stats.t.ppf((1 + confidence) / 2, len(values) - 1) * (sample_std / math.sqrt(len(values)))
    return sample_mean - margin, sample_mean + margin


def iqr_outliers(values):
    if len(values) < 4:
        return set()
    q1, q3 = stats.scoreatpercentile(values, 25), stats.scoreatpercentile(values, 75)
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    return {value for value in values if value < lower or value > upper}


def read_manifest(path):
    rows = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)
    return rows


def find_summary_file(run_dir):
    charts_dir = run_dir / "charts"
    if not charts_dir.exists():
        return None
    for path in charts_dir.glob("summary_*.csv"):
        if path.name != "summary_comparison.csv":
            return path
    return None


def load_run_metric_rows(manifest_rows, results_dir):
    records = []
    for row in manifest_rows:
        summary_file = find_summary_file(results_dir / row["test_id"])
        if summary_file is None:
            continue
        summary = parse_summary(summary_file)
        record = dict(row)
        for metric in METRICS:
            record[metric] = parse_float(summary.get(metric))
        records.append(record)
    return records


def summarize_groups(records):
    grouped = defaultdict(list)
    for record in records:
        grouped[(record["mode"], record["scenario"])].append(record)

    summary_rows = []
    outlier_rows = []
    for (mode, scenario), group in grouped.items():
        summary = {
            "mode": mode,
            "scenario": scenario,
            "run_count": len(group),
        }

        for metric in METRICS:
            values = [record[metric] for record in group if not math.isnan(record[metric])]
            if not values:
                continue
            flagged_values = iqr_outliers(values)
            outlier_rows.extend(
                {
                    "mode": mode,
                    "scenario": scenario,
                    "metric": metric,
                    "test_id": record["test_id"],
                    "value": record[metric],
                    "flagged": "yes" if record[metric] in flagged_values else "no",
                }
                for record in group
            )
            ci_low, ci_high = confidence_interval(values)
            summary[f"{metric}_mean"] = round(mean(values), 4)
            summary[f"{metric}_std"] = round(stdev(values), 4) if len(values) > 1 else 0.0
            summary[f"{metric}_ci_low"] = round(ci_low, 4)
            summary[f"{metric}_ci_high"] = round(ci_high, 4)
            summary[f"{metric}_outlier_count"] = len(flagged_values)

        summary_rows.append(summary)

    return summary_rows, outlier_rows, grouped


def compute_t_tests(grouped):
    by_scenario = defaultdict(dict)
    for (mode, scenario), records in grouped.items():
        by_scenario[scenario][mode] = records

    rows = []
    for scenario, modes in by_scenario.items():
        baseline = modes.get("baseline")
        hpa = modes.get("hpa")
        if not baseline or not hpa:
            continue

        for metric in METRICS:
            baseline_values = [record[metric] for record in baseline if not math.isnan(record[metric])]
            hpa_values = [record[metric] for record in hpa if not math.isnan(record[metric])]
            if len(baseline_values) < 2 or len(hpa_values) < 2:
                continue

            result = stats.ttest_ind(baseline_values, hpa_values, equal_var=False)
            rows.append(
                {
                    "scenario": scenario,
                    "metric": metric,
                    "baseline_mean": round(mean(baseline_values), 4),
                    "hpa_mean": round(mean(hpa_values), 4),
                    "t_statistic": round(result.statistic, 4),
                    "p_value": round(result.pvalue, 6),
                }
            )

    return rows


def write_csv(path, rows):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--results-dir", default="testing/results")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    results_dir = Path(args.results_dir)
    output_dir = Path(args.output_dir) if args.output_dir else manifest_path.parent / "analysis" / manifest_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows = read_manifest(manifest_path)
    records = load_run_metric_rows(manifest_rows, results_dir)
    summary_rows, outlier_rows, grouped = summarize_groups(records)
    t_test_rows = compute_t_tests(grouped)

    write_csv(output_dir / "group_summary.csv", summary_rows)
    write_csv(output_dir / "outliers.csv", outlier_rows)
    write_csv(output_dir / "t_tests.csv", t_test_rows)


if __name__ == "__main__":
    main()
