# Scrapy – Quality Attribute Analysis (Software Architecture, Assignment 2)

Analysis of **Scrapy** (https://github.com/scrapy/scrapy) regarding
Modifiability, Testability and Performance.

## Analysed version

| | |
|---|---|
| Scrapy release | `2.19.0` |
| Scrapy commit | `8026deeaac371a5d9a3edbe4886d58f61139d464` |
| How it is pinned | git submodule `scrapy-src/` |

## Reproduce

```bash
git clone --recurse-submodules <this-repo-url>
cd Scrape_Quality_Analysis
python3.12 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e ./scrapy-src

bash run_all.sh        # re-runs all analyses from the stored raw data (no benchmark, no test run)
```

The two expensive data-collection steps are run separately:

```bash
# Testability raw data (full Scrapy test suite, run inside scrapy-src)
cd scrapy-src
python -m pytest tests -q --no-cov --junitxml=../data/testability/junit.xml
python -m pytest tests -q --cov=scrapy --cov-branch --cov-report=xml:../data/testability/coverage.xml --cov-report=term
cd ..

# Performance benchmark (overwrites data/raw/4_*), see README_PERFORMANCE.md
python scripts/4_run_performance.py --pages 200 --repeats 3 --concurrency 1 8 16 32 --delay-ms 50
python scripts/4_plot_performance.py
```

## Artifacts per report section

| Report section | Script / tool | Outputs |
|---|---|---|
| 1.1 System Description & History | `scripts/1_1_system_history.py` | `data/raw/commits.csv`, `data/raw/loc_per_file.csv`, `data/processed/summary_1_1.json`, `contributors.csv`, `activity_per_year.csv`, `loc_per_area.csv`, `figures/1_1_*.png` |
| 1.3 Decomposition view (module definition) | `config/modules.json` | 9 modules, file-to-module mapping and `<<allowed to use>>` rules |
| 2 Modifiability – M1 import coupling | `scripts/2_m1_import_coupling.py` | `data/raw/import_edges.csv`, `data/processed/m1_*`, `figures/2_m1_dependency_matrix.png`, `figures/2_m1_module_graph.dot` |
| 2 Modifiability – M2 change coupling | `scripts/2_m2_change_coupling.py` | `data/raw/window_commits.csv`, `data/raw/window_changes.csv`, `data/processed/m2_*`, `figures/2_m2_change_coupling_matrix.png` |
| 2 Modifiability – M3 hotspots | `scripts/2_m3_hotspots.py` | `data/raw/function_complexity.csv`, `data/processed/m3_*`, `figures/2_m3_hotspots.png` |
| 3 Testability – raw test data | pytest, pytest-cov | `data/testability/coverage.xml`, `data/testability/junit.xml`, `data/testability/*.txt`, `data/testability/collected-tests.txt`, `config/test-environment.txt` |
| 3 Testability – T1 coverage | `scripts/3_t1_coverage.py` | `data/processed/t1_*`, `figures/3_t1_coverage_per_module.png` |
| 3 Testability – T2 test execution time | `scripts/3_t2_test_execution_time.py` | `data/raw/test_cases.csv`, `data/processed/t2_*`, `figures/3_t2_test_execution_time.png` |
| 3 Testability – T3 test co-evolution (change sets) | `scripts/3_t3_test_coevolution.py` | `data/raw/changesets.csv`, `data/processed/t3_*`, `figures/3_t3_test_coevolution.png` |
| 3 Testability – T3 variant (single commits) | `scripts/3_m3_test_coevolution.py` | `data/raw/test_coevolution_commits.csv`, `data/processed/test_coevolution_*`, `figures/3_m3_test_coevolution.png` |
| 4 Performance – throughput, P95 latency, peak memory vs. concurrency | `scripts/4_run_performance.py`, `scripts/4_performance_spider.py`, `scripts/4_plot_performance.py` | `data/raw/4_*`, `data/processed/4_*`, `figures/4_*_with_sd.png` – details in `README_PERFORMANCE.md` |

## Method notes (1.1)

* **Commits** are all commits reachable from the pinned commit. Contributor and
  activity numbers use **non-merge commits by humans** (bots such as dependabot excluded).
* **Contributors**: identities are merged when they share an e-mail address or the
  same accent-insensitive name; manual merges are listed in `config/author_aliases.json`.
* **Lines of code**: own counter over git-tracked files. For Python, comments and
  docstrings count as comments, blank lines are separate (like `cloc`).
  "Production code" = `scrapy/`, "tests" = `tests/`.
* Age is measured from the first commit's author date to the pinned commit.

## Method notes (Modifiability)

* **Modules**: production code `scrapy/*.py` is split into 9 modules (`config/modules.json`);
  a file belongs to the module with the longest matching path prefix. The `Spider` base
  class is part of the Framework API because every component receives it.
* **M1**: imports parsed with Python's `ast`; imports through package re-exports are followed
  to the defining file. `runtime` = top-level, `lazy` = inside a function, `typing` = inside
  `if TYPE_CHECKING:` (never executed, excluded from coupling). Components loaded by name from
  settings (e.g. `DOWNLOADER = "scrapy.core.downloader.Downloader"`) are not visible as imports.
* **M2/M3 time window**: the 3 years before the pinned commit (2023-09-11 – 2026-09-10),
  non-merge commits, renames followed to the current file name, commits touching
  more than 30 production files excluded (bulk changes).
* **M3 complexity**: McCabe cyclomatic complexity per function computed with `ast`
  (same counting rules as `radon cc`).

## Method notes (Testability)

* **Test environment**: Windows, Python 3.14.6; exact package versions in `config/test-environment.txt`.
* **Test suite**: 4,910 unique tests collected; 4,494 passed, 395 skipped, 22 expected failures,
  0 unexpected failures. JUnit contains 4,911 records because `tests.test_downloader_handler_httpx`
  creates one extra module-level skip record (`data/testability/count-mismatch-explanation.txt`).
* **T1 coverage**: run inside `scrapy-src` so Scrapy's own coverage configuration applies, with
  branch coverage: 93.7 % line and 93.5 % branch coverage (13,643 statements, 3,492 branches).
  Coverage instrumentation caused two timeout failures, so the normal (non-coverage) run is used
  for test results and execution times. An earlier run from the repository root without Scrapy's
  configuration gave 88 % statement coverage.
* **Skipped tests**: 206 tests were skipped because Twisted's HTTP/2 support (`Twisted[http2]`)
  was not installed, others because optional tools (mitmdump, boto3, uvloop, google-cloud) were
  missing. The HTTP/2 download handler therefore shows 0 % coverage, which lowers the Downloader
  module's coverage; this is an environment limitation, not necessarily missing tests.
* **T2 execution time**: per-test run times from the JUnit report (skipped tests excluded). Each
  test is grouped under every module its test file imports directly (re-exports followed).
  Times depend on the machine.
* **T3 co-evolution**: main variant = one change set per commit on the first-parent history of
  the main branch (a merged PR or a direct commit), so tests added in a separate commit of the
  same PR still count; bug fix = message contains fix/fixes/fixed/bug; change sets touching
  > 30 production files excluded. Variant (`3_m3_test_coevolution.py`) = single non-merge commits
  in the 3-year window. Last 3 years: 75.8 % of change sets vs. 67.1 % of single commits also
  changed tests.

## Method notes (Performance)

See `README_PERFORMANCE.md`. Controlled localhost benchmark (50 ms server delay, 200 pages per
run, 3 repetitions, concurrency 1/8/16/32, server connection backlog 128), Windows 11, Python 3.12.10.

## Use of generative AI

The data-collection and analysis scripts were developed with the help of a generative AI
assistant (Claude, Anthropic), as permitted by the assignment's GenAI policy. All results were
produced by running the scripts on the pinned Scrapy version and checked by the authors.
