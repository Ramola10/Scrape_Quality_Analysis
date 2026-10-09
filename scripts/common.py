"""Shared helpers for the analysis scripts (paths, git, module mapping, figure style)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

ROOT = Path(__file__).resolve().parent.parent
RAW, PROCESSED, FIGURES, CONFIG = (ROOT / "data/raw", ROOT / "data/processed",
                                   ROOT / "figures", ROOT / "config")
for _d in (RAW, PROCESSED, FIGURES):
    _d.mkdir(parents=True, exist_ok=True)

# Figure colours (light, print-friendly)
BLUE, ORANGE, INK, INK_2, MUTED, GRID, SURFACE = (
    "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb",
)
BLUE_RAMP = ["#fcfcfb", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    ).stdout


def pinned_version(repo: Path) -> tuple[str, str, str]:
    """(tag, commit sha, commit date) of the checked-out Scrapy version."""
    sha = git(repo, "rev-parse", "HEAD").strip()
    tag = git(repo, "describe", "--tags", "--always").strip()
    date = git(repo, "log", "-1", "--format=%cI").strip()
    return tag, sha, date


class ModuleMap:
    """Maps a file path (relative to the Scrapy repo) to a module of config/modules.json."""

    def __init__(self, path: Path = CONFIG / "modules.json"):
        cfg = json.loads(path.read_text())["modules"]
        self.names = list(cfg)
        self.labels = {m: c["label"] for m, c in cfg.items()}
        self.allowed = {m: set(c["allowed"]) for m, c in cfg.items()}
        # longest prefix first so that scrapy/core/downloader/ beats scrapy/core/
        self._prefixes = sorted(((p, m) for m, c in cfg.items() for p in c["paths"]),
                                key=lambda x: -len(x[0]))

    def module_of(self, path: str) -> str | None:
        for prefix, mod in self._prefixes:
            if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
                return mod
        return None


def is_production(path: str) -> bool:
    return path.startswith("scrapy/") and path.endswith(".py")


def style_axes(ax, title: str, xlabel: str = "", ylabel: str = "") -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=11, fontweight="bold", pad=10)
    ax.set_xlabel(xlabel, color=INK_2, fontsize=9)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=9)
    ax.tick_params(colors=MUTED, labelsize=8, length=0)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)


def _split_rename(p: str) -> tuple[str, str]:
    """'a/{b => c}/d.py' -> ('a/b/d.py', 'a/c/d.py');  'x.py => y.py' -> ('x.py', 'y.py')."""
    import re

    m = re.match(r"(.*)\{(.*) => (.*)\}(.*)", p)
    if m:
        pre, a, b, post = m.groups()
        return (pre + a + post).replace("//", "/"), (pre + b + post).replace("//", "/")
    a, b = p.split(" => ")
    return a, b


def window_commits(repo: Path, years: float = 3.0, max_files: int = 30):
    """Non-merge commits in the `years` before the pinned commit, with per-file churn.

    Returns (commits, changes):
      commits: DataFrame [sha, date, author, n_prod_files, bulk]
      changes: DataFrame [sha, file, added, deleted]  (production .py files only)
    Commits touching more than `max_files` production files are flagged `bulk`
    (mass reformatting / renames); they are usually excluded because they say
    nothing about logical coupling.
    """
    import pandas as pd

    end = pd.Timestamp(git(repo, "log", "-1", "--format=%cI").strip())
    start = end - pd.DateOffset(days=int(365.25 * years))
    out = git(repo, "log", "--no-merges", "-M", "--numstat",
              f"--since={start.isoformat()}", "--format=@@%H\x1f%aI\x1f%aN", "HEAD")
    current = set(git(repo, "ls-files").splitlines())
    renamed: dict[str, str] = {}  # old path -> path at the pinned version
    commits, changes, cur = [], [], None
    for line in out.splitlines():
        if line.startswith("@@"):
            sha, date, author = line[2:].split("\x1f")
            cur = sha
            commits.append({"sha": sha, "date": date, "author": author})
        elif line.strip() and cur:
            added, deleted, path = line.split("\t", 2)
            if " => " in path:  # rename, e.g. scrapy/core/{http2 => _http2}/agent.py
                old, new = _split_rename(path)
                new = renamed.get(new, new)
                renamed[old] = new
                path = new
            else:
                path = renamed.get(path, path)
            # history is read newest -> oldest, so `path` is now the file's current name;
            # files deleted before the pinned version are ignored
            if is_production(path) and path in current:
                changes.append({"sha": cur, "file": path,
                                "added": int(added) if added.isdigit() else 0,
                                "deleted": int(deleted) if deleted.isdigit() else 0})
    commits = pd.DataFrame(commits)
    commits["date"] = pd.to_datetime(commits["date"], utc=True)
    changes = pd.DataFrame(changes)
    n = changes.groupby("sha")["file"].nunique()
    commits["n_prod_files"] = commits["sha"].map(n).fillna(0).astype(int)
    commits["bulk"] = commits["n_prod_files"] > max_files
    meta = {"window_start": start.date().isoformat(), "window_end": end.date().isoformat(),
            "years": years, "bulk_threshold_files": max_files}
    return commits, changes, meta
