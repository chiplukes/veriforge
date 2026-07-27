"""Semantic-core parity characterization (work plan item 4.2, Phase A).

**Phase A deliverable**: this test plus the resolution notes below — no
production code changes. It runs a fixture list of expressions through
every existing const-evaluation / width-computation implementation and
documents where they agree and where they don't, so that Phase B
(``src/veriforge/semantics.py``) has a written spec to build to instead of
guessing which of six copies is "the real" behavior.

The seven implementations named in the work plan collapsed to six distinct
call sites at Phase A time (``analysis/width_inference.py`` used to delegate
to ``analysis/const_fold.py`` via its own private ``_const_int``/
``_range_width`` wrappers). As of Phase G, all six delegate directly to
``semantics.py``, and width_inference's private wrappers were deleted
(its call sites now call ``const_fold.const_int``/``const_range_width``
directly) -- the six original call sites, as they stood at Phase A:

1. ``sim/scheduler.py``: ``_const_int``, ``_range_width``, ``_var_width``
2. ``sim/vm/compiler.py``: same three, near-identical copies
3. ``sim/compiled/_codegen_utils.py``: ``_const_int``
4. ``sim/compiled/codegen.py``: ``_range_width``, ``_var_width``
5. ``analysis/width_inference.py`` + ``analysis/const_fold.py``: ``_const_int``
   / ``const_int``, ``_range_width`` / ``const_range_width`` (same underlying
   implementation, exposed as two call sites)
6. ``sim/elaborate.py``: ``_eval_const_expr`` — the shared low-level
   primitive that (1)-(4) all fell back to for non-literal expressions
   (still true today, since ``sim/elaborate.py`` itself is out of scope).

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

**Original (wrong) hypothesis, corrected during Phase F**: this section
originally attributed the gap to `expr.resolved` being unpopulated for an
identifier used inside *another parameter's own default-value expression*
(as opposed to a module body statement). That was never actually verified
by inspecting the AST directly, and it's false: `analyze_design()` **does**
resolve `N`'s `Identifier.resolved` to its `Parameter` in this exact
position (confirmed directly: `N_ident.resolved` is the `N` `Parameter`
object, not `None`).

**Actual root cause** (found while migrating `const_fold.const_int` to
delegate to `semantics.const_int` in Phase F, when a couple of tests here
kept failing in the *opposite* direction from what this file predicted):
`analysis/const_fold.py`'s old `const_int`'s `FunctionCall` branch only
evaluated the call if `expr.is_system` was `True`. The real parser does
**not** set `is_system=True` for `$clog2`/`$bits`/etc. parsed from source
text (verified directly: a parsed `$clog2(N)` call has `is_system == False`,
only its `name` starts with `$`) — `is_system` only comes out `True` for
hand-built `FunctionCall` nodes in tests that pass it explicitly, which is
exactly what this file's own fixtures and `test_const_fold.py`'s fixtures
do, masking the bug from both test suites. `sim/elaborate.py`'s
`_eval_const_expr` never had this bug — its `FunctionCall` guard is
`expr.is_system or expr.name.startswith("$")`, so it works on real parsed
input regardless of `is_system`. `.resolved` was a red herring throughout.

**Resolution**: `semantics.const_int`'s `FunctionCall` handling
(`_eval_const_func`) dispatches purely on `expr.name` (never consults
`is_system` at all), so it isn't exposed to this bug in either direction.
`const_fold.const_int` delegating to it in Phase F fixes this gap as a side
effect — for both the `env`-dict path (Phase C-E's engines already worked
around it via `elaborate._eval_const_expr`'s more permissive guard) and the
`.resolved`-based path (`const_fold`/`width_inference`, which had no such
workaround and were the only two implementations actually exhibiting the
bug).

**Difference 2 — ascending (unusual but legal) range `[0:7]`.** IEEE
1364-2005 permits declaring a net/port with the MSB *numerically smaller*
than the LSB (e.g. `output [0:7] busA;`, bit 0 is the MSB) — width is always
`abs(msb - lsb) + 1`, never negative.

- `sim/compiled/codegen.py`'s `_range_width` and
  `analysis/width_inference.py`'s (via `const_fold.const_range_width`)
  correctly use `abs(...)` and return `8`.
- `sim/scheduler.py`'s and `sim/vm/compiler.py`'s `_range_width` fast path
  (`int(r.msb.value) - int(r.lsb.value) + 1`, no `abs()`) returned `-6` for
  `[0:7]` — a **latent, pre-existing bug**, not something introduced by this
  characterization pass. It had apparently never been hit by the existing
  test suites (no test declared an ascending-order range), which is exactly
  the kind of gap this phase exists to surface.

**Resolution**: `semantics.range_width` uses `abs(msb - lsb) + 1`
unconditionally (matching the compiled engine and width_inference). Phase C
(reference scheduler migration) and Phase D (VM compiler migration), both
done, fixed scheduler.py's and vm/compiler.py's copies as a side effect;
this is flagged explicitly since it's a genuine bug fix bundled into a
refactor, not a pure behavior-preserving rename. All four implementations
now agree.

**Difference 3 — non-constant expressions: `None` vs. raised `ValueError`.**
`sim/elaborate._eval_const_expr` *raises* `ValueError`/`TypeError` for an
expression it cannot evaluate (e.g. one that reads an actual signal, not a
parameter) — this is intentional; it is the low-level primitive, and all
other wrapper functions (`scheduler`/`vm`/`compiled_utils`'s `_const_int`,
`const_fold.const_int`, `width_inference`'s own call sites) catch this
internally and return `None` uniformly.

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
agrees across every implementation as of Phase F (all six delegate to
`semantics.py` for `_const_int`/`_range_width`/`_var_width` as of Phases
C-F; see each Phase's Result note in the work plan).**

**Difference 4 — bitwise `~`/`~^`/`^~`/reduction ops on a bare constant
with no known width (found mid-Phase-F, not by this Phase A pass).**
`analysis/const_fold.py`'s `_unary_op`/`_binary_op` and
`sim/elaborate.py`'s `_UNARY_OPS`/`_BINARY_OPS` (which items 1-4 above all
fall back to for non-literal expressions) actually disagree here:

- `~0`: const_fold gives `-1` (raw Python two's-complement semantics);
  elaborate.py gives `4294967295` (fakes a 32-bit width, matching Verilog's
  default unsized-integer width for its own generate-block/genvar use case).
- Reduction `&`, `~&`, `~^` on a bare int (e.g. unary `&0xFF`): const_fold
  correctly returns `None` ("cannot determine without width" — genuinely
  ambiguous without a declared bit count); elaborate.py guesses using
  `v.bit_length()` as a width proxy, which is wrong in general (e.g. a
  3-bit signal holding `3` has `bit_length() == 2`, not 3).
- Binary `~^`/`^~` (XNOR): same masking-vs-raw disagreement as unary `~`.
- `/`, `%` by zero: const_fold returns `None`; elaborate.py's `_BINARY_OPS`
  returns `0`.

This slipped past Phase A's fixture list (~60 expressions, none of which
exercised a bare `~`/reduction-op/XNOR/div-by-zero on a constant) and was
only caught while migrating `const_fold.const_int` itself to delegate to
`semantics.const_int` in Phase F, when this file's own long-standing
`tests/test_analysis/test_const_fold.py` (which predates this
characterization) started failing.

**Resolution** (confirmed with the user mid-Phase-F): `const_fold.py`'s
behavior is authoritative here, not `elaborate.py`'s — `semantics.const_int`
was changed to match const_fold (raw `~`/`~^`/`^~`, `None` for
width-ambiguous reduction ops and for division/modulo by zero).
`sim/elaborate.py` itself is unchanged (out of migration scope; nothing
currently delegates to it), so this only affects `semantics.py`'s own
behavior, which items 1-4 above now delegate to as of Phases C-F.
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
        "width_inference": wi_module.const_int(expr),
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


def test_const_int_clog2_derived_parameter_now_agrees_everywhere():
    """Difference 1 (see module docstring): was a real bug in
    const_fold.py's FunctionCall dispatch (trusted `is_system`, which the
    real parser doesn't set for `$clog2`), fixed in Phase F by delegating
    to `semantics.const_int` (which dispatches on `expr.name`, matching
    `elaborate._eval_const_expr`'s more permissive guard). All six
    implementations now agree."""
    top = _parse()
    env = _build_param_env(top)
    idx_bits_expr = next(p for p in top.parameters if p.name == "IDX_BITS").default_value

    assert scheduler_module._const_int(idx_bits_expr, env) == 4
    assert vm_compiler_module._const_int(idx_bits_expr, env) == 4
    assert compiled_utils_module._const_int(idx_bits_expr, env) == 4
    assert _eval_const_expr(idx_bits_expr, env) == 4
    assert wi_module.const_int(idx_bits_expr) == 4
    assert const_fold.const_int(idx_bits_expr) == 4


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
        "width_inference": wi_module.const_range_width(rng),
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


def test_range_width_transitively_agrees_via_the_clog2_fix():
    """A range referencing a `$clog2`-derived parameter (port idx's
    [IDX_BITS-1:0]) transitively exercises Difference 1's fix:
    width_inference's chain now evaluates IDX_BITS's own default value
    ($clog2(N)) correctly, via the same Phase F const_fold.const_int fix as
    test_const_int_clog2_derived_parameter_now_agrees_everywhere, just
    reached via a range instead of a direct parameter lookup."""
    top = _parse()
    env = _build_param_env(top)
    idx_range = next(p for p in top.ports if p.name == "idx").width

    assert scheduler_module._range_width(idx_range, env) == 4
    assert vm_compiler_module._range_width(idx_range, env) == 4
    assert compiled_codegen_module._range_width(idx_range, env) == 4
    assert wi_module.const_range_width(idx_range) == 4


def test_range_width_ascending_range_now_agrees_everywhere():
    """Difference 2 (see module docstring): was a latent bug where
    scheduler.py's and vm/compiler.py's `_range_width` fast paths lacked
    `abs()`, giving a negative width for a legal ascending [0:7] range.
    Fixed in item 4.2 Phases C and D (both migrated to delegate to
    `semantics.range_width`, which uses `abs(msb - lsb) + 1`
    unconditionally) -- all four implementations now agree."""
    from veriforge.model.expressions import Literal, Range

    ascending = Range(Literal(0), Literal(7))

    assert compiled_codegen_module._range_width(ascending, {}) == 8
    assert wi_module.const_range_width(ascending) == 8
    assert scheduler_module._range_width(ascending, {}) == 8
    assert vm_compiler_module._range_width(ascending, {}) == 8


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
