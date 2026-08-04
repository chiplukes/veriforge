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

**Phase 3**: random `case`/`casex`/`casez` statements can now appear
anywhere an if-chain could (`_gen_stmt` picks between leaf/if-chain/case-
statement at each eligible recursion point), with 1-2 non-default items
(1-2 comma-separated values each) plus a mandatory `default` (same
latch-avoidance reasoning as phase 1's mandatory `else`). This is the
first time the harness has generated Verilog LITERALS at all -- phases
1/2's leaves are exclusively signal references/selects
(`test_differential.py`'s `_gen_leaf`) -- so this adds a new sized-literal
generator (`_gen_case_literal`), including x/z bits for casex/casez items
specifically (the whole point of exercising wildcard matching). Case item
literal widths are deliberately NOT always matched to the selector's own
self-determined width (`_gen_case_literal`'s width is
`sel_width + randint(-2, 2)`, clamped to >= 1): reading the pre-fuzzing
implementation showed `sim/executor.py`'s `_case_match` and
`sim/vm/compiler.py`'s `_compile_case` apply NO shared width between
selector and item at all (self-determined only, the same "missing
self-determined width" gap phase 1 fixed for `if`/`for`/`while`
conditions but never extended to `case`), while
`sim/compiled/_stmt_emitters.py`'s `_emit_case` forces every item value to
the SELECTOR's own width -- three different behaviors, and a width
mismatch is specifically meant to surface the resulting divergence.

Conditions and RHS values both reuse `test_differential.py`'s own
expression generator (`_gen_expr`) and fixed 8-signal input set unchanged,
so this only adds a NEW statement-shape layer around already-fuzzer-
verified expression generation, not a second copy of it.

**Phase 4**: random `for`/`while` loops can now appear anywhere an
if-chain or case statement could. `for` loops use SystemVerilog's inline
loop-variable declaration (`for (int _li{n} = 0; _li{n} < N; _li{n} =
_li{n} + 1) begin ... end`, `N` a small random bound) -- self-contained,
no module-structure changes needed. `while` loops have no init clause,
so their counter needs a real module-level `integer` declaration (a
separately proven-supported pattern); to guarantee termination
regardless of what the (genuinely data-dependent, possibly always-false)
loop condition evaluates to, the generated condition is `(_li{n} < N) &&
(<random expr>)` -- bounded by the same small `N`, while still
exercising early exit on the data-dependent half. Because a `while` loop
can legitimately run zero iterations (unlike `for`, whose `N >= 1` bound
guarantees at least one pass), `target` gets an unconditional fallback
assignment immediately before the loop, then possibly overwritten by the
loop body -- same latch-avoidance reasoning as phases 1/3's mandatory
`else`/`default`. Loop-variable names are unique per case AND per
comb/ff render (`_li{case_idx}_{c,f}{n}`) since the same variable can't
be procedurally driven by two different `always` blocks. Nesting depth
is bounded by the existing `_STMT_MAX_DEPTH` budget (unchanged), so
worst-case total loop-body executions from nested loops stays small
(bounded by N^depth, not exponential in any generator parameter that
isn't already capped). This is the first time the harness generates a
statement whose body can execute MORE THAN ONCE per always-block
evaluation -- exercising "last assignment wins" semantics for repeated
blocking/nonblocking writes to the same target within one loop, a shape
no prior phase could produce (if/case each assign exactly once per
path).

**Phase 5**: random sequential multi-statement blocks (`begin...end` with
2-3 statements) can now appear anywhere an if-chain, case statement, or
loop could. Every EARLIER phase generates exactly one assignment target
per rendered tree, with every leaf expression reading only from the 8
fixed input ports -- never from a value a PRIOR statement in the same
procedural block just wrote, despite that being arguably the single most
common shape in real combinational/sequential RTL (`tmp = a + b; y = tmp
* c;`). `_gen_seq_stmt` generates a block whose first N-1 statements each
assign a FRESH local temp (`_tmp{case_idx}_{c,f}{n}`, a fresh name/random
width from `td._WIDTHS`/random signedness, module-level `reg` declared
like a while-loop counter), using an expression that can reference any
temp declared EARLIER in the same sequence (`td._gen_expr`'s new
`extra_signals` parameter) -- never itself or a later one, matching real
read-after-write ordering. The final statement recurses into `_gen_stmt`
as usual. Crucially, temp assignments use the SAME operator (`op`) as the
enclosing render: for the comb-phase (`=`) render this is ordinary
blocking-assignment same-time-step visibility (a later statement sees the
value just written), but for the ff-phase (`<=`) render, a later
statement reading a temp must see whatever it held BEFORE this block
started executing, since a nonblocking write doesn't take effect until
the whole block finishes -- exercising NBA's deferred-update semantics
for LOCAL (non-port) variables specifically, a genuinely different code
path from `y_ff_stmt_N`'s own NBA staging that no earlier phase reached.
Available temps are threaded through the WHOLE `_gen_stmt` recursion (a
new `_SeqState`, alongside `_LoopState`) so a sequence nested inside an
if-arm inside another sequence keeps accumulating into one growing pool,
and every leaf/condition-generating call site (not just the sequence's
own statements) can read from it, exactly like the fixed 8-signal set.

Deliberately NOT yet covered: `unique`/`priority` case modifiers, casex/
casez wildcard ranges via `?`, case-inside-case nesting beyond what the
shared `_gen_stmt` recursion naturally produces, `forever`/`repeat`
loops, `break`/`continue`/`disable`, multiple loop variables per `for`,
and user-defined `function`/`task` calls -- each is its own follow-up
phase (if warranted); see `notes/plans/work_plan_2026-07.md` item 3.4's
"Deferred / future work" note for why `forever`/`repeat` and
function/task calls specifically were reprioritized below phase 5.

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

_STMT_CASE_PROB = 0.35
"""At an eligible (non-leaf) recursion point, the probability of
generating a case/casex/casez statement instead of an if-chain."""
_STMT_CASE_MAX_ITEMS = 2
"""Max non-default case items (plus the always-present final default)."""
_STMT_CASE_MAX_VALUES = 2
"""Max comma-separated values per non-default case item."""

_STMT_LOOP_PROB = 0.3
"""At an eligible (non-leaf, non-case) recursion point, the probability
of generating a for/while loop instead of an if-chain."""
_STMT_LOOP_MAX_N = 4
"""Max loop-bound constant -- combined with `_STMT_MAX_DEPTH` capping
nesting, keeps worst-case total loop-body executions small."""

_STMT_SEQ_PROB = 0.3
"""At an eligible (non-leaf, non-case, non-loop) recursion point, the
probability of generating a sequential multi-statement block (phase 5)
instead of an if-chain."""
_STMT_SEQ_LEN_CHOICES = (2, 3)
"""Number of statements in a generated sequence block, including the
final one that recurses into `_gen_stmt` -- kept small, matching the
existing arm-count/case-item-count conservatism."""

# =====================================================================
# Random if/else-if/else and case/casex/casez statement generation
# =====================================================================


def _gen_case_selector(rng: random.Random) -> tuple[int, str]:
    """A case expression: a fixed signal, optionally bit/part-selected.

    Mirrors `test_differential.py`'s `_gen_leaf` exactly (same restriction
    to a fresh leaf reference, same shape distribution), but additionally
    returns the resulting self-determined width -- `_gen_leaf` only
    returns the string, and case item literals need to know this width to
    size themselves relative to it (see `_gen_case_literal`).
    """
    name, width, _signed = rng.choice(td.FIXED_SIGNALS)
    if width == 1:
        return 1, name
    choice = rng.random()
    if choice < 0.4:
        return width, name
    if choice < 0.7:
        idx = rng.randrange(width)
        return 1, f"{name}[{idx}]"
    hi = rng.randrange(width)
    lo = rng.randrange(hi + 1)
    if hi == lo:
        return 1, f"{name}[{hi}]"
    return hi - lo + 1, f"{name}[{hi}:{lo}]"


def _gen_case_literal(rng: random.Random, sel_width: int, allow_xz: bool) -> str:
    """A sized Verilog literal for a case item value.

    Deliberately does NOT always match `sel_width` -- see this file's
    docstring (phase 3 section) for why a width mismatch here is exactly
    what's meant to be exercised. `allow_xz` is only set for casex/casez
    items (a plain `case` item practically never contains x/z bits, and
    exercising that combination is a separate, narrower question this
    phase doesn't target).
    """
    width = max(1, sel_width + rng.randint(-2, 2))
    chars = "01xz" if allow_xz else "01"
    bits = "".join(rng.choice(chars) for _ in range(width))
    return f"{width}'b{bits}"


@dataclass
class _LoopState:
    """Threaded through the whole `_gen_stmt` recursion for one render
    (one case's comb OR ff body -- see `_generate_cases`) so every
    generated for/while loop gets a unique Verilog identifier and every
    while-loop's counter declaration ends up collected in one place,
    ready to emit at module scope. `prefix` alone (`_li{case_idx}_c`/
    `_li{case_idx}_f`) guarantees no collision across different cases or
    between the comb/ff render of the SAME case; `ctr` (incremented once
    per loop, regardless of kind) guarantees no collision between
    multiple loops within one render.
    """

    prefix: str
    ctr: list[int]
    decls: list[str]

    def next_var(self) -> str:
        n = self.ctr[0]
        self.ctr[0] += 1
        return f"{self.prefix}{n}"


@dataclass
class _SeqState:
    """Threaded through the whole `_gen_stmt` recursion for one render,
    alongside `_LoopState` (phase 5) -- tracks local "temp" variables
    declared by sequential-block statements generated SO FAR, so a LATER
    statement's expression generation can reference one as an extra leaf
    (`td._gen_expr`'s `extra_signals` parameter), reproducing real
    read-after-write data dependencies within a procedural block. Never
    exposes a temp to anything generated BEFORE it -- `available` only
    grows as `_gen_seq_stmt` appends to it, in generation order, matching
    real sequential execution order exactly (a temp assigned via `op`
    becomes an eligible read for every statement generated afterward,
    whether in the same sequence or a sibling/nested one sharing this
    same state). Same per-case/per-comb-or-ff `prefix` separation
    reasoning as `_LoopState` (a variable can't be procedurally driven by
    two different `always` blocks).
    """

    prefix: str
    ctr: list[int]
    decls: list[str]
    available: list[tuple[str, int, bool]]

    def next_var(self) -> str:
        n = self.ctr[0]
        self.ctr[0] += 1
        return f"{self.prefix}{n}"


def _gen_case_stmt(rng: random.Random, depth: int, target: str, op: str, loops: _LoopState, seq: _SeqState) -> str:
    """A case/casex/casez statement whose every item (including the
    mandatory final default) recurses into `_gen_stmt` -- same "every path
    assigns `target` exactly once" invariant as the if-chain generator.
    """
    case_type = rng.choice(("case", "casex", "casez"))
    allow_xz = case_type != "case"
    sel_width, sel_expr = _gen_case_selector(rng)
    n_items = rng.randint(1, _STMT_CASE_MAX_ITEMS)
    parts = [f"{case_type} ({sel_expr})"]
    for _ in range(n_items):
        n_vals = rng.randint(1, _STMT_CASE_MAX_VALUES)
        vals = ", ".join(_gen_case_literal(rng, sel_width, allow_xz) for _ in range(n_vals))
        parts.append(f"{vals}: begin\n{_gen_stmt(rng, depth - 1, target, op, loops, seq)}\nend")
    parts.append(f"default: begin\n{_gen_stmt(rng, depth - 1, target, op, loops, seq)}\nend")
    parts.append("endcase")
    return "\n".join(parts)


def _gen_for_stmt(rng: random.Random, depth: int, target: str, op: str, loops: _LoopState, seq: _SeqState) -> str:
    """A `for` loop using SystemVerilog's inline loop-variable
    declaration -- self-contained, no module-level declaration needed.
    `bound >= 1` guarantees the body (and therefore `target`) always
    executes at least once, same "no latch inference" invariant as
    if-chains/case, just via a different mechanism (a loop that always
    runs, rather than a branch that's always taken).
    """
    var = loops.next_var()
    bound = rng.randint(1, _STMT_LOOP_MAX_N)
    body = _gen_stmt(rng, depth - 1, target, op, loops, seq)
    return f"for (int {var} = 0; {var} < {bound}; {var} = {var} + 1) begin\n{body}\nend"


def _gen_while_stmt(rng: random.Random, depth: int, target: str, op: str, loops: _LoopState, seq: _SeqState) -> str:
    """A `while` loop whose counter needs a real module-level `integer`
    declaration (collected into `loops.decls`, no init clause to hang an
    inline declaration off like `for` has). The condition is `(counter <
    bound) && (random expr)` -- bounded by the same small `bound` `for`
    uses, so the loop is guaranteed to terminate regardless of what the
    data-dependent half evaluates to, while still exercising a genuinely
    data-dependent early exit. Unlike `for`, a `while` loop can
    legitimately run zero iterations, so `target` gets an unconditional
    fallback assignment first (same latch-avoidance reasoning as the
    mandatory final else/default elsewhere), then possibly overwritten by
    the loop body.
    """
    var = loops.next_var()
    loops.decls.append(f"integer {var};")
    bound = rng.randint(1, _STMT_LOOP_MAX_N)
    fallback = td._gen_expr(rng, td._MAX_DEPTH, tuple(seq.available))
    cond = td._gen_expr(rng, td._MAX_DEPTH, tuple(seq.available))
    body = _gen_stmt(rng, depth - 1, target, op, loops, seq)
    return (
        f"{target} {op} {fallback};\n"
        f"{var} = 0;\n"
        f"while (({var} < {bound}) && ({cond})) begin\n"
        f"{body}\n"
        f"{var} = {var} + 1;\n"
        f"end"
    )


def _gen_seq_stmt(rng: random.Random, depth: int, target: str, op: str, loops: _LoopState, seq: _SeqState) -> str:
    """Phase 5: a `begin...end` block of 2-3 sequential statements. Each
    of the first N-1 assigns a FRESH local temp (a fresh name, random
    width from `td._WIDTHS`, random signedness) using an expression that
    can reference any temp declared EARLIER in this same sequence (never
    itself or a later one -- `seq.available` only grows as each temp is
    declared, so generation order IS read-after-write order). The final
    statement recurses into `_gen_stmt` as usual (still possibly a
    leaf/if/case/loop/nested-sequence), so `target` still ends up
    assigned on every path exactly like every other statement shape.

    Uses the SAME assignment operator (`op`) for the temp assignments as
    the enclosing render -- deliberate: for a comb-phase (`=`) render, a
    later statement reading a temp sees the value JUST written (ordinary
    blocking-assignment same-time-step visibility); for an ff-phase
    (`<=`) render, a later statement reading a temp must see whatever the
    temp held BEFORE this block started executing, since a nonblocking
    write doesn't take effect until the whole block finishes -- the
    single most common real-RTL data-dependency shape (`tmp = a + b; y =
    tmp * c;`, or the read-old-value NBA equivalent) that no earlier
    phase could generate, since every prior phase writes exactly one
    target signal and reads only from the 8 fixed input ports.
    """
    n_stmts = rng.choice(_STMT_SEQ_LEN_CHOICES)
    parts = []
    for _ in range(n_stmts - 1):
        name = seq.next_var()
        width = rng.choice(td._WIDTHS)
        signed = rng.random() < 0.5
        sign_kw = "signed " if signed else ""
        seq.decls.append(f"reg {sign_kw}[{width - 1}:0] {name};")
        rhs = td._gen_expr(rng, td._MAX_DEPTH, tuple(seq.available))
        parts.append(f"{name} {op} {rhs};")
        seq.available.append((name, width, signed))
    parts.append(_gen_stmt(rng, depth - 1, target, op, loops, seq))
    return "begin\n" + "\n".join(parts) + "\nend"


def _gen_stmt(rng: random.Random, depth: int, target: str, op: str, loops: _LoopState, seq: _SeqState) -> str:
    """One statement: a leaf assignment, an if/else-if/.../else chain, a
    case/casex/casez statement, a for/while loop, or a sequential
    multi-statement block (phase 5) -- every arm/item (including the
    mandatory final else/default) recurses into another statement, and
    both loop shapes plus the sequence's final statement guarantee
    `target` ends up assigned regardless of iteration count, so every
    path assigns `target` at least once, with no latch-inference
    ambiguity for the oracle to disagree about.

    *op* is `"="` (blocking, phase 1) or `"<="` (nonblocking, phase 2) --
    it only changes the leaf assignment operator, never the tree shape, so
    a caller that snapshots/restores `rng`'s state around two calls with
    different `op` values gets byte-identical condition/RHS-expression
    trees for both (see `_generate_cases`).
    """
    if depth <= 0 or rng.random() < _STMT_LEAF_PROB:
        return f"{target} {op} {td._gen_expr(rng, td._MAX_DEPTH, tuple(seq.available))};"
    if rng.random() < _STMT_CASE_PROB:
        return _gen_case_stmt(rng, depth, target, op, loops, seq)
    if rng.random() < _STMT_LOOP_PROB:
        if rng.random() < 0.5:
            return _gen_for_stmt(rng, depth, target, op, loops, seq)
        return _gen_while_stmt(rng, depth, target, op, loops, seq)
    if rng.random() < _STMT_SEQ_PROB:
        return _gen_seq_stmt(rng, depth, target, op, loops, seq)
    n_arms = rng.randint(1, _STMT_MAX_ARMS)
    parts = []
    for i in range(n_arms):
        cond = td._gen_expr(rng, td._MAX_DEPTH, tuple(seq.available))
        kw = "if" if i == 0 else "else if"
        parts.append(f"{kw} ({cond}) begin\n{_gen_stmt(rng, depth - 1, target, op, loops, seq)}\nend")
    parts.append(f"else begin\n{_gen_stmt(rng, depth - 1, target, op, loops, seq)}\nend")
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
    comb_decls: tuple[str, ...]
    """Phase 4/5: module-level `integer` declarations for any while-loop
    counters, plus (phase 5) local temp `reg` declarations for any
    sequential-block statements, generated in `comb_body` (empty if
    neither appears)."""
    ff_decls: tuple[str, ...]
    """Phase 4/5: same, for `ff_body` -- a SEPARATE set of names (see
    `_LoopState`/`_SeqState`'s docstrings) since the same variable can't
    be procedurally driven by two different `always` blocks."""


def _generate_cases(seed: int, n: int) -> list[_StmtCase]:
    rng = random.Random(seed)
    cases = []
    for i in range(n):
        target = f"y_stmt_{i}"
        state = rng.getstate()
        comb_loops = _LoopState(f"_li{i}_c", [0], [])
        comb_seq = _SeqState(f"_tmp{i}_c", [0], [], [])
        comb_body = _gen_stmt(rng, _STMT_MAX_DEPTH, target, "=", comb_loops, comb_seq)
        # `op` is only interpolated into the leaf string, never consumed by
        # `rng` -- replaying from the same starting state with a different
        # `op` draws the identical condition/RHS-expression sequence (byte-
        # identical tree shape) AND leaves `rng` in the exact same final
        # state the comb-only draw above did, so no separate "advance past
        # this case" step is needed before generating case i+1.
        rng.setstate(state)
        ff_loops = _LoopState(f"_li{i}_f", [0], [])
        ff_seq = _SeqState(f"_tmp{i}_f", [0], [], [])
        ff_body = _gen_stmt(rng, _STMT_MAX_DEPTH, target, "<=", ff_loops, ff_seq)
        cases.append(
            _StmtCase(
                i,
                comb_body,
                ff_body,
                tuple(comb_loops.decls) + tuple(comb_seq.decls),
                tuple(ff_loops.decls) + tuple(ff_seq.decls),
            )
        )
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
    decl_lines = [d for c in cases for d in (*c.comb_decls, *c.ff_decls)]
    decls = "\n".join(f"    {d}" for d in decl_lines)
    comb_blocks = "\n".join(f"    always @(*) begin\n{c.comb_body}\n    end" for c in cases)
    ff_blocks = "\n".join(f"    always @(posedge clk) begin\n{c.ff_body}\n    end" for c in cases)
    return f"module t(\n    {header}\n);\n{decls}\n{comb_blocks}\n{ff_blocks}\nendmodule\n"


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
