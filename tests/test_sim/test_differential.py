"""Randomized differential cross-engine conformance testing (work plan item 3.4).

Generates random expression trees over a fixed 8-signal input set and checks
that every available non-reference engine ("vm", "vm-fast", and "compiled"
when a C compiler + Cython are available) agrees bit-for-bit (value *and*
x/z mask) with the reference engine, both combinationally and through one
register stage.

Determinism: `VERIFORGE_DIFF_SEED` (default 20260701) seeds the generator;
`VERIFORGE_DIFF_CASES` (default 150) sets how many random expressions are
generated. Cases are batched (`_BATCH_SIZE` expressions per module, sharing
one input port list) to keep the number of distinct compiled-engine builds
bounded regardless of case count.

The "compiled" engine only participates when `VERIFORGE_DIFF_COMPILED=1` is
set: the compiled engine's known gaps (see `notes/known_issues.md`) would
otherwise make the default run red, and per-module Cython compilation is too
slow for the default run's "a few seconds" budget. The default run covers
reference/vm/vm-fast only; a heavier compiled-enabled run with a larger case
count is intended for a scheduled CI job once one exists (work plan item
3.2's `weekly.yml`, not yet landed).

On a mismatch the assertion message includes the seed, the batch index, the
failing case's expression, the stimulus vector, and the full generated
Verilog source -- that is the repro. Per the work plan, a discovered
divergence should be reduced to a deterministic case in
`test_compiled_edge_shapes.py` (item 2.2) before fixing, following the same
known_issues/xfail protocol used elsewhere.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass

import pytest

from veriforge.analysis.resolver import link_instances, resolve_port_connections
from veriforge.model.design import Design
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

# =====================================================================
# Configuration
# =====================================================================

_SEED = int(os.environ.get("VERIFORGE_DIFF_SEED", "20260701"))
_NUM_CASES = int(os.environ.get("VERIFORGE_DIFF_CASES", "150"))
_COMPILED_ENABLED = os.environ.get("VERIFORGE_DIFF_COMPILED") == "1"
_BATCH_SIZE = 10
_MAX_DEPTH = 4
_NUM_VECTORS = 8
_NUM_X_VECTORS = 2

# Fixed signal set: 8 inputs, widths cycling over the required set, half
# declared signed (even indices).
_WIDTHS = [1, 8, 16, 63, 64, 65, 80]
FIXED_SIGNALS: list[tuple[str, int, bool]] = [(f"a{i}", _WIDTHS[i % len(_WIDTHS)], i % 2 == 0) for i in range(8)]

_BINARY_OPS = [
    "+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
    "<", "<=", "==", "!=", "&&", "||",
]  # fmt: skip
_UNARY_OPS = ["~", "-", "!"]
_REDUCTION_OPS = ["&", "|", "^", "~&", "~|", "~^"]
_NODE_KINDS = ["binary", "unary", "reduction", "ternary", "concat", "replicate", "cast"]

# =====================================================================
# Random expression generation
# =====================================================================


def _gen_leaf(rng: random.Random) -> str:
    """A reference to one of the fixed signals, optionally bit/part-selected.

    Selects are restricted to a fresh leaf reference (not an arbitrary
    subexpression) so the index/range is always known to be in-range without
    needing to compute the self-determined width of a compound expression.
    """
    name, width, _signed = rng.choice(FIXED_SIGNALS)
    if width == 1:
        return name
    choice = rng.random()
    if choice < 0.5:
        return name
    if choice < 0.75:
        idx = rng.randrange(width)
        return f"{name}[{idx}]"
    hi = rng.randrange(width)
    lo = rng.randrange(hi + 1)
    if hi == lo:
        return f"{name}[{hi}]"
    return f"{name}[{hi}:{lo}]"


def _gen_expr(rng: random.Random, depth: int) -> str:
    if depth <= 0 or rng.random() < 0.35:
        return _gen_leaf(rng)
    kind = rng.choice(_NODE_KINDS)
    if kind == "binary":
        op = rng.choice(_BINARY_OPS)
        lhs = _gen_expr(rng, depth - 1)
        rhs = _gen_expr(rng, depth - 1)
        if op in ("/", "%"):
            # Avoid div-by-zero noise; x-results are still covered via the
            # x-contaminated stimulus vectors, not via generated zero RHS.
            rhs = f"({rhs} | 1)"
        return f"({lhs} {op} {rhs})"
    if kind == "unary":
        op = rng.choice(_UNARY_OPS)
        return f"({op}{_gen_expr(rng, depth - 1)})"
    if kind == "reduction":
        op = rng.choice(_REDUCTION_OPS)
        return f"({op}{_gen_expr(rng, depth - 1)})"
    if kind == "ternary":
        cond = _gen_expr(rng, depth - 1)
        a = _gen_expr(rng, depth - 1)
        b = _gen_expr(rng, depth - 1)
        return f"({cond} ? {a} : {b})"
    if kind == "concat":
        n = rng.choice((2, 3))
        parts = [_gen_expr(rng, depth - 1) for _ in range(n)]
        return "{" + ", ".join(parts) + "}"
    if kind == "replicate":
        n = rng.choice((2, 3))
        return "{" + str(n) + "{" + _gen_expr(rng, depth - 1) + "}}"
    if kind == "cast":
        cast = rng.choice(("signed", "unsigned"))
        return f"${cast}({_gen_expr(rng, depth - 1)})"
    raise AssertionError(kind)


@dataclass(frozen=True)
class _Case:
    idx: int
    expr: str


def _generate_cases(seed: int, n: int) -> list[_Case]:
    rng = random.Random(seed)
    return [_Case(i, _gen_expr(rng, _MAX_DEPTH)) for i in range(n)]


def _batches(cases: list[_Case]) -> list[list[_Case]]:
    return [cases[i : i + _BATCH_SIZE] for i in range(0, len(cases), _BATCH_SIZE)]


_CASES = _generate_cases(_SEED, _NUM_CASES)
_BATCHES = _batches(_CASES)

# =====================================================================
# Verilog module generation
# =====================================================================


def _build_batch_module(cases: list[_Case]) -> str:
    ports = ["input clk"]
    for name, width, signed in FIXED_SIGNALS:
        sig = "signed " if signed else ""
        ports.append(f"input {sig}[{width - 1}:0] {name}")
    for c in cases:
        ports.append(f"output [95:0] y_comb_{c.idx}")
        ports.append(f"output reg [95:0] y_ff_{c.idx}")
    header = ",\n    ".join(ports)
    assigns = "\n".join(f"    assign y_comb_{c.idx} = {c.expr};" for c in cases)
    nba = "\n".join(f"        y_ff_{c.idx} <= {c.expr};" for c in cases)
    return f"module t(\n    {header}\n);\n{assigns}\n    always @(posedge clk) begin\n{nba}\n    end\nendmodule\n"


# =====================================================================
# Stimulus generation
# =====================================================================


def _gen_vectors(seed: str) -> list[dict[str, Value]]:
    rng = random.Random(seed)
    x_indices = set(rng.sample(range(_NUM_VECTORS), _NUM_X_VECTORS))
    vectors: list[dict[str, Value]] = []
    for i in range(_NUM_VECTORS):
        x_signal = rng.choice(FIXED_SIGNALS)[0] if i in x_indices else None
        values: dict[str, Value] = {}
        for name, width, _signed in FIXED_SIGNALS:
            val = rng.getrandbits(width)
            mask = (1 << width) - 1 if name == x_signal else 0
            values[name] = Value(val, width=width, mask=mask)
        vectors.append(values)
    return vectors


# =====================================================================
# Simulation plumbing
# =====================================================================


def _parse_design(source: str) -> Design:
    vp = verilog_parser(start="source_text")
    tree = vp.build_tree(source)
    return tree_to_design(tree, source_file="test.v")


def _sim_for(source: str, engine: str) -> Simulator:
    design = _parse_design(source)
    link_instances(design)
    resolve_port_connections(design)
    top = next(m for m in design.modules if m.name == "t")
    return Simulator(top, engine=engine, design=design)


def _run_batch(
    source: str, engine: str, cases: list[_Case], vectors: list[dict[str, Value]]
) -> list[tuple[dict[int, Value], dict[int, Value]]]:
    sim = _sim_for(source, engine)
    sim.drive("clk", Value(0, width=1))
    for name, width, _signed in FIXED_SIGNALS:
        sim.drive(name, Value(0, width=width))
    sim.settle()

    results: list[tuple[dict[int, Value], dict[int, Value]]] = []
    for vec in vectors:
        for name, value in vec.items():
            sim.drive(name, value)
        sim.settle()
        comb = {c.idx: sim.read(f"y_comb_{c.idx}") for c in cases}
        sim.drive("clk", Value(1, width=1))
        sim.settle()
        sim.drive("clk", Value(0, width=1))
        sim.settle()
        ff = {c.idx: sim.read(f"y_ff_{c.idx}") for c in cases}
        results.append((comb, ff))
    return results


# =====================================================================
# Test
# =====================================================================


@pytest.mark.cross_engine
@pytest.mark.parametrize("batch_idx", range(len(_BATCHES)))
def test_differential(batch_idx: int) -> None:
    cases = _BATCHES[batch_idx]
    source = _build_batch_module(cases)
    vectors = _gen_vectors(f"{_SEED}:vectors:{batch_idx}")

    oracle = _run_batch(source, "reference", cases, vectors)
    engines = [e for e in ENGINES if e != "reference" and (e != "compiled" or _COMPILED_ENABLED)]
    for engine in engines:
        got = _run_batch(source, engine, cases, vectors)
        for vec_idx, ((exp_comb, exp_ff), (got_comb, got_ff)) in enumerate(zip(oracle, got, strict=True)):
            for c in cases:
                exp_c, got_c = exp_comb[c.idx], got_comb[c.idx]
                assert exp_c == got_c, (
                    f"differential mismatch: engine={engine} seed={_SEED} batch={batch_idx} "
                    f"case={c.idx} vector={vec_idx} signal=y_comb_{c.idx}\n"
                    f"expr: {c.expr}\n"
                    f"vector: {vectors[vec_idx]}\n"
                    f"expected(reference)={exp_c!r} got({engine})={got_c!r}\n"
                    f"module source:\n{source}"
                )
                exp_f, got_f = exp_ff[c.idx], got_ff[c.idx]
                assert exp_f == got_f, (
                    f"differential mismatch: engine={engine} seed={_SEED} batch={batch_idx} "
                    f"case={c.idx} vector={vec_idx} signal=y_ff_{c.idx}\n"
                    f"expr: {c.expr}\n"
                    f"vector: {vectors[vec_idx]}\n"
                    f"expected(reference)={exp_f!r} got({engine})={got_f!r}\n"
                    f"module source:\n{source}"
                )
