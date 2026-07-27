"""Semantic-core parity characterization (work plan item 4.2, Phase A).

**Phase A deliverable**: this test plus the resolution notes below — no
production code changes. It runs a fixture list of expressions through
every existing const-evaluation / width-computation implementation and
documents where they agree and where they don't, so that Phase B
(``src/veriforge/semantics.py``) has a written spec to build to instead of
guessing which of six copies is "the real" behavior.

The seven implementations named in the work plan collapse to six distinct
call sites once duplication is accounted for (``analysis/width_inference.py``
already delegates to ``analysis/const_fold.py`` for both ``_const_int`` and
``_range_width`` — see ``width_inference._const_int``/``_range_width``):

1. ``sim/scheduler.py``: ``_const_int``, ``_range_width``, ``_var_width``
2. ``sim/vm/compiler.py``: same three, near-identical copies
3. ``sim/compiled/_codegen_utils.py``: ``_const_int``
4. ``sim/compiled/codegen.py``: ``_range_width``, ``_var_width``
5. ``analysis/width_inference.py`` + ``analysis/const_fold.py``: ``_const_int``
   / ``const_int``, ``_range_width`` / ``const_range_width`` (same underlying
   implementation, exposed as two call sites)
6. ``sim/elaborate.py``: ``_eval_const_expr`` — the shared low-level
   primitive that (1)-(4) all fall back to for non-literal expressions.

Per the work plan's explicit non-goal, this phase (and the eventual
``semantics.py`` module) does **not** cover ``expr_width``/``expr_signed``
parity across the VM/compiled *emitters* (``sim/vm/compiler.py``'s and
``sim/compiled/_expr_emitter.py``'s own ``_expr_width``/``_expr_signed``) —
those mix width computation with codegen slot allocation and are deferred to
a follow-up once ``semantics.py`` exists.

## Confirmed differences and their resolutions

**Difference 1 — `$clog2`-derived parameter referenced by ANOTHER
parameter's default value.** For
``parameter N = 16; parameter IDX_BITS = $clog2(N);``, evaluating
``IDX_BITS``'s default-value expression:

- `sim/scheduler.py`, `sim/vm/compiler.py`, `sim/compiled/_codegen_utils.py`,
  `sim/elaborate.py` (`_eval_const_expr` with an explicit `env` dict built by
  `elaborate._build_param_env`) all correctly return `4`.
- `analysis/const_fold.py`'s `const_int` (and therefore
  `analysis/width_inference.py`, which delegates to it) returns `None`.

Root cause: `const_fold.const_int`'s `Identifier` case resolves a parameter
reference via `expr.resolved` (an attribute populated by the name-resolution
analysis pass for identifiers used in module *body* statements/expressions),
not via an explicit environment dict. An `Identifier` appearing *inside
another parameter's own default-value expression* is not resolved by that
pass, so `expr.resolved` is `None` there and `const_int` gives up.

**Resolution**: `semantics.const_int` uses the `env`-dict-based mechanism
(`elaborate._eval_const_expr`'s approach — built once per module via
`_build_param_env`, threaded through as the `env` parameter) as its
*primary* resolution path, not `.resolved`-attribute lookup. This is also
what the plan's proposed signature already implies (`env:
Mapping[str, int] | None = None`, a parameter, not an implicit
attribute-based lookup). Phase C+ migrates `const_fold.const_int`'s callers
to pass an explicit `env` (built once via `_build_param_env` and cached per
module), which fixes this gap as a side effect rather than as a special case.

**Difference 2 — ascending (unusual but legal) range `[0:7]`.** IEEE
1364-2005 permits declaring a net/port with the MSB *numerically smaller*
than the LSB (e.g. `output [0:7] busA;`, bit 0 is the MSB) — width is always
`abs(msb - lsb) + 1`, never negative.

- `sim/compiled/codegen.py`'s `_range_width` and
  `analysis/width_inference.py`'s (via `const_fold.const_range_width`)
  correctly use `abs(...)` and return `8`.
- `sim/scheduler.py`'s and `sim/vm/compiler.py`'s `_range_width` fast path
  (`int(r.msb.value) - int(r.lsb.value) + 1`, no `abs()`) returns `-6` for
  `[0:7]` — a **latent, pre-existing bug**, not something introduced by this
  characterization pass. It has apparently never been hit by the existing
  test suites (no test declares an ascending-order range), which is exactly
  the kind of gap this phase exists to surface.

**Resolution**: `semantics.range_width` uses `abs(msb - lsb) + 1`
unconditionally (matching the compiled engine and width_inference). Phase C
(reference scheduler migration) fixes scheduler.py's copy as a side effect;
this is flagged explicitly since it's a genuine bug fix bundled into a
refactor, not a pure behavior-preserving rename.

**Difference 3 — non-constant expressions: `None` vs. raised `ValueError`.**
`sim/elaborate._eval_const_expr` *raises* `ValueError`/`TypeError` for an
expression it cannot evaluate (e.g. one that reads an actual signal, not a
parameter) — this is intentional; it is the low-level primitive, and all
five wrapper functions (`scheduler`/`vm`/`compiled_utils`'s `_const_int`,
`const_fold.const_int`, `width_inference._const_int`) catch this internally
and return `None` uniformly.

**Resolution**: `semantics.const_int`'s public contract (per the plan's own
signature, `-> int | None`) matches the wrapper convention: catch, return
`None`, never raise. `_eval_const_expr`'s raising behavior is preserved
as-is as an internal implementation detail `semantics.const_int` wraps
(mirroring exactly how the 4 existing wrapper functions already do this).

**Everything else the fixtures below exercise — unsized/sized/based
literals (`8'hFF`), plain parameter references, arithmetic on parameters
(`W-1`, `2*W`), parametric ranges (`[W-1:0]`, `[$clog2(N)-1:0]`), variable
kind-based widths (`integer`/`real`/`time`/`byte`/`shortint`/`int`/
`longint`), and hierarchical-name environment scoping (`_scoped_env`,
verified identical by direct source comparison across all three copies) —
agree across every implementation that can express the case at all (i.e.
excluding `width_inference`/`const_fold`'s inherent inability to evaluate
expressions with no `.resolved` identifiers, which Difference 1 already
covers as the general form of that gap).**
"""

from __future__ import annotations

from veriforge.analysis import analyze_design
from veriforge.analysis import const_fold as const_fold_module
from veriforge.analysis import width_inference as wi_module
from veriforge.analysis.resolver import link_instances, resolve_port_connections
from veriforge.sim import scheduler as scheduler_module
from veriforge.sim.compiled import _codegen_utils as compiled_utils_module
from veriforge.sim.compiled import codegen as compiled_codegen_module
from veriforge.sim.elaborate import _build_param_env, _eval_const_expr
from veriforge.sim.vm import compiler as vm_compiler_module
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

# `veriforge.analysis.__init__` re-exports a *function* named `const_fold`
# (from `analysis/const_fold.py`), which shadows the submodule as a package
# attribute -- `from veriforge.analysis import const_fold` would silently
# bind the function, not the module. Import the submodule directly instead.
import importlib  # noqa: E402

const_fold = importlib.import_module("veriforge.analysis.const_fold")

_SRC = """
module top #(
    parameter W = 8,
    parameter N = 16,
    parameter IDX_BITS = $clog2(N),
    parameter [7:0] BASE = 8'hFF,
    parameter signed SW = -3
) (
    input [W-1:0] a,
    input [W-1:0] b,
    output [W-1:0] y,
    output [IDX_BITS-1:0] idx
);
    wire [W-1:0] c;
    wire [2*W-1:0] wide;
    reg [7:0] r0to7;
    integer i_var;
    real r_var;
    time t_var;
    byte b_var;
    shortint si_var;
    longint li_var;

    assign c = (a > b) ? a : b;
    assign wide = {a, b};
    assign y = c >> 1;
endmodule
"""


def _parse():
    vp = verilog_parser(start="source_text")
    tree = vp.build_tree(_SRC)
    design = tree_to_design(tree, source_file="test.v")
    link_instances(design)
    resolve_port_connections(design)
    analyze_design(design)
    return next(m for m in design.modules if m.name == "top")


def _const_int_all(expr, env):
    """Run every _const_int-family implementation; each must catch and
    return None for non-constant/unresolvable input, never raise."""
    return {
        "scheduler": scheduler_module._const_int(expr, env),
        "vm": vm_compiler_module._const_int(expr, env),
        "compiled_utils": compiled_utils_module._const_int(expr, env),
        "width_inference": wi_module._const_int(expr),
        "const_fold": const_fold.const_int(expr),
    }


def test_const_int_agrees_on_plain_and_based_literal_parameters():
    top = _parse()
    env = _build_param_env(top)
    params = {p.name: p for p in top.parameters}

    # W = 8 (unsized literal), BASE = 8'hFF (sized/based literal), SW = -3 (signed unary)
    for name, expected in [("W", 8), ("N", 16), ("BASE", 255), ("SW", -3)]:
        results = _const_int_all(params[name].default_value, env)
        assert all(v == expected for v in results.values()), (name, results)
        assert _eval_const_expr(params[name].default_value, env) == expected


def test_const_int_clog2_derived_parameter_difference():
    """Difference 1 (see module docstring): const_fold/width_inference can't
    see a parameter referenced inside ANOTHER parameter's own default-value
    expression (no .resolved there); env-dict-based paths can."""
    top = _parse()
    env = _build_param_env(top)
    idx_bits_expr = next(p for p in top.parameters if p.name == "IDX_BITS").default_value

    assert scheduler_module._const_int(idx_bits_expr, env) == 4
    assert vm_compiler_module._const_int(idx_bits_expr, env) == 4
    assert compiled_utils_module._const_int(idx_bits_expr, env) == 4
    assert _eval_const_expr(idx_bits_expr, env) == 4

    # Confirmed gap -- documents current behavior, not desired behavior.
    assert wi_module._const_int(idx_bits_expr) is None
    assert const_fold.const_int(idx_bits_expr) is None


def test_const_int_non_constant_expr_is_none_not_raise():
    """Difference 3: the 5 wrapper functions never raise; only the raw
    `_eval_const_expr` primitive does (by design, for its own callers)."""
    top = _parse()
    env = _build_param_env(top)
    y_rhs = next(ca.rhs for ca in top.continuous_assigns if str(ca.lhs) == "Identifier('y')")

    results = _const_int_all(y_rhs, env)
    assert all(v is None for v in results.values()), results

    try:
        _eval_const_expr(y_rhs, env)
    except (ValueError, TypeError):
        pass
    else:
        raise AssertionError("_eval_const_expr was expected to raise for a signal-dependent expression")


def _range_width_all(rng, env):
    return {
        "scheduler": scheduler_module._range_width(rng, env),
        "vm": vm_compiler_module._range_width(rng, env),
        "compiled_codegen": compiled_codegen_module._range_width(rng, env),
        "width_inference": wi_module._range_width(rng),
    }


def test_range_width_agrees_on_literal_and_parametric_ranges():
    top = _parse()
    env = _build_param_env(top)
    nets = {n.name: n for n in top.nets}
    variables = {v.name: v for v in top.variables}

    for label, rng, expected in [
        ("net c [W-1:0]", nets["c"].width, 8),
        ("net wide [2*W-1:0]", nets["wide"].width, 16),
        ("var r0to7 [7:0]", variables["r0to7"].width, 8),
        ("None range", None, 1),
    ]:
        results = _range_width_all(rng, env)
        assert all(v == expected for v in results.values()), (label, results)


def test_range_width_transitively_hits_the_clog2_gap():
    """A range referencing a `$clog2`-derived parameter (port idx's
    [IDX_BITS-1:0]) transitively hits Difference 1: width_inference's
    .resolved-based chain fails evaluating IDX_BITS's own default value
    ($clog2(N)), since N's reference *inside that default-value expression*
    has no .resolved -- same root cause as
    test_const_int_clog2_derived_parameter_difference, just reached via a
    range instead of a direct parameter lookup."""
    top = _parse()
    env = _build_param_env(top)
    idx_range = next(p for p in top.ports if p.name == "idx").width

    assert scheduler_module._range_width(idx_range, env) == 4
    assert vm_compiler_module._range_width(idx_range, env) == 4
    assert compiled_codegen_module._range_width(idx_range, env) == 4

    # Confirmed gap (same root cause as Difference 1) -- documents current
    # behavior, not desired behavior.
    assert wi_module._range_width(idx_range) is None


def test_range_width_ascending_range_difference():
    """Difference 2 (see module docstring): scheduler/vm's fast path lacks
    abs(), giving a negative width for a legal ascending [0:7] range;
    compiled_codegen/width_inference are correct."""
    from veriforge.model.expressions import Literal, Range

    ascending = Range(Literal(0), Literal(7))

    assert compiled_codegen_module._range_width(ascending, {}) == 8
    assert wi_module._range_width(ascending) == 8

    # Confirmed pre-existing bug -- documents current behavior, not desired.
    assert scheduler_module._range_width(ascending, {}) == -6
    assert vm_compiler_module._range_width(ascending, {}) == -6


def test_var_width_special_kinds_agree():
    top = _parse()
    env = _build_param_env(top)
    variables = {v.name: v for v in top.variables}

    for name, expected in [
        ("r0to7", 8),  # reg [7:0]
        ("i_var", 32),  # integer
        ("r_var", 64),  # real
        ("t_var", 64),  # time
        ("b_var", 8),  # byte
        ("si_var", 16),  # shortint
        ("li_var", 64),  # longint
    ]:
        var = variables[name]
        results = {
            "scheduler": scheduler_module._var_width(var, env),
            "vm": vm_compiler_module._var_width(var, env),
            "compiled_codegen": compiled_codegen_module._var_width(var, env),
        }
        assert all(v == expected for v in results.values()), (name, results)


def test_scoped_env_hierarchical_prefix_aliasing_identical_across_copies():
    """`_scoped_env` (hierarchical-name hint aliasing) is a byte-for-byte
    identical copy in scheduler.py, vm/compiler.py, and compiled/codegen.py
    -- confirmed by direct source comparison. This test exercises the
    behavior all three share, matching the pattern documented in each
    copy's own docstring."""
    hier_env = {"uut.W": 8, "uut.N": 16, "top_level_only": 99}

    for scoped_env in (
        scheduler_module._scoped_env,
        vm_compiler_module._scoped_env,
        compiled_codegen_module._scoped_env,
    ):
        scoped = scoped_env("uut.some_signal", hier_env)
        assert scoped["uut.W"] == 8
        assert scoped["W"] == 8  # unprefixed alias added
        assert scoped["N"] == 16
        assert scoped["top_level_only"] == 99
        # A name outside the "uut." prefix should not gain a bogus alias.
        assert "some_signal" not in scoped
