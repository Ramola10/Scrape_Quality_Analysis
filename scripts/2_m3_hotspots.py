"""Modifiability – Metric M3: hotspots (change frequency x code complexity).

A hotspot is a file that is both complex and changed often. Complex code that
never changes costs little; complex code that changes all the time is where
modifiability problems hurt most (Tornhill, "Your Code as a Crime Scene").

  * Complexity: McCabe cyclomatic complexity (CC) per function, computed with
    Python's `ast` on the pinned Scrapy version, counted the same way as the
    `radon` tool (1 + if/elif/for/while/except/with-less decision points,
    boolean operators, comprehensions, conditional expressions, match cases,
    asserts). Per file: total CC, max CC, number of functions with CC > 10.
  * Change frequency: number of commits (revisions) and lines added+deleted
    per file in the 3 years before the pinned version (bulk commits excluded).
  * Hotspot score = (revisions / max revisions) x (total CC / max total CC), 0..1.

Outputs
  data/raw/function_complexity.csv
  data/processed/m3_file_hotspots.csv, m3_module_hotspots.csv, m3_summary.json
  figures/2_m3_hotspots.png

Usage:  python scripts/2_m3_hotspots.py --repo scrapy-src [--years 3]
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from common import (BLUE, FIGURES, INK, INK_2, MUTED, PROCESSED, RAW, SURFACE, ModuleMap,
                    git, is_production, pinned_version, style_axes, window_commits)

GREY = "#c3c2b7"
TOP_N = 10


# --------------------------------------------------------------------------- complexity
def _decisions(node: ast.AST) -> int:
    """Decision points inside one function body (nested functions excluded)."""
    count = 0
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue  # counted separately
        if isinstance(n, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.Assert)):
            count += 1
            if isinstance(n, (ast.For, ast.AsyncFor, ast.While)) and n.orelse:
                count += 1
        elif isinstance(n, ast.ExceptHandler):
            count += 1
        elif isinstance(n, ast.Try) and n.orelse:
            count += 1
        elif isinstance(n, ast.BoolOp):
            count += len(n.values) - 1
        elif isinstance(n, ast.comprehension):
            count += 1 + len(n.ifs)
        elif isinstance(n, ast.match_case):
            count += 1
        stack.extend(ast.iter_child_nodes(n))
    return count


def function_complexity(path: str, source: str) -> list[dict]:
    tree = ast.parse(source, filename=path)
    rows = []

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                rows.append({"file": path, "function": f"{prefix}{child.name}",
                             "line": child.lineno, "cc": 1 + _decisions(child),
                             "loc": (child.end_lineno or child.lineno) - child.lineno + 1})
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree)
    return rows


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="scrapy-src", type=Path)
    ap.add_argument("--years", default=3.0, type=float)
    args = ap.parse_args()
    repo = args.repo.resolve()
    tag, sha, _ = pinned_version(repo)
    mm = ModuleMap()

    files = [f for f in git(repo, "ls-files", "scrapy").splitlines() if is_production(f)]
    funcs = pd.DataFrame([r for f in files for r in
                          function_complexity(f, (repo / f).read_text(encoding="utf-8"))])
    funcs["module"] = funcs["file"].map(mm.module_of)
    funcs.to_csv(RAW / "function_complexity.csv", index=False)

    per_file = funcs.groupby("file").agg(functions=("cc", "size"), cc_total=("cc", "sum"),
                                         cc_max=("cc", "max"),
                                         complex_functions=("cc", lambda s: int((s > 10).sum())))
    per_file = per_file.reindex(files, fill_value=0)
    per_file["sloc"] = [sum(1 for ln in (repo / f).read_text(encoding="utf-8").splitlines()
                            if ln.strip() and not ln.strip().startswith("#")) for f in files]

    commits, changes, meta = window_commits(repo, args.years)
    keep = set(commits.loc[~commits["bulk"], "sha"])
    ch = changes[changes["sha"].isin(keep)]
    churn = ch.groupby("file").agg(revisions=("sha", "nunique"), added=("added", "sum"),
                                   deleted=("deleted", "sum"))
    churn["lines_changed"] = churn["added"] + churn["deleted"]

    hot = per_file.join(churn[["revisions", "lines_changed"]], how="left").fillna(0)
    hot[["revisions", "lines_changed"]] = hot[["revisions", "lines_changed"]].astype(int)
    hot["module"] = [mm.module_of(f) for f in hot.index]
    hot["hotspot_score"] = ((hot["revisions"] / hot["revisions"].max())
                            * (hot["cc_total"] / hot["cc_total"].max())).round(3)
    hot = hot.sort_values("hotspot_score", ascending=False)
    hot.index.name = "file"
    hot.to_csv(PROCESSED / "m3_file_hotspots.csv")

    by_mod = hot.groupby("module").agg(files=("sloc", "size"), sloc=("sloc", "sum"),
                                       cc_total=("cc_total", "sum"),
                                       complex_functions=("complex_functions", "sum"),
                                       revisions=("revisions", "sum"),
                                       hotspot_score_sum=("hotspot_score", "sum"))
    by_mod["cc_per_100_sloc"] = (100 * by_mod["cc_total"] / by_mod["sloc"]).round(1)
    by_mod["files_in_top10"] = hot.head(TOP_N)["module"].value_counts().reindex(by_mod.index).fillna(0).astype(int)
    by_mod = by_mod.reindex(mm.names)
    by_mod.insert(0, "label", [mm.labels[m] for m in by_mod.index])
    by_mod.to_csv(PROCESSED / "m3_module_hotspots.csv")

    top = hot.head(TOP_N)
    score_share = top["hotspot_score"].sum() / hot["hotspot_score"].sum()
    rev_share = top["revisions"].sum() / hot["revisions"].sum()
    summary = {
        "scrapy_version": tag, "scrapy_commit": sha, **meta,
        "files": len(hot), "functions": len(funcs),
        "mean_cc_per_function": round(funcs["cc"].mean(), 2),
        "median_cc_per_function": float(funcs["cc"].median()),
        "functions_cc_over_10": int((funcs["cc"] > 10).sum()),
        "functions_cc_over_20": int((funcs["cc"] > 20).sum()),
        "most_complex_functions": funcs.nlargest(5, "cc")[["file", "function", "cc"]].to_dict("records"),
        "top10_files_share_of_all_files_pct": round(100 * TOP_N / len(hot), 1),
        "top10_hotspots_share_of_revisions_pct": round(100 * rev_share, 1),
        "top10_hotspots_share_of_cc_pct": round(100 * top["cc_total"].sum() / hot["cc_total"].sum(), 1),
        "top10_hotspots_share_of_hotspot_score_pct": round(100 * score_share, 1),
        "top10_hotspots": top.reset_index()[["file", "module", "revisions", "cc_total",
                                             "cc_max", "hotspot_score"]].to_dict("records"),
    }
    (PROCESSED / "m3_summary.json").write_text(json.dumps(summary, indent=2, default=int))

    # ---- figure: scatter revisions vs. total complexity, top hotspots labelled
    fig, ax = plt.subplots(figsize=(10.5, 5.6), facecolor=SURFACE)
    rest = hot.iloc[TOP_N:]
    ax.scatter(rest["revisions"], rest["cc_total"], s=22, color=GREY, edgecolor=SURFACE,
               linewidth=1, label="Other files", zorder=2)
    ax.scatter(top["revisions"], top["cc_total"], s=46, color=BLUE, edgecolor=SURFACE,
               linewidth=1.5, label=f"Top {TOP_N} hotspots", zorder=3)
    # Rank numbers next to the points + a ranked list on the right (no overlapping labels)
    lines = []
    for rank, (f, r) in enumerate(top.iterrows(), start=1):
        ax.annotate(str(rank), (r["revisions"], r["cc_total"]), xytext=(5, 3),
                    textcoords="offset points", fontsize=7.5, color=INK, fontweight="bold")
        lines.append(f"{rank:>2}  {f.replace('scrapy/', '')}")
    fig.text(0.735, 0.88, "Top hotspots (rank)", fontsize=8.5, color=INK, fontweight="bold", va="top")
    fig.text(0.735, 0.84, "\n".join(lines), fontsize=7.5, color=INK_2, va="top",
             family="monospace", linespacing=1.6)
    style_axes(ax, "Hotspots: change frequency vs. complexity",
               f"Commits touching the file ({meta['window_start']} – {meta['window_end']})",
               "Total cyclomatic complexity of the file")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK_2, loc="upper left")
    fig.text(0.01, 0.01, f"Scrapy {tag}, production code (scrapy/*.py), bulk commits (>30 files) "
             "excluded. Hotspot score = normalised revisions × normalised complexity.",
             fontsize=7, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 0.72, 1))
    fig.savefig(FIGURES / "2_m3_hotspots.png", dpi=200)
    plt.close(fig)

    print(json.dumps({k: v for k, v in summary.items() if k != "top10_hotspots"}, indent=2, default=int))
    print("\nTop hotspots:\n", top[["module", "revisions", "lines_changed", "cc_total", "cc_max",
                                     "complex_functions", "hotspot_score"]].to_string())
    print("\nPer module:\n", by_mod.to_string())


if __name__ == "__main__":
    main()
