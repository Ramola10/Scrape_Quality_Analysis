"""Plot Scrapy Performance metrics with sample standard-deviation error bars.

Input:  data/raw/4_performance_runs.csv (one record per benchmark execution)
Output: figures/4_{throughput,latency,memory}_with_sd.png
        data/processed/4_performance_summary_with_sd.csv

Run from project root:
    python scripts/4_plot_performance.py

This script does not re-run benchmarks or overwrite existing figures or summary.
Error bars represent the sample standard deviation of independent runs (n=3),
not the uncertainty of individual request latencies.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / "data" / "raw" / "4_performance_runs.csv"
FIGURES = ROOT / "figures"
PROCESSED = ROOT / "data" / "processed"

METRICS = [
    ("throughput_pages_per_s", "Throughput", "Pages processed per second", "4_throughput_with_sd.png"),
    ("p95_download_latency_ms", "P95 download latency", "Latency (ms)", "4_latency_with_sd.png"),
    ("peak_rss_mib", "Peak process memory (RSS)", "Peak RSS (MiB)", "4_memory_with_sd.png"),
]


def main() -> None:
    if not INPUT.is_file():
        raise SystemExit(f"Missing {INPUT}. First run the benchmark script.")
    with INPUT.open(newline="", encoding="utf-8-sig") as f:
        runs = list(csv.DictReader(f))
    if not runs:
        raise SystemExit("Performance runs CSV is empty")

    grouped = defaultdict(list)
    for r in runs:
        c = int(r["concurrency"])
        pages = int(r["pages"])
        success = int(r["successful_pages"])
        if pages != success:
            raise SystemExit(f"Incomplete run at concurrency {c}: {success}/{pages} pages")
        grouped[c].append(r)
    levels = sorted(grouped)
    if any(len(grouped[c]) < 2 for c in levels):
        raise SystemExit("At least two repeated runs per concurrency are needed for sample SD")

    FIGURES.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)

    result_rows = []
    for c in levels:
        result = {"concurrency": c, "run_count": len(grouped[c])}
        for metric, _, _, _ in METRICS:
            vals = [float(r[metric]) for r in grouped[c]]
            result[f"mean_{metric}"] = round(mean(vals), 4)
            result[f"sd_{metric}"] = round(stdev(vals), 4)
        result_rows.append(result)

    out_csv = PROCESSED / "4_performance_summary_with_sd.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(result_rows[0]))
        writer.writeheader()
        writer.writerows(result_rows)
    print(f"Saved {out_csv}")

    for metric, title, ylabel, filename in METRICS:
        y = [r[f"mean_{metric}"] for r in result_rows]
        sd = [r[f"sd_{metric}"] for r in result_rows]
        fig, ax = plt.subplots(figsize=(8, 4.6))
        ax.errorbar(levels, y, yerr=sd, marker="o", linestyle="-", capsize=5, linewidth=1.8)
        ax.set_xticks(levels)
        ax.set_xlabel("Concurrent requests")
        ax.set_ylabel(ylabel)
        ax.set_title(f"Scrapy {title} vs. concurrency")
        ax.grid(alpha=0.25)
        ax.set_axisbelow(True)
        if metric != "peak_rss_mib":
            ax.set_ylim(bottom=0)
        else:
            ax.set_ylim(bottom=80)  # Truncated axis for readability; disclose explicitly.
            ax.text(0.01, 0.02, "Y-axis starts at 80 MiB", transform=ax.transAxes,
                    fontsize=8, va="bottom", alpha=0.8)
        fig.text(0.5, 0.01, "Mean ± sample standard deviation of 3 runs; 200 pages/run; local server delay 50 ms",
                 ha="center", fontsize=8)
        fig.tight_layout(rect=(0, 0.045, 1, 1))
        output = FIGURES / filename
        fig.savefig(output, dpi=180)
        plt.close(fig)
        print(f"Saved {output}")


if __name__ == "__main__":
    main()
