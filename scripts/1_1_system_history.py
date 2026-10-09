"""Section 1.1 - System Description & History for Scrapy.

Collects:
  * project age (first commit -> analysed commit)
  * size in lines of code (production code vs. tests vs. docs)
  * number of contributors (all, and with 50+ commits)
  * project activity over time (commits and active contributors per year)

Usage (from the root of this repository, with the venv active):
    python scripts/1_1_system_history.py --repo scrapy-src

All outputs are written to data/raw, data/processed and figures/.
Everything is computed from the commit currently checked out in --repo
(the pinned Scrapy version), so re-running gives the same numbers.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import tokenize
import unicodedata
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW, PROCESSED, FIGURES = ROOT / "data/raw", ROOT / "data/processed", ROOT / "figures"

# Colours / ink for the figures (light theme, print-friendly)
BLUE, INK, INK_2, MUTED, GRID, SURFACE = (
    "#2a78d6", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb",
)


# --------------------------------------------------------------------------- git
def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, encoding="utf-8", errors="replace",  # some old commits are not UTF-8
    ).stdout


def collect_commits(repo: Path) -> pd.DataFrame:
    """One row per commit reachable from the checked-out commit.

    %aN / %aE respect the project's .mailmap, so one person using several
    e-mail addresses is (mostly) counted once.
    """
    sep = "\x1f"
    out = git(repo, "log", f"--format=%H{sep}%aN{sep}%aE{sep}%aI{sep}%P", "HEAD")
    rows = []
    for line in out.splitlines():
        sha, name, email, date, parents = line.split(sep)
        rows.append(
            {
                "sha": sha,
                "author": name,
                "email": email.lower(),
                "date": date,
                "is_merge": len(parents.split()) > 1,
            }
        )
    df = pd.DataFrame(rows)
    df["author_raw"] = df["author"]
    df["author"] = merge_identities(df)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df["year"] = df["date"].dt.year
    # Bots are not "people who contributed"
    df["is_bot"] = df["author"].str.contains(r"\[bot\]|dependabot|pre-commit-ci", case=False)
    return df


def merge_identities(df: pd.DataFrame) -> pd.Series:
    """Map each (name, e-mail) to one canonical person.

    Two identities are the same person if they share an e-mail address or the
    same accent/case-insensitive name ("Daniel Graña" == "Daniel Grana").
    Extra manual merges come from config/author_aliases.json.
    """
    aliases = {k.lower(): v for k, v in
               json.loads((ROOT / "config/author_aliases.json").read_text()).items()
               if not k.startswith("_")}
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    def norm(name: str) -> str:
        n = unicodedata.normalize("NFKD", name)
        return "".join(c for c in n if not unicodedata.combining(c)).lower().strip()

    generic = {"none@none", "", "(none)"}
    for name, email in df[["author", "email"]].drop_duplicates().itertuples(index=False):
        key = f"id:{name}|{email}"
        union(key, f"name:{norm(name)}")
        if email not in generic:
            union(key, f"email:{email}")
        for alias in (name.lower(), email):
            if alias in aliases:
                union(key, f"name:{norm(aliases[alias])}")

    ids = "id:" + df["author"] + "|" + df["email"]
    roots = ids.map(find)
    # Canonical name = the name used most often within each group
    canonical = df.assign(root=roots).groupby("root")["author"].agg(lambda s: s.value_counts().index[0])
    return roots.map(canonical)


# --------------------------------------------------------------------------- LOC
def python_loc(path: Path) -> tuple[int, int, int]:
    """Return (code, comment, blank) lines of one Python file.

    Docstrings and comments count as 'comment', like cloc does.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    code_lines: set[int] = set()
    comment_lines: set[int] = set()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Unparseable file: fall back to a simple heuristic
        blank = sum(1 for ln in lines if not ln.strip())
        comment = sum(1 for ln in lines if ln.strip().startswith("#"))
        return len(lines) - blank - comment, comment, blank

    skip = {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
            tokenize.ENDMARKER, tokenize.ENCODING}
    prev_sig = None  # previous significant token type
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.COMMENT:
            comment_lines.add(tok.start[0])
            continue
        if tok.type in skip:
            if tok.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                prev_sig = tok.type
            continue
        span = range(tok.start[0], tok.end[0] + 1)
        # A string that forms a statement on its own is a docstring
        nxt = next((t for t in tokens[i + 1:] if t.type not in (tokenize.NL, tokenize.COMMENT)), None)
        is_docstring = (
            tok.type == tokenize.STRING
            and prev_sig in (None, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)
            and nxt is not None
            and nxt.type in (tokenize.NEWLINE, tokenize.ENDMARKER)
        )
        (comment_lines if is_docstring else code_lines).update(span)
        prev_sig = tok.type

    comment_lines -= code_lines
    blank = sum(1 for ln in lines if not ln.strip())
    return len(code_lines), len(comment_lines), blank


def count_loc(repo: Path) -> pd.DataFrame:
    """LOC per area of the repository, only for files tracked by git."""
    files = git(repo, "ls-files").splitlines()
    areas = {"scrapy/": "production code", "tests/": "tests", "docs/": "docs"}
    rows = []
    for f in files:
        area = next((a for p, a in areas.items() if f.startswith(p)), "other")
        p = repo / f
        if not p.is_file():
            continue
        ext = p.suffix.lower() or "(none)"
        if ext == ".py":
            code, comment, blank = python_loc(p)
        else:
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue  # binary file
            blank = sum(1 for ln in lines if not ln.strip())
            code, comment = len(lines) - blank, 0
        rows.append({"file": f, "area": area, "ext": ext,
                     "code": code, "comment": comment, "blank": blank})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- figures
def style(ax, title: str, ylabel: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=11, fontweight="bold", pad=10)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=9)
    ax.tick_params(colors=MUTED, labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)


def bar_chart(series: pd.Series, title: str, ylabel: str, out: Path, note: str) -> None:
    fig, ax = plt.subplots(figsize=(9, 3.6), facecolor=SURFACE)
    years = series.index.astype(int)
    ax.bar(years, series.values, color=BLUE, width=0.75, edgecolor=SURFACE, linewidth=1.5)
    style(ax, title, ylabel)
    ax.set_xticks(years)
    ax.set_xticklabels([str(y) if (y % 2 == 1 or y == years[-1]) else "" for y in years])
    # Label only the peak and the last year (selective labels)
    for y in {series.idxmax(), series.index[-1]}:
        ax.annotate(f"{series[y]:,}", (y, series[y]), xytext=(0, 3),
                    textcoords="offset points", ha="center", fontsize=8, color=INK_2)
    fig.text(0.01, 0.01, note, fontsize=7, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out, dpi=200)
    plt.close(fig)


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="scrapy-src", type=Path,
                    help="path to the (pinned) Scrapy checkout")
    args = ap.parse_args()
    repo = args.repo.resolve()
    for d in (RAW, PROCESSED, FIGURES):
        d.mkdir(parents=True, exist_ok=True)

    head = git(repo, "rev-parse", "HEAD").strip()
    tag = git(repo, "describe", "--tags", "--always").strip()
    print(f"Analysing Scrapy {tag} ({head})")

    # ---- commits
    commits = collect_commits(repo)
    commits.to_csv(RAW / "commits.csv", index=False)
    print(f"Identities merged: {commits['author_raw'].nunique()} names -> "
          f"{commits['author'].nunique()} people")
    humans = commits[~commits["is_bot"]]
    non_merge = humans[~humans["is_merge"]]

    first, last = commits["date"].min(), commits["date"].max()
    age_years = (last - first).days / 365.25

    # ---- contributors (non-merge commits by humans)
    per_author = non_merge["author"].value_counts().rename_axis("author").reset_index(name="commits")
    per_author["share_pct"] = (100 * per_author["commits"] / per_author["commits"].sum()).round(1)
    per_author.to_csv(PROCESSED / "contributors.csv", index=False)
    core = per_author[per_author["commits"] >= 50]

    # ---- activity per year (incomplete last year is marked in the figure)
    per_year = pd.DataFrame({
        "commits": non_merge.groupby("year").size(),
        "active_contributors": non_merge.groupby("year")["author"].nunique(),
        "new_contributors": non_merge.groupby("author")["year"].min().value_counts(),
    }).fillna(0).astype(int).sort_index()
    per_year.index.name = "year"
    per_year.to_csv(PROCESSED / "activity_per_year.csv")

    # ---- lines of code
    loc = count_loc(repo)
    loc.to_csv(RAW / "loc_per_file.csv", index=False)
    loc_area = loc.groupby("area")[["code", "comment", "blank"]].sum()
    loc_area["files"] = loc.groupby("area").size()
    loc_area.to_csv(PROCESSED / "loc_per_area.csv")
    loc_lang = (loc[loc["area"] == "production code"]
                .groupby("ext")[["code", "comment", "blank"]].sum()
                .sort_values("code", ascending=False))
    loc_lang.to_csv(PROCESSED / "loc_production_per_language.csv")

    # ---- summary
    summary = {
        "analysed_tag": tag,
        "analysed_commit": head,
        "first_commit_date": first.date().isoformat(),
        "analysed_commit_date": last.date().isoformat(),
        "age_years": round(age_years, 1),
        "commits_total": int(len(commits)),
        "commits_non_merge_by_humans": int(len(non_merge)),
        "contributors_total": int(per_author.shape[0]),
        "contributors_50plus_commits": int(core.shape[0]),
        "top_contributor_share_pct": float(per_author["share_pct"].iloc[0]),
        "top5_contributors_share_pct": float(per_author["share_pct"].head(5).sum().round(1)),
        "loc_production_code": int(loc_area.loc["production code", "code"]),
        "loc_tests": int(loc_area.loc["tests", "code"]) if "tests" in loc_area.index else 0,
        "test_to_code_ratio": round(
            loc_area.loc["tests", "code"] / loc_area.loc["production code", "code"], 2),
        "production_python_files": int(loc[(loc["area"] == "production code") & (loc["ext"] == ".py")].shape[0]),
    }
    (PROCESSED / "summary_1_1.json").write_text(json.dumps(summary, indent=2))

    # ---- figures
    note = (f"Non-merge commits by humans (bots excluded). Scrapy {tag}. "
            f"{last.year} is partial (data until {last.date()}).")
    bar_chart(per_year["commits"], "Scrapy commits per year", "Commits",
              FIGURES / "1_1_commits_per_year.png", note)
    bar_chart(per_year["active_contributors"], "Scrapy active contributors per year",
              "People with ≥1 commit", FIGURES / "1_1_active_contributors_per_year.png", note)

    # ---- console report
    print(json.dumps(summary, indent=2))
    print("\nLOC per area:\n", loc_area.to_string())
    print("\nTop 10 contributors:\n", per_author.head(10).to_string(index=False))
    print("\nActivity per year:\n", per_year.to_string())
    print(f"\nWritten: {RAW}, {PROCESSED}, {FIGURES} (generated {datetime.now():%Y-%m-%d})")


if __name__ == "__main__":
    main()
