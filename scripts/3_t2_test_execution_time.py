"""Testability – Metric T2: test execution time (operational metric).

Code that is easy to test can be tested in isolation, so its tests are small
and fast (unit tests). Code that is hard to test needs a running reactor, a
local web server, timers or a whole crawl, so its tests are slow (integration
tests). Slow tests also mean slower feedback for developers.

Input: the JUnit XML report of a normal (non-coverage) full test run:

    cd scrapy-src; python -m pytest tests --junitxml=../data/testability/junit.xml

Every <testcase> has a run time and the test file it belongs to. Each test
file is linked to the production modules it imports directly (ast analysis,
re-exports followed, same as M1), so tests can be grouped per module.

Measures
  * total suite time, number of tests, median / 90th / 99th percentile per test
  * share of the total time used by the slowest 5 % of tests
  * per module: number of tests, median time per test, share of slow tests (> 1 s)
  * slowest test files

Outputs
  data/raw/test_cases.csv
  data/processed/t2_per_module.csv, t2_slowest_files.csv, t2_summary.json
  figures/3_t2_test_execution_time.png

Usage:  python scripts/3_t2_test_execution_time.py --repo scrapy-src --junit data/testability/junit.xml
"""

from __future__ import annotations

import argparse
import ast
import importlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from common import (BLUE, FIGURES, INK, INK_2, MUTED, PROCESSED, RAW, SURFACE, ModuleMap, git,
                    is_production, pinned_version, style_axes)

m1 = importlib.import_module("2_m1_import_coupling")  # reuse ImportVisitor / ReexportResolver
SLOW_S = 1.0  # a test slower than this is counted as "slow"


def parse_junit(path: Path, test_files: set[str]) -> pd.DataFrame:
    rows = []
    for tc in ET.parse(path).getroot().iter("testcase"):
        f = (tc.get("file") or "").replace("\\", "/")
        if not f:  # fall back to classname "tests.test_engine.TestEngine"
            parts = (tc.get("classname") or "").split(".")
            for i in range(len(parts), 0, -1):
                cand = "/".join(parts[:i]) + ".py"
                if cand in test_files:
                    f = cand
                    break
        status = "passed"
        for tag in ("failure", "error", "skipped"):
            if tc.find(tag) is not None:
                status = tag
        rows.append({"test_file": f, "test": f"{tc.get('classname')}::{tc.get('name')}",
                     "time_s": float(tc.get("time") or 0), "status": status})
    return pd.DataFrame(rows)


def test_file_modules(repo: Path, tests: list[str], prodset: set[str], mm: ModuleMap) -> dict[str, list[str]]:
    resolver = m1.ReexportResolver(repo, prodset)
    out = {}
    for t in tests:
        try:
            tree = ast.parse((repo / t).read_text(encoding="utf-8"), filename=t)
        except SyntaxError:
            continue
        v = m1.ImportVisitor(t.rsplit("/", 1)[0].replace("/", "."))
        v.visit(tree)
        mods = {mm.module_of(tg) for d, k, _ in v.found if k != "typing" and (tg := resolver(d))}
        out[t] = sorted(m for m in mods if m)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="scrapy-src", type=Path)
    ap.add_argument("--junit", default="data/testability/junit.xml", type=Path)
    ap.add_argument("--environment", default="Windows, Python 3.14.6 (see config/test-environment.txt)",
                    help="machine/OS/Python the tests ran on (times depend on it)")
    args = ap.parse_args()
    repo = args.repo.resolve()
    if not args.junit.exists():
        raise SystemExit(f"{args.junit} not found. Run the test command in the "
                         "docstring inside scrapy-src first.")
    tag, sha, _ = pinned_version(repo)
    mm = ModuleMap()

    tracked = git(repo, "ls-files").splitlines()
    tests = [f for f in tracked if f.startswith("tests/") and f.endswith(".py")]
    prodset = {f for f in tracked if is_production(f)}

    suite = next(ET.parse(args.junit).getroot().iter("testsuite"), None)
    junit_meta = dict(suite.attrib) if suite is not None else {}
    cases = parse_junit(args.junit, set(tests))
    cases.to_csv(RAW / "test_cases.csv", index=False)
    ran = cases[cases["status"] != "skipped"].copy()   # skipped tests have ~0 s
    t = ran["time_s"]

    # ---- whole suite
    slowest5 = t.sort_values(ascending=False).head(max(1, int(len(t) * 0.05)))
    summary = {
        "scrapy_version": tag, "scrapy_commit": sha,
        "test_run_host": junit_meta.get("hostname"),
        "test_run_timestamp": junit_meta.get("timestamp"),
        "test_run_environment": args.environment,
        "tests_total": int(len(cases)),
        "tests_by_status": cases["status"].value_counts().to_dict(),
        "total_time_s": round(t.sum(), 1),
        "median_time_per_test_ms": round(1000 * t.median(), 1),
        "p90_time_per_test_ms": round(1000 * t.quantile(0.9), 1),
        "p99_time_per_test_ms": round(1000 * t.quantile(0.99), 1),
        "tests_slower_than_1s": int((t > SLOW_S).sum()),
        "tests_slower_than_1s_pct": round(100 * (t > SLOW_S).mean(), 1),
        "slowest_5pct_share_of_total_time_pct": round(100 * slowest5.sum() / t.sum(), 1),
    }

    # ---- per test file
    per_file = ran.groupby("test_file").agg(tests=("time_s", "size"), total_s=("time_s", "sum"),
                                            median_ms=("time_s", lambda s: 1000 * s.median()))
    per_file = per_file.sort_values("total_s", ascending=False).round(2)
    per_file.to_csv(PROCESSED / "t2_slowest_files.csv")
    summary["slowest_test_files"] = per_file.head(10).reset_index().to_dict("records")

    # ---- per module (a test counts for every module its test file imports directly)
    fmods = test_file_modules(repo, tests, prodset, mm)
    ran["modules"] = ran["test_file"].map(lambda f: fmods.get(f, []))
    exploded = ran.explode("modules").dropna(subset=["modules"])
    per_mod = exploded.groupby("modules").agg(
        tests=("time_s", "size"), total_s=("time_s", "sum"),
        median_ms=("time_s", lambda s: 1000 * s.median()),
        slow_pct=("time_s", lambda s: 100 * (s > SLOW_S).mean())).round(1)
    per_mod = per_mod.reindex(mm.names)
    per_mod.insert(0, "label", [mm.labels[m] for m in per_mod.index])
    per_mod.index.name = "module"
    per_mod.to_csv(PROCESSED / "t2_per_module.csv")
    summary["unmapped_tests"] = int(ran["modules"].str.len().eq(0).sum())
    (PROCESSED / "t2_summary.json").write_text(json.dumps(summary, indent=2, default=str))

    # ---- figure: median time per test per module (one axis, ms)
    pm = per_mod.dropna(subset=["median_ms"]).sort_values("median_ms")
    fig, ax = plt.subplots(figsize=(9, 4.8), facecolor=SURFACE)
    ax.barh(pm["label"], pm["median_ms"], color=BLUE, height=0.6, edgecolor=SURFACE, zorder=3)
    for i, (med, n, slow) in enumerate(zip(pm["median_ms"], pm["tests"], pm["slow_pct"])):
        ax.text(med * 1.15, i, f"{med:.0f} ms  ({int(n)} tests, {slow:.0f}% > 1 s)",
                va="center", fontsize=7, color=INK_2, zorder=4)
    style_axes(ax, "Median execution time per test, by module", "Milliseconds per test (median, log scale)")
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", colors=INK_2)
    ax.set_xscale("log")  # CLI tests are ~100x slower than the rest
    ax.set_xlim(1, pm["median_ms"].max() * 12)
    fig.text(0.01, 0.01, f"Scrapy {tag}, full test suite on {args.environment}. Skipped tests excluded. "
             "A test counts for every module its test file imports directly.", fontsize=7, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIGURES / "3_t2_test_execution_time.png", dpi=200)
    plt.close(fig)

    print(json.dumps({k: v for k, v in summary.items() if k != "slowest_test_files"}, indent=2, default=str))
    print("\nPer module:\n", per_mod.to_string())
    print("\nSlowest test files:\n", per_file.head(10).to_string())


if __name__ == "__main__":
    main()
