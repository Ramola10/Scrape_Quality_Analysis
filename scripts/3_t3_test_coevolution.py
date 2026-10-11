"""Testability – Metric T3: co-evolution of production code and tests (process metric).

If code is easy to test, developers add or update tests together with the code
change. For every change set integrated into Scrapy's main branch we check
whether it modified tests as well as production code.

A "change set" is one commit on the first-parent history of the main branch:
a merged pull request (its whole diff against the previous main) or a commit
pushed directly. This avoids under-counting PRs whose tests were added in a
separate commit.

  * production file: scrapy/**/*.py that is not a test file
  * test file: tests/**, scrapy/tests/** (old layout), or test_*.py
  * bug fix: message contains fix / fixes / fixed / bug (case-insensitive)
  * bulk change sets (> 30 production files) are excluded

Measures (per year, whole history -> trend)
  * % of change sets touching production code that also touch tests
  * the same for bug-fix change sets only

Outputs
  data/raw/changesets.csv
  data/processed/t3_per_year.csv, t3_summary.json
  figures/3_t3_test_coevolution.png

Usage:  python scripts/3_t3_test_coevolution.py --repo scrapy-src
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from common import (BLUE, FIGURES, INK, INK_2, MUTED, ORANGE, PROCESSED, RAW, SURFACE, git,
                    pinned_version, style_axes)

FIX = re.compile(r"\b(fix(e[sd])?|bug(s|fix)?|hotfix)\b", re.I)
BULK = 30


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (path.startswith("tests/") or "/tests/" in path or name.startswith("test_")) and path.endswith(".py")


def is_prod(path: str) -> bool:
    return path.startswith("scrapy/") and path.endswith(".py") and not is_test(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="scrapy-src", type=Path)
    repo = ap.parse_args().repo.resolve()
    tag, sha, _ = pinned_version(repo)

    sep, end = "\x1f", "\x1e"
    out = git(repo, "log", "--first-parent", "--diff-merges=first-parent", "--name-only",
              f"--format={end}%H{sep}%aI{sep}%P{sep}%B{sep}", "HEAD")
    rows = []
    for block in out.split(end)[1:]:
        head, _, files_part = block.rpartition(sep)
        h, date, parents, msg = head.split(sep, 3)
        files = [f for f in files_part.splitlines() if f.strip()]
        prod = [f for f in files if is_prod(f)]
        tests = [f for f in files if is_test(f)]
        is_merge = len(parents.split()) > 1
        title = msg.strip().splitlines()
        # for "Merge pull request #123 from user/branch" the PR title is in the body
        rows.append({"sha": h, "date": date, "is_pr_merge": is_merge,
                     "title": " | ".join(title[:3])[:200],
                     "prod_files": len(prod), "test_files": len(tests),
                     "is_fix": bool(FIX.search(msg))})
    cs = pd.DataFrame(rows)
    cs["date"] = pd.to_datetime(cs["date"], utc=True)
    cs["year"] = cs["date"].dt.year
    cs["bulk"] = cs["prod_files"] > BULK
    cs["touches_tests"] = cs["test_files"] > 0
    cs.to_csv(RAW / "changesets.csv", index=False)

    rel = cs[(cs["prod_files"] > 0) & ~cs["bulk"]]
    def share(df):
        return round(100 * df["touches_tests"].mean(), 1) if len(df) else None

    per_year = rel.groupby("year").apply(lambda d: pd.Series({
        "changesets": len(d),
        "pr_merges": int(d["is_pr_merge"].sum()),
        "with_tests_pct": share(d),
        "fix_changesets": int(d["is_fix"].sum()),
        "fixes_with_tests_pct": share(d[d["is_fix"]]),
    }), include_groups=False)
    per_year.to_csv(PROCESSED / "t3_per_year.csv")

    last3 = rel[rel["date"] >= rel["date"].max() - pd.DateOffset(years=3)]
    summary = {
        "scrapy_version": tag, "scrapy_commit": sha,
        "changesets_on_main": int(len(cs)),
        "changesets_touching_production_code": int(len(rel)),
        "bulk_changesets_excluded": int(((cs["prod_files"] > 0) & cs["bulk"]).sum()),
        "with_tests_pct_all_time": share(rel),
        "fixes_with_tests_pct_all_time": share(rel[rel["is_fix"]]),
        "with_tests_pct_last_3_years": share(last3),
        "fixes_with_tests_pct_last_3_years": share(last3[last3["is_fix"]]),
        "pr_merges_share_last_3_years_pct": round(100 * last3["is_pr_merge"].mean(), 1),
    }
    (PROCESSED / "t3_summary.json").write_text(json.dumps(summary, indent=2))

    # ---- figure: two lines, one axis (%)
    py = per_year[per_year["changesets"] >= 10]  # skip years with too little data
    fig, ax = plt.subplots(figsize=(9, 4.2), facecolor=SURFACE)
    ax.plot(py.index, py["with_tests_pct"], color=BLUE, linewidth=2, marker="o", markersize=5,
            markeredgecolor=SURFACE, markeredgewidth=1.5, label="All change sets")
    fx = py[py["fix_changesets"] >= 5]
    ax.plot(fx.index, fx["fixes_with_tests_pct"], color=ORANGE, linewidth=2, marker="o", markersize=5,
            markeredgecolor=SURFACE, markeredgewidth=1.5, label="Bug-fix change sets")
    last = py.index[-1]
    ax.annotate(f"{py.loc[last, 'with_tests_pct']:.0f}%", (last, py.loc[last, "with_tests_pct"]),
                xytext=(6, 0), textcoords="offset points", fontsize=8, color=INK_2, va="center")
    if last in fx.index:
        ax.annotate(f"{fx.loc[last, 'fixes_with_tests_pct']:.0f}%", (last, fx.loc[last, "fixes_with_tests_pct"]),
                    xytext=(6, 0), textcoords="offset points", fontsize=8, color=INK_2, va="center")
    ax.set_ylim(0, 105)
    ax.set_xticks(list(py.index))
    ax.set_xticklabels([str(y) if (y % 2 == 0 or y == last) else "" for y in py.index])
    style_axes(ax, "Change sets that also change tests", "", "Share of change sets (%)")
    ax.grid(axis="x", visible=False)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK_2, loc="lower right")
    fig.text(0.01, 0.01, f"First-parent history of Scrapy's main branch up to {tag} (merged PRs + direct "
             f"commits) touching production code; > {BULK} files excluded. {last} is partial.",
             fontsize=7, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(FIGURES / "3_t3_test_coevolution.png", dpi=200)
    plt.close(fig)

    print(json.dumps(summary, indent=2))
    print(per_year.to_string())


if __name__ == "__main__":
    main()
