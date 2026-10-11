#!/usr/bin/env bash
# Re-runs every analysis script on the pinned Scrapy version in scrapy-src/.
# Usage (venv active, from the repository root):  bash run_all.sh
# The performance benchmark is NOT included (it takes a while and overwrites its
# results); run it separately, see README_PERFORMANCE.md.
set -e
python scripts/1_1_system_history.py    --repo scrapy-src
python scripts/2_m1_import_coupling.py  --repo scrapy-src
python scripts/2_m2_change_coupling.py  --repo scrapy-src
python scripts/2_m3_hotspots.py         --repo scrapy-src
python scripts/3_t1_coverage.py         --repo scrapy-src --coverage data/testability/coverage.xml --junit data/testability/junit.xml
python scripts/3_t2_test_execution_time.py --repo scrapy-src --junit data/testability/junit.xml
python scripts/3_t3_test_coevolution.py --repo scrapy-src
python scripts/3_m3_test_coevolution.py --repo scrapy-src   # per-commit variant of T3
python scripts/4_plot_performance.py                         # figures from data/raw/4_performance_runs.csv
