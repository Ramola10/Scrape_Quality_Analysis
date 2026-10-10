"""Reproducible localhost Scrapy performance benchmark for PA1483 Assignment 2.

Run this from the quality-analysis repository root:
    python scripts/4_run_performance.py --pages 20 --repeats 1 --concurrency 1 8
    python scripts/4_run_performance.py --pages 200 --repeats 3 --concurrency 1 8 16 32

Dependencies: Scrapy, psutil (pip install psutil).
Optional for charts: matplotlib (already in the analysis requirements).
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import threading
import time

import psutil


class LocalPageHandler(BaseHTTPRequestHandler):
    delay_s = 0.05
    page_body = b"<html><head><title>Benchmark</title></head><body>" + b"x" * 1024 + b"</body></html>"

    def do_GET(self):
        if not self.path.startswith("/page/"):
            self.send_error(404)
            return
        time.sleep(self.delay_s)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(self.page_body)))
        self.end_headers()
        try:
            self.wfile.write(self.page_body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format, *args):
        return  # Silence requests; metrics are written to CSV instead.


def peak_process_tree_rss(pid):
    """Snapshot RSS for Scrapy process and any subprocess children."""
    try:
        root = psutil.Process(pid)
        processes = [root] + root.children(recursive=True)
        return sum(p.memory_info().rss for p in processes if p.is_running())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0


def run_one(spider_path, base_url, pages, concurrency, run_index, raw_dir, timeout):
    result_file = raw_dir / f"4_perf_c{concurrency}_run{run_index}.json"
    log_file = raw_dir / f"4_perf_c{concurrency}_run{run_index}.log"
    if result_file.exists():
        result_file.unlink()  # Avoid confusing stale results with current runs.
    command = [
        sys.executable, "-m", "scrapy", "runspider", str(spider_path),
        "-a", f"base_url={base_url}",
        "-a", f"pages={pages}",
        "-a", f"result_file={result_file.resolve()}",
        "-s", f"CONCURRENT_REQUESTS={concurrency}",
        "-s", f"CONCURRENT_REQUESTS_PER_DOMAIN={concurrency}",
        "-s", "DOWNLOAD_DELAY=0",
        "-s", "DOWNLOAD_DELAY_JITTER=0",
        "-s", "AUTOTHROTTLE_ENABLED=False",
        "-s", "HTTPCACHE_ENABLED=False",
        "-s", "RETRY_ENABLED=False",
        "-s", "ROBOTSTXT_OBEY=False",
        "-s", "LOG_LEVEL=ERROR",
        "-s", "TELNETCONSOLE_ENABLED=False",
    ]
    # stdout/stderr to disk avoids deadlocks from filled pipes.
    with log_file.open("w", encoding="utf-8") as log:
        start = time.perf_counter()
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=spider_path.parent.parent)
        peak_rss_bytes = 0
        timed_out = False
        while process.poll() is None:
            peak_rss_bytes = max(peak_rss_bytes, peak_process_tree_rss(process.pid))
            if time.perf_counter() - start > timeout:
                timed_out = True
                process.kill()
                break
            time.sleep(0.02)
        rc = process.wait()
        elapsed = time.perf_counter() - start
        peak_rss_bytes = max(peak_rss_bytes, peak_process_tree_rss(process.pid))

    if timed_out or rc != 0 or not result_file.exists():
        raise RuntimeError(f"Benchmark failed for concurrency={concurrency}, run={run_index}. See {log_file}")
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if result["reason"] != "finished" or result["successful_pages"] != pages or result["latency_sample_count"] != pages:
        raise RuntimeError(f"Incomplete crawl: {result}. See {log_file}")
    return {
        "concurrency": concurrency,
        "run": run_index,
        "pages": pages,
        "successful_pages": result["successful_pages"],
        "elapsed_s": round(elapsed, 4),
        "throughput_pages_per_s": round(pages / elapsed, 4),
        "p95_download_latency_ms": round(result["p95_download_latency_s"] * 1000, 4),
        "peak_rss_mib": round(peak_rss_bytes / (1024 * 1024), 4),
    }


def write_csv(path, rows, cols):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 8, 16, 32])
    parser.add_argument("--delay-ms", type=float, default=50.0, help="Simulated server wait per request")
    parser.add_argument("--timeout", type=float, default=180.0, help="Timeout per run, seconds")
    args = parser.parse_args()
    if args.pages < 1 or args.repeats < 1 or any(c < 1 for c in args.concurrency) or args.delay_ms < 0:
        parser.error("pages, repeats, concurrency must be positive; delay-ms cannot be negative")

    root = Path(__file__).resolve().parent.parent
    spider = root / "scripts" / "4_performance_spider.py"
    if not spider.exists():
        parser.error(f"Missing spider script {spider}")
    raw = root / "data" / "raw"
    processed = root / "data" / "processed"
    raw.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)
    LocalPageHandler.delay_s = args.delay_ms / 1000.0
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalPageHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    run_rows = []
    try:
        for index in range(1, args.repeats + 1):
            # Rotate the order of conditions to reduce fixed order bias.
            settings = args.concurrency[index - 1:] + args.concurrency[:index - 1]
            for c in settings:
                print(f"Running concurrency={c}, repetition={index}/{args.repeats} ...", flush=True)
                row = run_one(spider, base_url, args.pages, c, index, raw, args.timeout)
                run_rows.append(row)
                print(f"  throughput={row['throughput_pages_per_s']:.2f} pages/s, "
                      f"p95={row['p95_download_latency_ms']:.2f} ms, "
                      f"peak RSS={row['peak_rss_mib']:.2f} MiB", flush=True)
    finally:
        server.shutdown()
        server.server_close()

    columns = ["concurrency", "run", "pages", "successful_pages", "elapsed_s",
               "throughput_pages_per_s", "p95_download_latency_ms", "peak_rss_mib"]
    write_csv(raw / "4_performance_runs.csv", run_rows, columns)
    summary = []
    for c in sorted(set(args.concurrency)):
        runs = [row for row in run_rows if row["concurrency"] == c]
        summary.append({
            "concurrency": c,
            "run_count": len(runs),
            "mean_throughput_pages_per_s": round(statistics.mean(r["throughput_pages_per_s"] for r in runs), 4),
            "mean_p95_download_latency_ms": round(statistics.mean(r["p95_download_latency_ms"] for r in runs), 4),
            "mean_peak_rss_mib": round(statistics.mean(r["peak_rss_mib"] for r in runs), 4),
            "mean_elapsed_s": round(statistics.mean(r["elapsed_s"] for r in runs), 4),
        })
    write_csv(processed / "4_performance_summary.csv", summary, list(summary[0]))
    environment = {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "pages_per_run": args.pages,
        "repetitions": args.repeats,
        "concurrency_levels": args.concurrency,
        "server_delay_ms": args.delay_ms,
        "server_host": "127.0.0.1",
        "benchmark_type": "controlled localhost synthetic workload",
        "scrapy_version": __import__("scrapy").__version__,
        "scrapy_source_commit": subprocess.run(["git", "-C", str(root / "scrapy-src"), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "cpu": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
    }
    (processed / "4_performance_environment.json").write_text(json.dumps(environment, indent=2), encoding="utf-8")
    print("\nSaved:")
    print(" ", raw / "4_performance_runs.csv")
    print(" ", processed / "4_performance_summary.csv")
    print(" ", processed / "4_performance_environment.json")


if __name__ == "__main__":
    main()
