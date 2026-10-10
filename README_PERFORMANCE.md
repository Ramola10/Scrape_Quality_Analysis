# Scrapy Performance Analysis

## Overview

This directory contains the performance benchmarking implementation for Section 4 of PA1483 – Software Architecture, Assignment 2.

The benchmark evaluates Scrapy's performance under different concurrency levels using a controlled local HTTP server.

## Quality Attribute and Metrics

Three performance metrics are measured:

1. **Throughput (pages/second):** Number of successfully processed pages divided by total elapsed execution time.
2. **P95 Download Latency (milliseconds):** The 95th percentile of recorded HTTP download latency observations, measured until response headers are received.
3. **Peak Memory Usage (MiB):** Maximum observed Resident Set Size (RSS) of the Scrapy process.

## Experimental Environment

- Scrapy version: 2.19.0
- Scrapy source commit: `8026deeaac371a5d9a3edbe4886d58f61139d464`
- Python version: 3.12.10
- Operating system: Windows 11
- CPU: Intel processor, 8 logical processors
- HTTP server: localhost (`127.0.0.1`)
- Artificial server delay: 50 milliseconds
- Pages per run: 200
- Repetitions per configuration: 3
- Concurrency levels: 1, 8, 16, 32
- Total benchmark runs: 12

## Setup

Clone the repository with its Scrapy submodule:

```bash
git clone --recurse-submodules https://github.com/Ramola10/Scrape_Quality_Analysis.git
cd Scrape_Quality_Analysis
```

Create and activate a Python virtual environment, then install the dependencies:

```bash
python -m pip install -r requirements.txt
python -m pip install -e ./scrapy-src
```

## Running the Benchmark

Run the complete performance experiment from the repository root:

```bash
python scripts/4_run_performance.py --pages 200 --repeats 3 --concurrency 1 8 16 32 --delay-ms 50
```

Generate the performance visualizations:

```bash
python scripts/4_plot_performance.py
```

**Important:** Running the benchmark regenerates the measurement files and may overwrite previously collected results. Back up any data you need to preserve before repeating the experiment.

## Scripts

- `scripts/4_performance_spider.py`: Defines the Scrapy spider and collects response-related measurements.
- `scripts/4_run_performance.py`: Runs the benchmark, collects performance measurements, and records experiment metadata.
- `scripts/4_plot_performance.py`: Generates performance charts and standard deviation summaries.

## Output Artifacts

**Raw data:** `data/raw/`

- `4_performance_runs.csv`
- `4_perf_c*_run*.json`
- `4_perf_c*_run*.log`

**Processed data:** `data/processed/`

- `4_performance_summary.csv`
- `4_performance_summary_with_sd.csv`
- `4_performance_environment.json`

**Figures:** `figures/`

- `4_throughput_with_sd.png`
- `4_latency_with_sd.png`
- `4_memory_with_sd.png`

The figures with the `_with_sd` suffix display mean values and sample standard deviations calculated from three runs per concurrency configuration.

## Reproducibility and Limitations

The benchmark uses a synthetic localhost workload to limit variability associated with external websites and network conditions.

Measurements may vary depending on system load, process scheduling, hardware, and operating-system behavior.

The benchmark measures performance under controlled conditions. It does not establish how Scrapy will perform for all production crawling workloads.

The scripts, raw measurements, processed results, experiment configuration, and figures are provided to support independent inspection and reproduction of the analysis.
