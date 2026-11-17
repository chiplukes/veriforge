"""
construct_census.py — compare the constructs real Verilog/SV uses with what the fuzzer generates.

Usage:
    uv run python tools/construct_census.py [--corpus DIR ...] [--fuzz-seeds N] [--jobs N]
                                            [--output DIR]

Parses every ``.v``/``.sv`` source under each corpus directory (default:
``examples/``) and a batch of fuzzer-generated designs (emitted, then
re-parsed, so both sides are counted the same way), then writes:

* ``census.json``  — per-file feature counts, parse failures, fuzzer counts;
* ``census.md``    — a ranked report: constructs real code uses that the
  fuzzer never or rarely generates, parse failures per project, and
  constructs the fuzzer spends effort on that real code rarely uses.

Features counted per parse tree
-------------------------------
* every grammar rule name (``loop_generate_construct``, ``always_ff_construct``...);
* ``rule:TOKEN`` for operator and keyword tokens (``binary_operator:OP_BINARY_ARITH_SHIFTR``);
* a few structural patterns that rule names alone miss: ``@nested_generate``,
  ``@process_in_generate``, ``@instance_in_generate``, ``@multi_range`` (two or
  more packed ranges), ``@param_override`` (``#(...)`` on an instance) and
  ``@dotted_name`` (a struct field or a hierarchical reference; the parse tree
  does not tell them apart).

A project is a corpus directory's first-level subdirectory
(``examples/ibex``). Identical files (same content) are counted once.
Header files (``.vh``/``.svh``) are only seen through ``include``.

See notes/plans/reliability_plan_2026-11.md, step 2a.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from lark import Token, Tree

SOURCE_SUFFIXES = {".v", ".sv"}
HEADER_SUFFIXES = {".vh", ".svh"}
SKIP_DIRS = {"build", "obj_dir", "__pycache__", ".pcache"}

# Token types that are names, digits or literal text, not constructs.
_NAME_TOKEN_SUFFIXES = ("IDENTIFIER", "_BASE")
_NAME_TOKEN_TYPES = {"STRING", "SIMPLE_IDENTIFIER", "ESCAPED_IDENTIFIER", "SYSTEM_TF_IDENTIFIER"}

_GENERATE_RULES = {"loop_generate_construct", "if_generate_construct", "case_generate_construct"}
_PROCESS_RULES = {
    "always_construct",
    "always_comb_construct",
    "always_ff_construct",
    "always_latch_construct",
    "initial_construct",
}

_parser = None


def _token_is_construct(rule: str, tok: Token) -> bool:
    t = tok.type
    if t in _NAME_TOKEN_TYPES or t.endswith(_NAME_TOKEN_SUFFIXES):
        return False
    if t.startswith(("CH", "__ANON", "KW_END")) or t.isdigit():
        return False
    # A keyword the rule is named after adds nothing (generate_region:KW_GENERATE).
    if t.startswith("KW_") and t[3:].lower() in rule.split("_"):
        return False
    return True


# cm:c3n5u7
def tree_features(tree: Tree) -> Counter[str]:
    """Count rule, operator/keyword-token and structural-pattern features in one parse tree."""
    feats: Counter[str] = Counter()
    # Iterative walk carrying the number of enclosing generate constructs.
    stack: list[tuple[Tree, int]] = [(tree, 0)]
    while stack:
        node, gen_depth = stack.pop()
        rule = str(node.data)
        feats[rule] += 1
        ranges = 0
        tokens = 0
        for ch in node.children:
            if isinstance(ch, Token):
                if _token_is_construct(rule, ch):
                    feats[f"{rule}:{ch.type}"] += 1
                tokens += 1
            elif isinstance(ch, Tree):
                if ch.data == "range":
                    ranges += 1
        if ranges >= 2:
            feats["@multi_range"] += 1
        if rule == "hierarchical_identifier":
            idents = sum(1 for ch in node.children if isinstance(ch, Token) and ch.type.endswith("IDENTIFIER"))
            if idents >= 2:
                feats["@dotted_name"] += 1
        elif rule in _GENERATE_RULES and gen_depth >= 1:
            feats["@nested_generate"] += 1
            feats[f"@nested_generate_depth{min(gen_depth + 1, 4)}"] += 1
        elif rule == "module_instantiation":
            if any(isinstance(ch, Tree) and ch.data == "parameter_value_assignment" for ch in node.children):
                feats["@param_override"] += 1
        elif rule in _PROCESS_RULES and gen_depth >= 1:
            feats["@process_in_generate"] += 1
        child_depth = gen_depth + (1 if rule in _GENERATE_RULES else 0)
        if rule == "module_instantiation" and gen_depth >= 1:
            feats["@instance_in_generate"] += 1
        for ch in node.children:
            if isinstance(ch, Tree):
                stack.append((ch, child_depth))
    return feats


def _get_parser():
    global _parser  # noqa: PLW0603
    if _parser is None:
        from veriforge.verilog_parser import VerilogParser

        _parser = VerilogParser(start="verilog")
    return _parser


def _parse_source(path: str, include_paths: list[str]) -> dict:
    """Worker: preprocess and parse one corpus file; return its features or the failure."""
    from veriforge.preprocessor import preprocess

    t0 = time.time()
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        text = preprocess(text, include_paths=include_paths, source_file=path)
        tree = _get_parser().build_tree(text)
    except Exception as exc:  # noqa: BLE001 -- every failure is a census result
        msg = str(exc).strip().splitlines()
        return {
            "path": path,
            "ok": False,
            "error": type(exc).__name__,
            "detail": (msg[0] if msg else "")[:300],
            "seconds": round(time.time() - t0, 2),
        }
    return {
        "path": path,
        "ok": True,
        "lines": text.count("\n") + 1,
        "features": dict(tree_features(tree)),
        "seconds": round(time.time() - t0, 2),
    }


def _fuzz_one(strategy_name: str, seed: int) -> dict:
    """Worker: generate one fuzzer design, emit it, re-parse it, return its features."""
    from veriforge.codegen.verilog_emitter import emit_design
    from veriforge.fuzz._module_gen import ModuleGenerator, Strategy

    strategy = Strategy[strategy_name]
    try:
        design = ModuleGenerator(random.Random(seed)).generate_design(strategy)  # noqa: S311
        text = emit_design(design)
        tree = _get_parser().build_tree(text)
    except Exception as exc:  # noqa: BLE001
        return {"strategy": strategy_name, "seed": seed, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    return {"strategy": strategy_name, "seed": seed, "ok": True, "features": dict(tree_features(tree))}


def discover(corpus_dirs: list[Path]) -> list[tuple[str, str, list[str]]]:
    """Return (project, path, include_paths) for each unique source file under the corpus directories."""
    out: list[tuple[str, str, list[str]]] = []
    seen_hashes: set[str] = set()
    for root in corpus_dirs:
        for project_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS):
            files = [
                p
                for p in sorted(project_dir.rglob("*"))
                if p.is_file() and not (SKIP_DIRS & set(p.relative_to(project_dir).parts))
            ]
            header_dirs = sorted({str(p.parent) for p in files if p.suffix in HEADER_SUFFIXES})
            include_dirs = sorted(
                {d for d in header_dirs} | {str(p) for p in project_dir.rglob("include") if p.is_dir()}
            )
            for p in files:
                if p.suffix not in SOURCE_SUFFIXES:
                    continue
                digest = hashlib.sha1(p.read_bytes()).hexdigest()  # noqa: S324 -- dedup only
                if digest in seen_hashes:
                    continue
                seen_hashes.add(digest)
                out.append((f"{root.name}/{project_dir.name}", str(p), [str(p.parent), *include_dirs]))
    return out


def _grammar_support() -> dict[str, str]:
    from veriforge.lark_file.parse_metadata import GrammarMetadataParser

    meta = GrammarMetadataParser()
    meta.parse()
    return {name: r.support for name, r in meta.rules.items()}


def build_report(corpus: list[dict], projects: dict[str, str], fuzz: list[dict], support: dict[str, str]) -> str:
    ok = [r for r in corpus if r["ok"]]
    failed = [r for r in corpus if not r["ok"]]
    fuzz_ok = [r for r in fuzz if r["ok"]]

    corpus_total: Counter[str] = Counter()
    corpus_files: Counter[str] = Counter()
    corpus_projects: dict[str, set[str]] = defaultdict(set)
    file_sets: dict[str, set[str]] = defaultdict(set)
    for r in ok:
        for f, n in r["features"].items():
            corpus_total[f] += n
            corpus_files[f] += 1
            corpus_projects[f].add(projects[r["path"]])
            file_sets[f].add(r["path"])
    fuzz_total: Counter[str] = Counter()
    fuzz_designs: Counter[str] = Counter()
    fuzz_strats: dict[str, set[str]] = defaultdict(set)
    for r in fuzz_ok:
        for f, n in r["features"].items():
            fuzz_total[f] += n
            fuzz_designs[f] += 1
            fuzz_strats[f].add(r["strategy"])

    n_files = max(len(ok), 1)
    n_fuzz = max(len(fuzz_ok), 1)
    all_projects = sorted(set(projects.values()))

    def sup(f: str) -> str:
        return support.get(f.split(":", maxsplit=1)[0], "") or "-"

    lines: list[str] = []
    lines.append("# Construct census\n")
    lines.append(
        f"Corpus: {len(corpus)} unique source files in {len(all_projects)} projects "
        f"({len(ok)} parsed, {len(failed)} failed, {sum(r['lines'] for r in ok)} preprocessed lines). "
        f"Fuzzer: {len(fuzz)} generated designs ({len(fuzz) - len(fuzz_ok)} failed to emit/re-parse).\n"
    )
    lines.append(
        "Columns: **files** = corpus files using the feature (of "
        f"{len(ok)}), **proj** = projects using it, **fuzz** = fuzzer designs using it (of {len(fuzz_ok)}), "
        "**sup** = grammar `SUPPORT` tag of the rule.\n"
    )

    # 1. Gaps: used by real code, never generated.
    gaps = [f for f in corpus_files if fuzz_designs[f] == 0]
    gaps.sort(key=lambda f: (-len(corpus_projects[f]), -corpus_files[f], f.startswith("@") is False, len(f)))
    # Features used by exactly the same files (rule chains such as
    # parameter_value_assignment -> list_of_parameter_assignments) share a row.
    groups: dict[frozenset[str], list[str]] = {}
    for f in gaps:
        groups.setdefault(frozenset(file_sets[f]), []).append(f)
    lines.append("## 1. Used by real code, never generated by the fuzzer\n")
    lines.append("Ranked by number of projects, then files. Features used by exactly the same files share a row.\n")
    lines.append("| feature | files | proj | total uses | sup | projects |")
    lines.append("|---|---:|---:|---:|---|---|")
    singles = 0
    for members in groups.values():
        f = members[0]
        if corpus_files[f] < 2 and len(corpus_projects[f]) < 2:
            singles += 1
            continue
        projs = ", ".join(sorted(p.split("/")[-1] for p in corpus_projects[f]))
        names = f"`{f}`"
        if len(members) > 1:
            names += " (with " + ", ".join(f"`{m}`" for m in members[1:4]) + (", ..." if len(members) > 4 else "") + ")"
        lines.append(
            f"| {names} | {corpus_files[f]} | {len(corpus_projects[f])} | {corpus_total[f]} | {sup(f)} | {projs} |"
        )
    lines.append(f"\n({singles} more features appear in a single corpus file only; see census.json.)\n")

    # 2. Under-represented: fuzzer frequency far below corpus frequency.
    lines.append("## 2. Generated, but much less often than real code uses them\n")
    lines.append("Share = fraction of files/designs containing the feature. Ratio = fuzz share / corpus share.\n")
    lines.append("| feature | corpus share | fuzz share | ratio | strategies |")
    lines.append("|---|---:|---:|---:|---|")
    under = []
    for f in corpus_files:
        if fuzz_designs[f] == 0 or corpus_files[f] / n_files < 0.05:
            continue
        ratio = (fuzz_designs[f] / n_fuzz) / (corpus_files[f] / n_files)
        if ratio < 0.25:
            under.append((ratio, f))
    for ratio, f in sorted(under):
        strats = ", ".join(sorted(fuzz_strats[f]))
        lines.append(
            f"| `{f}` | {corpus_files[f] / n_files:.0%} | {fuzz_designs[f] / n_fuzz:.1%} | {ratio:.2f} | {strats} |"
        )
    lines.append("")

    # 3. Over-represented: fuzzer generates often, real code rarely uses.
    lines.append("## 3. Generated often, rarely used by real code\n")
    lines.append("| feature | corpus share | fuzz share |")
    lines.append("|---|---:|---:|")
    over = []
    for f in fuzz_designs:
        cs = corpus_files[f] / n_files
        fs = fuzz_designs[f] / n_fuzz
        if fs >= 0.10 and cs <= 0.02:
            over.append((-fs, f, cs))
    for neg_fs, f, cs in sorted(over):
        lines.append(f"| `{f}` | {cs:.1%} | {-neg_fs:.0%} |")
    lines.append("")

    # 4. Parse failures.
    lines.append("## 4. Corpus parse failures\n")
    per_proj: dict[str, list[dict]] = defaultdict(list)
    for r in failed:
        per_proj[projects[r["path"]]].append(r)
    total_per_proj = Counter(projects[r["path"]] for r in corpus)
    lines.append("| project | failed / files |")
    lines.append("|---|---|")
    for p in all_projects:
        lines.append(f"| {p} | {len(per_proj.get(p, []))} / {total_per_proj[p]} |")
    lines.append("")
    by_error: Counter[str] = Counter()
    for r in failed:
        by_error[r["error"]] += 1
    lines.append("By error type: " + ", ".join(f"{e} {n}" for e, n in by_error.most_common()) + "\n")
    lines.append("<details><summary>Failure details</summary>\n")
    for r in sorted(failed, key=lambda r: r["path"]):
        lines.append(f"- `{r['path']}` — {r['error']}: {r['detail']}")
    lines.append("\n</details>\n")

    if len(fuzz_ok) != len(fuzz):
        lines.append("## 5. Fuzzer designs that failed to emit or re-parse\n")
        for r in fuzz:
            if not r["ok"]:
                lines.append(f"- {r['strategy']} seed {r['seed']}: {r['error']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--corpus", type=Path, action="append", help="corpus directory (repeatable; default examples/)")
    ap.add_argument("--fuzz-seeds", type=int, default=40, help="fuzzer designs per strategy (default 40)")
    ap.add_argument("--jobs", type=int, default=8, help="worker processes (default 8)")
    ap.add_argument("--output", type=Path, default=Path("census_out"), help="output directory (default census_out/)")
    ap.add_argument("--max-files", type=int, default=0, help="limit corpus files (0 = all; for quick checks)")
    args = ap.parse_args()

    from veriforge.fuzz._module_gen import Strategy

    corpus_dirs = args.corpus or [Path("examples")]
    sources = discover(corpus_dirs)
    if args.max_files:
        sources = sources[: args.max_files]
    projects = {path: proj for proj, path, _ in sources}
    print(f"[census] {len(sources)} unique corpus files; {args.fuzz_seeds} fuzz seeds x {len(Strategy)} strategies")

    corpus: list[dict] = []
    fuzz: list[dict] = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futs = [pool.submit(_parse_source, path, inc) for _, path, inc in sources]
        futs += [pool.submit(_fuzz_one, s.name, seed) for s in Strategy for seed in range(args.fuzz_seeds)]
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            (fuzz if "strategy" in r else corpus).append(r)
            if i % 50 == 0:
                print(f"[census] {i}/{len(futs)} done ({time.time() - t0:.0f}s)", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    corpus.sort(key=lambda r: r["path"])
    fuzz.sort(key=lambda r: (r["strategy"], r["seed"]))
    (args.output / "census.json").write_text(
        json.dumps({"projects": projects, "corpus": corpus, "fuzz": fuzz}, indent=1), encoding="utf-8"
    )
    report = build_report(corpus, projects, fuzz, _grammar_support())
    (args.output / "census.md").write_text(report, encoding="utf-8")
    print(f"[census] wrote {args.output / 'census.md'} and census.json in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
