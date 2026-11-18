"""
corpus_check.py — run every module of a Verilog corpus on every engine and compare.

Usage:
    uv run python tools/corpus_check.py [--corpus DIR ...] [--project NAME ...] [--engines E ...]
                                        [--cycles N] [--seeds N] [--param-variants N]
                                        [--jobs N] [--timeout S] [--output DIR]

For every project (a corpus directory's first-level subdirectory, as in
``tools/construct_census.py``):

1. parse each source once (preprocessed, with the project's include
   directories) into one design -- the first definition of a module wins;
2. for every module with ports, in its own subprocess (``--timeout``):
   elaborate it as the top, drive random stimulus (clocks and resets
   detected by name) on every engine with ``veriforge.sim.diverge``, and
   compare every signal against the reference engine;
3. for a mismatch, locate the first divergence (the driver where the runs
   part) and print the ``diverge``/``reduce`` command lines that reproduce it.

``--param-variants N`` also runs N random overrides of each module's small
integer parameters (1..64), since parameterization is where many
elaboration bugs live.

Outcomes per module and variant: ``agree``, ``mismatch`` (an engine differs
from reference), ``engine-error`` (an engine raised where reference did
not), ``error`` (reference itself failed -- usually unsupported syntax or
semantics), ``timeout``, ``memory`` (over ``--mem-limit-gb``), ``skipped``
(no ports).

Writes ``results.json`` (every outcome) and ``report.md`` to ``--output``
(default ``corpus_out/``). See notes/plans/reliability_plan_2026-11.md, step 2c.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import random
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from construct_census import discover  # noqa: E402

_RESET_RE = re.compile(
    r"(^|_)(rst|reset|areset|srst|arst)(_?n|_ni|_b|_l)?(_i|_in)?$|^(rstn|resetn|aresetn|rst_ni)$", re.I
)
_ACTIVE_LOW_RE = re.compile(r"(n|_n|_ni|_b|_l|_n_i|n_i)$", re.I)


# ── Stimulus detection ───────────────────────────────────────────────


def detect_resets(inputs: list[str], *, cycles: int = 3) -> dict[str, tuple[int, int]]:
    """Reset inputs by name, active-low when the name ends in n/_n/_ni/_b/_l."""
    out: dict[str, tuple[int, int]] = {}
    for name in inputs:
        base = name.lower()
        if not _RESET_RE.search(base):
            continue
        stem = re.sub(r"(_i|_in)$", "", base)
        low = bool(_ACTIVE_LOW_RE.search(stem)) and not stem.endswith(("reset", "rst"))
        out[name] = (0 if low else 1, cycles)
    return out


def param_variants(module, n: int, rng: random.Random) -> list[dict[str, int]]:
    """Up to *n* overrides of *module*'s small integer parameters (literal defaults 1..64)."""
    from veriforge.model.expressions import Literal

    candidates: dict[str, int] = {}
    for p in module.parameters:
        if p.is_local or not isinstance(p.default_value, Literal):
            continue
        try:
            v = int(p.default_value.value)
        except (TypeError, ValueError):
            continue
        if 1 <= v <= 64:
            candidates[p.name] = v
    if not candidates:
        return []
    variants: list[dict[str, int]] = []
    seen: set[tuple] = set()
    for _ in range(n * 4):
        if len(variants) >= n:
            break
        name = rng.choice(sorted(candidates))
        v = candidates[name]
        new = rng.choice([x for x in {1, 2, 3, v + 1, max(1, v - 1), min(64, 2 * v), 65 if v > 32 else 33} if x != v])
        key = (name, new)
        if key not in seen:
            seen.add(key)
            variants.append({name: new})
    return variants


_SCALAR_TYPES = {"logic", "bit", "reg", "byte", "shortint", "int", "longint", "integer", "wire"}


def _struct_type_params(module, design, memo: dict) -> dict[str, set[str]]:
    """``{type parameter: {why...}}`` for *module*'s type parameters that default to a
    built-in scalar and are used as a struct: a signal of that type with a field
    access, or passed (`.req_t(req_t)`) to a child's such parameter."""
    from veriforge.model.expressions import BitSelect, Identifier, RangeSelect

    if id(module) in memo:
        return memo[id(module)]
    memo[id(module)] = {}  # (cycle guard)

    def scalar_default(expr) -> bool:
        while isinstance(expr, (RangeSelect, BitSelect)):
            expr = expr.target
        return isinstance(expr, Identifier) and not expr.hierarchy and expr.name in _SCALAR_TYPES

    tparams = {p.name for p in module.parameters if scalar_default(p.default_value)}
    found: dict[str, set[str]] = {}
    if tparams:
        sig_type = {p.name: p.data_type for p in module.ports if getattr(p, "data_type", None) in tparams}
        sig_type |= {v.name: v.type_name for v in module.variables if getattr(v, "type_name", None) in tparams}
        for node in module.walk():
            if not isinstance(node, Identifier):
                continue
            dotted = ".".join([*(node.hierarchy or ()), node.name])
            if "." in dotted:
                base = dotted.split(".", 1)[0].split("[", 1)[0]
                if base in sig_type:
                    found.setdefault(sig_type[base], set()).add(base)
        if design is not None:
            for inst in module.instances:
                child = design.get_module(inst.module_name)
                if child is None:
                    continue
                child_found = _struct_type_params(child, design, memo)
                for binding in inst.parameter_bindings or ():
                    value = binding.value
                    if (
                        binding.name in child_found
                        and isinstance(value, Identifier)
                        and not value.hierarchy
                        and value.name in tparams
                    ):
                        found.setdefault(value.name, set()).add(f"{inst.instance_name}.{binding.name}")
    memo[id(module)] = found
    return found


def scalar_type_params_used_as_structs(module, design=None) -> list[str]:
    """Where *module* uses a type parameter that defaults to a built-in scalar
    (``parameter type resp_t = logic``) as a struct: its signals with a field
    access (``resp_o.aw_ready``), or ``instance.param`` for one passed down to a
    child that does (pulp's ``axi_to_axi_lite`` -> ``..._id_reflect``).

    With its defaults such a module is not a valid design -- a field of a
    1-bit `logic` -- and only its parents (which pass real struct types)
    can be checked: pulp's axi modules, where compiled refused with "cannot
    resolve LHS identifier" and reference silently accepted it.
    """
    return sorted(set().union(*_struct_type_params(module, design, {}).values()))


def zero_width_ports_by_default(module) -> list[str]:
    """Ports whose range is empty with the default parameters (`[N-1:0]`,
    N = 0): the module needs a parent's override to be a design at all
    (serv_rf_ram's `width = 0`, which then divides by zero; pulp's
    `stream_join` with `N_INP = 0`, which replicates `{0{1'b1}}`)."""
    from veriforge.sim.elaborate import _build_param_env, _eval_const_expr

    try:
        env = _build_param_env(module)
    except Exception:  # noqa: BLE001 -- an environment we can't build: leave it to the check
        return []
    empty = []
    for port in module.ports:
        rng = getattr(port, "width", None)
        if rng is None:
            continue
        try:
            msb, lsb = _eval_const_expr(rng.msb, env), _eval_const_expr(rng.lsb, env)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(msb, int) and isinstance(lsb, int) and msb == lsb - 1:
            empty.append(port.name)
    return empty


def apply_params(design, top: str, overrides: dict[str, int]) -> None:
    from veriforge.model.expressions import Literal

    module = design.get_module(top)
    for p in module.parameters:
        if p.name in overrides:
            p.default_value = Literal(overrides[p.name], width=32, base="d", original_text=str(overrides[p.name]))


# ── One module (subprocess) ──────────────────────────────────────────


def _normalize_error(msg: str) -> str:
    """Bucket key for an error message: numbers and quoted names replaced."""
    msg = msg.splitlines()[0] if msg else ""
    msg = re.sub(r"'[^']*'", "'…'", msg)
    msg = re.sub(r"\b\d+\b", "N", msg)
    return msg[:160]


def _peak_rss_mb() -> tuple[float | None, float | None]:
    """Peak resident memory of this process and of its waited-for children
    (compiled's Cython and C-compiler processes), MB. None where unavailable."""
    try:
        import resource
    except ImportError:  # (Windows)
        return None, None
    scale = 1 / (1 << 20) if sys.platform == "darwin" else 1 / 1024  # (bytes on macOS, KB elsewhere)
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
    kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * scale
    return round(own, 1), round(kids, 1)


def _compiled_generated_kb() -> float | None:
    """Size of the generated .pyx/.c/.h of the compiled extension this process loaded, KB."""
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None) or ""
        if Path(f).name.startswith("vtc_") and Path(f).suffix in (".so", ".pyd"):
            d = Path(f).parent
            total = sum(p.stat().st_size for p in d.iterdir() if p.suffix in (".pyx", ".c", ".h", ".pxd"))
            return round(total / 1024, 1)
    return None


def _load_design(job: dict):
    with open(job["pickle"], "rb") as f:
        design = pickle.load(f)  # noqa: S301 -- written by this tool's parent process
    if job.get("params"):
        apply_params(design, job["module"], job["params"])
    return design


# cm:b7e2d4
def run_engine(job: dict) -> dict:
    """One engine on one module (one subprocess): record its trace to
    ``job["trace_out"]`` and report time and memory."""
    from veriforge.sim.diverge import Stimulus, record_trace

    design = _load_design(job)
    stim = Stimulus(cycles=job["cycles"], seed=job["seed"], resets={k: tuple(v) for k, v in job["resets"].items()})
    t0 = time.time()
    try:
        tr = record_trace(design, job["module"], job["engine"], stim)
        error, setup, run = tr.error, tr.setup_seconds, tr.run_seconds
    except Exception as exc:  # noqa: BLE001 -- the failure is the result
        tr, error, setup, run = None, f"{type(exc).__name__}: {exc}", time.time() - t0, 0.0
    if tr is not None:
        with open(job["trace_out"], "wb") as f:
            pickle.dump(tr, f, protocol=pickle.HIGHEST_PROTOCOL)
    own, kids = _peak_rss_mb()
    status = "ok" if error is None else "memory" if error.startswith("MemoryError") else "error"
    return {
        "status": status,
        "error": error[:2000] if error else None,
        "setup_s": round(setup, 2),
        "run_s": round(run, 2),
        "rss_mb": own,
        "child_rss_mb": kids,
        "gen_kb": _compiled_generated_kb() if job["engine"] == "compiled" else None,
    }


def compare(job: dict) -> dict:
    """Compare every engine's trace with the first's; locate each divergence."""
    from veriforge.sim.diverge import first_divergences, locate_divergence

    traces = {}
    for engine, path in job["traces"].items():
        with open(path, "rb") as f:
            traces[engine] = pickle.load(f)  # noqa: S301
    engines = list(job["traces"])
    ref = traces[engines[0]]
    mismatches = {}
    for engine in engines[1:]:
        diffs = first_divergences(ref, traces[engine])
        if not diffs:
            continue
        try:
            report = locate_divergence(ref, traces[engine], _load_design(job), job["module"]).format()
        except Exception as exc:  # noqa: BLE001
            report = f"(locator failed: {type(exc).__name__}: {exc})"
        first = min(diffs.values(), key=lambda d: d.time)
        mismatches[engine] = {"signals": len(diffs), "first_time": first.time, "report": report[:6000]}
    return {"mismatches": mismatches}


def group_outcome(engines: list[str], runs: dict[str, dict], mismatches: dict) -> tuple[str, dict, str | None]:
    """(outcome, engine_errors, error) of one module check from its engine runs.

    ``engine-resource``: an engine timed out or went over the memory limit
    where the reference engine did not -- the memory/scale problems this
    harness exists to catch early.
    """
    ref = runs[engines[0]]
    if ref["status"] != "ok":
        return ref["status"], {}, ref.get("error")
    errors = {e: r.get("error") or r["status"] for e, r in runs.items() if e != engines[0] and r["status"] != "ok"}
    if mismatches:
        return "mismatch", errors, None
    if any(runs[e]["status"] in ("timeout", "memory") for e in errors):
        return "engine-resource", errors, None
    if errors:
        return "engine-error", errors, None
    return "agree", errors, None


# ── Driver ───────────────────────────────────────────────────────────


def load_project(files: list[str], include_dirs: list[str]):
    """Parse *files* one by one into one design; return (design, parse failures)."""
    from veriforge.model.design import Design
    from veriforge.project import parse_file
    from veriforge.verilog_parser import VerilogParser

    parser = VerilogParser(start="verilog")
    design = Design()
    failures: dict[str, str] = {}
    for f in files:
        try:
            part = parse_file(f, preprocess=True, include_paths=include_dirs, _parser=parser)
        except Exception as exc:  # noqa: BLE001
            failures[f] = f"{type(exc).__name__}: {str(exc).strip().splitlines()[0] if str(exc).strip() else ''}"[:300]
            continue
        design.merge(part)
    from veriforge.analysis.resolver import link_instances

    link_instances(design)
    return design, failures


def _memory_limiter(limit_gb: float):
    """preexec_fn capping a job's address space (POSIX only; None elsewhere).

    A runaway check (an enormous value built from invalid default
    parameters: `serv_rf_ram`) once grew one process to 29 GB, and the OOM
    killer took the whole WSL VM with it. Capped, it fails with MemoryError
    and is reported. The cap is inherited by the compiled engine's
    C-compiler processes.
    """
    if not limit_gb or sys.platform == "win32":
        return None
    import resource

    limit = int(limit_gb * (1 << 30))

    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return apply


def _run_job(job: dict, timeout: float, mem_limit_gb: float = 0.0) -> dict:
    """Run *job* (``--one``) in a subprocess; return its JSON result, or a
    ``{"status": "timeout" | "memory" | "error"}`` record."""
    cmd = [sys.executable, __file__, "--one", json.dumps(job)]
    t0 = time.time()
    try:
        res = subprocess.run(  # noqa: PLW1509 -- preexec_fn only sets an rlimit
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            preexec_fn=_memory_limiter(mem_limit_gb),
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": f"over {timeout:g} s", "seconds": round(time.time() - t0, 1)}
    lines = [ln for ln in res.stdout.splitlines() if ln.startswith("{")]
    if res.returncode != 0 or not lines:
        tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
        text = " | ".join(tail)
        if "MemoryError" in text or "Cannot allocate memory" in text or "out of memory" in text.lower():
            return {"status": "memory", "error": f"over the {mem_limit_gb:g} GB limit: {text}"[:1500]}
        return {"status": "error", "error": "harness crashed: " + text[:1500]}
    out = json.loads(lines[-1])
    out.setdefault("status", "ok")
    out["seconds"] = round(time.time() - t0, 1)
    return out


def _repro_lines(r: dict, files: list[str], engine: str, cycles: int) -> list[str]:
    reset_args = " ".join(f"--reset {k}={v[0]}:{v[1]}" for k, v in r.get("resets", {}).items())
    file_args = " ".join(files) if len(files) <= 6 else f"<{len(files)} project files>"
    common = f"{file_args} --preprocess --top {r['module']} --cycles {cycles} --seed {r['seed']} {reset_args}".strip()
    note = f"  (parameters {r['params']} -- apply to the source first)" if r.get("params") else ""
    return [
        f"uv run -m veriforge.sim.diverge {common} --engines reference,{engine}{note}",
        f"uv run -m veriforge.reduce {common} --mismatch --engines reference,{engine} -o min.v",
    ]


def _resource_report(results: list[dict], args) -> list[str]:
    """Time and memory per engine: summary, the heaviest runs, compiled's generated code."""
    runs = [
        (r, e, run)
        for r in results
        if r["outcome"] != "skipped"
        for e, run in r.get("engines", {}).items()
        if e in args.engines
    ]
    if not runs:
        return []

    def peak(run: dict) -> float:
        return max(run.get("rss_mb") or 0.0, run.get("child_rss_mb") or 0.0)

    def med(xs: list[float]) -> float:
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else 0.0

    lines = ["## Time and memory\n"]
    lines.append(
        "Peak = the larger of the engine process's own peak resident memory and its build children's "
        "(compiled: Cython, C compiler). Setup = elaboration (+ compiled's build); run = the stimulus.\n"
    )
    lines.append(
        "| engine | runs | ok | timeout | memory | median / max setup s | median / max run s | median / max peak MB |"
    )
    lines.append("|---|---:|---:|---:|---:|---|---|---|")
    for e in args.engines:
        er = [run for _r, en, run in runs if en == e]
        st = Counter(run["status"] for run in er)
        ok = [run for run in er if run["status"] == "ok"]
        su = [run.get("setup_s", 0.0) for run in ok]
        ru = [run.get("run_s", 0.0) for run in ok]
        pk = [peak(run) for run in er if run.get("rss_mb") is not None]
        lines.append(
            f"| {e} | {len(er)} | {st['ok']} | {st['timeout']} | {st['memory']} | "
            f"{med(su):.1f} / {max(su, default=0):.1f} | {med(ru):.1f} / {max(ru, default=0):.1f} | "
            f"{med(pk):.0f} / {max(pk, default=0):.0f} |"
        )
    lines.append("")

    ref_peak = {
        (id(r)): peak(r["engines"][args.engines[0]]) for r in results if args.engines[0] in r.get("engines", {})
    }
    lines.append("### Heaviest runs by peak memory\n")
    lines.append("| module | engine | status | peak MB | own / build MB | x reference | setup s | generated KB |")
    lines.append("|---|---|---|---:|---|---:|---:|---:|")
    heavy = sorted((x for x in runs if x[2].get("rss_mb") is not None), key=lambda x: -peak(x[2]))[:20]
    for r, e, run in heavy:
        ref = ref_peak.get(id(r)) or 0.0
        ratio = f"{peak(run) / ref:.1f}" if ref else "-"
        gen = run.get("gen_kb")
        lines.append(
            f"| {r['project'].split('/')[-1]}:{r['module']} | {e} | {run['status']} | {peak(run):.0f} | "
            f"{run.get('rss_mb') or 0:.0f} / {run.get('child_rss_mb') or 0:.0f} | {ratio} | "
            f"{run.get('setup_s', 0):.1f} | {gen if gen is not None else '-'} |"
        )
    lines.append("")
    gens = sorted(
        ((r, run) for r, e, run in runs if e == "compiled" and run.get("gen_kb")), key=lambda x: -x[1]["gen_kb"]
    )
    if gens:
        lines.append("### Largest compiled generated code\n")
        lines.append("| module | generated KB | build MB | setup s |")
        lines.append("|---|---:|---:|---:|")
        for r, run in gens[:15]:
            lines.append(
                f"| {r['project'].split('/')[-1]}:{r['module']} | {run['gen_kb']:.0f} | "
                f"{run.get('child_rss_mb') or 0:.0f} | {run.get('setup_s', 0):.1f} |"
            )
        lines.append("")
    return lines


def build_report(results: list[dict], parse_failures: dict[str, dict[str, str]], project_files: dict, args) -> str:  # noqa: PLR0912, PLR0915
    lines = ["# Corpus check\n"]
    lines.append(
        f"Engines: {', '.join(args.engines)} (compared with {args.engines[0]}). "
        f"{args.cycles} cycles, {args.seeds} seed(s), {args.param_variants} parameter variant(s) per module.\n"
    )
    by_proj: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        by_proj[r["project"]][r["outcome"]] += 1
    outcomes = ["agree", "mismatch", "engine-error", "engine-resource", "error", "timeout", "memory", "skipped"]
    lines.append("| project | parse failures | " + " | ".join(outcomes) + " |")
    lines.append("|---|---:|" + "---:|" * len(outcomes))
    for proj in sorted(set(by_proj) | set(parse_failures)):
        c = by_proj[proj]
        lines.append(
            f"| {proj} | {len(parse_failures.get(proj, {}))} | " + " | ".join(str(c[o]) for o in outcomes) + " |"
        )
    total = Counter(r["outcome"] for r in results)
    lines.append(
        f"| **total** | {sum(len(v) for v in parse_failures.values())} | "
        + " | ".join(f"**{total[o]}**" for o in outcomes)
        + " |\n"
    )

    mism = [r for r in results if r["outcome"] == "mismatch"]
    lines.append(f"## Mismatches ({len(mism)})\n")
    # Group by root driver text so one bug hit by many modules is one entry.
    groups: dict[str, list[tuple[dict, str]]] = defaultdict(list)
    for r in mism:
        for engine, m in r["mismatches"].items():
            root = re.search(r"^Driver: .*\n((?:    .*\n?)*)", m["report"], re.M)
            key = f"{engine}: " + (root.group(1).strip() if root else m["report"].splitlines()[-1])
            groups[key].append((r, engine))
    for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        r, engine = items[0]
        where = ", ".join(sorted({f"{x['project'].split('/')[-1]}:{x['module']}" for x, _ in items})[:8])
        lines.append(f"### {key.splitlines()[0][:150]}\n")
        lines.append(f"{len(items)} run(s): {where}\n")
        lines.append("```text")
        lines.append(r["mismatches"][engine]["report"])
        lines.append("```")
        lines.append("Reproduce:\n")
        lines.append("```bash")
        lines.extend(_repro_lines(r, project_files[r["project"]], engine, args.cycles))
        lines.append("```\n")

    for title, outcome, field in (
        ("Engine errors (reference ran)", "engine-error", "engine_errors"),
        ("Reference errors (unsupported or broken)", "error", "error"),
    ):
        rs = [r for r in results if r["outcome"] == outcome]
        lines.append(f"## {title} ({len(rs)})\n")
        buckets: dict[str, list[dict]] = defaultdict(list)
        for r in rs:
            if field == "engine_errors":
                for engine, msg in r[field].items():
                    buckets[f"[{engine}] {_normalize_error(msg)}"].append(r)
            else:
                buckets[_normalize_error(r.get(field, ""))].append(r)
        lines.append("| count | error | examples |")
        lines.append("|---:|---|---|")
        for key, rs2 in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            ex = ", ".join(sorted({f"{x['project'].split('/')[-1]}:{x['module']}" for x in rs2})[:5])
            lines.append(f"| {len(rs2)} | `{key.replace('|', '/')}` | {ex} |")
        lines.append("")

    def where(r: dict) -> str:
        return f"{r['project'].split('/')[-1]}:{r['module']}" + (f" {r['params']}" if r.get("params") else "")

    # (Whatever the outcome: a module can also mismatch on another engine,
    # and that must not hide an engine running out of time or memory.)
    def over(r: dict) -> list[str]:
        ref = r.get("engines", {}).get(args.engines[0], {})
        if ref.get("status") in ("timeout", "memory"):
            return []
        return [
            f"{e} {run['status']}"
            for e, run in r.get("engines", {}).items()
            if e != args.engines[0] and run.get("status") in ("timeout", "memory")
        ]

    res = [r for r in results if over(r)]
    lines.append(f"## Engine over time or memory where reference was not ({len(res)})\n")
    for r in sorted(res, key=where):
        lines.append(f"- {where(r)}: {', '.join(over(r))} (outcome {r['outcome']})")
    lines.append("")
    for outcome, title in (("timeout", f"{args.timeout:.0f} s"), ("memory", f"{args.mem_limit_gb:g} GB")):
        rs = [r for r in results if r["outcome"] == outcome]
        if rs:
            lines.append(f"## Reference over the limit: {outcome} ({len(rs)}, {title} each)\n")
            lines.append(", ".join(sorted(where(r) for r in rs)) + "\n")
    invalid = [r for r in results if r["outcome"] == "skipped" and r.get("reason", "no ports") != "no ports"]
    if invalid:
        lines.append(f"## Skipped: not a valid design with its default parameters ({len(invalid)})\n")
        lines.append("Checked only within their parents.\n")
        lines.extend(f"- {where(r)}: {r['reason']}" for r in sorted(invalid, key=where))
        lines.append("")
    lines.extend(_resource_report(results, args))
    if any(parse_failures.values()):
        lines.append("## Parse failures\n")
        for _proj, fails in sorted(parse_failures.items()):
            for f, msg in sorted(fails.items()):
                lines.append(f"- `{f}` — {msg}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:  # noqa: PLR0912, PLR0915
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--corpus", type=Path, action="append", help="corpus directory (repeatable; default examples/)")
    ap.add_argument("--project", action="append", help="only these projects (name or corpus/name; repeatable)")
    ap.add_argument("--module", action="append", help="only these modules (repeatable)")
    ap.add_argument(
        "--engines",
        nargs="+",
        default=["reference", "vm", "vm-fast", "compiled"],
        help="engines; the first is the oracle",
    )
    ap.add_argument("--cycles", type=int, default=40, help="cycles of random stimulus (default 40)")
    ap.add_argument("--seeds", type=int, default=1, help="stimulus seeds per module (default 1)")
    ap.add_argument("--param-variants", type=int, default=0, help="random parameter overrides per module (default 0)")
    ap.add_argument("--jobs", type=int, default=4, help="parallel module checks (default 4)")
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per engine run (default 300)")
    ap.add_argument(
        "--mem-limit-gb",
        type=float,
        default=6.0,
        help="address-space cap per engine run, GB (POSIX; 0 = none; default 6)",
    )
    ap.add_argument("--output", type=Path, default=Path("corpus_out"), help="output directory (default corpus_out/)")
    ap.add_argument(
        "--compile-cache",
        type=Path,
        help="compiled-engine build cache for this run (VERIFORGE_COMPILE_CACHE); a fresh one measures every build",
    )
    ap.add_argument("--one", help=argparse.SUPPRESS)  # internal: run one job (JSON) and print its result
    args = ap.parse_args()

    if args.one:
        job = json.loads(args.one)
        res = compare(job) if job["kind"] == "compare" else run_engine(job)
        print(json.dumps(res))
        return 0

    if args.compile_cache:
        os.environ["VERIFORGE_COMPILE_CACHE"] = str(args.compile_cache.resolve())  # (inherited by every job)
    corpus_dirs = args.corpus or [Path("examples")]
    sources = discover(corpus_dirs)
    by_project: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for proj, path, inc in sources:
        if args.project and proj not in args.project and proj.split("/")[-1] not in args.project:
            continue
        by_project[proj].append((path, inc))

    args.output.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="vf_corpus_"))
    jobs: list[dict] = []
    parse_failures: dict[str, dict[str, str]] = {}
    project_files: dict[str, list[str]] = {}
    skipped: list[dict] = []
    t0 = time.time()
    for proj, entries in sorted(by_project.items()):
        files = [p for p, _ in entries]
        include_dirs = sorted({d for _, inc in entries for d in inc})
        project_files[proj] = files
        design, failures = load_project(files, include_dirs)
        parse_failures[proj] = failures
        pkl = tmp / (re.sub(r"\W", "_", proj) + ".pkl")
        with open(pkl, "wb") as f:
            pickle.dump(design, f, protocol=pickle.HIGHEST_PROTOCOL)
        rng = random.Random(f"{proj}")  # noqa: S311
        n_mod = 0
        for module in design.modules:
            if args.module and module.name not in args.module:
                continue
            reason = None
            if not module.ports:
                reason = "no ports"
            elif structs := scalar_type_params_used_as_structs(module, design):
                reason = f"struct fields of type-parameter signals that default to a scalar ({', '.join(structs[:4])})"
            elif empty := zero_width_ports_by_default(module):
                reason = f"zero-width ports with the default parameters ({', '.join(empty[:4])})"
            if reason:
                skipped.append(
                    {
                        "project": proj,
                        "module": module.name,
                        "seed": 0,
                        "params": None,
                        "outcome": "skipped",
                        "reason": reason,
                    }
                )
                continue
            n_mod += 1
            resets = {k: list(v) for k, v in detect_resets([p.name for p in module.input_ports()]).items()}
            variants: list[dict | None] = [None, *param_variants(module, args.param_variants, rng)]
            for params in variants:
                for seed in range(args.seeds):
                    jobs.append(
                        {
                            "project": proj,
                            "module": module.name,
                            "seed": seed,
                            "params": params,
                            "resets": resets,
                            "pickle": str(pkl),
                            "cycles": args.cycles,
                        }
                    )
        print(
            f"[corpus] {proj}: {len(files)} files ({len(failures)} parse failures), {n_mod} modules "
            f"({time.time() - t0:.0f}s)",
            flush=True,
        )

    # Phase 1: every engine on every module, each in its own capped
    # subprocess (per-engine time and memory; one engine's crash or
    # runaway no longer hides the others'). Phase 2: compare the traces.
    engine_jobs = []
    for gi, job in enumerate(jobs):
        for engine in args.engines:
            engine_jobs.append(
                (gi, engine, {**job, "kind": "engine", "engine": engine, "trace_out": str(tmp / f"t{gi}_{engine}.pkl")})
            )
    print(f"[corpus] {len(jobs)} checks x {len(args.engines)} engines on {args.jobs} workers", flush=True)
    runs: dict[int, dict[str, dict]] = defaultdict(dict)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {
            pool.submit(_run_job, ej, args.timeout, args.mem_limit_gb): (gi, engine) for gi, engine, ej in engine_jobs
        }
        for i, fut in enumerate(as_completed(futs), 1):
            gi, engine = futs[fut]
            r = fut.result()
            runs[gi][engine] = r
            job = jobs[gi]
            if r["status"] != "ok" or i % 50 == 0:
                print(
                    f"[corpus] {i}/{len(engine_jobs)} {engine:<9} {r['status']:<8} "
                    f"{job['project'].split('/')[-1]}:{job['module']}"
                    + (f" {job['params']}" if job.get("params") else "")
                    + f" ({time.time() - t0:.0f}s)",
                    flush=True,
                )

    compare_jobs = {}
    for gi, job in enumerate(jobs):
        ok = [e for e in args.engines if runs[gi][e]["status"] == "ok"]
        if args.engines[0] in ok and len(ok) > 1:
            compare_jobs[gi] = {**job, "kind": "compare", "traces": {e: str(tmp / f"t{gi}_{e}.pkl") for e in ok}}
    print(f"[corpus] comparing {len(compare_jobs)} checks", flush=True)
    compared: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs2 = {pool.submit(_run_job, cj, args.timeout, args.mem_limit_gb): gi for gi, cj in compare_jobs.items()}
        for fut in as_completed(futs2):
            compared[futs2[fut]] = fut.result()

    results: list[dict] = list(skipped)
    for gi, job in enumerate(jobs):
        cmp = compared.get(gi, {})
        if cmp and cmp.get("status") != "ok":
            # (The comparison itself failed: keep the runs, report it.)
            runs[gi]["(compare)"] = cmp
        mismatches = cmp.get("mismatches", {}) if cmp.get("status") == "ok" else {}
        outcome, engine_errors, error = group_outcome(args.engines, runs[gi], mismatches)
        rec = {k: v for k, v in job.items() if k not in ("pickle", "kind")}
        rec.update(engines=runs[gi], outcome=outcome, mismatches=mismatches, engine_errors=engine_errors)
        if error:
            rec["error"] = error
        results.append(rec)
        if outcome not in ("agree", "skipped"):
            print(f"[corpus] {outcome:<15} {job['project'].split('/')[-1]}:{job['module']}", flush=True)
    results.sort(key=lambda r: (r["project"], r["module"], json.dumps(r.get("params")), r["seed"]))
    (args.output / "results.json").write_text(
        json.dumps({"parse_failures": parse_failures, "results": results}, indent=1), encoding="utf-8"
    )
    (args.output / "report.md").write_text(build_report(results, parse_failures, project_files, args), encoding="utf-8")
    counts = Counter(r["outcome"] for r in results)
    print(f"[corpus] {dict(counts)} -> {args.output / 'report.md'} ({time.time() - t0:.0f}s)")
    return 1 if counts["mismatch"] or counts["engine-error"] or counts["engine-resource"] else 0


if __name__ == "__main__":
    sys.exit(main())
