#!/usr/bin/env python3
"""
aggregate_analysis.py — Statistical aggregation and plotting for experiment results.

Scans all run directories for an experiment, computes mean ± std dev for each
metric group (mode × scenario), and generates box plots and bar charts.

Usage:
    python aggregate_analysis.py --experiment stage1-baseline \\
        --results-dir testing/results --output-dir testing/results/analysis-stage1-baseline
"""

import argparse
import csv
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np


# ── Constants ─────────────────────────────────────────────────────────────────

KNOWN_SCENARIOS = {"smoke", "load", "stress", "long-stress", "soak", "breakpoint"}

SCENARIO_ORDER = ["smoke", "load", "stress", "long-stress", "soak", "breakpoint"]

MODE_COLORS = {
    "baseline":  "#1f77b4",   # blue
    "prescaled": "#2ca02c",   # green
    "hpa":       "#ff7f0e",   # orange
    "vpa":       "#d62728",   # red
    "tuned":     "#9467bd",   # purple
}
DEFAULT_COLOR = "#8c564b"

# (display label, y-axis unit, lower-is-better)
METRIC_META = {
    "avg_throughput_rps":     ("Throughput",       "req/s",   False),
    "avg_error_rate_percent": ("Error Rate",        "%",       True),
    "avg_p50_ms":             ("p50 Latency",       "ms",      True),
    "avg_p95_ms":             ("p95 Latency",       "ms",      True),
    "avg_p99_ms":             ("p99 Latency",       "ms",      True),
    "max_web_restart_total":  ("Web Pod Restarts",  "count",   True),
    "avg_web_memory_mb":      ("Avg Web Memory",    "MB",      True),
}

# Subset shown in the console table and bar summary
SUMMARY_METRICS = [
    "avg_error_rate_percent",
    "avg_p95_ms",
    "avg_p99_ms",
    "avg_throughput_rps",
    "max_web_restart_total",
    "avg_web_memory_mb",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_summary_csv(path: Path) -> dict:
    """Load a summary_*.csv into {metric: raw_string_value}."""
    data = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            key, val = row[0].strip(), row[1].strip()
            if key == "metric":
                continue
            data[key] = val
    return data


def parse_run_dir(name: str, experiment: str):
    """
    Parse a run directory name into (mode, scenario, run_number).

    Expected pattern: {experiment}-{mode}-{scenario}-run{nn}
    where scenario may contain hyphens (e.g. long-stress).

    Returns None if the name doesn't match.
    """
    prefix = experiment + "-"
    if not name.startswith(prefix):
        return None
    remainder = name[len(prefix):]          # e.g. "baseline-long-stress-run01"

    m = re.match(r"^(.+)-run(\d+)$", remainder)
    if not m:
        return None
    mode_scenario_str = m.group(1)           # e.g. "baseline-long-stress"
    run_number = int(m.group(2))

    # Match scenario suffix (longest first so "long-stress" beats "stress")
    for scenario in sorted(KNOWN_SCENARIOS, key=len, reverse=True):
        if mode_scenario_str.endswith("-" + scenario):
            mode = mode_scenario_str[: -(len(scenario) + 1)]
            if mode:
                return mode, scenario, run_number

    return None


def discover_runs(results_dir: Path, experiment: str) -> dict:
    """
    Scan results_dir for matching run directories.
    Returns {(mode, scenario): [row_dict, ...]} sorted by run number.
    """
    groups = defaultdict(list)

    for d in sorted(results_dir.iterdir()):
        if not d.is_dir():
            continue
        parsed = parse_run_dir(d.name, experiment)
        if parsed is None:
            continue

        mode, scenario, run_number = parsed
        slug = scenario.replace("-", "_")
        summary_path = d / f"summary_{slug}.csv"
        if not summary_path.exists():
            print(f"  [skip] No summary_{slug}.csv in {d.name}", file=sys.stderr)
            continue

        row = load_summary_csv(summary_path)
        row["_run_number"] = run_number
        row["_dir"] = str(d)
        row["_test_id"] = d.name
        groups[(mode, scenario)].append(row)

    # Sort each group by run number
    for key in groups:
        groups[key].sort(key=lambda r: r["_run_number"])

    return dict(groups)


def stats(values: list) -> dict:
    """Sample statistics for a list of floats."""
    clean = [v for v in values if not math.isnan(v)]
    n = len(clean)
    if n == 0:
        nan = float("nan")
        return {"n": 0, "mean": nan, "std": nan, "min": nan, "max": nan, "median": nan}
    mean = sum(clean) / n
    sorted_v = sorted(clean)
    if n % 2 == 1:
        median = sorted_v[n // 2]
    else:
        median = (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    std = math.sqrt(sum((v - mean) ** 2 for v in clean) / max(n - 1, 1))
    return {"n": n, "mean": mean, "std": std,
            "min": min(clean), "max": max(clean), "median": median}


def get_values(run_rows: list, metric: str) -> list:
    """Extract float values for a metric from a list of run row dicts."""
    result = []
    for r in run_rows:
        try:
            result.append(float(r.get(metric, "nan")))
        except (ValueError, TypeError):
            pass
    return result


def fmt(value, decimals=1):
    if math.isnan(value):
        return "—"
    return f"{value:.{decimals}f}"


# ── CSV output ────────────────────────────────────────────────────────────────

def write_aggregate_csv(groups: dict, output_path: Path):
    all_metrics = list(METRIC_META.keys())
    stat_fields = ["mean", "std", "min", "max", "median"]

    fieldnames = ["mode", "scenario", "n_runs"] + [
        f"{m}_{s}" for m in all_metrics for s in stat_fields
    ]

    rows = []
    for (mode, scenario), run_rows in sorted(groups.items()):
        row = {"mode": mode, "scenario": scenario, "n_runs": len(run_rows)}
        for metric in all_metrics:
            values = get_values(run_rows, metric)
            s = stats(values)
            for sf in stat_fields:
                v = s[sf]
                row[f"{metric}_{sf}"] = "" if math.isnan(v) else round(v, 3)
        rows.append(row)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Saved: {output_path.name}")


# ── Console table ─────────────────────────────────────────────────────────────

def print_console_table(groups: dict):
    print()
    print("=" * 90)
    print(f"  {'Mode':<14} {'Scenario':<14} {'n':>3}  "
          f"{'Error%':>11}  {'p95 ms':>11}  {'p99 ms':>11}  "
          f"{'RPS':>7}  {'Restarts':>9}")
    print("-" * 90)

    for (mode, scenario) in sorted(groups.keys(), key=lambda x: (x[1], x[0])):
        run_rows = groups[(mode, scenario)]
        n = len(run_rows)
        err  = stats(get_values(run_rows, "avg_error_rate_percent"))
        p95  = stats(get_values(run_rows, "avg_p95_ms"))
        p99  = stats(get_values(run_rows, "avg_p99_ms"))
        rps  = stats(get_values(run_rows, "avg_throughput_rps"))
        rest = stats(get_values(run_rows, "max_web_restart_total"))

        def ms_cell(s):
            return f"{fmt(s['mean'], 0)}±{fmt(s['std'], 0)}"

        print(f"  {mode:<14} {scenario:<14} {n:>3}  "
              f"{fmt(err['mean'], 1)}±{fmt(err['std'], 1):>5}  "
              f"{ms_cell(p95):>11}  "
              f"{ms_cell(p99):>11}  "
              f"{fmt(rps['mean'], 2):>7}  "
              f"{fmt(rest['mean'], 1)}±{fmt(rest['std'], 1):>4}")

    print("=" * 90)
    print()


# ── Per-run table ─────────────────────────────────────────────────────────────

def print_per_run_table(groups: dict):
    print()
    print("Per-run breakdown:")
    print("-" * 80)
    for (mode, scenario) in sorted(groups.keys(), key=lambda x: (x[1], x[0])):
        print(f"\n  {mode} / {scenario}")
        print(f"  {'Run':<8} {'Error%':>7} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8} "
              f"{'RPS':>6} {'Restarts':>9}")
        for r in groups[(mode, scenario)]:
            rn  = r.get("_run_number", "?")
            err = fmt(float(r.get("avg_error_rate_percent", "nan")), 1)
            p50 = fmt(float(r.get("avg_p50_ms", "nan")), 0)
            p95 = fmt(float(r.get("avg_p95_ms", "nan")), 0)
            p99 = fmt(float(r.get("avg_p99_ms", "nan")), 0)
            rps = fmt(float(r.get("avg_throughput_rps", "nan")), 2)
            rst = fmt(float(r.get("max_web_restart_total", "nan")), 0)
            print(f"  run{rn:02d}    {err:>7} {p50:>8} {p95:>8} {p99:>8} {rps:>6} {rst:>9}")
    print()


# ── Box plots ─────────────────────────────────────────────────────────────────

def plot_boxplots(groups: dict, output_dir: Path):
    """One PNG per metric — box + individual point overlay."""
    all_metrics = list(METRIC_META.keys())

    for metric_key in all_metrics:
        label, unit, lower_better = METRIC_META[metric_key]

        group_keys = sorted(groups.keys(), key=lambda x: (
            SCENARIO_ORDER.index(x[1]) if x[1] in SCENARIO_ORDER else 99, x[0]
        ))

        plot_data, tick_labels, colors = [], [], []
        for mode, scenario in group_keys:
            values = get_values(groups[(mode, scenario)], metric_key)
            if not values:
                continue
            plot_data.append(values)
            tick_labels.append(f"{mode}\n({scenario})")
            colors.append(MODE_COLORS.get(mode, DEFAULT_COLOR))

        if not plot_data:
            continue

        fig, ax = plt.subplots(figsize=(max(6, len(plot_data) * 2.0), 5))

        bp = ax.boxplot(
            plot_data, patch_artist=True, widths=0.55,
            showfliers=False,
            medianprops={"color": "black", "linewidth": 2.0},
            whiskerprops={"linewidth": 1.2},
            capprops={"linewidth": 1.5},
        )
        for patch, color in zip(bp["boxes"], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.65)

        # Overlay individual data points (jitter for clarity)
        rng = np.random.default_rng(seed=42)
        for i, (values, color) in enumerate(zip(plot_data, colors), start=1):
            jitter = rng.uniform(-0.08, 0.08, size=len(values))
            ax.scatter(
                np.array([i] * len(values)) + jitter, values,
                color=color, s=45, zorder=5,
                edgecolors="black", linewidths=0.6, alpha=0.9,
            )

        ax.set_xticks(range(1, len(tick_labels) + 1))
        ax.set_xticklabels(tick_labels, fontsize=9)
        ax.set_ylabel(f"{label} ({unit})", fontsize=10)
        ax.set_title(f"{label} — Distribution Across Runs", fontsize=11)
        ax.grid(axis="y", linestyle="--", alpha=0.35)

        arrow = "↓ better" if lower_better else "↑ better"
        ax.text(0.99, 0.97, arrow, transform=ax.transAxes,
                fontsize=8, ha="right", va="top", color="gray")

        fig.tight_layout()
        out = output_dir / f"boxplot_{metric_key}.png"
        fig.savefig(out, dpi=150)
        plt.close(fig)
        print(f"  Saved: {out.name}")


# ── Multi-panel bar summary ───────────────────────────────────────────────────

def plot_bar_summary(groups: dict, output_dir: Path, experiment: str):
    """Multi-panel bar chart: mean ± std for each metric."""
    metrics = SUMMARY_METRICS
    n_metrics = len(metrics)
    n_cols = 2
    n_rows = math.ceil(n_metrics / n_cols)

    all_modes = sorted({m for (m, _) in groups.keys()})
    scenarios = sorted(
        {s for (_, s) in groups.keys()},
        key=lambda s: SCENARIO_ORDER.index(s) if s in SCENARIO_ORDER else 99,
    )

    fig, axes = plt.subplots(n_rows, n_cols, figsize=(n_cols * 6, n_rows * 4.2))
    axes_flat = axes.flatten() if n_metrics > 1 else [axes]

    for ax_idx, metric_key in enumerate(metrics):
        ax = axes_flat[ax_idx]
        label, unit, lower_better = METRIC_META[metric_key]

        n_sc = len(scenarios)
        n_mo = len(all_modes)
        width = 0.72 / max(n_mo, 1)
        x = np.arange(n_sc)

        for mo_idx, mode in enumerate(all_modes):
            means, stds = [], []
            for scenario in scenarios:
                values = get_values(groups.get((mode, scenario), []), metric_key)
                s = stats(values)
                means.append(s["mean"] if not math.isnan(s["mean"]) else 0.0)
                stds.append(s["std"]  if not math.isnan(s["std"])  else 0.0)

            offset = (mo_idx - n_mo / 2 + 0.5) * width
            color = MODE_COLORS.get(mode, DEFAULT_COLOR)
            ax.bar(
                x + offset, means, width,
                yerr=stds, color=color, alpha=0.75,
                capsize=4, error_kw={"elinewidth": 1.5, "ecolor": "#333"},
                label=mode.capitalize(),
            )

        ax.set_xticks(x)
        ax.set_xticklabels(scenarios, fontsize=9)
        ax.set_ylabel(unit, fontsize=9)
        ax.set_title(label, fontsize=10, fontweight="bold")
        ax.grid(axis="y", linestyle="--", alpha=0.35)
        ax.set_ylim(bottom=0)

    # Hide unused panels
    for i in range(n_metrics, len(axes_flat)):
        axes_flat[i].set_visible(False)

    # Unified legend from last plotted axis
    handles, lbls = axes_flat[0].get_legend_handles_labels()
    if handles:
        fig.legend(
            handles, lbls,
            loc="upper center", ncol=len(all_modes),
            fontsize=10, frameon=True,
            bbox_to_anchor=(0.5, 1.01),
        )

    fig.suptitle(
        f"Experiment: {experiment} — Mean ± Std Dev ({len(next(iter(groups.values())))} runs each)",
        fontsize=12, y=1.04,
    )
    fig.tight_layout()
    out = output_dir / f"barplot_summary_{experiment}.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out.name}")


# ── Outlier detection ─────────────────────────────────────────────────────────

def flag_outliers(groups: dict):
    """
    Print a warning for any run whose error rate deviates by > 2 std dev
    from the group mean (potential outlier for thesis reporting).
    """
    flagged = []
    for (mode, scenario), run_rows in groups.items():
        values = get_values(run_rows, "avg_error_rate_percent")
        s = stats(values)
        if s["n"] < 3 or s["std"] < 1.0:
            continue
        for r in run_rows:
            try:
                v = float(r.get("avg_error_rate_percent", "nan"))
            except (ValueError, TypeError):
                continue
            if abs(v - s["mean"]) > 2 * s["std"]:
                flagged.append((r["_test_id"], mode, scenario, v, s["mean"], s["std"]))

    if flagged:
        print("⚠  Potential outliers (error_rate > 2σ from group mean):")
        for tid, mode, sc, v, mean, std in flagged:
            print(f"   {tid}: error_rate={v:.1f}%  (group mean={mean:.1f}% ±{std:.1f})")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Aggregate experiment results and generate statistical charts."
    )
    parser.add_argument("--experiment", required=True,
                        help="Experiment name prefix, e.g. stage1-baseline")
    parser.add_argument("--results-dir", default="testing/results",
                        help="Root results directory (default: testing/results)")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory for charts/CSV "
                             "(default: <results-dir>/analysis-<experiment>)")
    parser.add_argument("--no-plots", action="store_true",
                        help="Skip chart generation (CSV + console only)")
    args = parser.parse_args()

    results_dir = Path(args.results_dir).resolve()
    if not results_dir.exists():
        print(f"ERROR: results directory not found: {results_dir}", file=sys.stderr)
        sys.exit(1)

    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else results_dir / f"analysis-{args.experiment}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nScanning : {results_dir}")
    print(f"Experiment: {args.experiment}")
    print(f"Output    : {output_dir}\n")

    groups = discover_runs(results_dir, args.experiment)

    if not groups:
        print("ERROR: No run directories found matching experiment prefix.", file=sys.stderr)
        sys.exit(1)

    total_runs = sum(len(v) for v in groups.values())
    for (mode, scenario), runs in sorted(groups.items()):
        print(f"  {len(runs):>2} run(s)  mode={mode:<14}  scenario={scenario}")
    print(f"\n  Total: {total_runs} run(s) across {len(groups)} group(s)")

    # Console tables
    print_per_run_table(groups)
    print_console_table(groups)

    # Outlier check
    flag_outliers(groups)

    # CSV
    print("Writing aggregate CSV...")
    write_aggregate_csv(groups, output_dir / f"aggregate_stats_{args.experiment}.csv")

    if not args.no_plots:
        print("\nGenerating box plots (one per metric)...")
        plot_boxplots(groups, output_dir)

        print("\nGenerating bar summary chart...")
        plot_bar_summary(groups, output_dir, args.experiment)

    print(f"\nDone. All outputs in: {output_dir}\n")


if __name__ == "__main__":
    main()
