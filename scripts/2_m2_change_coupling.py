"""Modifiability – Metric M2: change coupling (logical coupling) between modules.

Two modules are change-coupled when the same commit modifies both. If a design
keeps modules independent, most changes should stay inside one module.

Data: non-merge commits of the last 3 years before the pinned Scrapy version that
touch production code (scrapy/*.py). Bulk commits (> 30 production files, e.g.
mass reformatting) are excluded.

Measures
  * share of commits that touch more than one module (per year -> trend)
  * coupling degree for each module pair = shared commits / average commits of
    both modules (same definition as CodeScene's "degree of coupling")
  * the most strongly coupled file pairs that cross module boundaries

Outputs
  data/raw/window_commits.csv, data/raw/window_changes.csv
  data/processed/m2_module_pairs.csv, m2_file_pairs.csv, m2_per_year.csv, m2_summary.json
  figures/2_m2_change_coupling_matrix.png

Usage:  python scripts/2_m2_change_coupling.py --repo scrapy-src [--years 3]
"""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap
from matplotlib.patches import Rectangle

from common import (BLUE_RAMP, FIGURES, INK, INK_2, MUTED, PROCESSED, RAW, SURFACE,
                    ModuleMap, pinned_version, window_commits)

MIN_SHARED_FILE_PAIR = 5  # ignore file pairs that changed together fewer times


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="scrapy-src", type=Path)
    ap.add_argument("--years", default=3.0, type=float)
    args = ap.parse_args()
    repo = args.repo.resolve()
    tag, sha, _ = pinned_version(repo)
    mm = ModuleMap()

    commits, changes, meta = window_commits(repo, args.years)
    commits.to_csv(RAW / "window_commits.csv", index=False)
    changes.to_csv(RAW / "window_changes.csv", index=False)

    keep = set(commits.loc[(commits["n_prod_files"] > 0) & ~commits["bulk"], "sha"])
    ch = changes[changes["sha"].isin(keep)].copy()
    ch["module"] = ch["file"].map(mm.module_of)
    ch = ch.dropna(subset=["module"])  # files that no longer exist in a mapped location

    mods_per_commit = ch.groupby("sha")["module"].apply(lambda s: sorted(set(s)))
    n_commits = len(mods_per_commit)
    multi = mods_per_commit[mods_per_commit.str.len() > 1]

    # ---- module pairs
    rev = pd.Series([m for ms in mods_per_commit for m in ms]).value_counts()
    pairs = pd.Series([p for ms in multi for p in combinations(ms, 2)]).value_counts()
    rows = []
    for (a, b), shared in pairs.items():
        avg = (rev[a] + rev[b]) / 2
        rows.append({"module_a": a, "module_b": b, "commits_a": int(rev[a]),
                     "commits_b": int(rev[b]), "shared_commits": int(shared),
                     "coupling_degree_pct": round(100 * shared / avg, 1)})
    mod_pairs = pd.DataFrame(rows).sort_values("coupling_degree_pct", ascending=False)
    mod_pairs.to_csv(PROCESSED / "m2_module_pairs.csv", index=False)

    # ---- file pairs across module boundaries
    files_per_commit = ch.groupby("sha")["file"].apply(lambda s: sorted(set(s)))
    frev = ch.groupby("file")["sha"].nunique()
    fpairs = pd.Series([p for fs in files_per_commit if len(fs) > 1
                        for p in combinations(fs, 2)]).value_counts()
    frows = []
    for (a, b), shared in fpairs.items():
        if shared < MIN_SHARED_FILE_PAIR or mm.module_of(a) == mm.module_of(b):
            continue
        frows.append({"file_a": a, "module_a": mm.module_of(a), "file_b": b,
                      "module_b": mm.module_of(b), "shared_commits": int(shared),
                      "coupling_degree_pct": round(100 * shared / ((frev[a] + frev[b]) / 2), 1)})
    file_pairs = pd.DataFrame(frows)
    if not file_pairs.empty:
        file_pairs = file_pairs.sort_values(["coupling_degree_pct", "shared_commits"], ascending=False)
    file_pairs.to_csv(PROCESSED / "m2_file_pairs.csv", index=False)

    # ---- trend per year
    cdate = commits.set_index("sha")["date"]
    per = pd.DataFrame({"modules": mods_per_commit})
    per["year"] = cdate.reindex(per.index).dt.year
    per["multi"] = per["modules"].str.len() > 1
    per_year = per.groupby("year").agg(commits=("multi", "size"), multi_module=("multi", "sum"))
    per_year["multi_module_pct"] = (100 * per_year["multi_module"] / per_year["commits"]).round(1)
    per_year.to_csv(PROCESSED / "m2_per_year.csv")

    summary = {
        "scrapy_version": tag, "scrapy_commit": sha, **meta,
        "commits_in_window": int(len(commits)),
        "commits_touching_production_code": int((commits["n_prod_files"] > 0).sum()),
        "bulk_commits_excluded": int(commits["bulk"].sum()),
        "commits_analysed": n_commits,
        "multi_module_commits": int(len(multi)),
        "multi_module_pct": round(100 * len(multi) / n_commits, 1),
        "commits_per_module": rev.to_dict(),
        "top_module_pairs": mod_pairs.head(8).to_dict("records"),
        "cross_module_file_pairs_found": int(len(file_pairs)),
    }
    (PROCESSED / "m2_summary.json").write_text(json.dumps(summary, indent=2, default=int))

    # ---- figure: coupling-degree matrix (symmetric, lower triangle shown)
    names = mm.names
    deg = pd.DataFrame(0.0, index=names, columns=names)
    shared = pd.DataFrame(0, index=names, columns=names)
    for r in mod_pairs.itertuples():
        for a, b in ((r.module_a, r.module_b), (r.module_b, r.module_a)):
            deg.loc[a, b] = r.coupling_degree_pct
            shared.loc[a, b] = r.shared_commits
    bounds = [0, 0.001, 5, 10, 20, 30, 40, 50, 1000]
    idx = np.digitize(deg.values, bounds) - 1
    labels = [mm.labels[m] for m in names]
    fig, ax = plt.subplots(figsize=(8.6, 7.4), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    masked = np.where(np.tril(np.ones_like(idx), -1) == 1, idx, -1).astype(float)
    masked[masked < 0] = np.nan
    ax.imshow(masked, cmap=ListedColormap(BLUE_RAMP), vmin=0, vmax=len(BLUE_RAMP) - 1)
    for i in range(len(names)):
        for j in range(len(names)):
            if i == j:
                ax.add_patch(Rectangle((j - .5, i - .5), 1, 1, color="#f0efec"))
                ax.text(j, i, f"{int(rev.get(names[i], 0))}", ha="center", va="center",
                        fontsize=7, color=MUTED)
            elif i > j and deg.iloc[i, j] > 0:
                ax.text(j, i, f"{deg.iloc[i, j]:.0f}%\n({shared.iloc[i, j]})", ha="center",
                        va="center", fontsize=7, color="#ffffff" if idx[i, j] >= 4 else INK)
    ax.set_xticks(range(len(names)), labels, rotation=40, ha="right", fontsize=8, color=INK_2)
    ax.set_yticks(range(len(names)), labels, fontsize=8, color=INK_2)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title("Change coupling between modules", loc="left", color=INK, fontsize=11,
                 fontweight="bold", pad=10)
    fig.text(0.01, 0.01, f"Coupling degree = shared commits / average commits of both modules "
             f"(shared commits in brackets;\ndiagonal = commits per module). "
             f"{meta['window_start']} to {meta['window_end']}, Scrapy {tag}, bulk commits excluded.",
             fontsize=6.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(FIGURES / "2_m2_change_coupling_matrix.png", dpi=200)
    plt.close(fig)

    print(json.dumps({k: v for k, v in summary.items() if k != "top_module_pairs"}, indent=2, default=int))
    print("\nTop module pairs:\n", mod_pairs.head(10).to_string(index=False))
    print("\nPer year:\n", per_year.to_string())
    print("\nTop cross-module file pairs:\n", file_pairs.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
