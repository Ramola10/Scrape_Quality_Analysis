"""Testability – Metric T1: line and branch coverage per module.

Input: the coverage.xml (Cobertura format) written by Scrapy's own test setup:

    cd scrapy-src
    python -m pytest tests -q --cov=scrapy --cov-branch --cov-report=xml:../data/testability/coverage.xml
    python -m pytest tests -q --no-cov --junitxml=../data/testability/junit.xml

Scrapy's pyproject.toml enables branch coverage (`branch = true`) and limits
measurement to `scrapy/*`, so the XML contains exactly the production code.

Measures (per file and aggregated per module of config/modules.json):
  * line coverage   = covered lines / executable lines
  * branch coverage = covered branch outcomes / all branch outcomes
  * number of files below 50 % line coverage
If data/processed/m3_file_hotspots.csv exists, coverage of the top hotspots is
reported too (are the riskiest files well tested?).

Outputs
  data/processed/t1_coverage_per_file.csv, t1_coverage_per_module.csv, t1_summary.json
  figures/3_t1_coverage_per_module.png

Usage:  python scripts/3_t1_coverage.py --repo scrapy-src
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from common import (BLUE, FIGURES, GRID, INK, INK_2, MUTED, PROCESSED, SURFACE, ModuleMap, git,
                    is_production, pinned_version, style_axes)

COND = re.compile(r"\((\d+)/(\d+)\)")
LIGHT_BLUE = "#9ec5f4"


def parse_coverage(xml_path: Path) -> pd.DataFrame:
    """One row per source file: executable/covered lines and branch outcomes."""
    root = ET.parse(xml_path).getroot()
    rows = []
    for cls in root.iter("class"):
        fname = cls.get("filename").replace("\\", "/")
        if not fname.startswith("scrapy/"):  # coverage.py may write paths relative to scrapy/
            fname = "scrapy/" + fname
        lines = cls.find("lines")
        stmts = covered = br_total = br_cov = 0
        for ln in (lines if lines is not None else []):
            stmts += 1
            if int(ln.get("hits", "0")) > 0:
                covered += 1
            if ln.get("branch") == "true":
                m = COND.search(ln.get("condition-coverage", ""))
                if m:
                    br_cov += int(m.group(1))
                    br_total += int(m.group(2))
        rows.append({"file": fname, "statements": stmts, "covered_statements": covered,
                     "branches": br_total, "covered_branches": br_cov})
    df = pd.DataFrame(rows).groupby("file", as_index=False).sum()
    return df


def rates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["line_coverage_pct"] = (100 * df["covered_statements"] / df["statements"]).round(1)
    df["branch_coverage_pct"] = (100 * df["covered_branches"] / df["branches"].where(df["branches"] > 0)).round(1)
    return df


def parse_junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    keys = ("tests", "failures", "errors", "skipped")
    out = {k: sum(int(s.get(k, 0)) for s in suites) for k in keys}
    out["time_seconds"] = round(sum(float(s.get("time", 0)) for s in suites), 1)
    out["passed"] = out["tests"] - out["failures"] - out["errors"] - out["skipped"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="scrapy-src", type=Path)
    ap.add_argument("--coverage", default="data/testability/coverage.xml", type=Path)
    ap.add_argument("--junit", default="data/testability/junit.xml", type=Path)
    args = ap.parse_args()
    repo = args.repo.resolve()
    if not args.coverage.exists():
        raise SystemExit(f"{args.coverage} not found. Run the coverage command in the "
                         "docstring inside scrapy-src first.")
    tag, sha, _ = pinned_version(repo)
    mm = ModuleMap()

    cov = parse_coverage(args.coverage)
    files = [f for f in git(repo, "ls-files", "scrapy").splitlines() if is_production(f)]
    missing = sorted(set(files) - set(cov["file"]))  # files never imported during the tests
    cov["module"] = cov["file"].map(mm.module_of)
    cov = cov.dropna(subset=["module"])
    per_file = rates(cov).sort_values("line_coverage_pct")
    per_file.to_csv(PROCESSED / "t1_coverage_per_file.csv", index=False)

    agg_cols = ["statements", "covered_statements", "branches", "covered_branches"]
    per_mod = rates(per_file.groupby("module")[agg_cols].sum().reset_index())
    per_mod["files"] = per_file.groupby("module").size().reindex(per_mod["module"]).values
    per_mod["files_below_50pct"] = (per_file[per_file["line_coverage_pct"] < 50]
                                    .groupby("module").size().reindex(per_mod["module"]).fillna(0).astype(int).values)
    per_mod["label"] = per_mod["module"].map(mm.labels)
    per_mod = per_mod.set_index("module").reindex(mm.names).reset_index()
    per_mod.to_csv(PROCESSED / "t1_coverage_per_module.csv", index=False)

    total = rates(per_file[agg_cols].sum().to_frame().T).iloc[0]
    summary = {
        "scrapy_version": tag, "scrapy_commit": sha,
        "files_measured": int(len(per_file)),
        "production_files_not_in_report": missing,
        "line_coverage_pct": float(total["line_coverage_pct"]),
        "branch_coverage_pct": float(total["branch_coverage_pct"]),
        "statements": int(total["statements"]), "branches": int(total["branches"]),
        "files_below_50pct_line_coverage": int((per_file["line_coverage_pct"] < 50).sum()),
        "files_100pct_line_coverage": int((per_file["line_coverage_pct"] >= 100).sum()),
        "least_covered_files": per_file.head(8)[["file", "module", "line_coverage_pct",
                                                 "branch_coverage_pct"]].to_dict("records"),
    }

    hot_path = PROCESSED / "m3_file_hotspots.csv"
    if hot_path.exists():  # are the hotspots (from Modifiability M3) well tested?
        hot = pd.read_csv(hot_path).head(10)[["file", "hotspot_score"]]
        hot = hot.merge(per_file[["file", "line_coverage_pct", "branch_coverage_pct"]], on="file", how="left")
        hot.to_csv(PROCESSED / "t1_hotspot_coverage.csv", index=False)
        summary["top10_hotspots_coverage"] = hot.to_dict("records")
        summary["top10_hotspots_mean_branch_coverage_pct"] = round(hot["branch_coverage_pct"].mean(), 1)

    if args.junit.exists():
        summary["test_run"] = parse_junit(args.junit)
    (PROCESSED / "t1_summary.json").write_text(json.dumps(summary, indent=2, default=float))

    # ---- figure: line + branch coverage per module (two bars per module, one axis in %)
    pm = per_mod.dropna(subset=["line_coverage_pct"]).sort_values("line_coverage_pct")
    fig, ax = plt.subplots(figsize=(9, 5.4), facecolor=SURFACE)
    y = range(len(pm))
    h = 0.36
    ax.barh([i + h / 2 for i in y], pm["line_coverage_pct"], height=h, color=BLUE, zorder=3,
            edgecolor=SURFACE, linewidth=1, label="Line coverage")
    ax.barh([i - h / 2 for i in y], pm["branch_coverage_pct"], height=h, color=LIGHT_BLUE, zorder=3,
            edgecolor=SURFACE, linewidth=1, label="Branch coverage")
    for i, (lc, bc) in enumerate(zip(pm["line_coverage_pct"], pm["branch_coverage_pct"])):
        ax.text(lc + 0.8, i + h / 2, f"{lc:.0f}%", va="center", fontsize=7, color=INK_2,
                bbox=dict(facecolor=SURFACE, edgecolor="none", pad=0.5), zorder=4)
        if pd.notna(bc):
            ax.text(bc + 0.8, i - h / 2, f"{bc:.0f}%", va="center", fontsize=7, color=MUTED,
                    bbox=dict(facecolor=SURFACE, edgecolor="none", pad=0.5), zorder=4)
    ax.set_yticks(list(y), pm["label"])
    ax.set_xlim(0, 108)
    style_axes(ax, "Test coverage per module", "Coverage (%)")
    ax.grid(axis="y", visible=False)
    ax.axvline(float(total["line_coverage_pct"]), color=MUTED, linewidth=1, linestyle=(0, (3, 3)), zorder=1)
    ax.text(float(total["line_coverage_pct"]), len(pm) - 0.3, f" overall {total['line_coverage_pct']:.0f}%",
            fontsize=7, color=MUTED, va="bottom")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK_2, loc="upper center",
              bbox_to_anchor=(0.5, -0.12), ncol=2)
    ax.tick_params(axis="y", colors=INK_2)
    fig.text(0.01, 0.01, f"Scrapy {tag}, full test suite, coverage.py with branch "
             "coverage, production code scrapy/*.py.", fontsize=7, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIGURES / "3_t1_coverage_per_module.png", dpi=200)
    plt.close(fig)

    print(json.dumps({k: v for k, v in summary.items() if k not in ("least_covered_files",)},
                     indent=2, default=float))
    print("\nPer module:\n", per_mod[["label", "files", "line_coverage_pct", "branch_coverage_pct",
                                      "files_below_50pct"]].to_string(index=False))


if __name__ == "__main__":
    main()
