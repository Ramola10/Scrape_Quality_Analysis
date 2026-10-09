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
cd scrapy-quality-analysis
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e ./scrapy-src

python scripts/1_1_system_history.py --repo scrapy-src
python scripts/2_m1_import_coupling.py --repo scrapy-src
python scripts/2_m2_change_coupling.py --repo scrapy-src
python scripts/2_m3_hotspots.py --repo scrapy-src
```

## Artifacts per report section

| Report section | Script | Outputs |
|---|---|---|
| 1.1 System Description & History | `scripts/1_1_system_history.py` | `data/raw/commits.csv`, `data/raw/loc_per_file.csv`, `data/processed/summary_1_1.json`, `data/processed/contributors.csv`, `data/processed/activity_per_year.csv`, `data/processed/loc_per_area.csv`, `figures/1_1_*.png` |
| 1.3 Decomposition view (module definition) | `config/modules.json` | 9 modules, file-to-module mapping and `<<allowed to use>>` rules |
| 2 Modifiability – M1 import coupling | `scripts/2_m1_import_coupling.py` | `data/raw/import_edges.csv`, `data/processed/m1_*.csv/json`, `figures/2_m1_dependency_matrix.png`, `figures/2_m1_module_graph.dot` |
| 2 Modifiability – M2 change coupling | `scripts/2_m2_change_coupling.py` | `data/raw/window_commits.csv`, `data/raw/window_changes.csv`, `data/processed/m2_*.csv/json`, `figures/2_m2_change_coupling_matrix.png` |
| 2 Modifiability – M3 hotspots | `scripts/2_m3_hotspots.py` | `data/raw/function_complexity.csv`, `data/processed/m3_*.csv/json`, `figures/2_m3_hotspots.png` |
| 3 Testability | _to do_ | |
| 4 Performance | _to do_ | |

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
  a file belongs to the module with the longest matching path prefix.
* **M1**: imports parsed with Python's `ast`. `runtime` = top-level, `lazy` = inside a
  function, `typing` = inside `if TYPE_CHECKING:` (never executed, excluded from coupling).
  Components loaded by name from settings (e.g. `DOWNLOADER = "scrapy.core.downloader.Downloader"`)
  are not visible as imports.
* **M2/M3 time window**: the 3 years before the pinned commit (2023-09-11 – 2026-09-10),
  non-merge commits, renames followed to the current file name, commits touching
  more than 30 production files excluded (bulk changes).
* **M3 complexity**: McCabe cyclomatic complexity per function computed with `ast`
  (same counting rules as `radon cc`).
