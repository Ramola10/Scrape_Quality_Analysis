"""Modifiability – Metric M1: structural coupling between modules (import analysis).

For every Python file of Scrapy's production code (scrapy/), all imports of other
Scrapy files are extracted with Python's `ast` module and mapped to the modules
defined in config/modules.json. Each import is classified as

  * runtime  – executed when the file is imported (top-level import)
  * lazy     – inside a function/method (executed only when called)
  * typing   – inside `if TYPE_CHECKING:` (only for type checkers, never executed)

Outputs
  data/raw/import_edges.csv                 every file -> file import
  data/processed/m1_module_matrix.csv       module x module, number of file->file imports
  data/processed/m1_module_metrics.csv      Ca, Ce, instability, violations per module
  data/processed/m1_violations.csv          imports that break the <<allowed to use>> rules
  data/processed/m1_summary.json
  figures/2_m1_dependency_matrix.png
  figures/2_m1_module_graph.dot             module graph (render with Graphviz, e.g. dreampuf.github.io/GraphvizOnline)

Usage:  python scripts/2_m1_import_coupling.py --repo scrapy-src
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap
from matplotlib.patches import Rectangle

from common import (FIGURES, INK, INK_2, MUTED, ORANGE, PROCESSED, RAW, SURFACE, BLUE_RAMP,
                    ModuleMap, git, is_production, pinned_version)


# --------------------------------------------------------------------------- import extraction
class ImportVisitor(ast.NodeVisitor):
    """Collects (imported dotted name, kind, line) for one file."""

    def __init__(self, package: str):
        self.package = package  # dotted package of the current file, for relative imports
        self.found: list[tuple[str, str, int]] = []
        self._func_depth = 0
        self._typing_depth = 0

    def _kind(self) -> str:
        if self._typing_depth:
            return "typing"
        return "lazy" if self._func_depth else "runtime"

    def visit_FunctionDef(self, node):
        self._func_depth += 1
        self.generic_visit(node)
        self._func_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node):
        test = ast.unparse(node.test)
        if test in ("TYPE_CHECKING", "typing.TYPE_CHECKING"):
            self._typing_depth += 1
            for n in node.body:
                self.visit(n)
            self._typing_depth -= 1
            for n in node.orelse:
                self.visit(n)
        else:
            self.generic_visit(node)

    def visit_Import(self, node):
        for a in node.names:
            self.found.append((a.name, self._kind(), node.lineno))

    def visit_ImportFrom(self, node):
        if node.level:  # relative import
            base = self.package.split(".")
            base = base[: len(base) - node.level + 1]
            mod = ".".join(base + ([node.module] if node.module else []))
        else:
            mod = node.module or ""
        for a in node.names:
            # "from scrapy.http import Request": try scrapy.http.Request first (could be a submodule)
            self.found.append((f"{mod}.{a.name}" if a.name != "*" else mod, self._kind(), node.lineno))


def resolve(dotted: str, files: set[str]) -> str | None:
    """Map a dotted name (scrapy.http.request.Request) to the file defining it."""
    if not dotted.startswith("scrapy"):
        return None
    parts = dotted.split(".")
    while parts:
        base = "/".join(parts)
        for cand in (f"{base}.py", f"{base}/__init__.py"):
            if cand in files:
                return cand
        parts.pop()
    return None


def extract_edges(repo: Path, files: list[str]) -> pd.DataFrame:
    fileset = set(files)
    rows = []
    for f in files:
        # package containing the file: scrapy/a/b.py and scrapy/a/__init__.py -> "scrapy.a"
        package = f.rsplit("/", 1)[0].replace("/", ".")
        tree = ast.parse((repo / f).read_text(encoding="utf-8"), filename=f)
        v = ImportVisitor(package)
        v.visit(tree)
        for dotted, kind, line in v.found:
            target = resolve(dotted, fileset)
            if target and target != f:
                rows.append({"source": f, "target": target, "kind": kind, "line": line,
                             "imported": dotted})
    return pd.DataFrame(rows).drop_duplicates(["source", "target", "kind"])


# --------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="scrapy-src", type=Path)
    repo = ap.parse_args().repo.resolve()
    tag, sha, _ = pinned_version(repo)
    mm = ModuleMap()

    files = [f for f in git(repo, "ls-files", "scrapy").splitlines() if is_production(f)]
    unmapped = [f for f in files if mm.module_of(f) is None]
    if unmapped:
        raise SystemExit(f"Files not mapped to any module (fix config/modules.json): {unmapped}")

    edges = extract_edges(repo, files)
    edges["src_mod"] = edges["source"].map(mm.module_of)
    edges["dst_mod"] = edges["target"].map(mm.module_of)
    edges["cross_module"] = edges["src_mod"] != edges["dst_mod"]
    edges["allowed"] = [(not c) or (d in mm.allowed[s])
                        for s, d, c in zip(edges["src_mod"], edges["dst_mod"], edges["cross_module"])]
    edges.to_csv(RAW / "import_edges.csv", index=False)

    executed = edges[edges["kind"] != "typing"]          # runtime + lazy = real dependencies
    cross = executed[executed["cross_module"]]

    # ---- module x module matrix (number of file->file imports, executed only)
    names = mm.names
    matrix = (cross.groupby(["src_mod", "dst_mod"]).size().unstack(fill_value=0)
              .reindex(index=names, columns=names, fill_value=0))
    matrix.to_csv(PROCESSED / "m1_module_matrix.csv")

    # ---- per-module metrics (Robert C. Martin's afferent/efferent coupling, file granularity)
    file_mod = {f: mm.module_of(f) for f in files}
    metrics = []
    for m in names:
        out_e = cross[cross["src_mod"] == m]
        in_e = cross[cross["dst_mod"] == m]
        ce = out_e["target"].nunique()   # files outside m that m depends on
        ca = in_e["source"].nunique()    # files outside m that depend on m
        viol = out_e[~out_e["allowed"]]
        metrics.append({
            "module": m, "label": mm.labels[m],
            "files": sum(1 for v in file_mod.values() if v == m),
            "Ca_afferent": ca, "Ce_efferent": ce,
            "instability": round(ce / (ca + ce), 2) if ca + ce else 0.0,
            "depends_on_modules": out_e["dst_mod"].nunique(),
            "used_by_modules": in_e["src_mod"].nunique(),
            "violating_imports": len(viol),
            "violating_imports_runtime": int((viol["kind"] == "runtime").sum()),
            "violating_imports_lazy": int((viol["kind"] == "lazy").sum()),
        })
    metrics = pd.DataFrame(metrics)
    metrics.to_csv(PROCESSED / "m1_module_metrics.csv", index=False)

    violations = cross[~cross["allowed"]].sort_values(["src_mod", "dst_mod", "source"])
    violations.to_csv(PROCESSED / "m1_violations.csv", index=False)

    # ---- cycles between modules (runtime imports only = import-time cycles)
    g = nx.DiGraph()
    rt = cross[cross["kind"] == "runtime"]
    g.add_edges_from(zip(rt["src_mod"], rt["dst_mod"]))
    sccs = [sorted(c) for c in nx.strongly_connected_components(g) if len(c) > 1]
    g_all = nx.DiGraph()
    g_all.add_edges_from(zip(cross["src_mod"], cross["dst_mod"]))
    sccs_all = [sorted(c) for c in nx.strongly_connected_components(g_all) if len(c) > 1]

    n_cross = len(cross)
    summary = {
        "scrapy_version": tag, "scrapy_commit": sha,
        "production_files": len(files),
        "imports_total": len(edges),
        "imports_by_kind": edges["kind"].value_counts().to_dict(),
        "cross_module_imports_executed": n_cross,
        "violating_imports": len(violations),
        "violation_rate_pct": round(100 * len(violations) / n_cross, 1) if n_cross else 0,
        "violations_by_pair": {f"{s} -> {d}": int(n) for (s, d), n in
                               violations.groupby(["src_mod", "dst_mod"]).size()
                               .sort_values(ascending=False).items()},
        "typing_only_imports_that_would_violate": int(
            (edges[(edges["kind"] == "typing") & edges["cross_module"]]["allowed"] == False).sum()),  # noqa: E712
        "module_cycles_runtime_imports": sccs,
        "module_cycles_incl_lazy_imports": sccs_all,
    }
    (PROCESSED / "m1_summary.json").write_text(json.dumps(summary, indent=2))

    # ---- figure: dependency structure matrix
    labels = [mm.labels[m] for m in names]
    data = matrix.values.astype(float)
    fig, ax = plt.subplots(figsize=(8.6, 7.2), facecolor=SURFACE)
    vmax = max(1, data.max())
    bounds = [0, 1, 5, 10, 25, 50, 100, 200, 10**6]  # colour bins
    idx = np.digitize(data, bounds) - 1
    ax.imshow(idx, cmap=ListedColormap(BLUE_RAMP[: len(bounds) - 1]), vmin=0,
              vmax=len(bounds) - 2, aspect="equal")
    for i in range(len(names)):
        for j in range(len(names)):
            v = int(data[i, j])
            if i == j:
                ax.add_patch(Rectangle((j - .5, i - .5), 1, 1, color="#f0efec"))
                continue
            if v:
                ax.text(j, i, str(v), ha="center", va="center", fontsize=8,
                        color="#ffffff" if idx[i, j] >= 4 else INK)
                if names[j] not in mm.allowed[names[i]]:
                    ax.add_patch(Rectangle((j - .45, i - .45), .9, .9, fill=False,
                                           edgecolor=ORANGE, linewidth=2))
    ax.set_xticks(range(len(names)), labels, rotation=40, ha="right", fontsize=8, color=INK_2)
    ax.set_yticks(range(len(names)), labels, fontsize=8, color=INK_2)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xlabel("…imports from this module (target)", color=INK_2, fontsize=9)
    ax.set_ylabel("This module (source)…", color=INK_2, fontsize=9)
    ax.set_title("Module dependencies: number of file-to-file imports", loc="left",
                 color=INK, fontsize=11, fontweight="bold", pad=10)
    fig.text(0.01, 0.01, f"Runtime + lazy imports, Scrapy {tag}. Orange outline = dependency not "
             "<<allowed to use>> in the intended architecture (config/modules.json).",
             fontsize=7, color=MUTED)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(FIGURES / "2_m1_dependency_matrix.png", dpi=200)
    plt.close(fig)

    # ---- graphviz file of the module graph
    lines = ["digraph scrapy_modules {", '  rankdir=TB; node [shape=box, style=rounded, fontname="Helvetica"];']
    for m in names:
        lines.append(f'  {m} [label="{mm.labels[m]}"];')
    for s in names:
        for d in names:
            n = int(matrix.loc[s, d])
            if n:
                ok = d in mm.allowed[s]
                lines.append(f'  {s} -> {d} [label="{n}"' + ("" if ok else ', color="#eb6834", fontcolor="#eb6834", style=dashed') + "];")
    lines.append("}")
    (FIGURES / "2_m1_module_graph.dot").write_text("\n".join(lines))

    print(json.dumps(summary, indent=2))
    print(metrics.to_string(index=False))


if __name__ == "__main__":
    main()
