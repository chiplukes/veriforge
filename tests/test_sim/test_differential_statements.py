"""Statement-level differential fuzzing (work plan item 3.4).

`test_differential.py` randomizes EXPRESSION trees only (assigned straight
to a wire/reg); no statement form (`if`/`case`/`for`/`while`) has ever been
differentially fuzzed. That gap is real, not theoretical: the condition-
truthiness fix applied to every `IfStatement`/`ForLoop`/`WhileLoop`/
`WaitStatement` check in `sim/executor.py` (the "known-1-bit-forces-true"
precision rule) was applied by pattern-matching against the differentially-
verified `TernaryOp` fix, NOT verified by an actual differential/Icarus
check against real `if`/`while` statements -- see `notes/known_issues.md`'s
fourth-wave entry, which flags this explicitly as "a good target for future
statement-level differential test coverage."

**Phase 1**: random `if`/`else if`/`else` chains (nested, mandatory `else`
on every chain so there's no latch-inference ambiguity to reason about)
inside a combinational `always @(*)` block, driving one blocking assignment
per case.

**Phase 2**: the SAME randomly generated if/else-chain STRUCTURE (identical
conditions and RHS expressions -- see `_generate_cases`'s rng-state
snapshot/restore, mirroring `test_differential.py`'s own re-use of one
expression for both `y_comb`/`y_ff`) rendered a second way: nonblocking
(`<=`) assignments inside a clocked `always @(posedge clk)` block instead of
blocking (`=`) inside `always @(*)`. This is a genuinely different codegen
path on every engine (NBA scheduling/deferred-update vs. immediate blocking
update) that phase 1 never touches, and had never been differentially
fuzzed before phase 2 landed. Both variants are checked against the
reference oracle every batch.

Conditions and RHS values both reuse `test_differential.py`'s own
expression generator (`_gen_expr`) and fixed 8-signal input set unchanged,
so this only adds a NEW statement-shape layer around already-fuzzer-
verified expression generation, not a second copy of it.

Deliberately NOT yet covered (see the scoping note in the session that
added phase 1): `case`/`casex`/`casez` and `for`/`while` loops -- each is
its own follow-up phase.

Determinism: `VERIFORGE_DIFF_STMT_SEED` (default 20260701) seeds the
generator; `VERIFORGE_DIFF_STMT_CASES` (default 40) sets how many random
if-chains are generated (kept smaller than `test_differential.py`'s default
since each case is a whole nested statement tree, not a single expression).
`VERIFORGE_DIFF_STMT_COMPILED=1` opts the compiled engine in, same as
`test_differential.py` and for the same reason (per-module Cython
compilation is too slow for a fast default run).
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass

import pytest

from . import test_differential as td
from .engines import ENGINES

# =====================================================================
# Configuration
# =====================================================================

_SEED = int(os.environ.get("VERIFORGE_DIFF_STMT_SEED", "20260701"))
_NUM_CASES = int(os.environ.get("VERIFORGE_DIFF_STMT_CASES", "40"))
_COMPILED_ENABLED = os.environ.get("VERIFORGE_DIFF_STMT_COMPILED") == "1"
_BATCH_SIZE = 5
"""Smaller than test_differential.py's 10 -- each case is a whole nested
if-chain (with a full expression tree per condition/leaf), not one
expression, so batches are correspondingly heavier."""

_STMT_MAX_DEPTH = 2
_STMT_LEAF_PROB = 0.45
_STMT_MAX_ARMS = 2
"""Max number of "else if" arms beyond the first "if" at each nesting
level (plus the always-present final "else") -- kept small since nesting
depth and arm count multiply."""

# =====================================================================
# Random if/else-if/else statement generation
# =====================================================================


def _gen_stmt(rng: random.Random, depth: int, target: str, op: str) -> str:
    """One statement: either a leaf assignment, or an if/else-if/.../else
    chain whose every arm (including the mandatory final else) recurses
    into another statement -- so every path assigns `target` exactly once,
    with no latch-inference ambiguity for the oracle to disagree about.

    *op* is `"="` (blocking, phase 1) or `"<="` (nonblocking, phase 2) --
    it only changes the leaf assignment operator, never the tree shape, so
    a caller that snapshots/restores `rng`'s state around two calls with
    different `op` values gets byte-identical condition/RHS-expression
    trees for both (see `_generate_cases`).
    """
    if depth <= 0 or rng.random() < _STMT_LEAF_PROB:
        return f"{target} {op} {td._gen_expr(rng, td._MAX_DEPTH)};"
    n_arms = rng.randint(1, _STMT_MAX_ARMS)
    parts = []
    for i in range(n_arms):
        cond = td._gen_expr(rng, td._MAX_DEPTH)
        kw = "if" if i == 0 else "else if"
        parts.append(f"{kw} ({cond}) begin\n{_gen_stmt(rng, depth - 1, target, op)}\nend")
    parts.append(f"else begin\n{_gen_stmt(rng, depth - 1, target, op)}\nend")
    return "\n".join(parts)


@dataclass(frozen=True)
class _StmtCase:
    idx: int
    comb_body: str
    """Phase 1: blocking (`=`) assignment tree, driven inside `always @(*)`."""
    ff_body: str
    """Phase 2: the SAME tree structure (see `_gen_stmt`'s docstring),
    rendered with nonblocking (`<=`) assignment, driven inside
    `always @(posedge clk)`."""


def _generate_cases(seed: int, n: int) -> list[_StmtCase]:
    rng = random.Random(seed)
    cases = []
    for i in range(n):
        target = f"y_stmt_{i}"
        state = rng.getstate()
        comb_body = _gen_stmt(rng, _STMT_MAX_DEPTH, target, "=")
        # `op` is only interpolated into the leaf string, never consumed by
        # `rng` -- replaying from the same starting state with a different
        # `op` draws the identical condition/RHS-expression sequence (byte-
        # identical tree shape) AND leaves `rng` in the exact same final
        # state the comb-only draw above did, so no separate "advance past
        # this case" step is needed before generating case i+1.
        rng.setstate(state)
        ff_body = _gen_stmt(rng, _STMT_MAX_DEPTH, target, "<=")
        cases.append(_StmtCase(i, comb_body, ff_body))
    return cases


def _batches(cases: list[_StmtCase]) -> list[list[_StmtCase]]:
    return [cases[i : i + _BATCH_SIZE] for i in range(0, len(cases), _BATCH_SIZE)]


_CASES = _generate_cases(_SEED, _NUM_CASES)
_BATCHES = _batches(_CASES)

# =====================================================================
# Verilog module generation
# =====================================================================


def _build_batch_module(cases: list[_StmtCase]) -> str:
    ports = ["input clk"]
    for name, width, signed in td.FIXED_SIGNALS:
        sig = "signed " if signed else ""
        ports.append(f"input {sig}[{width - 1}:0] {name}")
    for c in cases:
        ports.append(f"output reg [95:0] y_stmt_{c.idx}")
        ports.append(f"output reg [95:0] y_ff_stmt_{c.idx}")
    header = ",\n    ".join(ports)
    comb_blocks = "\n".join(f"    always @(*) begin\n{c.comb_body}\n    end" for c in cases)
    ff_blocks = "\n".join(f"    always @(posedge clk) begin\n{c.ff_body}\n    end" for c in cases)
    return f"module t(\n    {header}\n);\n{comb_blocks}\n{ff_blocks}\nendmodule\n"


# =====================================================================
# Simulation plumbing (same pattern as test_differential.py)
# =====================================================================


def _run_batch(
    source: str, engine: str, cases: list[_StmtCase], vectors: list[dict[str, td.Value]]
) -> list[tuple[dict[int, td.Value], dict[int, td.Value]]]:
    sim = td._sim_for(source, engine)
    sim.drive("clk", td.Value(0, width=1))
    for name, width, _signed in td.FIXED_SIGNALS:
        sim.drive(name, td.Value(0, width=width))
    sim.settle()

    results: list[tuple[dict[int, td.Value], dict[int, td.Value]]] = []
    for vec in vectors:
        for name, value in vec.items():
            sim.drive(name, value)
        sim.settle()
        comb = {c.idx: sim.read(f"y_stmt_{c.idx}") for c in cases}
        sim.drive("clk", td.Value(1, width=1))
        sim.settle()
        sim.drive("clk", td.Value(0, width=1))
        sim.settle()
        ff = {c.idx: sim.read(f"y_ff_stmt_{c.idx}") for c in cases}
        results.append((comb, ff))
    return results


# =====================================================================
# Test
# =====================================================================


@pytest.mark.cross_engine
@pytest.mark.parametrize("batch_idx", range(len(_BATCHES)))
def test_differential_statements(batch_idx: int) -> None:
    cases = _BATCHES[batch_idx]
    source = _build_batch_module(cases)
    vectors = td._gen_vectors(f"{_SEED}:stmt_vectors:{batch_idx}")

    oracle = _run_batch(source, "reference", cases, vectors)
    engines = [e for e in ENGINES if e != "reference" and (e != "compiled" or _COMPILED_ENABLED)]
    for engine in engines:
        got = _run_batch(source, engine, cases, vectors)
        for vec_idx, ((exp_comb, exp_ff), (got_comb, got_ff)) in enumerate(zip(oracle, got, strict=True)):
            for c in cases:
                exp_c, got_c = exp_comb[c.idx], got_comb[c.idx]
                assert exp_c == got_c, (
                    f"differential mismatch: engine={engine} seed={_SEED} batch={batch_idx} "
                    f"case={c.idx} vector={vec_idx} signal=y_stmt_{c.idx}\n"
                    f"body:\n{c.comb_body}\n"
                    f"vector: {vectors[vec_idx]}\n"
                    f"expected(reference)={exp_c!r} got({engine})={got_c!r}\n"
                    f"module source:\n{source}"
                )
                exp_c, got_c = exp_ff[c.idx], got_ff[c.idx]
                assert exp_c == got_c, (
                    f"differential mismatch: engine={engine} seed={_SEED} batch={batch_idx} "
                    f"case={c.idx} vector={vec_idx} signal=y_ff_stmt_{c.idx}\n"
                    f"body:\n{c.ff_body}\n"
                    f"vector: {vectors[vec_idx]}\n"
                    f"expected(reference)={exp_c!r} got({engine})={got_c!r}\n"
                    f"module source:\n{source}"
                )
