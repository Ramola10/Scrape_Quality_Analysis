"""Measure how often production-code changes also change tests."""

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from common import FIGURES, PROCESSED, RAW, git, pinned_version, style_axes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--years", type=float, default=3.0)
    args = parser.parse_args()

    repo = args.repo.resolve()
    tag, commit, commit_date = pinned_version(repo)

    end = pd.Timestamp(commit_date)
    start = end - pd.DateOffset(days=int(365.25 * args.years))

    output = git(
        repo,
        "log",
        "--no-merges",
        f"--since={start.isoformat()}",
        "--name-only",
        "--format=@@%H%x1f%aI",
        "HEAD",
    )

    commits = []
    current = None

    for line in output.splitlines():
        if line.startswith("@@"):
            sha, date = line[2:].split("\x1f")
            current = {
                "sha": sha,
                "date": date,
                "production_files": set(),
                "test_files": set(),
            }
            commits.append(current)

        elif line.strip() and current is not None:
            path = line.strip().replace("\\", "/")

            if path.startswith("scrapy/") and path.endswith(".py"):
                current["production_files"].add(path)

            elif path.startswith("tests/") and path.endswith(".py"):
                current["test_files"].add(path)

    rows = []

    for item in commits:
        if not item["production_files"]:
            continue

        rows.append(
           {
                "sha": item["sha"],
                "date": item["date"],
                "production_file_count": len(item["production_files"]),
                "test_file_count": len(item["test_files"]),
                "tests_changed": len(item["test_files"]) > 0,
            }
        )

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df["year"] = df["date"].dt.year

    total = len(df)
    with_tests = int(df["tests_changed"].sum())
    percentage = round(100 * with_tests / total, 1) if total else 0.0

    yearly = (
        df.groupby("year")
        .agg(
            production_commits=("sha", "count"),
            commits_with_tests=("tests_changed", "sum"),
        )
        .reset_index()
    )

    yearly["coevolution_pct"] = (
        100 * yearly["commits_with_tests"] / yearly["production_commits"]
    ).round(1)

    summary = {
        "scrapy_version": tag,
        "scrapy_commit": commit,
        "window_start": start.date().isoformat(),
        "window_end": end.date().isoformat(),
        "years": args.years,
        "production_changing_commits": total,
        "production_commits_also_changing_tests": with_tests,
        "test_coevolution_pct": percentage,
    }

    RAW.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)

    df.to_csv(
        RAW / "test_coevolution_commits.csv",
        index=False,
    )

    yearly.to_csv(
        PROCESSED / "test_coevolution_per_year.csv",
        index=False,
    )

    (PROCESSED / "test_coevolution_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    fig, ax = plt.subplots(figsize=(8, 4.5))

    ax.bar(
        yearly["year"].astype(str),
        yearly["coevolution_pct"],
    )

    style_axes(
        ax,
        "Scrapy test co-evolution by year",
        "Year",
        "Production changes also changing tests (%)",
    )

    ax.set_ylim(0, 100)
    fig.tight_layout()

    fig.savefig(
        FIGURES / "3_m3_test_coevolution.png",
        dpi=200,
        bbox_inches="tight",
    )

    plt.close(fig)

    print(json.dumps(summary, indent=2))
    print("\nPer year:")
    print(yearly.to_string(index=False))


if __name__ == "__main__":
    main()