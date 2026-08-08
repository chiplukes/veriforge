# Known Issues

## Test suite

### tests/test_sim/compiled/ — runtime and cache size

**Status**: Partially addressed (May 2026). Split from a single 62k-line
`test_compiled.py` into a feature-organized package (July 2026, work plan
item 2.5) — same tests, same collected count, now spread across
`tests/test_sim/compiled/*.py`.

`tests/test_sim/compiled/` is the compiled-engine regression suite.

#### Test count and runtime

The bulk of the tests live in `TestWideSignalExternalIO` (~3843 parametrized
tests across wide-signal ops and values). They are tagged `@pytest.mark.slow`
and **skipped by default**. Use `--run-slow` to include them:

```
uv run pytest tests/test_sim/compiled/ --run-slow
```

Full count with slow tests enabled: **4516 tests** (down from 6304 after Wave F-4
redundancy reduction on May 2026). Each slow test compiles a unique Cython module
(~5s first-run, <1s on cache hit).

#### Parallel execution with pytest-xdist

`pytest-xdist` is included in the `test` optional-dependency group. Each test
compiles a uniquely named Cython module (content-hash keyed) so parallel workers
never collide:

```
# fast path (skip slow): all CPU cores
uv run pytest tests/test_sim/compiled/ -n auto

# full suite with slow tests: parallel over all cores
uv run pytest tests/test_sim/compiled/ -n auto --run-slow
```

#### Cache size

`.cycache/` content-hashes compiled `.pyd` files per module. The full slow suite
generates ~4500 unique entries. Wave F-4 reduced that by ~1788 entries (28%).
Use `--clear-cython-cache` to wipe and rebuild from scratch.

#### Known xfail tests

- `TestWideSignalExternalIO::test_wide_posedge_signal_probe_cross_engine` —
  **xfail (May 2026)**: posedge on >64-bit signals is not supported in the
  compiled engine (`NotImplementedError`). Marked strict xfail.

## Simulator

### vm-fast: native heap-corruption crash on a wide-signal module (undiagnosed)

**Status**: Open, not investigated beyond isolating a minimal repro.
**Found**: August 2026, incidentally, while verifying the fuzzer's new
Icarus-first-activation-artifact filter (`random-verilog-gen` branch) —
not a targeted crash hunt.
**Severity**: High in kind (native heap corruption, not just a wrong
value — `glibc`'s `free(): invalid next size (fast)` abort, which means
some earlier write already corrupted heap metadata before the crashing
`free()` call; the actual out-of-bounds write happened upstream of where
the process aborts) but apparently narrow in practical scope so far: only
`vm-fast` crashes, `reference` and `vm` handle the identical module and
stimulus cleanly, and only one module in a 300-seed sweep triggered it.

**Repro**: `uv run python -m veriforge.fuzz --repro 91` (seed 91, this
branch's grammar-driven fuzzer) — reproduces cleanly and immediately with
`--engines reference vm-fast --no-icarus` (Icarus and the `vm` engine are
not needed to trigger it; `reference` alone is fine). Generated module
(103/113-bit inputs, a 127-bit output built from nested concatenations,
ternaries, reductions, and signed casts):

```verilog
module t (
    input [102:0] i1,
    input [112:0] i2,
    input signed [7:0] i3,
    output [126:0] o4,
    output signed [55:0] o5,
    output signed [1:0] o6
);
    assign o4 = {$unsigned($unsigned(i2)), {$signed(o5 == i2), $unsigned(o6[32'd1:32'd0]) ? {i3, o6[32'd0], o5[32'd3]} : o6[32'd0]}, i3[32'd2:32'd0] ? {o6, i2 != o6, -i3[32'd4:32'd2]} : ~o6[32'd1]};
    assign o5 = $unsigned(i3) ? ~i3[32'd2] : i1[32'd25] == o6[32'd1];
    assign o6 = ^i1[32'd15:32'd3];
endmodule
```

Not yet root-caused: suspect a wide-value (>64-bit) scratch/stack buffer
in `sim/vm/_interp_fast.pyx` sized or indexed incorrectly for this
specific nesting shape (127-bit destination, multiple >64-bit
intermediate concat/ternary results) — this would be the `vm-fast`
analogue of the `compiled` engine's own long-running "64-bit signal width
limit" gap (see the entry below), but is a *crash*, not a silent wrong
value, and hasn't been connected to that entry's root cause or scope.
Deliberately not investigated further per explicit user direction
("just document it for now") — the next step, if picked up, should be
bisecting which piece of the expression (the 127-bit concat destination,
the nested ternary, or the reduction) actually triggers the corruption,
e.g. by trying reduced variants of the module above under `vm-fast`
directly (bypass the fuzzer entirely once isolated).

### Fresh fuzzer mismatches (August 2026 survey, `random-verilog-gen` branch) — first-pass triage only

**Status**: Mostly root-caused, one large fix still pending. Of the
original 10 mismatched seeds: 4 are confirmed genuine Icarus-specific
bugs (2166, 2208, 2219, 2261), 1 is fully explained as a downstream
propagation of an already-known Icarus artifact (2154), 1 was a genuine
bug in THIS codebase's own engines, found and FIXED across all four
engines (2197 — `$signed`/`$unsigned` ignoring an enclosing ternary's own
combined signedness), 2 (2182, 2102) plus part of a third (2262's `o9`)
are now root-caused to a SECOND genuine bug in this codebase's own
engines — `_is_fixed_self_determined()`'s special-casing of `~`/unary
`-` for comparison/reduction/`&&`/`||`/`!` operands appears to be
backwards (see seed 2182's entry below for the full truth-table
characterization) — but this one is NOT YET FIXED, deliberately, given
its size (a large, multi-engine, heavily-cited piece of prior work with
follow-on fixes built on top of it). That leaves 2243 genuinely
unexamined. Separately, a genuine bug was found and fixed in the
fuzzer's own test harness (x-contaminated stimulus was silently sent to
Icarus as `z` instead of `x` — see below) — a real, confirmed fix on its
own merits, but it does not explain any of the 10 seeds: every case
re-verified after the fix still mismatched identically. Do not assume a
direction of fault (our engine vs. Icarus) without redoing the same
careful, Icarus-verified methodology as work plan item 2.7's multi-wave
campaign — and see the "if this batch is picked up" note at the end of
this section for a hard-won caution about re-verifying prior "confirmed
against Icarus" citations from scratch before trusting them (this
happened TWICE in this session alone: once for seed 2197's fix, once for
seed 2182's root cause).

**Source**: a 400-module survey (`reference`+`vm` engines, seeds
2000-2400, `--engines reference vm`) run to validate the new
Icarus-first-activation-artifact filter (see `_runner.py`'s
`_is_icarus_first_activation_artifact`) found 11 genuine (non-filtered)
mismatches. All are `[iverilog]` mismatches — i.e. our own engines agree
with each other; only the Icarus cross-check disagrees. Reproduce any of
these with `uv run python -m veriforge.fuzz --repro <seed>` (prints the
module and each engine's per-vector output; does not itself re-check
against Icarus — see the confirmed entries below for how to get Icarus's
raw output directly).

**Fixed: fuzzer harness bug — x-contaminated stimulus was driven into
Icarus as literal `z`, not `x`.** `FuzzRunner._value_to_verilog` chose
between emitting `'x'` or `'z'` for a masked (ambiguous) bit based on
`value.val`'s own bit at that position — but `Value.__init__`
unconditionally zeroes `val` wherever `mask` is set (`val & wmask &
~mask`), so that bit is *always* 0 for any ambiguous bit, meaning the
`'x'` branch was unreachable dead code and every x-contaminated stimulus
bit (roughly 15% of driven inputs per `_gen_stimulus`) was silently sent
to Icarus as a literal `z` instead. This simulator's `Value` model has no
z state distinct from x at all (see "x and z share one representation"
above) — the codebase-wide intent for a masked bit is `x`, and Icarus
*does* distinguish z from x, so this was quietly feeding Icarus a
different (genuinely tri-state) scenario than intended on every x-vectored
run. Fixed by always emitting `'x'` for a masked bit and confirmed correct
by direct inspection (constructing a `Value` with a masked bit and
checking the emitted literal). **This does NOT explain any of the ten
mismatched seeds** — re-verified all six that still had unconfirmed
mismatches (2102, 2154, 2197, 2243, 2261, 2262) with `FuzzRunner._compare`
directly (not a hand-rolled string comparison — see the caution below)
before and after the fix, and every one is byte-for-byte identical either
way. (An earlier draft of this note incorrectly claimed the fix resolved
seed 2243, based on a quick verification script that used a *substring*
check — `icarus_value in reference_value_repr` — instead of exact value
comparison; since the two values share a long common suffix, that
substring check produced a false "0 mismatches" result. Always use
`FuzzRunner._compare()` itself, or exact `Value.__eq__`, never a
string-containment shortcut, when checking whether two `Value`s actually
match.) The fix is still worth keeping (it's a real, independently
confirmed bug — every future x-vectored fuzzer run now drives genuine `x`
instead of accidentally-`z`), but it turned out to be orthogonal to this
whole batch of mismatches. A fresh full survey re-run (to see whether the
fix changes the *aggregate* mismatch rate on seeds not yet individually
examined) has not been done.

**Explained by the already-confirmed first-activation artifact (seed
2154) — a real gap in the fuzzer's filter, not a new Icarus bug.** `o11`
depends on `r8` (`reg signed [1:0] r8;`, never assigned anywhere in the
module — permanently undriven, always x). Checked `o11` directly (not
just the `o10` signal `mismatches.txt` reported): Icarus's `o11` is fully
ambiguous (all 65 bits `x`) on every vector, exactly the already-known
first-activation artifact — but it's on the `o10` signal (`o10 = {2{w6}}`,
`w6` includes `^o11[32'd16]` as one of three concatenated members mixed
with real, mostly-defined bits from `i1`) that the mismatch actually
surfaces, because `o10` is only ever *partially* ambiguous (one or a few
extra bits versus our engine), never *fully* ambiguous — so
`_is_icarus_first_activation_artifact`'s strict "Icarus reports the
signal as fully ambiguous" requirement never matches on `o10` itself,
even though the underlying cause is identical. **This means the filter
has a real, narrower-than-intended scope**: it only catches a first-
activation artifact on the exact signal that's directly stuck, not on any
signal downstream of it that mixes in other, correctly-varying data. The
filter's OTHER safety condition (constant value across every mismatching
vector) still holds here too (`^o11[16]` never changes since `r8` never
changes), so relaxing the "fully ambiguous" requirement to "Icarus's mask
is a strict superset of ours" while keeping the constant-value guard
would likely generalize this correctly — not attempted here, flagged as
a natural follow-up to `_runner.py`'s filter rather than a new
known-issue investigation. Seed 2154 itself needs no further work; it's
fully explained.

**Confirmed Icarus-specific bug (seeds 2166 and 2219 — same root cause)**:
a division whose divisor expression reduces to the constant `1`
(regardless of the actual input values feeding it) computes correctly on
its first evaluation, then silently returns `0` on every later
re-evaluation of the identical statement, even though the dividend keeps
changing. Confirmed in two independent shapes: seed 2166's continuous
assign with a very wide (226-bit) dividend, and seed 2219's clocked
`always @(posedge clk)` assignment with only a 14-bit dividend — the
width is NOT the trigger (2219 disproves that theory: 14 bits still
reproduces it), so this is a general division-by-derived-constant
re-evaluation bug in Icarus, not specifically a wide-value one. Minimal
repro (continuous-assign form):

```verilog
module t (input signed [112:0] i2, output [106:0] o4);
    output signed [64:0] o5;
    assign o5 = i2[13];
    assign o4 = -{i2, i2} / (o5 | 1'b1);   // o5|1'b1 is always exactly 1
endmodule
```
Driving `i2` with three different values across `#10` steps: Icarus gives
the correct result on the first evaluation, then `0` for every subsequent
one. Isolating just the negation (`-{i2,i2}` alone) or just the division
by a bare `1'b1` constant (without negation) does NOT reproduce this in
the 2166 shape — only the combination, in a continuous assign, across a
re-evaluation, does; the clocked 2219 shape reproduces with a much
simpler `r5[34:21] / (~(|i2) | 1'b1)` (14-bit dividend, no negation
involved at all), confirming negation isn't required either. Not
investigated further (division re-evaluation caching bug inside Icarus's
own arithmetic, most plausibly) — flagged so a future differential-fuzzer
run doesn't waste time re-diagnosing the same thing under a different
seed.

**Confirmed second, distinct Icarus-specific bug (seed 2208)**: a
compound nested-ternary-then-AND expression, evaluated directly, gives a
spuriously *more resolved* (wrong) x-mask than the same logic gives when
split into separate intermediate wire assignments — a constant-folding/
simplification defect under x-propagation, not a re-evaluation timing
issue like seed 2166. Minimal repro: with `i1` driven `1'bx` (1-bit
signed) and `i2`/`i3` fully defined,

```verilog
module t (
    input signed [0:0] i1, input signed [54:0] i2, input signed [127:0] i3,
    output [127:0] outer_tern, output [25:0] xorval,
    output [62:0] o4_direct, output [62:0] o4_indirect
);
    assign outer_tern = (i1 & i3) ? (i1 ? i3 : i1) : i1;
    assign xorval = i2[41:16] ^ i2[41:24];
    assign o4_direct   = (i1 & i3 ? i1 ? i3 : i1 : i1) & (i2[41:16] ^ i2[41:24]);
    assign o4_indirect = outer_tern & xorval;
endmodule
```
`o4_indirect` (built from the already-materialized `outer_tern`/`xorval`
wires) gives the textbook-correct 4-state AND result — its mask exactly
equals `xorval`'s own bit pattern (`outer_tern` is provably all-x here, so
AND-with-value's mask should just mirror the value's 1-bits) — and this
matches our reference engine's answer for the equivalent expression
exactly. `o4_direct` (the literal nested expression, structurally
identical to the fuzzer's original module) gives a strict subset of that
mask instead, i.e. Icarus resolves some bits to a definite value that a
correct 4-state AND cannot resolve. Confirmed with `%b` (bit-exact);
`%h`'s nibble-granularity x-display initially made this look like it
might *match* our engine (a nibble reads as `x` if any bit within it is
unknown) — re-derive with `%b` before trusting any `%h`-based comparison
on a mismatch like this one.

**Confirmed third, distinct Icarus-specific bug (seed 2261) — a
sequential-feedback variant of the "compute once, then frozen forever"
family.** `r5 <= ^o7;` inside `always @(posedge clk)`, where `o7` (a
continuous assign) itself reads `r5` (via `{2{{2{clk, r5[99], r5}}}} |
...`). Traced `r5` via hierarchical reference (`dut.r5`) across 9 clock
edges with the actual fuzzer-driven stimulus (`clk` toggling normally,
other regs/inputs changing every cycle, confirmed via a parallel `r4`
trace showing real per-cycle movement): Icarus's `r5` is bit-for-bit
IDENTICAL across all 9 edges (`128'h00...0x`, only bit 0 ever
ambiguous) — it never updates again after whatever its first evaluation
produced, even though `o7` (its own input) is read fresh every edge and
nothing about the design prevents `^o7` from changing. Our reference
engine correctly re-evaluates `r5` (and downstream `o7`) each cycle,
converging to a fully-defined value once real (non-x) data has flowed
through enough cycles — which is what a conformant simulator should do.
Not identical in mechanism to the confirmed division bug (2166/2219 —
no division here) or the compound-expression bug (2208 — no ternary
chain here), but the same *shape* as both: Icarus correctly computes
something on a first pass and then silently never revisits it. Combined
with the first-activation artifact (a THIRD "computes once, sticks"
shape, already filtered separately in the fuzzer harness), Icarus
appears to have a family of related x-propagation/re-evaluation defects
around self-referential and sequential-feedback constructs, not one
single bug — worth keeping in mind rather than assuming each new "stuck
at first value" mismatch needs its own fresh diagnosis from scratch.

**FIXED: a genuine, previously-shipped-but-wrong bug in OUR OWN engines
(seed 2197) — `$signed`/`$unsigned` ignored an enclosing TernaryOp's own
combined signedness.** Unlike every other case in this batch, this one
turned out to be a real bug in this codebase, not an Icarus quirk — and
it directly contradicted an already-shipped, "already-Icarus-verified"
fix (`sim/evaluator.py`'s FunctionCall handling, mirrored in `sim/vm/
compiler.py` and `sim/compiled/_expr_emitter.py`/`_wide_emitter.py`),
discovered while root-causing why `o8 <= {...} ? $signed($unsigned(clk))
: r5[32'd13:32'd12];` gave `16'hFFFF` (our reference) instead of Icarus's
`16'h0001`.

Root cause: `$signed(...)`/`$unsigned(...)` was hardcoded to always
sign-/zero-extend based on its OWN name, in all four engines,
unconditionally discarding any `signed_override` passed in from an
enclosing TernaryOp's own combined signedness (IEEE 1364-2005 §5.5.1:
signed only if BOTH branches are signed) — directly violating `eval()`'s
own documented contract ("*signed_override*, when not None, replaces
`_expr_signed()` for every extension decision"). The prior "fix" that
introduced this behavior cited `{3{(a0 ? $signed(a4[4:2]) : a3)}}` as
Icarus-confirmed proof that `$signed` must always win — re-verified this
exact case directly against Icarus and found the original verification
had (accidentally) declared `a3` as `signed` instead of its real
`unsigned` declaration (from `test_differential.py`'s `FIXED_SIGNALS`),
making it a degenerate test where both rules happen to agree and prove
nothing. With `a3` correctly `unsigned`, Icarus zero-extends — the
ternary's own combined signedness wins, confirming the ORIGINAL,
pre-"fix" behavior was actually right and the "fix" itself introduced the
regression seed 2197 tripped over. This is now the second time in this
same triage session that a previously-"confirmed" verification turned out
to have a data-entry error in the test setup (see the earlier, still-
unresolved seed 2182 investigation for the first) — always re-derive from
a from-scratch minimal repro with the REAL signal declarations before
trusting a cited "confirmed against Icarus" claim, rather than assuming
prior verification work is infallible.

Fixed in all four engines by making the sign/zero-extension decision use
`signed_override if signed_override is not None else (name == "$signed")`
instead of unconditionally `name == "$signed"`:
- `sim/evaluator.py`: the `$signed`/`$unsigned` `FunctionCall` branch in
  `eval()`.
- `sim/vm/compiler.py`: the identical branch in `_compile_expr` (fixes
  both `vm` and `vm-fast`, which share this compiler).
- `sim/compiled/_expr_emitter.py`: `_emit_func_call` (VALUE side, gained a
  new `signed_override` parameter threaded from `_emit_expr`'s
  `FunctionCall` case) and `_emit_mask_expr`'s `FunctionCall` branch (MASK
  side); `_emit_ternary_value_mask_exprs` now threads its already-computed
  `t_signed_override`/`f_signed_override` into the mask-side calls too
  (previously only the value side received them).
- `sim/compiled/_wide_emitter.py`: `_emit_wide_expr_to_scratch`'s
  `FunctionCall` case (`signed_override` was already in scope in this
  function, used by the neighboring `Replication` case, but the
  `FunctionCall` case never consulted it).

Verified: a minimal isolated repro (`o8 = clk ? $signed($unsigned(clk)) :
r5slice;`, `r5slice` unsigned) gives `16'h0001` on all four engines,
matching Icarus, after the fix (was `16'hFFFF` on reference/vm/vm-fast/
compiled before). Full regression: `test_differential_functions.py`/
`test_compiled_edge_shapes.py`/`test_assignment_matrix.py` (1039 passed),
`test_differential.py`/`test_differential_statements.py` with
`VERIFORGE_DIFF_COMPILED=1` (23 passed), the full `tests/test_sim/
compiled/` suite (783 passed, only the 1 known pre-existing
`test_or_chain_max_line_length` failure), and a full-repo `-n 8` sweep
(pending at time of writing — see the session's own final report for the
outcome). Seed 2197 itself needs no further work; it's fully fixed and
verified.

**Two remaining unexamined cases** (2243, 2262's `o8`/`o10` — already
separately attributed to the first-activation artifact; re-verified
directly against Icarus, with exact `Value` comparison, both before and
after the x/z harness fix above; neither is explained by that bug —
both mismatch identically either way; module source + Icarus's raw
`$display` output only, deliberately not annotated with a guessed root
cause beyond what's noted):

- **Seed 2102 — root-caused, same bug as seed 2182 below, NOT YET
  FIXED.** Clocked feedback (`r3 <= ~(r3 && ~|o6);`) where Icarus
  stabilizes `o7` (`r3[36:28]`, truncated into a 2-bit port) to `2'b11`
  from the first vector onward; our reference gives `2'b00`. This is
  `~` applied directly to `r3 && ~|o6` (a plain `&&`, fixed-self-
  determined, no `$signed` wrapper, no ternary) — exactly the bare-`~`-
  of-`&&` shape the seed 2182 truth-table sweep below characterizes.
  Not independently re-verified with the full sweep methodology (the
  2182 sweep used simpler synthetic modules), but the shape match is
  strong enough that this is very likely the same root cause, not a
  fourth one — treat as such rather than re-diagnosing from scratch.
- **Seed 2182 — RESOLVED via systematic truth-table sweep (root cause
  found, fix NOT yet applied — see below for why).** Follow-up
  investigation (triggered by the seed 2197 fix above) explains the
  earlier "contradictory evidence": the fifth-wave fix's own citation
  (`$signed(~({a0, a6, a0} && a7))`) is degenerate for a DIFFERENT reason
  than first suspected. `$signed(...)`'s argument is always evaluated at
  its OWN self-determined width (per the established, correct,
  independent "casts are self-determined" rule) — and `~`'s own
  self-determined width, when its operand is itself fixed-1-bit
  (`&&`/reduction/comparison/`!`), is ALSO exactly 1 bit. So inside a
  `$signed(...)`/`$unsigned(...)` wrapper, `~` is asked to extend to
  a width (1 bit) that already equals its operand's own width — there is
  NO extension to do either way, so `_is_fixed_self_determined()`'s
  special case is never actually exercised there, regardless of whether
  it's right or wrong. The fix's own motivating example could not have
  caught a bug in either direction.

  A systematic sweep (no `$signed`/`$unsigned` anywhere — bare
  assignments, a signed-but-uncast destination, and a ternary branch)
  across SIX different fixed-self-determined operators (`&&`, `||`, `!`,
  `==`, reduction-AND) and BOTH affected unary operators (`~`, unary `-`)
  found EVERY case matches "zero-extend the operand to context width
  FIRST (using the operand's own signedness, always unsigned per IEEE
  1364-2005 Table 5-22 for these operators), THEN apply `~`/`-` to the
  WHOLE extended value" — the exact opposite of what
  `_is_fixed_self_determined()` implements ("apply the operator at the
  operand's own fixed width, THEN extend the result"). Destination
  signedness does not affect this (a signed-declared-but-uncast
  destination gave the identical answer to an unsigned one in every
  test), and the ternary-branch position is affected identically to a
  bare top-level assignment (`sel ? ~(a && b) : unsigned_other` gives
  `16'hFFFE` for `a=b=1`, not the `16'h0000` the current fix predicts).

  **Conclusion**: `_is_fixed_self_determined()`'s entire special-casing
  for `~`/unary `-` (added in the "eleventh"/"fifth" wave, see its
  original entry above) appears to be actively wrong, and its own
  verification was structurally incapable of detecting this because every
  cited confirmation happened to be wrapped in a `$signed`/`$unsigned`
  cast, which neutralizes the special case's effect. The simpler,
  original (pre-fifth-wave) behavior — treating `~`/`-` as ordinary
  context-determined operators with no special case for a
  fixed-self-determined operand — appears to be correct after all.
  **Not yet fixed**: this touches a large, heavily-cited, multi-engine
  piece of prior work (present in `sim/evaluator.py`, `sim/vm/
  compiler.py`, `sim/compiled/_expr_emitter.py`, `_wide_emitter.py`, plus
  several MASK-side call sites that reference the same helper — e.g. the
  eighteenth-wave entries above), with follow-on fixes built on top of it
  across multiple later waves; per explicit user direction, this was
  investigated to full characterization but deliberately NOT fixed in
  this session, pending a decision on how to approach reverting/
  correcting something this broadly depended-upon. This is also almost
  certainly the same root cause as seed 2102 (`r3 <= ~(r3 && ~|o6);`,
  identical bare-`~`-of-`&&` shape) and seed 2262's `o9` sub-case.
- **Seed 2243** — clocked, `o10 = (i5 ^ r8[32'd4]) & i4;` (continuous,
  reads a clocked reg `r8`, itself fed back from `o10[32'd0]` among other
  things). The original "one missing/extra high bit, sign-extension"
  guess doesn't hold up: comparing all 11 vectors' raw Icarus output
  against the reference engine directly (not just the one
  `mismatches.txt` line), 6 of 11 vectors match exactly and 5 don't, with
  no consistent single-bit-position pattern — some mismatching vectors
  have our engine's value as a strict superset of extra high 1-bits, but
  not at a fixed bit offset each time. Since `r8` is itself computed from
  a self-referential expression involving `o10`'s own previous value,
  this looks more likely to be an accumulating divergence in `r8`'s own
  state from an earlier cycle than a direct bug in `o10`'s own
  combinational expression — not yet isolated to a single statement the
  way 2166/2208/2219 were. This module's fuzzer-generated stimulus
  includes literal x-contaminated bits, which raised (and ruled out) the
  x/z harness bug above as a candidate explanation — the mismatch is
  byte-for-byte identical before and after that fix. Not root-caused.
- **Seed 2262** — two `always @(*)` blocks reading a mutually-undriven
  internal reg (`r6`, never assigned anywhere) through `w4 = {3{i2}} &&
  ~&r6[1];` and `o9 = ~(r6[9:0] || w4);`. Re-checked with `_compare`
  directly: `o8`/`o10`'s mismatches ARE the already-known Icarus
  first-activation artifact and get auto-filtered (confirmed via
  `icarus_artifacts_filtered` incrementing by exactly the count of those
  entries) — only `o9` is a genuine remaining mismatch, and it's the
  SAME complement-like shape as seed 2182
  (`expected=0b0000000000000000 x=1 got=1111111111111110 x=1` — our
  engine gives a mostly-0 value, Icarus gives the bitwise complement
  mostly-1, both agreeing only on bit 0 being ambiguous). Treat as a
  probable third instance of whatever seed 2182's still-undetermined rule
  is, not a fourth distinct bug — do not investigate this one separately
  until 2182's rule itself is pinned down.
- **Seed 2381** — pure combinational, `o6 = ~(!(~i1[32'd61] % (i3[10:0]
  | 1'b1)));` — several nested context-determined operators (`~`, `%`,
  `!`, outer `~`) stacked together. Icarus gives `o6` all-1s (110 bits);
  ours gives only bit 0 set. Same family of concern as seed 2182 (see its
  entry above for why this needs a dedicated re-derivation, not a
  re-guess), with enough additional nesting (`%`'s own width
  contribution, `!`'s fixed-width result feeding a further `~`) that it
  may not even share the exact same root cause as 2182 — do not assume
  it does until 2182's own rule is actually pinned down.

**If this batch is picked up**: of the ten originally-mismatched seeds,
only **2243** is genuinely unexamined beyond a raw Icarus dump — every
other seed is either confirmed-Icarus, confirmed-and-fixed, or
root-caused-but-not-yet-fixed:
- 2166/2208/2219/2261: confirmed genuine Icarus bugs (three distinct
  mechanisms — division re-evaluation, compound-expression constant
  folding, and sequential-feedback freezing — but all in the same family
  of "computes correctly once, then never revisits it").
- 2154: fully explained as a downstream propagation of the already-known
  first-activation artifact (itself already filtered by the fuzzer
  harness).
- 2197: a genuine, now-FIXED bug in all four of THIS codebase's own
  engines (`$signed`/`$unsigned` ignoring an enclosing ternary's own
  combined signedness — see its entry above).
- 2182, 2102, and 2262's `o9` sub-case: a SECOND genuine bug in this
  codebase's own engines, root-caused via a systematic truth-table sweep
  but deliberately **NOT YET FIXED** given its size (see seed 2182's
  entry above for the full characterization and exact fix locations to
  touch — `_is_fixed_self_determined()`'s special-casing of `~`/unary
  `-` in `sim/evaluator.py`, `sim/vm/compiler.py`,
  `sim/compiled/_expr_emitter.py`, `_wide_emitter.py`, and the MASK-side
  call sites that reference the same helper). **This is the natural next
  thing to pick up** if continuing this batch: the root cause is solid
  (a clean, multi-operator, multi-position truth table, not a guess), the
  fix shape is simple to describe (remove the special case, let `~`/`-`
  behave like any other context-determined operator), but it needs the
  SAME careful one-engine-at-a-time-plus-full-regression discipline as
  item 2.6/2.7's own waves, given how much later work cites/builds on
  the mechanism being changed.
- 2262's `o8`/`o10`: already attributed to the first-activation artifact
  (auto-filtered by the fuzzer harness).

The fuzzer's own x/z harness bug (see above) was found and fixed along
the way but turned out to explain NONE of the ten seeds — don't assume
it's the cause of a mismatch just because a module happens to use
x-contaminated stimulus.

Five techniques proved essential during this pass and should be used
again: (1) always split a suspicious compound expression into
separately-assigned intermediate wires (`%b`, not `%h`) and compare
against the original literal expression before assuming *our* engine is
at fault — this is what confirmed both 2166 and 2208; (2) when checking
whether a `Value` matches, always use `FuzzRunner._compare()` or exact
`Value.__eq__` — never a hand-rolled string/substring comparison, which
can silently produce false negatives when two mismatching values share a
long common prefix or suffix (as happened once already during this
session's own triage, on seed 2243); (3) when a mismatching signal's
value stays suspiciously constant across many vectors despite other
state changing, trace every reg in its dependency chain via hierarchical
reference (`dut.<reg>`) across several clock edges directly in Icarus —
this is what confirmed seed 2261 was a frozen-feedback-register bug
(`r5`) rather than a downstream artifact of the already-confirmed
division bug (`r4`, which does change every cycle, ruling that theory
out before it led anywhere), and what confirmed seed 2154's `o11` (fully
ambiguous in Icarus, exactly the known artifact) as the true cause of
the `o10` mismatch that was actually reported; (4) when re-testing a
cited "confirmed against Icarus" claim from prior work, rebuild the
EXACT signal declarations the original test used (real width/signedness
table, not an approximation) before trusting or overturning the
citation — two separate previously-"confirmed" verifications in this
codebase (the `_is_fixed_self_determined()` citation for seed 2182's
family, and the `$signed`-always-wins citation that seed 2197's fix
corrected) turned out to rest on a test setup that didn't actually match
its own real signal declarations; (5) when a citation's test expression
is wrapped in `$signed(...)`/`$unsigned(...)`, check whether that
wrapper's own "argument is always self-determined" rule makes the
citation degenerate for what it's actually trying to prove — this is
what explained seed 2182's remaining contradiction after technique (4)
alone wasn't enough. A fresh full fuzzer survey (this session only
re-checked the specific already-known seeds against the x/z fix) would
still be worth running to see whether the aggregate mismatch rate
changes for seeds not individually examined here.

### Cython VM interpreter drift (vm-fast engine)

**Status**: Resolved (July 2026, work plan item 3.3). CI now builds the
extension and runs the VM test selection twice — with it built, and with
`VERIFORGE_DISABLE_CYTHON_VM=1` forcing the pure-Python path — requiring
both green, so future drift fails the build instead of being silently
masked (see `notes/developer_guide.md` §5, "Cython VM sync policy").

Root causes found and fixed in `_interp_fast.pyx`:

- **Memory read-after-write divergence** (`tests/test_sim/test_bench_native.py`,
  ~18 failures) — fixed to match `sim/vm/interpreter.py`'s memory NBA
  handling.
- **Narrow-path (<=64-bit) signed/unsigned C-arithmetic bugs** — several
  opcodes compared/shifted/divided `a.val`/`b.val` as signed `long long`
  instead of casting to `unsigned long long` first, so any 64-bit value with
  the MSB set (stored as a negative two's-complement `long long`) produced
  wrong results under Verilog's unsigned semantics: `OP_CMP_LT`/`LE`/`GT`/`GE`,
  `OP_SHR` (was also sign-extending instead of logical-shifting), `OP_DIV`,
  `OP_MOD`. (The signed variants — `OP_CMP_SLT` etc., `OP_SDIV`/`OP_SMOD` —
  already sign-extended correctly and were unaffected.)
- **`OP_SHL`/`OP_SHR` shift-by->=64 wraparound** — C's `<<`/`>>` on a 64-bit
  type is undefined for a shift count >= the type width (some platforms wrap
  the count mod 64 instead of producing 0); added an explicit `b.val >= 64`
  guard, mirroring the equivalent compiled-engine fix in item 2.3 Part B.
- **`OP_SIGN_EXT` any-x-taints-sign-extension** — checked "any bit of the
  operand is x" instead of specifically the sign bit, so an unrelated
  unknown low bit would incorrectly X-contaminate the entire sign-extended
  result. Also, the wide path (extending to >64 bits) was missing entirely —
  the new upper bit(s) were silently left unfilled. Both fixed to check only
  the sign bit (bit `width-1`), matching `Value.sign_extend`.

These were found via
`tests/test_sim/compiled/test_execution.py::TestNarrow64BitUnsignedOps`,
`tests/test_sim/test_assignment_matrix.py`, and
`tests/test_sim/test_compiled_edge_shapes.py` (all vm-fast-only failures,
confirmed pre-existing via `git stash` against item 2.4's commit, not a
regression from that item's `OP_ASHR` fix).

### Randomized differential harness (work plan item 3.4): bugs found and fixed

**Status**: Harness landed and green (July 2026) for its default scope
(reference/vm/vm-fast). See `tests/test_sim/test_differential.py`.

Building the harness (random expression trees over a fixed 8-signal set,
checked across engines) immediately surfaced a batch of real, previously
undetected correctness bugs — reference and vm/vm-fast had each independently
drifted from true IEEE 1364/1800 semantics in ways that happened to agree
with each other often enough to go undetected by the existing curated test
suites. All of the following were verified against Icarus Verilog before
fixing, and are now covered by the harness across 10+ seeds x 400-500 cases
each (see `VERIFORGE_DIFF_SEED`/`VERIFORGE_DIFF_CASES` in the test file):

- **Bit-select/part-select signedness** (`sim/evaluator.py`, `sim/vm/compiler.py`,
  `sim/compiled/_expr_emitter.py`) — a bit-select/range-select/part-select
  result is always unsigned per IEEE 1364-2005 §5.5.1, regardless of the
  sliced signal's own declared signedness; all three engines' `_expr_signed`
  incorrectly inherited the target signal's signedness. Also required the
  same fix in the compiled engine's separate `_emit_signed_widen` helper and
  two `PartSelect` code paths in `_expr_emitter.py`, which bypassed
  `_expr_signed` entirely with their own (unfixed) direct signedness check.
- **Conditional operator (`?:`) signedness** (`sim/evaluator.py`,
  `sim/vm/compiler.py`) — per IEEE 1364-2005 §5.5.1, a ternary's *own*
  combined signedness (signed only if BOTH branches are signed) governs
  every extension needed while evaluating whichever branch is selected — not
  that branch's own individual signedness, and not the branch's containing
  operator's usual rule. Fixed by threading a `signed_override` parameter
  through `eval()`/`_compile_expr()`, established fresh by each ternary for
  its own branches (verified against Icarus across several nesting shapes:
  nested unary +/-, nested binary ops, plain identifiers).
- **Comparison/logical operators wrongly context-determined**
  (`sim/evaluator.py`) — `==`/`!=`/`<`/`<=`/`>`/`>=`/`&&`/`||` produce a 1-bit
  result and must NOT have their operands extended to an enclosing
  assignment's width (only self-determined, matching each other) — the code
  had a comment saying so but didn't actually implement it, corrupting
  comparisons whenever the two operands' individual signedness differed.
- **`&&`/`||`/`==`/`!=` imprecise x-handling** (`sim/value.py`
  `logical_and`/`logical_or`/`_cmp`, `sim/vm/_interp_fast.pyx`
  `OP_LOG_AND`/`OP_LOG_OR`/`OP_CMP_EQ`/`OP_CMP_NE`) — all four naively
  treated "any operand has an x/z bit" as "result is x", when IEEE requires
  checking whether a *known* bit already resolves the result first (a
  known-1 bit anywhere makes `||`/logical-truth definitely true regardless
  of other unknown bits; a known-differing bit makes `==`/`!=` definitely
  resolved) — this is a simple bitwise check, unlike ordering comparisons
  (`<` etc.) which genuinely need full certainty and were left as-is.
- **`OP_TERNARY` wide condition** (`_interp_fast.pyx`) — the
  fully-defined-condition fast path read the condition's truthiness/definedness
  from the narrow `.val`/`.mask` stack slot unconditionally, never checking
  `wflag` — for a >64-bit (wide) condition, that slot doesn't hold the real
  data (it lives in `wv`/`wm`), so a wide condition could be silently
  misread, picking the wrong branch.
- **Shift-amount wide/huge-value handling** (`_interp_fast.pyx`, all of
  `OP_SHL`/`OP_SHR`/`OP_ASHL`/`OP_ASHR`) — the shift-amount operand's
  x-ness and value were always read from the narrow `.mask`/`.val` slot even
  when the shift amount itself was wide (>64 bits) or simply a value whose
  magnitude didn't fit a 32-bit `<int>` cast; both cases silently produced
  wrong or garbage results, and in one case (a raw C shift by a
  corrupted-via-truncation huge count) a segfault. Fixed by checking
  `wflag` for the shift amount and saturating (not truncating) an
  out-of-32-bit-range magnitude to a safe "definitely >= any width" sentinel.
- **`Value.__mul__`'s sum-width rule leaking through an enclosing
  context-determined operator** (`sim/value.py`, `sim/evaluator.py`) —
  ~~multiplication's own self-determined width is the SUM of its operand
  widths (IEEE 1364-2005 Table 5-22), correct when `*` is unconstrained~~
  **Correction (tenth wave, below): verified directly against the IEEE
  1364-2005 primary text that this claim was wrong all along —
  self-determined `*` is `max(L(i),L(j))`, the SAME row as `+ - / % & |
  ^ ^~ ~^`, with no sum-width exception anywhere in Table 5-22. The fix
  described below (narrowing via `_expr_self_width`'s already-correct
  max-based floor) was still the right fix and remains valid — only this
  one sentence's rationale was backwards; `Value.__mul__`'s sum-width
  behavior is better understood as a deliberate internal-precision
  detail of the arithmetic primitive, not a self-determined-width rule.
  This mislabeling is what caused `*`'s matching, still-live bug (the
  RESULT not being narrowed back to a requested self-determined width
  when `*` is the node being evaluated, as opposed to an OPERAND of one)
  to go unnoticed until the tenth wave.** But when `*` is itself an
  operand of a further context-determined operator
  (e.g. the left side of `>>`), the enclosing context must narrow the
  product to its own width before that operator runs, or the subsequent
  operation's zero/sign-fill lands at the wrong bit position once the
  oversized intermediate result is later truncated back down, corrupting
  x-precision. Fixed in `evaluator.py` via a new `_expr_self_width()`
  helper (self-determined width using the generic max-of-operands rule for
  every binary op, including `*`, mirroring `sim/vm/compiler.py`'s
  `_expr_width`) used as a floor: `target = max(context_width,
  _expr_self_width(operand))`, applied symmetrically (narrows OR widens) to
  BinaryOp/UnaryOp context-determined operands.
- **Compiled engine: same `*`-into-shift bug, in a separate legacy
  fast-path** (`sim/compiled/templates/{narrow_assign,narrow_stage}.pxi`) —
  `_whole_{assign,stage}_mul_{const,signal}_sh{l,r}` hardcode the sum-width
  rule directly (`prod_width = c.width[lhs] + c.width[rhs]`) in
  hand-written Cython, bypassing the (already-correct) `_expr_width()` used
  by the newer recursive wide emitter; a legacy-fast-path dispatch order
  intentionally routes `(a*b)>>N`/`(a*K)>>N` through these functions first.
  Fixed by narrowing `prod_width` to `max(dst_width, max(operand widths))`,
  matching the other two engines.
- **`_word_mask64`/`wmask` undefined behavior for negative width**
  (`sim/compiled/templates/narrow_accessors.pxi`) — uncovered by the fix
  above: once `prod_width` is correctly narrowed, a later word's "remaining
  valid bits" computation can go negative (meaning "no valid bits in this
  word"), and shifting by a negative C `int` is undefined behavior (some
  platforms mask the shift count to its low 6 bits instead of erroring,
  silently producing a garbage mask). Fixed by clamping `w <= 0` to return 0.
- **`Value.__lshift__`/`__rshift__` `OverflowError` crash** (`sim/value.py`)
  — a shift amount that's itself a huge (but validly-typed) value from a
  wide self-determined operand could make CPython's own big-int shift guard
  raise `OverflowError` before the result ever got masked down to width;
  added an `if other >= self.width: return Value(0, ...)` short-circuit
  (correct per Verilog semantics: any shift by >= the operand's width
  produces 0, avoiding ever constructing the oversized intermediate). Same
  fix applied to the `>>>` (arithmetic shift) operator's `sign_extend(width
  + shift)` construction in both `evaluator.py` and `sim/vm/interpreter.py`,
  which had the identical crash risk for a huge self-determined shift amount.

**Update (July 2026, work plan item 2.7 sub-item 3)**: the compiled-engine
`signed_override`-threading gap described above is now fixed — see the new
"Compiled-engine ternary/context-determined-operator codegen" section below,
which also documents a large family of related bugs across all four
engines (including, importantly, a REFERENCE-engine bug that had been
masquerading as compiled-engine divergences) that the differential
harness surfaced once it was finally run with `VERIFORGE_DIFF_COMPILED=1`
for real. The harness's default run still excludes the compiled engine
(opt in via `VERIFORGE_DIFF_COMPILED=1`) because a larger-than-default
case count (`VERIFORGE_DIFF_CASES=300`+) at alternate seeds still
surfaces further divergences of the same shapes — see that section's
"Residual gap" note. **When the harness reports a compiled-engine
divergence, check it against Icarus before assuming compiled is at
fault** — the harness's reference-engine oracle has itself had real bugs
in this area.

### Compiled-engine ternary/context-determined-operator codegen, and a wide family of related width/signedness/x-propagation bugs

**Status**: Substantially resolved (July 2026, work plan item 2.7 sub-item
3). The originally-scoped fix (threading `signed_override` through the
compiled engine's ternary codegen, mirroring `sim/evaluator.py`/
`sim/vm/compiler.py`) led to root-causing and fixing a much larger family of
real, independently-reproducible bugs across **all four** engines
(reference, vm, vm-fast, compiled) — the differential fuzzer harness
(`tests/test_sim/test_differential.py`) had never actually been run with
`VERIFORGE_DIFF_COMPILED=1` before, so none of these had been caught. All
fixes below were verified against Icarus Verilog and/or first-principles
IEEE 1364-2005 derivation before applying; the default-seed 10-batch
differential run (`VERIFORGE_DIFF_COMPILED=1 VERIFORGE_DIFF_CASES=100`) is
now fully green.

**Compiled engine — the original scope**:
- `_wide_emitter.py`'s `_emit_wide_expr_to_scratch` TernaryOp case now
  computes `own_signed` once and forces it (never the branch's own
  signedness) into both branches' recursive calls at the FULL destination
  width, matching the already-correct narrow-path pattern from item 3.4.
- `UnaryOp` (`~`/`-`) and `BinaryOp` (bitwise/arithmetic) cases in the same
  file now thread `signed_override` into their operand recursions, and
  (for `~`/`-`/`+`) recurse directly at the full `dst_width` rather than
  the operand's own self-width followed by a post-hoc wrap — computing
  `~x` at x's own narrow self-width then zero/sign-padding the *result* is
  not equivalent to extending x to the context width first and
  complementing all of it.
- The narrow (`_expr_emitter.py`) `_emit_expr`/`_emit_unary`/`_emit_binary`
  got the equivalent `signed_override` threading.

**Compiled engine — bugs found while verifying the above**:
- `RangeSelect`/`PartSelect`/struct-field/reduction-op/`!`/bitwise-op-result
  scratch fills, when `signed_override` forces sign-extension, called
  `wide_sign_extend(..., n_words, ...)` — filling all the way to the
  scratch array's max word count for the WHOLE statement, not just up to
  THIS call's own `dst_width`. Harmless when `dst_width` happens to equal
  `n_words*64` (the common top-level-assignment case, which is why the
  original sub-item-2 fix didn't catch it), but corrupts a small
  `$signed()`-wrapped concatenation MEMBER (e.g. `{a, $signed(b[3:0])}`)
  by smearing the sign fill into the *next* concat member's shared scratch
  words. Fixed with a new shared helper,
  `_WideEmitterMixin._wide_sign_extend_to_dst_lines()`, which sign-extends
  and then applies an explicit tail mask so the result is bounded to
  exactly `dst_width` bits (not just whole words) — `wide_sign_extend`'s
  own `n` parameter only understands whole words.
- `RangeSelect`/`PartSelect`'s `_emit_wide_expr_to_scratch` cases (and the
  two struct-field-access variants) previously had NO `signed_override`
  handling at all — always zero-filled beyond the slice's own width,
  wrong for `$signed(a4[24:19])` assigned into a wider destination.
- `_WIDE_BINARY_PRIMS` (bitwise `&`/`|`/`^`/`~^`/`^~`) recursed into each
  operand at the operand's OWN self-width instead of `max(both operands'
  self-widths)` — the same bug class as the (already-fixed) `~`/`-` gap,
  just for `BinaryOp` — e.g. `$signed(!a4) ^ a1` (`!a4` self-width 1,
  `a1` self-width 8) lost the sign-extension of the 1-bit operand before
  combining. Fixed by computing `op_width = max(lw, rw)` and recursing
  both operands there (matching `_expr_emitter.py`'s `_NATURAL_WIDTH_OPS`
  handling), then a separate post-hoc sign-extension of the RESULT (not
  each operand) to the enclosing `dst_width` using the whole BinaryOp's
  combined signedness.
- Reduction ops (`&`/`|`/`^`/`~&`/`~|`/`~^`/`^~`) and `!` had the identical
  "always unsigned, zero-fill beyond bit 0" gap when `$signed()`-wrapped —
  fixed the same way.
- `_WIDE_CMP_PRIMS` (`==`/`!=`/`<`/`<=`/`>`/`>=`) recursed into each
  operand using the OUTER destination's own word count (`n_words`) instead
  of `n_operands` (sized for `max(operand widths)`) — comparing a 234-bit
  concatenation against a 1-bit value truncated the wider operand's
  scratch array to 2 words when it needed 4, reading garbage beyond that
  and (in one case) corrupting an unrelated scratch slot badly enough to
  trigger a C stack-smashing abort. Fixed by using `n_operands` for the
  operand recursion and adding a new `_expr_max_internal_width()` scanner
  (recurses into every operand, not just each node's own self-determined
  RESULT width) so the top-level statement's scratch-array sizing
  (`_emit_wide_lhs_write_new`) also accounts for a comparison's internal
  operand width, not just the comparison's own always-1-bit result.
- `wide_cmp_eq`/`wide_cmp_ne` (in `_gen_wide_section.py`) set `dm[0] = 1 if
  has_x else 0` unconditionally, never checking whether a KNOWN mismatch
  had already resolved the comparison to a definite result — mirrors
  `Value._cmp`'s existing "==`/`!=` short-circuit" precision note
  (a known-differing bit resolves the comparison regardless of x/z bits
  elsewhere). Also switched the mismatch check from "skip the whole word
  if either word has ANY x/z bit" to a proper bit-level
  `(av[i]^bv[i]) & ~am[i] & ~bm[i]` check — a word can have some x/z bits
  and some known-mismatching bits at the same time.
- `&&`/`||`'s wide-path result mask (`_sc{slot}_m[0] = bl_m | br_m`) had
  the same missing-short-circuit shape — fixed using `wide_reduce_or`'s
  already-correct per-operand truthiness/definedness to implement the
  proper `Value.logical_and`/`logical_or` precision rule (a known-nonzero
  operand forces `||` definitely true regardless of the other operand's
  x/z bits; a known-EXACTLY-zero operand forces `&&` definitely false).

**Reference engine (`sim/evaluator.py`) — bugs found while cross-checking
against the compiled fixes above (these predate this work, independently
reachable, not introduced by it)**:
- `Concatenation`/`Replication`/`AssignmentPattern`'s positional-parts
  branch evaluated each part with `self.eval(p, ctx)` (width=0, the
  default) — leaving any context-determined operator WITHIN a concat/
  replication member (a nested `~`, arithmetic, or ternary) entirely
  unresized, since context-determined resizing is gated on a nonzero
  width being passed in. Each part is self-determined (IEEE 1364-2005
  §5.4.1): its OWN natural width is the context that should resize a
  nested context-determined operator within it. Fixed by evaluating each
  part with `width=_expr_self_width(part, ctx)`.
- `BinaryOp` bitwise ops (`&`/`|`/`^`/`~^`/`^~`) extended each operand
  straight to the OUTER enclosing `width` using that operand's OWN
  individual signedness — the same architectural bug as the compiled
  engine's `_WIDE_BINARY_PRIMS` gap above, and fixed the same way: combine
  at `op_width = max(both operands' self-determined widths)` first (each
  operand evaluated there, using its own signedness), THEN extend the
  RESULT separately to the outer `width` using the whole expression's own
  combined signedness. The naive "extend each operand straight to outer
  width" approach lets one signed operand's sign-extension (e.g. of an x
  value) smear across the whole outer width even when the operator's own
  combined signedness is unsigned.
- `_expr_self_width`'s `BitSelect` case unconditionally returned `1`,
  correct for a true scalar bit-select (`a[3]`) but wrong for unpacked-
  ARRAY element access using the same AST node shape (`arr[3]` where
  `arr` is `logic [31:0] arr[5]` — a full 32-bit element read, not a
  single bit). This is a long-standing latent bug (masked everywhere else
  `_expr_self_width` is used, since it's only ever consulted as a floor
  alongside an outer context width that already dominated the wrong `1`)
  that only became fatal once the new bitwise-op fix above relied on it
  as the SOLE determinant of `op_width` — confirmed via a real regression
  in `ibex_alu.sv`'s RV32B butterfly network (`invbutterfly_result &
  butterfly_mask_not[stg]`, an unpacked-array element read). Fixed by
  checking `ctx._memory_names`/`ctx._memories`, mirroring
  `sim/vm/compiler.py`'s `_expr_width`, which already made this
  distinction correctly.
- `_expr_self_width` didn't special-case `<<`/`<<<`: a left-shift's
  self-determined width needs `left_width + shift_amount` (for a constant
  shift amount), not `max(left, right)` — otherwise `hi << 32` (`hi` 31
  bits) is underestimated at 32 bits instead of 63, silently truncating
  the shifted-in bits once that underestimate is used as an operand's
  evaluation width. Confirmed via `tests/test_sim/test_compiled_edge_shapes.py`'s
  `seam*_overflow` cases (`lo | (hi << 32)`, the intermediate-overflow
  class from work item 2.2). Mirrors `sim/vm/compiler.py`'s `_expr_width`,
  which already had this special case.
- `_expr_signed`'s `UnaryOp` case only special-cased `!` as always-
  unsigned; all other reduction ops (`&`/`|`/`^`/`~&`/`~|`/`~^`/`^~`) fell
  through to "signed if operand is signed" — wrong, since reduction ops
  ALWAYS produce an unsigned 1-bit result regardless of operand
  signedness (IEEE 1364-2005 §5.5.1). `~& a4` (`a4` declared signed) on a
  non-all-1s value should give a defined `1` zero-extended into a wider
  context, but instead sign-extended to all-1s.
- `_expr_signed`'s `BinaryOp` case didn't special-case comparisons
  (`==`/`!=`/`<`/`<=`/`>`/`>=`) or logical ops (`&&`/`||`) — these ALWAYS
  produce an unsigned 1-bit result (IEEE 1364-2005 Table 5-22) regardless
  of operand signedness, but fell through to "signed if both operands
  signed." `(a == b)` with both operands declared signed, assigned into a
  wider destination, sign-extended a `1` result to all-1s instead of
  zero-extending to `1`.
- `_merge_xz` (used when a ternary's condition is x/z: bitwise-agreement
  merge of both branches per IEEE 1364-2005 Table 5-4) computed `agree =
  ~(a.val^b.val) & ~(a.mask^b.mask)` — treating two x/z bits as
  "agreeing" whenever their (val, mask) *representations* happened to
  match, which is always true since this codebase pairs x/z with a
  placeholder `val=0`. Two genuinely-unknown branches should stay
  unknown, not collapse to a defined `0`. Fixed to require BOTH operands
  known (`~mask`) AND equal, not just representation-equal.
  `semantics.py`, `sim/compiled/_expr_emitter.py`'s narrow ternary merge,
  and `_wide_emitter.py`'s `wide_mux` were all ALREADY correct (built or
  verified after item 3.4/2.6); only `sim/evaluator.py` had the bug.

**`sim/vm/compiler.py` (bytecode compiler) — same-shaped bugs, since it's
a structurally parallel (but independently written) engine**:
- `_expr_width`'s `BitSelect` case already correctly distinguished
  memory-element access from a true scalar bit-select (no bug there), but
  its `BinaryOp` case had the identical missing-shift-special-case bug as
  `_expr_self_width` above, AND `_expr_signed`'s `UnaryOp`/`BinaryOp`
  cases had the identical missing-reduction/comparison-always-unsigned
  bugs — fixed the same way. `_compile_expr`'s bitwise-op branch got the
  same `op_width = max(...)` restructuring as `eval()`'s.

**`sim/value.py` / VM interpreters — X-propagation precision bugs, found
alongside the above (not introduced by this work)**:
- `Value.reduce_and()` returned x whenever ANY bit was x/z, never checking
  whether a KNOWN-0 bit had already resolved the result to definite `0`
  (mirrors the ALREADY-correct `reduce_or`, which does check for a
  known-1). `sim/vm/interpreter.py` and `sim/vm/_interp_fast.pyx`'s
  `OP_RED_AND` (both its narrow and wide-condition branches) called
  `Value.reduce_and()`/had an inlined copy of the same bug and needed the
  identical fix (the compiled engine's `wide_reduce_and` primitive was
  already correct).
- `Value.logical_not()` (`!`) had the same shape: returned x whenever ANY
  bit was x/z, without checking for a known-1 bit first (which should
  force `!x` to definite `0`). `_interp_fast.pyx`'s `OP_LOG_NOT` had an
  inlined copy of the same bug.
- The compiled engine's own narrow-path mask computation for reduction
  ops (`_expr_emitter.py`'s `_emit_mask_expr` UnaryOp branch) had a
  BLANKET fallback (`return self._emit_mask_expr(expr.operand, ow)`,
  passing the operand's raw multi-bit mask straight through) for ALL
  unary ops including reductions — wrong for `&`/`~&` (needs the
  known-0-forces-definite check) and `|`/`~|`/`!` (needs the
  known-1-forces-definite check); only `~`/`+`/`-`/`^`-family reductions
  (which genuinely have no absorbing bit value) were fine with the
  blanket pass-through. Fixed with explicit per-op mask expressions.
- The compiled engine's narrow-path `==`/`!=` mask computation
  (`_emit_mask_expr`'s `BinaryOp` fallback, `lm | rm`) had the same
  missing-short-circuit gap as `wide_cmp_eq`/`wide_cmp_ne` above — a
  known-differing bit should resolve `==`/`!=` to a definite result
  regardless of x/z bits elsewhere; `<`/`<=`/`>`/`>=`/`&&`/`||` correctly
  keep the blanket `lm | rm` fallback (they genuinely need full certainty,
  matching `Value._cmp`'s non-`==` branch). The compiled engine's `&&`/
  `||` mask computation had the identical gap and needed the identical
  known-nonzero/known-zero short-circuit fix as the wide-path version
  above.

**Second wave (July 2026, same work-plan item, continued)**: a systematic
audit of every node-type branch in `_wide_emitter.py`'s
`_emit_wide_expr_to_scratch` (rather than continuing to fuzz-and-patch one
divergence at a time) found three more real compiled-engine gaps of the
exact same shape as above, all missed by the first pass because the
default-seed 100-case differential run happened not to exercise them:
- `Literal` never consulted `signed_override` at all — a declared-signed
  literal (`4'sb1000` = -8) whose own top bit is 1, or any literal used as
  a $signed()-wrapped/ternary-forced-signed operand, always zero-extended
  into a wider destination instead of sign-extending.
- `BitSelect`'s `signed_override` branch had the identical
  fill-boundary-uses-n_words-not-dst_width bug as the RangeSelect/
  PartSelect/struct-field/reduction cases already fixed, AND separately
  forced the extension fill to a defined 0 whenever the selected bit
  itself was x/z ("conservative", matching the OLD, since-corrected
  `wide_load_signal_s` choice) instead of propagating x/z into the filled
  region.
- `Concatenation`'s own aggregate result ignored an INCOMING
  `signed_override` entirely (individual concat MEMBERS correctly never
  see it, per IEEE — only the concatenation's own total value can be
  wrapped, e.g. `$signed({a, b})`) — always zero-filled beyond the
  concat's own total width instead of sign-extending when the whole
  concatenation was cast.

A new shared helper, `_WideEmitterMixin._wide_sign_extend_to_dst_lines()`,
replaced the various hand-rolled/duplicated fill-loop implementations
across all of these cases (Literal, BitSelect, RangeSelect, PartSelect,
struct-field ×2, reduction-ops, `!`, bitwise-op-result, Concatenation,
Replication) — it sign-extends via `wide_sign_extend` and then applies an
explicit tail mask, since `wide_sign_extend`'s own `n` parameter only
understands whole words and would otherwise over-fill a `dst_width` that
isn't a multiple of 64.

**A pre-existing REFERENCE-engine bug found while re-verifying the above**:
while chasing what looked like yet another compiled-engine divergence
(`{3{(a0 ? $signed(a4[4:2]) : a3)}}`, a replication of a ternary), Icarus
confirmed the COMPILED engine was actually already correct and the
REFERENCE engine (the differential harness's oracle) was wrong — an
important reminder that this harness's "expected" side is not infallible,
and a diverging result should be checked against Icarus before assuming
the compiled engine is at fault. Root cause, present in **both**
`sim/evaluator.py` and `sim/vm/compiler.py` (structurally parallel, same
bug independently): `eval()`/`_compile_expr()`'s `FunctionCall` dispatch
for `$signed`/`$unsigned` evaluates/compiles its argument SELF-DETERMINED
(no width, no signed_override) and returns that directly — which only
happens to produce the right answer when the `$signed(...)` call is the
assignment's own top-level RHS, where a SEPARATE post-hoc step
(`_maybe_sign_extend` in `executor.py`/`scheduler.py`, or the equivalent
statement-level sign-extend in the VM compiler) covers for it. One level
of nesting deeper — e.g. a ternary branch, `cond ? $signed(a4[4:2]) : a3`
— that top-level cover never runs, and the cast's own argument never gets
extended to the ternary's combined width at all. The SAME architectural
gap existed one layer further down: `eval()`'s `BitSelect`/`RangeSelect`/
`PartSelect` branches (a bit-/range-/part-select is always unsigned in its
own right per IEEE 1364-2005 §5.5.1, but a `$signed()` wrapper or an
outer ternary/bitwise-op's forced signedness still needs to sign-extend it
when the requested `width` is wider) ignored their `width`/
`signed_override` parameters entirely — fixed the same way, and
`sim/vm/compiler.py`'s equivalent `BitSelect`/`RangeSelect`/`PartSelect`
compilation got the identical `SIGN_EXT`/`RESIZE`-opcode-emission fix. The
compiled engine's own `$signed`/`$unsigned` handling (both narrow, via an
inline `_sign_ext(...)` wrap baked directly into the emitted expression
string at the point of use, and wide, via `signed_override` threaded all
the way through `_emit_wide_expr_to_scratch`) never had this gap — it
doesn't defer to a separate post-hoc statement-level step the way
reference/VM do, so nesting depth was never an issue there.

**Third wave (July 2026, same work-plan item, continued)**: continuing the
same per-node-type audit + bisect + Icarus-verify + fix-both-reference-and-
VM cadence, against the `VERIFORGE_DIFF_CASES=300` alternate-seed run
(progress: 8/30 → 13/30 after the second wave above):
- `Literal`'s hot-path cache lookup in `eval()` (`sim/evaluator.py`)
  returned the cached `Value` directly, ignoring `width`/`signed_override`
  — same shape as the `$signed`/`BitSelect`/etc. gap above, just for the
  cached-literal fast path. `sim/vm/compiler.py`'s `Literal` compile branch
  had the identical gap (never emitted a trailing `SIGN_EXT`/`RESIZE`).
- `Concatenation`/`Replication`'s own AGGREGATE result (as opposed to the
  self-width propagation INTO their parts, fixed earlier) ignored an
  incoming `signed_override` in both `sim/evaluator.py` and
  `sim/vm/compiler.py` — e.g. `{2{(a0 ? $signed({a1, a2}) : a3)}}` needs
  the concatenation's own combined value sign-extended once wrapped by the
  ternary's forced signedness, not just zero-extended.
- `UnaryOp`'s `!`/reduction-op (`&`/`|`/`^`/`~&`/`~|`/`~^`/`^~`) branch
  evaluated its OPERAND self-determined-but-uncontextualized (`width=0`,
  no `_expr_self_width`) in both engines — the operand is still
  self-determined (IEEE 1364-2005 §5.4.1: its own natural width is the
  context), so a nested context-determined operator within it (e.g. `~` in
  `!(~(cond ? a : b))`) never got resized before the outer op ran. Fixed by
  passing `_expr_self_width(expr.operand, ctx)` (reference) /
  `self._expr_width(expr.operand)` (VM) instead of leaving it at 0.

**Fourth wave (July 2026, same work-plan item) — the ambiguous-condition
"known-1-bit-forces-true" bug family**: bisecting a remaining `VERIFORGE_
DIFF_CASES=300` failure (`{2{a3}} ? (((|a6) ? {a6[14], a2[12:6], a6} :
a6[15]) ? ... : ...) : {3{(-a7)}}`) against Icarus found that **compiled**
was already correct and **reference** was wrong (per the "check Icarus
before blaming compiled" lesson above) — a genuinely new, distinct bug
shape from the first three waves. `TernaryOp`'s condition-truthiness check
in `eval()` used `cond.is_defined` (require EVERY bit defined) before
picking a branch, falling back to the x-merge path otherwise. That is too
strict: per IEEE 1364-2005 the conditional operator's condition is reduced
to boolean the same way `!`/`&&`/`||`/`|` (reduction) already are — a
KNOWN-1 bit ANYWHERE makes the condition definitely true regardless of
unrelated x/z bits elsewhere (`Value.reduce_or`/`logical_and`/
`logical_or`/`logical_not` already implement this correctly; `TernaryOp`
just never used it). E.g. a condition like `{a6[14], a2[12:6], a6}` with
`a2[12:6]` unknown but `a6` itself definitely nonzero must resolve to
definitely-true, not fall into the ambiguous bitwise-merge path — the old
code merged the branches instead, producing a mostly-x result where Icarus
(and now every engine) gives a fully-defined one. Once this was understood
as a systemic precision gap (not the width/signed_override-propagation
shape of the first three waves), the same pattern was found and fixed
across **every** condition-truthiness check in **all four** engines:
- `sim/evaluator.py`: `TernaryOp`'s `cond = self.eval(expr.condition,
  ctx)` → `.reduce_or()` before the `is_defined`/`.val` check.
- `sim/executor.py`: every `IfStatement`/`ForLoop`/`WhileLoop`/
  `WaitStatement` condition check (in both `execute` and the coroutine
  variant `execute_coroutine` — 8 call sites total) had the identical
  `cond.is_defined and cond.val` shape; same `.reduce_or()` fix applied
  uniformly.
- `sim/vm/interpreter.py`: `Op.TERNARY` used `cond.mask == 0` to decide
  "fully defined, pick a branch" vs. "ambiguous, merge" — fixed to check
  `cond.val & ~cond.mask` (known-1-bit) first. `Op.JUMP_IF_ZERO`/
  `Op.JUMP_IF_NONZERO` (compiled `if`/`while`/`for` bytecode) had the same
  shape, fixed the same way.
- `sim/vm/_interp_fast.pyx` (vm-fast): `OP_TERNARY` had the identical
  `cond_defined`/`cond_nonzero` shape (both the narrow-slot and the
  wide-word-array branches) — fixed to compute `cond_known1` first.
  `OP_JUMP_IF_ZERO`/`OP_JUMP_IF_NONZERO` had the same precision bug AND a
  separate, more severe pre-existing bug: they only ever consulted the
  narrow `stack[sp].val/.mask` slot, never `wflag[sp]`/`wv`/`wm` — for a
  condition wider than 64 bits (a wide concat used as an `if`/`while`
  condition) the narrow slot does not hold the real data at all, so the
  jump decision was reading stale/wrong data. Fixed to branch on `wflag`
  the same way `OP_TERNARY` already did.
- `sim/compiled/_expr_emitter.py`: `_emit_ternary_value_mask_exprs`
  (the narrow/scalar ternary codegen, shared by both the Cython and
  Python-bignum sub-emitters) built `value_expr`/`mask_expr` as `merged if
  cond_mask else (true if cond else false)` — same "any x/z bit in the
  condition is fully ambiguous" bug. Fixed to check `cond & ~cond_mask`
  (known-1) first, `cond_mask == 0` (defined-zero) second, merge last. A
  SECOND, independent bug in the same function: `cond = self._emit_expr(
  expr.condition, 1)` forced the condition to width 1 — harmless for
  already-self-determined-1-bit conditions (comparisons, `!`, reductions),
  but wrong when the condition is itself a further `TernaryOp`/
  `Concatenation`/`Replication`, which use the incoming width to size
  their OWN internal merge/shift computation (e.g. a nested ternary
  condition's `wmask(1)` truncating its ambiguous-branch merge down to a
  single bit, corrupting every bit but the LSB). Fixed to pass
  `self._expr_width(expr.condition)` instead of a hardcoded `1`. Confirmed
  against Icarus for `a0 * (((|a1[0]) ? a4 : {3{a0}}) ? a3 : (~|a5[58:17]))`.
- `sim/compiled/_gen_wide_section.py`: `wide_mux` (the wide-destination
  ternary's runtime merge primitive) had the identical `if cond_m != 0:
  merge elif cond_v != 0: pick a else: pick b` shape — fixed to check
  `cond_v & ~cond_m` first. Note `wide_logical_truth`, a few lines below
  `wide_mux` in the same generated section, already implements the correct
  three-way logic (it's used for wide if-condition evaluation elsewhere) —
  `wide_mux` just never used the same pattern for its own condition.
  `sim/compiled/_wide_emitter.py`'s `TernaryOp` case (which computes the
  scalar `cond_v`/`cond_m` fed into `wide_mux`) had the same width-1
  forcing bug as `_emit_ternary_value_mask_exprs` above — fixed the same
  way, though this scalar reduction remains fundamentally limited to 64
  bits by the `<unsigned long long>` cast regardless of the width passed
  in (see residual gap below).
- `sim/compiled/_stmt_emitters.py`: `_emit_while`'s loop-continuation
  check was `if (cond_mask) != 0 or not (cond): break` — same "any x/z bit
  breaks the loop" bug, fixed to `if not (cond & ~cond_mask): break`.
  `_emit_if`/`_emit_for` do NOT have this bug — they never consult the
  condition's mask at all, which (given x/z-position value bits are
  conventionally stored as 0) already implements the correct "known-1
  forces true, else false" semantics for free; only `_emit_while`'s
  explicit-but-wrong mask check needed fixing. **Caveat**: the differential
  fuzzer only generates combinational expression trees, never `if`/`while`/
  `for`/`case` statements, so `_emit_if`/`_emit_for`/`_emit_while` (and the
  equivalent `IfStatement`/`ForLoop`/`WhileLoop` fixes in
  `sim/executor.py`/`interpreter.py`/`_interp_fast.pyx` above) were
  verified by manual reasoning and pattern-consistency with the
  fuzzer-verified `TernaryOp` sibling fix, not by an automated Icarus/
  differential check against real `if`/`while` statements — a good target
  for future statement-level differential test coverage (see item 3.4).

**Residual gap (not fully characterized)**: this is a large, open-ended
architectural area (the wide emitter, the narrow/scalar emitter, and the
reference/VM engines each reimplement width/signedness/x-propagation
independently, per-node-type, rather than sharing `semantics.py`'s already-
unified logic — see item 4.2's explicit non-goal), not yet exhaustively
characterized. Specific known-remaining gaps:
- **Follow-up (August 2026): re-investigated, confirmed no longer
  reachable — was a real gap at the time this note was written, closed
  as a side effect of the Eighteenth wave's `_expr_max_internal_width`
  generalization.** The narrow/scalar compiled-engine emitter
  (`_emit_concat`/`_emit_replication` in `_expr_emitter.py`) silently
  drops any part whose shift amount would reach or exceed 64 bits (`if
  shift >= 64: continue`) — but `Concatenation`'s own `_expr_width` is
  exactly `sum(part widths)`, and `_expr_max_internal_width` folds that
  `own` width into its `max(...)` at every level of recursion
  (`BinaryOp`/`UnaryOp`/`TernaryOp`/`Concatenation`/`Replication`/select/
  `FunctionCall`-argument), so any Concatenation whose own combined
  width exceeds 64 bits now always causes `_rhs_needs_wide_eval`
  (statement level), `_emit_wide_truthy_to_value` (ternary condition),
  `_emit_wide_binary_to_value` (comparison/binary operand), and
  `_emit_wide_arg_to_value` (function-call argument) to route it through
  the wide scratch-based emitter — which has its own dedicated
  `Concatenation`/`Replication` handling in `_wide_emitter.py`
  (`_emit_wide_expr_to_scratch`) — before `_emit_concat`'s narrow path
  is ever reached. `_emit_concat`/`_emit_replication`'s own `shift`
  variable starts at `sum(widths)` and only decreases, so it can only
  reach 64 when the concatenation's own total width already exceeds 64
  — precisely the case that's now always intercepted upstream. In other
  words the `if shift >= 64: continue` line is currently unreachable
  dead code, not a live truncation gap. Verified directly (not merely
  inferred): five hand-built cross-engine repros matching this note's
  exact scenarios (a wide concat as a ternary condition under a narrow
  destination; as a comparison operand feeding a ternary condition; as
  a comparison inside a function-call argument; a wide concat directly
  truncated to a narrower destination; a wide concat used as a shift
  amount) all agree across reference/vm/vm-fast/compiled. New regression
  test `test_compiled_wide_concat_in_narrow_context_not_truncated` in
  `tests/test_sim/test_differential_functions.py` pins the first two of
  those shapes (ternary condition + function-argument comparison) as a
  guard against a future routing regression re-exposing this path. No
  production code change was needed. (The related `AssignmentPattern`
  gap below turned out, on investigation, NOT to be a live compiled-
  engine width-truncation bug: a field whose own self-width is narrow
  but internally needs wide computation (e.g. a reduction over a
  negated >64-bit operand) is already correctly hoisted through
  `_emit_wide_reduction_to_value`/etc., because `_et_pending` is opened
  unconditionally by the enclosing narrow-statement compiler regardless
  of whether `_rhs_needs_wide_eval` flagged the AssignmentPattern
  itself wide — each field's own local wide-hoisting check is what
  matters, not a routing decision made at the AssignmentPattern level.
  A genuinely >64-bit-total AssignmentPattern destination is also
  handled correctly, via the Python-bignum fallback path (`_emit_py_
  assignment_pattern`) when the C wide-scratch emitter's `_emit_wide_
  expr_to_scratch` returns `None` for the unhandled node shape (slower,
  but correct). Confirmed both with concrete cross-engine repros.

  **What WAS found while building those repros: a genuine, more severe,
  NON-compiled-engine bug** — a signal referenced ONLY inside an
  assignment pattern's field values was invisible to sensitivity/
  dependency analysis on the reference and vm/vm-fast engines
  (`sim/scheduler.py`'s `_walk_expr_reads` and `sim/vm/compiler.py`'s
  `_walk_expr_signals` both dispatch per node type with no
  `AssignmentPattern` case, silently falling through to a no-op instead
  of recursing into `named_pairs`/`positional`/`default_value`), so a
  continuous assign or `always @(*)` block driven solely by such a
  signal was NEVER scheduled to (re-)run — output permanently `x`. The
  compiled engine's analogous collector (`compiled_scheduler.py`'s
  `_walk_for_idents`) walks every AST node's `__slots__` generically
  rather than dispatching per type, so it alone was unaffected — this
  is how the asymmetry was first noticed (compiled gave a correct,
  concrete answer while the other three gave `x`). Fixed by adding the
  missing `AssignmentPattern` case to both walkers. New regression test
  `TestAssignmentPatternSensitivity::test_signal_only_in_assignment_
  pattern_is_sensed` (parametrized over all four engines) in `tests/
  test_sim/test_sim_sv.py`, confirmed to fail on reference/vm/vm-fast
  (compiled unaffected) before the fix via `git stash` bisection.

  **Self-caught regression during verification**: the first version of
  this fix iterated `expr.positional` unconditionally (`for value_expr
  in expr.positional`) — but `AssignmentPattern.positional` is typed
  `list[Expression] | None` and defaults to `None` (every other
  consumer in the codebase guards with `if expr.positional:` first, per
  `expressions.py`). Any assignment pattern using only `named_pairs` or
  `default_value` (e.g. the common `'{default: '0}` reset idiom) hit a
  `TypeError: 'NoneType' object is not iterable` inside the sensitivity
  walker for every such design — caught immediately by a full
  fast-suite run spiking to 115+ failures instead of the expected 16
  partway through, well before it finished. Fixed by adding the missing
  `if expr.positional:` guard in both files.

  Verified (with the guard in place): `test_sim_sv.py` (65 passed),
  `test_differential_functions.py` (23 passed), `test_function_task.py`
  (52 passed), plus a full fast-suite regression (7906 passed -- 6 more
  than the prior wave's 7900, matching the 6 new tests added this
  follow-up -- the same 16 pre-existing failures, `-n 8`, ~34.5 min,
  zero new failures).
- **Follow-up (August 2026): the "theoretical" `AssignmentPattern`
  `signed_override` gap was real -- confirmed and fixed, in the
  REFERENCE engine only (vm/vm-fast/compiled were already correct).**
  Two independent, compounding bugs in `sim/evaluator.py`, both
  invisible to the differential fuzzer because it never generates
  `'{...}` assignment-pattern nodes (see the Eighteenth-wave follow-up
  above, same limitation): (1) `_expr_self_width` had no
  `AssignmentPattern` case, silently falling through to the generic
  `32`-bit default -- so `$signed(...)`'s own handling (`eval(inner,
  ctx, _expr_self_width(inner, ctx))`, evaluating its argument at its
  own self-determined width before deciding how to extend it to the
  requested context width) evaluated the pattern at a bogus 32-bit
  self-width instead of its true width, corrupting the value before the
  cast's own sign-extend step ever ran; (2) even with (1) fixed, `eval
  ()`'s three `AssignmentPattern` branches (named_pairs/positional/
  default_value) never consulted `signed_override` at all when resizing
  to a wider requested `width`, unconditionally `.resize()`-ing
  (zero-extending) instead of `.sign_extend()`-ing when the context
  demands sign extension. Confirmed against cross-engine agreement for
  `$signed('{flag})` with `flag=1`: vm/vm-fast/compiled all correctly
  give `0xFF` (sign-extended -1); reference gave `0x01` (wrongly
  zero-extended) before this fix -- caught while investigating this
  exact gap per explicit user direction to keep pursuing every known
  failure, rather than continuing to carry it as an accepted "low-
  priority/rare, not observed" deferral. Fixed by adding the missing
  `AssignmentPattern` case to `_expr_self_width` (computing the true
  self-width via `match_assignment_pattern_layout`'s `total_width` for
  named_pairs, summed part self-widths for positional, or the default
  value's own self-width) and threading `signed_override` through all
  three `eval()` branches' width-mismatch handling. New regression
  tests `TestAssignmentPatternSignedOverride::test_signed_cast_of_
  positional_pattern_sign_extends`/`test_signed_cast_of_named_pattern_
  sign_extends` (parametrized over all four engines) in `tests/test_sim/
  test_sim_sv.py`, confirmed to fail on reference only (not vm/vm-fast/
  compiled) before the fix via `git stash` bisection. Verified:
  `test_sim_sv.py` (73 passed), `test_differential_functions.py`/
  `test_function_task.py` (unaffected), and a full fast-suite regression
  (7929 passed -- 8 more than the prior wave's 7921, matching the 8 new
  tests added -- down to just the 1 remaining pre-existing failure,
  `test_or_chain_max_line_length`, `-n 8`, ~38 min, zero new failures).
- Any new divergence found in this area should be checked against Icarus
  before assuming the compiled engine is at fault, per the reference-engine
  bug found in wave two above.

**Fifth wave (July 2026, same work-plan item) — three more distinct bug
shapes found while continuing the `VERIFORGE_DIFF_CASES=300` bisection
(17/30 → 20/30 passing)**:
- **Shift-count sign-extension.** The shift-COUNT operand of `<<`/`>>` is
  always an unsigned magnitude (IEEE 1364-2005 Table 5-22/§5.6) regardless
  of its own declared signedness -- but `sim/compiled/_wide_emitter.py`'s
  generic shift branch computed the amount via `<int>(self._emit_expr(
  expr.right, 32))`, and `sim/compiled/_expr_emitter.py`'s `_emit_binary`
  computed the right operand at `op_width`/`signed_override` same as the
  left -- both let a declared-`signed` 1-bit shift-count operand get
  sign-extended (e.g. `1'sb1` sign-extends to -1) before being used as a
  shift amount, producing a nonsense `wshift`/`bshift`. Fixed by forcing
  `signed_override=False` when compiling the shift-count operand
  specifically, in both files. Confirmed against Icarus for
  `$unsigned(a1) << a0` with `a0` declared `signed [0:0]` and `a0=1`.
- **`~`/unary `-` widening a self-determined-fixed operand before
  applying the operator.** `~`/unary `-` are context-determined (their
  OPERAND is widened to match the enclosing context before the operator
  runs) -- correct for a regular signal or another context-determined
  operator, but WRONG when the operand is itself one of IEEE 1364-2005
  Table 5-22's self-determined-ALWAYS-1-bit operators (comparisons,
  `&&`/`||`, reduction ops, `!`): their result is fixed at 1 bit
  regardless of context, so widening it BEFORE complementing/negating
  flips/negates bits that shouldn't exist yet (e.g. zero-extending a
  1-bit `&&` result to 96 bits before `~` gives `~0...01 = 1...110`
  instead of the correct `resize(~1'b1) = resize(1'b0) = 0`). This is
  distinct from arithmetic BinaryOp operand-widening (`+`/`-`/bitwise
  binary ops), which IS safe there since extension commutes with those
  operations. Added a new `_is_fixed_self_determined()` helper (present
  identically in `sim/evaluator.py`, `sim/vm/compiler.py`, and
  `sim/compiled/_expr_emitter.py`/`_wide_emitter.py`) that detects this
  operand category; when true, `~`/`-` now evaluate/compile the operand at
  its own fixed width, apply the operator, THEN extend the RESULT.
  Confirmed against Icarus for `$signed(~({a0, a6, a0} && a7))`.
- **`$signed()`-wrapped 1-bit condition pollutes `wide_mux`'s
  "known-1-bit" check with spurious high bits.** `$signed(1'b1)` compiles
  to `_sign_ext(1, 1)`, which is a legitimate raw C representation of "the
  value -1 at 1-bit width" (`long long` has no native 1-bit type, so all
  64 bits get set) -- but `_wide_emitter.py`'s `TernaryOp` case and
  `_expr_emitter.py`'s `_emit_ternary_value_mask_exprs` fed this raw,
  unmasked value straight into `cond_v_expr`/the `cond_known1` check
  without first masking it down to `cond_w` bits. Since the corresponding
  mask value has those same high bits as 0 (correctly reflecting "not
  unknown"), `cond_v & ~cond_mask` spuriously read those garbage
  sign-extended bits as a "known-1", forcing a branch to be selected
  outright instead of correctly falling through to the ambiguous
  bitwise-merge path. Fixed by masking both `cond`/`cond_mask` to
  `wmask(cond_w)` before the known-1 check, in both files. Confirmed
  against Icarus for `-($signed((a7 == a2[4])) ? $unsigned({3{a3[60]}}) :
  (a5[3:1] ? a4[41] : a6))`.

**Sixth wave (July 2026, same work-plan item) -- six more distinct bug
shapes, all confirmed against Icarus and fixed** (17/30 → 26/30 on
`VERIFORGE_DIFF_CASES=300`):
- **vm-fast's `OP_RED_NAND` had its own, separately-naive x-precision
  implementation instead of reusing `OP_RED_AND`'s already-correct one.**
  `OP_RED_AND` right above it already implements "a known-0 bit anywhere
  forces the reduction definitely non-x, even with other x/z bits present"
  (mirroring `Value.reduce_and`), in both its narrow and wide-value
  branches -- `OP_RED_NAND` had a from-scratch reimplementation that
  missed this and fell straight to "any x/z bit -> x". Fixed by mirroring
  `OP_RED_AND`'s known-0 check (NAND: any known-0 -> definite 1). Confirmed
  against Icarus for `(~&{(-a6), a7, {a4[16:7], a6[29:16]}}) ? a1[0] :
  {...}` with `a4` fully x.
- **`TernaryOp`'s own condition, one leaf position missed by the
  fourth-wave `reduce_or()` fix.** `eval()`/`_compile_expr()` reduced the
  condition's ALREADY-EVALUATED value with `.reduce_or()`, but evaluated
  it at `width=0` (self-determined-but-uncontextualized) instead of the
  condition's own self-determined width -- the same "leaf ignoring width"
  bug already fixed for Concatenation/Replication members and the
  `!`/reduction-op operand, just one more position. When the condition is
  itself e.g. a nested ternary whose OTHER branch is a wide arithmetic
  result, a `~`/unary-minus branch nested within it never got resized
  before running, corrupting the outer condition's truthiness. Fixed by
  evaluating at `_expr_self_width(expr.condition, ctx)` /
  `self._expr_width(expr.condition)` in both `sim/evaluator.py` and
  `sim/vm/compiler.py`. Confirmed against Icarus for `(($unsigned(a1[5]) ?
  (a4 ^ a4[1]) : (~a0)) ? a0 : (^{2{a0}}))`.
- **Compiled engine: a scratch-slot LIFO violation corrupted `wide_mux`'s
  condition data.** The fourth-wave fix routing a wide-signal-touching
  `TernaryOp` condition through `wide_logical_truth` freed its `cond_slot`
  scratch allocation before the `truth_slot` holding the reduced result
  was done being read by the later `wide_mux` call -- `_alloc_scratch`/
  `_free_scratch` (`codegen.py`) is a plain LIFO stack-depth counter, not
  a real pool, so freeing out of order let a LATER allocation (`tslot`,
  for the ternary's true branch) reuse `truth_slot`'s number and silently
  overwrite the condition's truthiness with the true branch's own data
  before `wide_mux` read it. Fixed by keeping `cond_slot`/`truth_slot`
  allocated through to the very end, freed together after `tslot`/`fslot`
  in one block.
- **Compiled engine: `_REDUCE_PRIMS` loaded a reduction's operand at the
  inherited (too-small) `n_words` instead of its own required word
  count.** `_wide_emitter.py`'s reduction-op handling computed `op_n`
  (the operand's OWN required word count) correctly for the final
  `wide_reduce_*` primitive call, but passed the INHERITED `n_words`
  (sized for the enclosing, possibly-narrower destination) to the
  recursive LOAD of the operand itself -- when a reduction over a wide
  (>64-bit) signal is nested inside a narrower enclosing context (e.g.
  `~^a6` as one AND-operand of a 20-bit subtraction that is itself a
  ternary condition), `wide_load_signal` only loaded 1 of the 2 words a
  wide signal actually needs, leaving the rest uninitialized garbage that
  the reduction then read. Fixed to use `max(n_words, op_n)`, mirroring
  the already-correct sibling `!` case a few lines below. Confirmed
  against Icarus for `((^a3) - ({2{a5[21:12]}} & (~^a6))) ? a1 : ...`
  where `a6` is 80 bits and the enclosing subtraction is only 20.
- **Compiled engine: unary `-`'s narrow/scalar mask computation just
  passed the operand's mask through unchanged, instead of propagating
  "any x anywhere -> the WHOLE result is x" the way `+`/`-` BinaryOp
  already does.** `~` (bitwise complement) correctly passes its operand's
  mask through bit-for-bit (complementing doesn't need full-x
  propagation), but arithmetic negation's 2's-complement borrow chain
  can't be computed with partial unknowns -- `_emit_mask_expr`'s UnaryOp
  fallthrough used the SAME pass-through for both, so `-$signed({a0, a1,
  a7})` with `a1` fully x showed only the 8 specific bit positions
  corresponding to `a1`'s own position as x, not the whole 10-bit
  negation result. Fixed by giving `-` its own branch:
  `wmask(width) if (operand_mask) else 0`. Confirmed against Icarus for
  `{(-$signed({a0, a1, a7})), a1[7:4]}` with `a1` fully x.
- **Compiled engine: a family of shift-operator bugs, found together
  while chasing one regression from the fixes above.** All four:
  1. A shift's left operand recursive computation was capped at its OWN
     self-width (`lw`), not `max(lw, dst_width)` -- when it's e.g. a
     `$signed(...)`-wrapped 1-bit value, that cap stopped the wrapper's
     own sign-extension from filling anywhere past bit 0. Fixed to widen
     the recursive `dst_width` when needed.
  2. Widening that same recursive call exposed a SEPARATE bug: `>>` is
     ALWAYS a logical (zero-fill) shift in Verilog regardless of the left
     operand's own declared signedness (only `>>>` sign-extends) -- once
     the left operand's recursive `dst_width` could exceed its own width,
     an unqualified recursive call let a plain `signed`-declared
     Identifier fall back to its OWN declared signedness and get
     sign-extended, which is wrong specifically for `>>`. Fixed by forcing
     `signed_override=False` for `>>`'s left operand only, in both the
     wide and narrow emitters.
  3. The shift COUNT (self-determined per Table 5-22) was evaluated at a
     fixed width of 32 bits (`_wide_emitter.py`) or the enclosing
     `op_width` (`_expr_emitter.py`), purely for `<int>`-cast convenience
     -- letting a context-determined operator WITHIN the amount (e.g. `~`
     in `~(cond ? a : b)`) wrongly treat that width as its own context and
     widen its operand before running, corrupting the amount. Fixed by
     evaluating the amount at its own self-determined width instead.
  4. That fix in turn exposed a FOURTH bug: `_expr_width`'s `+`/`-` case
     deliberately uses a max-of-operands rule with no headroom for the
     carry bit (documented, pre-existing simplification -- normally fine
     because callers pass a wider enclosing context anyway), but a shift
     amount now evaluated at its own tight self-width has no such outer
     context, so a genuine carry-out got silently masked away. Fixed with
     a new `_shift_amount_width()` helper that adds 1 bit of headroom
     specifically when the amount's top-level node is a `+`/`-` BinaryOp.
  Confirmed against Icarus for `$unsigned(a1) << a0` (signedness),
  `$signed((!a6[52])) << (~({a0, a0, a5[54]} ? a4[13] : (~^a1[2])))`
  (left-operand width), `a2 >> ((^(a1 ? a5[4:3] : a5)) + a3[25:16])`
  (amount width/carry AND the `>>`-is-always-unsigned regression), all in
  `_wide_emitter.py`; the narrow-emitter counterparts in
  `_expr_emitter.py` were fixed defensively in parallel (same shape,
  not independently fuzzer-triggered but the reasoning is identical).
- **Compiled engine: the wide emitter's signed-comparison detection only
  recognized a LITERAL `$signed(x) < $signed(y)` syntactic pattern**,
  missing the general case where either side is signed for some OTHER
  reason (a ternary whose own combined signedness is true because both
  branches are individually signed; a plain `signed`-declared Identifier).
  Using the unsigned comparison primitive on operands that are actually
  meant to be interpreted as negative silently gives the wrong result.
  Fixed by deciding signedness via `_expr_signed(expr.left) and
  _expr_signed(expr.right)` (the same general rule used everywhere else in
  this codebase), keeping the `$signed(...)`-unwrapping as a separate,
  independent optimization. Confirmed against Icarus for `(a4[6] ?
  $signed(a4[52:24]) : (~a6)) < a4`, where only the ternary's `true_expr`
  is a literal `$signed(...)` call.

**Seventh wave (July 2026, same work-plan item) -- one more distinct bug,
found while re-running the large batch after the sixth wave** (26/30 on
`VERIFORGE_DIFF_CASES=300`, up from 17/30 before this session's most
recent round):
- **Compiled engine: `_emit_concat`/`_emit_replication` embed each
  member's raw C value into shift+OR tiling without masking it to its own
  self-width first.** Same root shape as the fourth-wave `wide_mux`
  pollution bug, just in a different consumer: `$signed(1'b1)` compiles to
  `_sign_ext(1, 1)` = -1 = ALL 64 bits set (a legitimate raw C
  representation of "signed -1", just not scoped to 1 bit) -- embedding
  that directly into `(val << shift) | ...` without masking first means
  the spurious high bits survive into the NEXT member's own bit range once
  shifted, corrupting neighboring concat members or replication tiles.
  Fixed by masking each member/tile to its own width right where it's
  read, in both the value (`_emit_concat`/`_emit_replication`) and mask
  (`_emit_mask_expr`'s Concatenation/Replication cases) computations.
  Confirmed against Icarus for `a2 << {2{$signed((a4 <= a6))}}`. **Note**:
  this expression's remaining residual failures (some vectors still
  mismatch after this fix) are the ALREADY-documented case-88 architectural
  gap below, not a new bug -- `a4 <= a6` compares a 64-bit and an 80-bit
  operand via the narrow/scalar comparison path, which reads the 80-bit
  `a6` via `c.val[sid]` (only its low 64 bits) since `_sign_ext(v, w)` is a
  no-op for `w >= 64`, silently dropping `a6`'s top 16 bits from the
  comparison.

**Eighth wave (July 2026, same work-plan item) -- the case-88 architectural
gap, FOUND AND FIXED** (26/30 → 27/30 on `VERIFORGE_DIFF_CASES=300`; the
biggest single fix of this whole multi-session investigation, given how
long it had been deferred as "too deep to chase right now"):
- **Root cause, finally located**: `_process_compiler.py`'s
  `_flatten_concat_identifier_parts` -- the helper `_compile_continuous_
  assigns` uses to turn a wide (`total_width > 64` or `lhs_w > 64`)
  Concatenation RHS into an efficient word-by-word `_emit_flat_concat_
  whole_assign` (instead of falling through to the fully general, scratch-
  based `_emit_wide_lhs_write_new`) -- has a fallback for any concat member
  that isn't a plain Identifier/RangeSelect/PartSelect (e.g. `$unsigned(
  ~^(cond ? wide_signal : narrow))`): it computes that member via the
  NARROW/SCALAR `_emit_expr`/`_emit_mask_expr`, gated only on the member's
  OWN self-determined result width being ≤64 bits (`0 < node_width <=
  _WORD_BITS`). A reduction/comparison/etc.'s own RESULT width being small
  says nothing about whether its INTERNAL computation reads a signal wider
  than 64 bits -- and the narrow emitter's Identifier case always returns
  `c.val[sid]`, which for a >64-bit signal only ever holds its low word.
  This is confirmed as the actual mechanism behind case 88's failure
  (`{$unsigned((~^(a5[56] ? a5 : a7))), a2[10:7]}`, `a5` 65 bits): traced
  by instrumenting `_emit_wide_lhs_write_new` and `_emit_wide_expr_to_
  scratch` directly (neither was ever even CALLED for the comb assign --
  confirming this fallback intercepted it before either ever ran) and by
  instrumenting `_flatten_concat_identifier_parts` itself. The earlier
  "comb-vs-ff asymmetry" observation (documented in the seventh-wave note,
  now superseded) was real but was a RED HERRING for the actual mechanism
  -- the always-block/NBA path doesn't have this SAME fast-path matcher at
  all, so it always falls through to the sound `_emit_wide_lhs_write_new`,
  while the comb-assign path's OWN fast-path silently miscomputes instead
  of falling through.
- **Fix**: added an `_expr_uses_wide_signal(node)` check (the SAME helper
  `_rhs_needs_wide_eval` already uses for its own top-level catch-all,
  just now applied recursively at each concat member too) to the fallback
  -- when the member touches a signal wider than 64 bits ANYWHERE in its
  tree, `walk()` now returns `False` for it, making the whole
  `_flatten_concat_identifier_parts` call fail (return `None`) for that
  Concatenation, so `_compile_continuous_assigns` correctly falls through
  to `_emit_wide_lhs_write_new` (the general, scratch-based, genuinely
  wide-aware emitter) instead. Confirmed against Icarus: case 88 now
  passes all 8 vectors.
- **This was NOT actually the same root cause as the still-open case
  111/286/298-residual failures below**, despite earlier waves'
  documentation guessing they were related (the "comb-vs-ff asymmetry" and
  "`c.val[sid]`-based narrow reads" symptoms looked identical from the
  outside). Re-investigating case 111 after this fix found `_emit_flat_
  concat_whole_assign` IS being reached and IS correctly building a
  proper word-by-word, wide-aware computation (confirmed by direct
  instrumentation: `_flatten_concat_identifier_parts` succeeds, `_emit_
  flat_concat_whole_assign` is called, and the generated code for both
  destination words correctly references `_sig_extract_word_val`/mixed
  "sig"/"expr"-kind parts, not a bare `c.val[sid]`) -- so case 111's
  remaining failure is a DIFFERENT, more localized bug, most likely inside
  the multi-word merge/bit-positioning logic for a Concatenation with
  MIXED "sig" and "expr"-kind flat parts crossing a 64-bit word boundary
  (`_concat_word_expr`/`_masked_flat_concat_word_exprs` in
  `_wide_emitter.py`), not yet isolated further.

**Ninth wave (July 2026, same work-plan item) -- cases 111/286/298-residual
all resolved, and the reference engine's OWN previously-undetected bug
turned out to be the dominant root cause** (large-batch differential run
now 30/30, up from 27/30; default-seed 150-case run now green apart from
one newly-characterized, deliberately-deferred multiplication-width
question -- see "Tenth wave" below):

- **Compiled engine: shift-COUNT touching a wide signal through the
  narrow/scalar emitter (case 298's residual).** `_wide_emitter.py`'s
  `_WIDE_SHIFT_PRIMS` handling computed the shift amount via
  `self._emit_expr(expr.right, amount_w, False)` unconditionally -- no
  `_expr_uses_wide_signal` guard at all, unlike every other narrow-meets-
  wide site fixed this session. Fixed by routing the amount through
  `_emit_wide_expr_to_scratch` (reading back a scalar `<int>` from the
  low scratch word) whenever `_expr_uses_wide_signal(expr.right)` OR
  `_expr_max_internal_width(expr.right) > _WORD_BITS` (see next bullet
  for why the second check is also needed here). Confirmed against
  Icarus: `a2 << {2{$signed((a4 <= a6))}}` (`a6` 80 bits) now passes all
  8 vectors.
- **Compiled engine: `_expr_uses_wide_signal` alone is insufficient --
  an expression's OWN internal width can exceed 64 bits even when every
  signal it reads is <=64 bits (case 111's actual root cause).**
  `_flatten_concat_identifier_parts`'s fallback (the eighth-wave fix)
  only checked `_expr_uses_wide_signal`, which detects a directly-wide
  *signal*, not a wide *intermediate value* built from narrow signals --
  e.g. `~&{2{a4}}` with `a4` a plain 64-bit signal: the reduction's
  operand is a 128-bit `Replication`, but no signal involved is itself
  >64 bits, so `_expr_uses_wide_signal` said "safe for the narrow
  emitter" and the narrow reduction codegen built a comparison against a
  literal 128-bit all-ones constant that a `long long` can never equal,
  always returning the wrong constant. Fixed by additionally gating on
  `_expr_max_internal_width(node) <= _WORD_BITS` (already existed,
  originally only used for scratch-array sizing) -- it recurses into
  every operand's OWN self-determined width, not just each node's
  result width, so it catches this case too. Confirmed against Icarus:
  `{{a2, $unsigned($unsigned(a1)), $unsigned($signed(a3))}, (a1[2:1] ? a3
  : (~&{2{a4}})), a5}` now passes all 8 vectors.
- **Reference engine (and `sim/vm/compiler.py`, structurally parallel):
  a self-determined-fixed-1-bit operator's RESULT was never resized up
  to a wider requested context width -- the actual dominant bug behind
  cases 111 AND 286 (the compiled-engine fixes above were real and
  necessary, but even after both, case 111 still failed until this was
  found).** `UnaryOp` reduction ops (`&`,`|`,`^`,`~&`,`~|`,`~^`,`^~`) and
  `!`, and `BinaryOp` comparisons/`&&`/`||`, are self-determined ALWAYS
  1-bit results (IEEE 1364-2005 Table 5-22) -- but when used as an
  operand of a WIDER context (a ternary branch whose other branch is
  wider, a concat member wrapped in a further context-determined
  operator), the caller passes a nonzero `width` into `eval()`/
  `_compile_expr()` expecting the result to come back at that width.
  Every sibling case that computes a fixed-width intermediate then
  widens it (`~`/unary `-` on such an operand, `BinaryOp` bitwise-op
  results) already had this "extend the RESULT after computing it"
  step -- reduction/`!` and comparison/`&&`/`||` simply never did,
  silently returning a 1-bit-wide `Value` regardless of the requested
  `width`. Harmless at a TOP-level assignment (a separate post-hoc
  statement-level resize step covers it there, the same "nesting depth"
  shape as the second-wave `$signed`/`BitSelect` bug), but corrupts
  anything that relies on the returned `Value.width` being correct
  mid-expression -- most visibly `Concatenation.concat()`'s bit-shifting
  arithmetic, which packs each part using its OWN reported width: a
  1-bit-wide reduction result silently occupying only 1 bit of a concat
  instead of its ternary's real (e.g. 63-bit) self-determined width
  shifts every subsequent bit into the wrong position. Fixed by adding
  the same "if width and result.width < width: sign_extend/resize"
  step (always unsigned in its own right per IEEE 1364-2005 §5.5.1,
  except when `signed_override` forces sign-extension) after computing
  the reduction/`!`/comparison/`&&`/`||` result, in both
  `sim/evaluator.py` and `sim/vm/compiler.py` (emitting a trailing
  `SIGN_EXT`/`RESIZE` opcode for the latter). Confirmed against Icarus
  for a minimal repro (`{8'hAA, (cond ? (a == b) : c)}` with `c` 64
  bits) and both full case 111 and case 286 (`{a3[37], (!{2{a6[36]}}),
  (... ? (~&$signed(a5)) : $signed({3{a0}}))}`) now passing all 8
  vectors each on every engine.

This was found by NOT trusting the differential harness's own "expected"
oracle once the compiled-engine fixes above still left case 111 failing
with a suspicious pattern (the mismatch's high word was entirely zero on
the compiled side but structured/nonzero on the reference side) --
building an Icarus testbench for the exact failing vector's values showed
Icarus agreed with the (newly-fixed) COMPILED engine, not the reference
oracle, confirming this was a reference-engine bug all along, exactly the
kind of "harness oracle is not infallible" trap flagged repeatedly
earlier in this investigation.

**Tenth wave -- the multiplication self-determined-width question,
resolved** (case 146, only visible once the differential run was widened
past the historical 100-case default to 150 cases; both the 150-case
default and the `VERIFORGE_DIFF_CASES=300` large batch are now fully
green with this fix applied):
`(-{$signed({2{a7}}), ((a3 ? a1[0] : a6[64]) * (a1[6] < a1[2:0]))})` --
reference (and `sim/vm/compiler.py`) disagreed with compiled/Icarus/
Verilator on the multiplication member's self-determined width.

**Root cause, confirmed against the IEEE 1364-2005 primary text**
(`Table 5-22 -- Bit lengths resulting from self-determined expressions`,
fetched directly rather than relied on from memory or this codebase's
own prior notes): the table's row for `i op j, where op is: + - * / % &
| ^ ^~ ~^` gives bit length `max(L(i),L(j))` for ALL of these operators,
INCLUDING `*` -- there is no separate sum-of-widths row for
multiplication anywhere in the table. The text immediately above the
table (a genuinely separate point) only notes: "Multiplication may be
performed without losing any overflow bits by assigning the result to
something wide enough to hold it" -- a remark about CONTEXT-DETERMINED
multiplication (a wider destination lets the full product survive), not
a claim about the SELF-DETERMINED width used when there is no such
context (e.g. a concat member). This directly contradicts this
codebase's own long-standing prior claim ("multiplication's own
self-determined width is the SUM of its operand widths (IEEE
1364-2005 Table 5-22)"), which had gone unquestioned since work plan
item 3.4 and was itself likely the original source of the confusion for
every prior note that cited it. Independently confirmed empirically
against BOTH Icarus (`-g2005`) and Verilator: `reg [7:0] a=200,b=200;
$display("%b",a*b);` truncates to 8 bits (=64), not the 16-bit sum-width
40000 a sum-width reading would predict.
- `_expr_self_width()` (`sim/evaluator.py`) and `_expr_width()`
  (`sim/vm/compiler.py`) already correctly use `max(left,right)` for `*`
  (no `*`-specific branch; falls through to the generic `BinaryOp`
  case) -- these were already right, and match Icarus/Verilator.
- `Value.__mul__` (`sim/value.py`) deliberately computes `width =
  self.width + other.width` (sum) -- kept exactly as-is (still
  correctly unit-tested by
  `tests/test_sim/test_value_widths.py::test_mul_result_width_is_sum`,
  which tests the raw arithmetic PRIMITIVE, a reasonable choice
  regardless of the self-determined-width question: never lose
  precision in the raw computation, let the caller's width-resize logic
  decide how much to keep -- e.g. this is exactly what makes
  CONTEXT-DETERMINED multiplication like `wire [15:0] p = a*b;` (8-bit
  `a`,`b`) work correctly without a separate special case).
- **The actual bug**: `eval()`'s generic context-determined arithmetic
  branch (`+`,`-`,`*`,`/`,`%`,`**`) resized each OPERAND up to
  `max(width, self_width(operand))` before calling `_eval_binary_op`,
  but never re-checked the RESULT against the originally-requested
  `width` afterward -- so `Value.__mul__`'s (correct, intentional)
  wider sum-width result was never truncated back down to what
  `_expr_self_width` (also correct) had actually requested, corrupting
  `.concat()`'s bit-packing the same mirror-image way the ninth wave's
  too-NARROW reduction/comparison results did. `sim/vm/compiler.py` had
  the identical gap: it never emitted a trailing `RESIZE`/`SIGN_EXT`
  after `Op.MUL` either.

**Fix**: generalized the ninth wave's comparison/`&&`/`||` result-width
fix into a single, uniform tail shared by every op that reaches it
(comparisons, shifts, and the arithmetic `+,-,*,/,%,**` branch) --
`sim/evaluator.py` now checks `result.width != width` unconditionally
(not just `< width`) and resizes/sign-extends either direction;
`sim/vm/compiler.py` tracks a statically-known `static_result_w` for
each op category (1 for comparisons/&&/||; the shift branch's already-
resized left-operand target for shifts, matching Table 5-22's `L(i)`
row for `>> << ** >>> <<<`; `target_left + target_right` for `*`
specifically, `max(target_left, target_right)` for the other arithmetic
ops -- both exact at compile time since `RESIZE`/`SIGN_EXT` deterministically
set the runtime width to their target argument) and emits a trailing
`RESIZE`/`SIGN_EXT` whenever it differs from `width`. For every op other
than `*`, the resized operands are already each `>= width`, so
`max(target_left, target_right) >= width` always and no correction was
ever actually needed there in practice -- confirmed by this fix being a
verified no-op for every previously-passing case; only `*` triggers real
narrowing. Confirmed against Icarus for the case-146 expression above,
and the full differential harness (default 150-case and
`VERIFORGE_DIFF_CASES=300` large-batch, both with
`VERIFORGE_DIFF_COMPILED=1`) is green with this fix applied.

**Eleventh wave -- the `**` (power) operator, full fix (width +
signedness + IEEE 1364-2005 Table 5-6 special values), across all four
engines.** The tenth wave's mul-width investigation noticed in passing
that this codebase's `**` treatment might also be spec-noncompliant;
following up confirmed THREE independent, real bugs, none previously
caught because `**` was never covered by the differential fuzzer (not in
its generated operator set) or any dedicated test file:

1. **Width/self-determination.** Per the primary spec text (Table 5-22:
   `>> << ** >>> <<<` -> `L(i)`, comment "j is self-determined"; and
   SS5.1.5: "In all cases, the second operand of the power operator shall
   be treated as self-determined") -- `**` is grouped with the SHIFT row,
   not the generic `max(L(i),L(j))` row `+ - * / %` share. Every engine
   previously treated `**` exactly like `+`/`-`/`*` (context-propagated
   the exponent into the outer width, used max-width). Fixed by carving
   `**` into its own branch everywhere, mirroring each engine's existing
   shift-operator handling: the BASE is context-determined (extended to
   `max(width, self_width(base))` before the operator runs, exactly like
   a shift's left operand), the EXPONENT is always evaluated at its own
   self-determined width (via `_expr_self_width`/`_expr_width`, not a
   bare width=0 call, so a nested context-determined operator within it
   still resizes correctly -- the same leaf-width bug class fixed
   repeatedly elsewhere this session), and the result's own width is the
   base's width alone.
2. **Signedness was entirely unimplemented.** `Value.__pow__` (and every
   engine's direct equivalent) always computed `self.val ** other.val`
   on raw UNSIGNED bit patterns, with no signed variant at all -- unlike
   `/`/`%`/comparisons, which already have `_eval_signed_divmod`/
   `_eval_signed_cmp`-style dispatch gated on both operands being
   declared signed (IEEE 1364-2005 SS5.5.1: "if all operands are signed,
   the result will be signed"). Fixed by adding the same all-or-nothing
   signed dispatch for `**`: `_eval_signed_pow` (`sim/evaluator.py`),
   `Op.SPOW` (`sim/vm/interpreter.py`, `sim/vm/compiler.py`'s
   `_SIGNED_POW_MAP`), `OP_SPOW` (`sim/vm/_interp_fast.pyx`, a new
   opcode appended after `OP_SMOD`), and a signed branch in the compiled
   engine's `_emit_binary` (`_expr_emitter.py`) that sign-extends both
   operands via the existing `_sign_ext` helper before calling the new
   `_verilog_ipow` runtime primitive.
3. **IEEE 1364-2005 Table 5-6's negative-base/negative-exponent special
   values were not implemented at all** (e.g. `0 ** -1` == `'bx`
   specifically, `2 ** -1` == `0`, `(-1) ** -3` == `-1`,
   `(-1) ** -4` == `1`) -- a negative exponent's raw two's-complement bit
   pattern would previously just get used directly as an enormous
   positive magnitude, producing nonsense (or, on the COMPILED engine's
   old `<unsigned long long>pow(<double>(...), <double>(...))`
   implementation, real undefined-behavior risk: floating-point `pow()`
   is imprecise for large integers, and casting an infinite or negative
   `double` back to `unsigned long long` is UB in C). Table 5-6 itself
   required care to decode correctly: the markdown conversion of the
   IEEE 1364-2005 PDF (fetched directly from the primary text rather
   than trusted from memory or this codebase's own prior notes) had
   transposed the table's row/column axis labels; the corrected reading
   (rows = exponent sign, columns = base magnitude) was cross-verified
   against four independent Icarus results before being trusted. A new
   shared `_verilog_pow(base, exp) -> int | None` helper (`sim/value.py`,
   `None` signaling the one genuinely-undefined `'bx` cell) implements
   the table once, reused by `Value.__pow__`'s unsigned path and
   `_eval_signed_pow`'s signed path; `sim/vm/interpreter.py`'s `Op.SPOW`
   imports the same helper; `sim/vm/_interp_fast.pyx`'s `OP_SPOW` and the
   compiled engine's new `_verilog_ipow` (`templates/narrow_tail.pxi`)
   are pure-integer Cython/C reimplementations of the identical logic
   (no shared import possible across the Python/Cython/C boundary).

All three fixes were verified against Icarus Verilog on every engine
(reference, vm, vm-fast, compiled) via `tests/test_sim/test_power_
operator.py`, a new dedicated cross-engine regression file (the
differential fuzzer's own architecture doesn't work for `**` -- see the
next entry -- so this needed a hand-written suite instead of a fuzzer
opt-in). 58 assertions pass across all four engines; two cases are
strict xfail for a separately-scoped, pre-existing gap (below).

**A separate, pre-existing gap this investigation surfaced -- the
`compiled` half now fixed (silent corruption -> loud failure), the
`vm-fast` half still open**: `**` over a >64-bit operand or destination
was broken on the two C-based engines, in two different ways, neither
introduced by (or specific to) the fixes above:
- **`vm-fast`** (still open): neither `OP_POW` nor the new `OP_SPOW`
  consult the wide (`wflag`/`wv`/`wm`) representation at all -- they
  only ever read a stack slot's narrow low-word fields. For a >64-bit
  base or exponent this silently computes a wrong answer (not even a
  clean crash/x). Pinned as strict xfail in `test_power_operator.py`.
- **`compiled`** (fixed): `**` isn't handled by EITHER wide emitter
  (`_wide_emitter.py`'s recursive scratch emitter, nor the
  Python-bignum `_emit_py_expr`/`_emit_wide_py_bits_lines` fallback) --
  true even before this session's fixes, since `**` was essentially
  never exercised at all previously. When BOTH wide handlers return
  `None` for a >64-bit-destination assignment, both
  `_compile_continuous_assigns` (`_process_compiler.py`) AND
  `_emit_lhs_write` (`_stmt_emitters.py`, the procedural
  blocking/nonblocking-assignment equivalent) fell through to a
  LAST-RESORT fallback (originally meant only for narrow, <=64-bit
  destinations), which unconditionally wrote only
  `c.val[lhs_sid]`/`c.mask[lhs_sid]` -- **never** the wide destination's
  actual `c.wide_val`/`c.wide_offset` storage. The signal silently
  stayed at its power-on-reset value of 0 forever, with **no warning or
  error of any kind**. This was a general architectural gap in both
  fallbacks (would have silently no-opped for ANY future
  wide-emitter-unsupported expression assigned to a wide destination,
  not something specific to `**`) -- confirmed via direct tracing that
  both wide emitters correctly return `None` and execution reaches this
  exact fallback.

  **Fix**: added an explicit guard immediately before each fallback --
  if the destination's real width exceeds `_WORD_BITS` (64), raise
  `NotImplementedError` with a clear message (naming the signal, its
  width, and suggesting `engine='vm'`/`'reference'`) instead of silently
  emitting code that writes nowhere the signal is actually read from.
  This does NOT make `**` (or anything else hitting this path) actually
  WORK on wide operands -- it converts silent, undetectable wrong
  simulation results into an immediate, clear compile-time failure,
  which is deliberately the smaller and safer of the two fixes discussed
  (the larger fix -- real wide-operand support, via either the
  Python-bignum fallback for `compiled` or a from-scratch wide `**`
  primitive, plus the matching `vm-fast` wide-read fix above -- remains
  future work). Verified: raising is confirmed for both the continuous-
  assign and nonblocking-assign paths in
  `test_wide_power_destination_raises_on_compiled`
  (`test_power_operator.py`); the full fast-suite regression (7097+
  tests) stayed green with this guard in place, confirming no existing,
  previously-passing construct was relying on this fallback for a
  genuinely wide (>64-bit) destination. `reference` and `vm` (pure
  Python, arbitrary-width `int`) were never affected by either half of
  this gap.

**Why `**` isn't added to the differential fuzzer's operator set**:
every fuzzer-generated case assigns to a fixed 96-bit destination
(`test_differential.py`'s `_build_batch_module`) -- so adding `**` would
immediately hit the compiled-engine gap above for virtually every
generated case (96 > 64 always), producing mass spurious failures
unrelated to the actual expression being fuzzed. Revisit once the wide-
destination gap above is fixed.

**Twelfth wave (July 2026, work plan item 3.4 phase 1) -- statement-level
differential fuzzing surfaces six more distinct bugs**: a new fuzzer,
`tests/test_sim/test_differential_statements.py`, generates random
`if`/`else if`/`else` chains (mandatory final `else`, blocking assignment)
inside `always @(*)` blocks -- the first time the differential harness
exercised blocking assignment inside a combinational process or real
`if`/`else` condition-truthiness at all (`test_differential.py` only ever
fuzzes `assign`/NBA expression trees). `VERIFORGE_DIFF_STMT_SEED`/
`_STMT_CASES`/`_STMT_COMPILED` control it, mirroring `test_differential.py`'s
knobs. All six bugs below were confirmed against Icarus Verilog before
fixing and are covered by this new file (8/8 batches green, including
`VERIFORGE_DIFF_STMT_COMPILED=1`).

- **Nested `$signed($unsigned(x))` cast precedence** (`sim/evaluator.py`,
  `sim/vm/compiler.py`) -- the OUTERMOST cast in a directly-nested chain
  governs extension, not whichever cast the naive recursive `eval()`/
  `_compile_expr()` call happens to reach first. Fixed by unwrapping the
  whole chain of directly-nested `$signed`/`$unsigned` calls down to the
  innermost non-cast argument before applying (only) the outermost cast's
  decision. Confirmed wrong against Icarus for `$unsigned($signed((a <
  b)))` assigned into a wide destination -- Icarus zero-extends (the outer
  `$unsigned` wins), but the un-fixed code recursed into the inner
  `$signed`'s own branch, which force-set `signed_override=True` and
  sign-extended instead.
- **`if`/`for`/`while` conditions missing self-determined-width evaluation**
  (`sim/executor.py`'s 8 condition-check call sites across `execute`/
  `execute_coroutine`; `sim/vm/compiler.py`'s 3 condition-compile call
  sites; `sim/compiled/_stmt_emitters.py`'s `_emit_if`/`_emit_for`/
  `_emit_while`, all hardcoded to width 1) -- a condition is self-
  determined (IEEE 1364-2005 Table 5-22: it must be evaluated at its OWN
  natural width, not forced to width 1/0), the same class of bug already
  fixed for ternary conditions and ternary-nested reductions earlier in
  this session, just never applied to statement-level conditions because
  nothing had exercised real `if`/`for`/`while` through the differential
  harness before. A condition that is itself a further context-determined
  operator (concatenation, a wide comparison, a nested ternary) was
  silently truncated before its own internal merge/shift logic ran.
- **Reduction-op / `!` value-formula x-imprecision** (`sim/compiled/
  _stmt_emitters.py`'s `_emit_reduction`, `_emit_unary`'s `!` handling) --
  both computed their VALUE purely from the raw (already x-zeroed) operand
  bits without independently checking for ambiguity, violating the
  "value=0 at x positions" invariant that `_emit_if` and other value-only
  truthiness checks rely on. A known-0 bit forces `&`/`~&` definitely
  non-x; a known-1 bit forces `|`/`~|`/`!` definitely non-x -- neither was
  checked, so e.g. a partially-x operand with a known-1 bit present could
  read back as an incorrectly-ambiguous or incorrectly-zero value. Fixed
  by rewriting both VALUE formulas to incorporate the same known-0/known-1
  logic already correctly present in the paired MASK formulas (mirrors
  `Value.reduce_and`/`reduce_or` in `sim/value.py`); `_emit_reduction`'s
  signature grew an `operand_mask` parameter it previously lacked.
- **Scratch-array buffer overflow for wide statement conditions**
  (`sim/compiled/_stmt_emitters.py`) -- the new `_emit_condition_lines_
  and_expr` helper (added to give `_emit_if`/`_emit_for`/`_emit_while`
  their self-determined-width fix above, reusing `wide_logical_truth` the
  same way wide ternary conditions already did) allocated wide scratch
  space for a >64-bit condition but never updated
  `_dynamic_max_wide_words` -- the running peak word-count used to size
  the module-level `_sc{n}_v[N]`/`_sc{n}_m[N]` C stack arrays -- the way
  every other wide-scratch consumer (`_emit_wide_lhs_write_new`) already
  did. Confirmed via direct inspection of generated `.pyx` source
  (`cdef unsigned long long _sc3_v[2]` declared but code indexing
  `_sc3_v[2]`, one past the end) -- an actual C buffer overflow, not just
  a wrong-answer bug. Fixed by adding the identical tracking-update call.
- **Arithmetic-operand-extension architecture for `+ - * / %`** (`sim/
  evaluator.py`, `sim/vm/compiler.py`) -- while chasing the nested-cast
  bug above through a real arithmetic operand (not just a ternary
  branch), a much deeper, general gap surfaced: the correct model (IEEE
  1364-2005 §5.5.2, "any context-determined operand shall be the same
  type and size as the result of the operator") is that each `+ - * / %`
  operand must be evaluated directly AT THE FULL PROPAGATED TARGET WIDTH
  (not its own self-width, resized afterward), because a NESTED context-
  determined operator within it (unary `-`, a further `+`/`-`, a
  `$signed`/`$unsigned` cast) needs that target width to propagate all
  the way down through the recursive `eval()`/`_compile_expr()` call and
  apply its OWN extension decision AT that width -- two's-complement
  negation does not commute with a later zero-extension, so computing
  `-a5` (a5 unsigned) at its own self-width and zero-extending the
  NEGATION RESULT afterward gives a different (wrong) value than zero-
  extending `a5` first and negating at the full width. Confirmed wrong
  against Icarus for `(-a5) - {(~&(~|a7)), a2, a6[63]}`. Once each
  operand is computed at the target width, EACH operand independently
  uses its OWN natural signedness (not the operator's combined
  signedness) to decide that extension -- `signed_override` is
  deliberately NOT forwarded into the per-operand recursive calls, since
  it describes how the WHOLE binary expression's *result* should later
  be reinterpreted by an even-further-out cast, not how each operand's
  own extension is decided. This differs from the bitwise-op branch
  (`&|^~^^~`), which safely combines at the narrower natural op-width
  first and extends the RESULT afterward (safe there because bitwise ops
  have no carry chain; unsafe for `+-*/%`, whose `Value.__add__`-family
  "any x bit anywhere taints the ENTIRE result" rule needs the full
  target width's worth of bits present before it runs, or a genuine x
  elsewhere in a wider-context operand fails to taint the destination's
  already-resolved extended bits).
- **`$signed`/`$unsigned` are themselves self-determined, not context-
  propagating** (`sim/evaluator.py`, `sim/compiled/_wide_emitter.py`) --
  a second, narrower bug in the SAME area, found only after the
  arithmetic-operand fix above started routing target widths through
  casts more aggressively: per Table 5-22, `$signed`/`$unsigned`'s
  ARGUMENT must be evaluated at the argument's OWN self-determined width,
  never the width requested by whatever outer context-determined operator
  is asking for the cast's value -- the cast's only job is deciding sign-
  vs zero-extension once that (already self-width-computed) result is
  later widened to the outer width. Passing the outer width straight into
  the argument used to force a nested context-determined operator inside
  the cast (e.g. `%`) to propagate that OUTER width into ITS OWN operands
  too. Confirmed wrong against Icarus for `$signed((a3 % (a0 | 1))) + a1`
  (a3 unsigned 63 bits): the divisor's own value changed depending on
  which width its internal `|` was evaluated at.
- **Division/modulus needs the OPERATOR's combined signedness for operand
  extension, not each operand's own individual signedness** (`sim/
  evaluator.py`, `sim/vm/compiler.py`, `sim/compiled/_expr_emitter.py`,
  `sim/compiled/_wide_emitter.py`) -- unlike `+-*`, whose fixed-width
  modular arithmetic is invariant to whether each operand was extended by
  its own individual signedness or a shared one (as long as each
  operand's own bit pattern is correct at the target width), DIVISION's
  actual algorithm depends on whether an operand is read as a two's-
  complement negative value, and per IEEE 1364-2005 §5.5.1 that decision
  ("signed division" only when BOTH operands are signed) must be made
  UNIFORMLY across both operands. Extending each operand by its own
  individual signedness -- correct for `+-*` -- corrupts an unsigned
  division/modulus whenever one operand happens to be individually
  signed: its sign-extension gets misread as a huge unsigned magnitude
  once the (necessarily unsigned, since not both operands are signed)
  division runs. Confirmed wrong against Icarus for `a4 / ((~^a1[0]) |
  1)` (a4 signed and negative, divisor an unsigned reduction-derived
  expression) and `a3 % (a0 | 1)` (a0 a signed 1-bit register nested
  inside the divisor's own `|`). Fixed by computing a single combined
  `both_signed` decision per division/modulus BinaryOp and threading it
  as a forced override into BOTH operands' own recursive evaluation --
  propagating into whatever nested operator either operand is (exactly
  like a ternary's combined signedness overrides its branches), so the
  combined decision governs every extension nested within either operand
  too, not just its own top-level widening. The `compiled` engine's
  narrow path (`_expr_emitter.py::_emit_binary`) needed the same
  combined-vs-individual fix for its own pre-division sign-extension AND
  its signed-vs-unsigned C-division dispatch (both previously consulted
  each operand's individual `_expr_signed`); the wide path
  (`_wide_emitter.py`) needed it threaded into
  `_emit_wide_expr_to_scratch`'s recursive calls the same way. **Residual
  gap**: `wide_div`/`wide_mod` (the >64-bit primitives `_gen_wide_
  section.py` generates) are UNSIGNED-only bit-by-bit implementations --
  genuinely-signed wide (>64-bit) division was already unimplemented
  before this fix and remains so; only the unsigned-dispatch case (the
  one this wave's bugs and the fuzzer's own signal widths actually
  exercise) is fixed.
- **Separately, in the same investigation: a pre-existing, compiled-
  engine-only bitwise-op (`& | ^`) mask leak** (`sim/compiled/
  _expr_emitter.py::_emit_binary`) -- `&`/`|`/`^` have `needs_mask=False`
  in `_BINARY_VALUE_OP`, relying on an outer assignment's own final mask
  to bound their result; that presumption breaks when such a bitwise
  BinaryOp is embedded as a SUB-expression of another operator (e.g. the
  divisor of `%`) rather than a direct assignment RHS -- no outer mask
  ever runs, and an individual operand's own `_sign_ext` call (which
  fills the full native 64-bit C register, not bounded to the bitwise
  op's own natural `op_width`) leaked straight through as garbage bits
  above `op_width` into whatever consumed the raw expression string.
  Confirmed wrong (cross-engine, against the corrected reference oracle)
  for `a3 % (a0 | 1)`, where `a0`'s own sign-extension leaked past `|`'s
  natural 32-bit width into the divisor once nested inside the modulus's
  wider context. Fixed by explicitly masking to `op_width` before
  optionally extending to the caller's requested `width` using the whole
  bitwise expression's own combined signedness, mirroring `sim/
  evaluator.py`'s already-correct bitwise-op branch.

All eight bugs (six from the primary chain plus the two found mid-
investigation) were verified via direct Icarus comparison scripts, then
confirmed clean across a 300-case `VERIFORGE_DIFF_CASES=300
VERIFORGE_DIFF_COMPILED=1` run of the original expression-tree fuzzer (30/30
batches), the new statement-level fuzzer (8/8 batches, `_STMT_COMPILED=1`),
`test_power_operator.py` (60 passed, 1 xfail, unaffected), and the full
fast-suite regression (7107 passed, 1 xfailed, 0 failed, `-n 8`, ~30 min).

**Thirteenth wave (July 2026, work plan item 3.4 phase 2) -- clocked/
nonblocking statement fuzzing surfaces ten more distinct bugs**: extended
`test_differential_statements.py` to render the SAME randomly generated
if/else-chain STRUCTURE a second way -- nonblocking (`<=`) assignments
inside a clocked `always @(posedge clk)` block, alongside the existing
combinational (`=` inside `always @(*)`) form (see the file's own updated
docstring for the rng-state snapshot/restore mechanism that keeps both
variants' condition/RHS trees byte-identical). This exercises NBA
scheduling/deferred-update codegen that phase 1 never touched. Stress-
testing at 150 cases across two seeds (default 40-case runs stayed green
throughout -- these bugs only showed up at larger scale) surfaced ten
distinct, real bugs, all confirmed against Icarus and/or cross-engine
agreement, requiring three separate `AskUserQuestion` scope-continuation
decisions as the investigation kept surfacing deeper, related issues:

- **Shift-amount sign-extension leak** (`sim/compiled/_wide_emitter.py`,
  the wide shift-count scalar path) -- a `$signed(...)`-wrapped shift
  count's raw C expression was cast straight to `<int>` without masking
  to its own natural width first; `_emit_func_call`'s `$signed` branch
  unconditionally `_sign_ext`s the underlying native C register (fills
  every bit above the argument's own width with the sign bit, regardless
  of whether the caller wants that), so a small positive shift amount
  wrapped in `$signed(...)` read back as a large negative `int`, and the
  wide shift primitive then shifted the wrong direction. Confirmed wrong
  against Icarus for `{a1[0], (-a1)} >> $signed({3{a0}})`. Fixed by
  masking to the shift count's own width (`& wmask(amount_w)`) before the
  `<int>` cast.
- **`_emit_if` use-before-define ordering bug**
  (`sim/compiled/_stmt_emitters.py`) -- `_emit_condition_lines_and_expr`'s
  wide-condition path can delegate a narrow-enough-to-fit sub-`TernaryOp`
  (e.g. `(a3 ? a2 : a6[16])` nested inside a larger wide-signal-touching
  condition) to the narrow emitter's `_et_pending` hoisting mechanism,
  which appends the computed `_et{n}_v = ...` line to a list flushed
  separately from the wide condition's own generated lines. `_emit_if`
  concatenated `[*cond_setup, *et_lines, ...]` -- `cond_setup` (which
  already CONTAINS a `wide_mux(..., _et0_v, ...)` call referencing the
  not-yet-computed value) came before `et_lines` (the actual
  computation), a genuine use-before-define bug in the generated Cython,
  confirmed via direct `.pyx` inspection. Every other `_et_pending`
  consumer in this codebase already puts `et_lines` first for exactly
  this reason. Confirmed wrong against Icarus for `if (a7 & ((a3 ? a2 :
  a6[16]) ? (a7 ? a2[1] : a6[9]) : (!a2))) ...`. Fixed by swapping the
  concatenation order to `[*et_lines, *cond_setup, ...]`.
- **Comparison operands missing context-width propagation**
  (`sim/evaluator.py`, `sim/vm/compiler.py`) -- `==`/`!=`/`<`/`<=`/`>`/`>=`
  operands were evaluated purely self-determined (no width propagated at
  all), which is fine for a plain operand but wrong whenever an operand
  is ITSELF a further context-determined operator (unary `-`, another
  `+`/`-`, a `$signed`/`$unsigned` cast): that nested operator needs the
  comparison's own shared width to propagate all the way down and apply
  its extension decision AT that width (two's-complement negation does
  not commute with a later zero-extension). Confirmed wrong against
  Icarus for `(-{2{a1}}) <= a4` (`a1` unsigned 8 bits, `a4` signed 64
  bits): negating `{2{a1}}` at its own 16-bit self-width THEN
  zero-extending the negation result gives a different value than
  zero-extending `{2{a1}}` to 64 bits first and negating at that width.
- **Comparisons need the operator's COMBINED signedness, not each
  operand's own individual signedness** (same two files) -- the initial
  fix for the bug above extended each comparison operand by its OWN
  individual signedness (mirroring `+ - *`'s already-correct model), but
  IEEE 1364-2005 §5.5.2 explicitly says relational/equality operands
  "affect each other as if they were context-determined operands with a
  result type ... determined from them" -- i.e. via §5.5.1's normal
  combining rule ("if any operand is unsigned, the result is unsigned"),
  the SAME "combined governs both uniformly" model already established
  for `/ %`, not `+ - *`'s per-operand model. Confirmed wrong
  (individual-signedness version) against Icarus for `(a5[5:2] < a0)`
  (`a5[5:2]` an unsigned part-select, `a0` a signed 1-bit register):
  sign-extending `a0` by its own signedness gave a different comparison
  outcome than zero-extending it per the comparison's combined
  (unsigned, since not both operands signed) decision. Fixed by
  computing `both_signed = _expr_signed(left) and _expr_signed(right)`
  once and forcing it as the `signed_override` into both operands'
  recursive evaluation, mirroring the `/ %` fix's own reasoning
  (`sim/compiled/_expr_emitter.py`'s narrow comparison path and
  `_wide_emitter.py`'s `_WIDE_CMP_PRIMS` path needed the identical
  combined-vs-individual correction, described further down).
- **Unary `-` wrongly inherited `~`'s "compute at the operand's own
  fixed width, then extend the RESULT" special case** (all four engines:
  `sim/evaluator.py`, `sim/vm/compiler.py`,
  `sim/compiled/_expr_emitter.py`, `sim/compiled/_wide_emitter.py`) --
  this special case (established earlier in the session, confirmed
  against Icarus for `$signed(~({a0, a6, a0} && a7))`) is correct for
  `~` specifically, because it is a bitwise, per-bit-independent
  operation: zero-extending a 1-bit value before complementing flips the
  newly-added padding bits too, which is wrong. Unary `-` is a genuine
  two's-complement ARITHMETIC negation, where zero-extending the operand
  and THEN negating gives exactly the modular wraparound representation
  of "minus that value" at the wider width -- which is what real
  hardware (and Icarus) actually compute. Confirmed wrong the other way
  (compute-at-1-bit-then-extend-result gives `1`, not Icarus's
  `all-ones`/-1) for `-(~&{2{(a5[5:2] < a0)}})` widened into a 96-bit
  destination. Fixed by restricting the fixed-width special case to `~`
  only; unary `-` (like `+`, a no-op either way) always falls through to
  the normal "widen operand to context width first" path.
- **`TernaryOp` branch selection blindly forwarding a caller's
  `width=0`** (`sim/evaluator.py`, `sim/vm/compiler.py`) -- the selected
  branch was evaluated/compiled at whatever `width` the ternary node
  itself was called with, including a bare `width=0` self-determined
  request (e.g. from `&&`/`||`, which never propagate a shared width
  into their own operands) -- this left a nested context-determined
  operator WITHIN the selected branch (e.g. `~a0`) unresized at its own
  narrow width (1 bit) instead of the ternary's TRUE combined width (the
  max of both branches' self-widths), silently flipping the branch's
  truthiness. Confirmed wrong against Icarus for `(((-a4[17:9]) ? (~a0) :
  a3) && a4)`: `~a0` computed at 1 bit (=0, falsy) instead of the
  ternary's own 63-bit width (=0xFFF...FFE, truthy) made the whole `&&`
  wrongly false. Fixed by computing `own_width = max(width,
  self_width(true_expr), self_width(false_expr))` once and using it as
  the floor for evaluating/compiling whichever branch is selected (or
  both, in the ambiguous-condition merge case).
- **Compiled `_emit_mask_expr` had no `$signed`/`$unsigned` case at all**
  (`sim/compiled/_expr_emitter.py`) -- every OTHER FunctionCall fell
  through to a generic "OR all argument masks at a hardcoded width of
  32" fallback, which the value-side `_emit_func_call`'s dedicated
  `$signed`/`$unsigned` handling never needed (it explicitly `_sign_ext`s
  the value from the argument's own self-width). The mask side never
  mirrored that sign-extension, so an unknown (masked) sign bit's
  ambiguity was silently dropped from the newly sign-filled upper bits,
  which then read back as spuriously DEFINED zero instead of x.
  Confirmed wrong (cross-engine, against the reference oracle) for
  `(|($signed(a2[6:1]) ^ a1))` with `a2` fully x: the top 2 bits of the
  8-bit XOR read as a defined `2'b11` instead of x, corrupting the
  reduction's truthiness and, downstream, an `if` statement's branch
  selection from ambiguous to spuriously true. Fixed by adding a
  dedicated `$signed`/`$unsigned` case that `_sign_ext`s (for `$signed`)
  or passes through unchanged (for `$unsigned`) the argument's own mask,
  exactly mirroring the value side.
- **Compiled's narrow ternary emitter computed branch masks at
  self-width, never sign-extending them to match the value side**
  (`sim/compiled/_expr_emitter.py`'s `_emit_ternary_value_mask_exprs`)
  -- `true_expr`/`false_expr` are computed at the FULL ternary width
  with the ternary's own combined signedness `_sign_ext`'d in via
  `_emit_expr`'s own internal logic, but `true_mask`/`false_mask` were
  computed via `_emit_mask_expr(expr.true_expr, tw)` at each branch's
  own self-width `tw` ONLY (`_emit_mask_expr` had no `signed_override`
  parameter to thread the same decision through). An unknown sign bit's
  ambiguity in a selected branch (e.g. a signed 1-bit x-valued register)
  never propagated into the value side's newly sign-filled upper bits,
  corrupting any downstream precision check that relied on those bits'
  mask (e.g. `==`'s own known-bit-differs short-circuit). Confirmed
  wrong for `(a2 ? a0 : a4)` with `a0` a signed 1-bit x-valued register.
  Fixed two ways: (a) added an optional `signed_override` parameter to
  `_emit_mask_expr` (mirroring `_emit_expr`'s), used in its `Identifier`
  case (which previously returned the raw mask register completely
  unaware of `width`/signedness) and threaded through the BinaryOp
  comparison/division mask computation the same way the value side's
  `combined_override` is; (b) in the ternary mask helper itself,
  explicitly `_sign_ext`s `true_mask`/`false_mask` to the full ternary
  width when `own_signed`, mirroring the value side's own extension.
- **Compiled's wide comparison emitter (`_WIDE_CMP_PRIMS` path,
  `sim/compiled/_wide_emitter.py`) had two related bugs** in the same
  area: (a) it passed each operand's own self-width (`lw`/`rw`) as
  `dst_width` into the recursive `_emit_wide_expr_to_scratch` call
  instead of the comparison's own SHARED width (`max(lw, rw)`) -- an
  Identifier operand narrower than the other operand only decides to
  sign-extend via the dedicated `wide_load_signal_s` primitive when its
  own self-width is less than the REQUESTED `dst_width`; passing its own
  self-width back as `dst_width` made that check always false, silently
  falling through to the plain (zero-filling) `wide_load_signal`
  instead, dropping an x/z sign bit's ambiguity from the extra words.
  (b) it used `use_signed` (deliberately scoped to `< <= > >=` only,
  since equality doesn't need a signed-vs-unsigned comparison PRIMITIVE)
  as the `signed_override` for OPERAND EXTENSION too -- but extension
  must respect combined signedness for `==`/`!=` exactly like the
  relational ops; `use_signed` being always-false for equality meant a
  narrower signed operand's own sign-extension never triggered
  regardless of its declared signedness. Confirmed wrong (cross-engine,
  against the reference oracle) for `(a0 == a6)` with `a0` a signed
  1-bit x-valued register and `a6` a large defined 80-bit signed value:
  `a0`'s un-sign-extended mask left bits 1-79 looking "definitely 0",
  so XOR-ing against `a6`'s own nonzero bits in that range falsely
  triggered `wide_cmp_eq`'s "known bit differs" short-circuit, resolving
  the comparison as definitely-not-equal instead of correctly ambiguous.
  Fixed by passing `max(lw, rw)` as `dst_width`, and by computing a
  separate `combined_signed` variable (covering all of `== != === !==
  < <= > >=`) used for operand-extension `signed_override`, keeping
  `use_signed` only for primitive selection.
- **Truncation bug in `evaluator.py`'s comparison/arithmetic/division
  branches** -- found by the full fast-suite regression itself (not the
  fuzzer): `target = max(..., _expr_self_width(left), ...)` uses a
  STATIC, AST-shape-based width estimate that can UNDER-estimate an
  operand's true width -- `_expr_self_width`'s `RangeSelect` case falls
  back to a bare `1` whenever msb/lsb aren't literal AST nodes, which is
  common for a parameter-expression bit range like
  `[PMP_ADDR_MSB:PMPGranularity+PMP_ADDR_LSB]` in generated/parameterized
  RTL (confirmed via `examples/ibex/rtl/ibex_pmp.sv`'s TOR address
  comparator). The operand's own `eval()` call independently and
  correctly computes its TRUE width by evaluating msb/lsb as
  expressions, so it can legitimately end up wider than this
  under-estimated `target` -- but the subsequent `if left.width !=
  target: left = left.resize(target)` fired unconditionally on ANY
  mismatch, including narrowing, and `.resize()` actively MASKS away
  real bits rather than just relabeling the width. Root-caused via
  `git stash push -- src/veriforge/sim/evaluator.py` bisection (isolated
  to evaluator.py), then progressively reverting individual hunks to
  narrow it down to the comparison branch specifically. Confirmed as the
  exact cause of 4 real fast-suite regressions:
  `test_ibex_pmp_multiphase_cross_engine[reference]`,
  `TestSignedDeclarationSupport::test_signed_net_comparison_works
  [compiled]`, `test_signed_var_comparison_works[compiled]`, and
  `TestSignedDeclarations::test_signed_comparison` (the latter three
  were a SEPARATE regression from the comparison combined-signedness fix
  above, needing its own explicit `_sign_ext(left, op_width)` at the
  dispatch point in `_expr_emitter.py`'s narrow comparison path, since
  `_emit_expr`'s Identifier case only sign-extends when `op_width >
  sig_width`, leaving the native-register upper bits un-sign-extended
  whenever the comparison's own `op_width` happened to already equal the
  operand's self-width). Fixed by changing the guard from `!=` to `<` in
  all three branches (comparison, division, arithmetic) -- only ever
  WIDEN, mirroring the pattern already used everywhere else in the file;
  never narrow a genuinely-wider-than-`target` operand back down.

All ten bugs were verified via direct Icarus comparison scripts and/or
`git stash` bisection, then confirmed clean across both statement-fuzzer
seeds (`VERIFORGE_DIFF_STMT_SEED=99999` and `424242`,
`VERIFORGE_DIFF_STMT_CASES=150`, `_STMT_COMPILED=1`, 30/30 batches each),
the original 300-case expression-tree fuzzer (30/30 batches,
`VERIFORGE_DIFF_COMPILED=1`), `test_power_operator.py` (60 passed, 1
xfail, unaffected), and a final full fast-suite regression (7107 passed,
1 xfailed, 0 failed, `-n 8`, ~30 min) -- including the 4 tests the
truncation bug had broken, all now passing individually and as part of
the full suite.

**Fourteenth wave (July 2026, work plan item 3.4 phase 3) -- case/casex/
casez statement fuzzing surfaces twelve more distinct bugs, half of them
pre-existing and only newly REACHABLE, not newly introduced, by the
generator's changed random-draw sequence**: extended
`test_differential_statements.py` to generate `case`/`casex`/`casez`
statements at eligible if/else-chain recursion points (`_STMT_CASE_PROB
= 0.35`), with 1-2 items per case, 1-2 values per item, always a
`default` arm, and deliberately width-mismatched selector/item literals
(selector via the same shape distribution as `test_differential.py`'s
leaf generator; item literal width = `sel_width + randint(-2, 2)`,
clamped to >=1) to exercise each engine's own pre-existing (and
different) truncation/extension behavior at the comparison boundary.
Default-scale runs (40 cases) stayed green throughout; stress-testing at
150 cases across five seeds (`99999`, `424242`, `13579`, `777777`,
`246810`) surfaced twelve distinct, real bugs, all confirmed against
Icarus and/or cross-engine agreement -- only two of them (the first two
below) are actually about case/casex/casez matching itself; the rest are
pre-existing bugs in comparison, shift, ternary, and division codegen
that the generator's case-statement branches merely shifted the RNG
sequence enough to reach for the first time:

- **`vm-fast`'s `OP_CMP_CASEX`/`OP_CMP_CASEZ` ignored wide (>64-bit)
  operands** (`sim/vm/_interp_fast.pyx`) -- both opcodes popped operands
  via `stack[sp].val`/`.mask` only, never checking `wflag` (the flag
  marking a stack slot's real data as living in the wide `wv`/`wm` word
  arrays instead of the narrow fields), the same "wide value ignored" bug
  class already fixed for `OP_TERNARY`'s condition truthiness in an
  earlier wave. Confirmed wrong against Icarus for a `casez` matching a
  65-bit selector against 66-bit/65-bit wildcard literals. Fixed by
  rewriting both opcodes to mirror `OP_CMP_EQ`/`OP_CMP_NE`'s already-
  correct wide-promotion pattern: if either operand's `wflag` is set,
  promote the other to wide (filling from its narrow `.val & ~.mask`),
  then compare word-by-word with `wm[i] = am[i] | bm[i]` (don't-care)
  masking; casez is treated identically to casex in this codebase's
  3-state model (x and z share one representation), a separately-
  documented residual gap versus real IEEE casez's stricter rule.
- **Compiled plain `case` statement matching ignored x/z entirely**
  (`sim/compiled/_stmt_emitters.py`'s `_emit_case`) -- unlike `casex`/
  `casez` (which at least attempted don't-care masking), the plain-`case`
  branch compared item values with a bare value-only `sel == val`, never
  checking either side's mask -- an x/z-bearing selector's "stored as 0"
  convention could accidentally equal a same-valued known item, wrongly
  matching instead of correctly falling through to `default` (real
  `case` uses exact 4-state `===` matching, the opposite of casex/casez's
  wildcarding). Confirmed against Icarus for `case (a2[14]) 1'b0: ...
  default: ...` with `a2` fully x. Fixed by computing each side's mask
  too (`_emit_mask_expr`, width-aware and consistent with the item
  literal's already-width-aware value emission, unlike the casex/casez
  path's own-natural-width-only `_emit_expr_mask` helper) and requiring
  BOTH value and mask to match exactly.
- **Compiled `_emit_expr_mask` had no `RangeSelect` case at all**
  (`sim/compiled/_expr_emitter.py`) -- this helper (used by casex/casez
  selector and item masks) handled `Identifier`/`Literal`/`BitSelect` but
  silently fell through to a hardcoded `"0"` (always-known) for any
  multi-bit part-select, e.g. a `casex (a5[39:2])` selector -- an entire
  x-valued signal read as fully known, making a wildcard item wrongly
  fail to match. Confirmed against Icarus for `casex (a5[39:2]) 37'b1z11
  xx00...: ...` and `casez (a4[44:1]) 45'bxx000xx00...: ...`, both with
  the base signal fully x. Fixed by adding a `RangeSelect` case mirroring
  `_emit_expr`'s own value-side handling (same Identifier-vs-struct,
  literal-vs-variable-msb/lsb branching), with `mask=True` threaded into
  `_emit_signal_slice_expr` instead of the value-side sign-extension.
- **Compiled narrow-path comparisons and XOR/XNOR broke the `if`/`for`/
  `while` "value already reads 0 wherever ambiguous" convention**
  (`sim/compiled/_expr_emitter.py`) -- `_emit_condition_lines_and_expr`
  (used by every statement-level condition) deliberately reads only the
  raw VALUE, never combining it with the mask, relying on every value-
  emitting operator upholding that convention on its own (already true
  for `+`/`-`/`==`-with-known-diff/`&&`/`||`, and trivially true for `&`/
  `|` since ANDing/ORing an x-as-0 bit can never spuriously read
  nonzero) -- but comparisons (`==`/`!=`/`<`/`<=`/`>`/`>=`) and XOR/XNOR
  (`^`/`~^`/`^~`) never enforced it: XOR has no absorbing bit value, so
  an x bit (stored as 0) XORed against the OTHER operand's known-1 bit
  gives a spurious raw value=1 at a position that's actually ambiguous;
  comparisons likewise computed a raw C `==`/`<`/etc. on the value fields
  alone with no ambiguity check at all. Confirmed against Icarus for `if
  (($unsigned(a2[5]) <= a1[6:1]) ^ (...))` and `if ((a2 == a2))`, both
  with `a2` fully x -- wrongly took the true branch. Fixed by reusing
  `_emit_mask_expr`'s own (already-correct, including its `==`/`!=`
  known-differing-bit short-circuit) mask formula and wrapping every
  comparison's value with `0 if (that mask) else (raw core)`; XOR/XNOR
  got the analogous `core & ~(left_mask | right_mask)` bitwise fix.
- **Comparison value-side masking (the fix immediately above) caused
  exponential code-size blowup on deeply-nested real-world comparison
  chains** (`sim/compiled/_expr_emitter.py`) -- the new mask lookup is a
  FRESH, unhoisted `_emit_mask_expr` call at every comparison node, and
  that helper's own `==`/`!=` known-diff formula independently re-emits
  `_emit_expr` on the SAME operands the value side already computed --
  for a comparison nested inside another comparison's operand (common in
  real CSR/control logic, never exercised by the fuzzer's shallow
  synthetic trees), each level doubles the generated string size, giving
  2^depth blowup. Confirmed via the real `ibex_cs_registers` design:
  compiling `test_ibex_cs_registers_assignment_patterns_cross_engine
  [compiled]` and `..._mml_exec_suppression_cross_engine[compiled]`
  (caught by the full fast-suite regression, not the fuzzer) went from
  completing in seconds to exhausting 17+ GB of RAM and still climbing.
  Fixed by hoisting both the derived value and mask into named `_et{n}_v`/
  `_et{n}_m` temps and registering them in `_et_node_vals`/
  `_et_node_masks` when `_et_pending is not None`, exactly mirroring the
  existing `TernaryOp` hoist block a few lines above (whose own comment
  already names this precise failure mode: "preventing 2^k recursion in
  right-recursive chains") -- a later query for the same node, from
  anywhere, now hits the existing cache checks in `_emit_expr`'s BinaryOp
  dispatch and `_emit_mask_expr`'s comparison branch instead of
  re-expanding.
- **Compiled `>>`'s forced-unsigned override leaked into a NESTED `&`'s
  own per-operand extension** (`sim/compiled/_expr_emitter.py`,
  `_wide_emitter.py`) -- `>>` forces `signed_override=False` onto its
  left operand's recursive evaluation (correct: `>>` is always a logical
  shift regardless of the operand's own declared type), but when that
  left operand is itself a natural-width bitwise op like `(a6 & a0)`,
  the forced `False` was ALSO forwarded into `&`'s own internal operand
  typing decision (via the same `combined_override`/`div_mod_override`
  forwarding mechanism division legitimately needs deep-threaded) --
  `a0`'s OWN sign-extension (needed to correctly compute `a6 & a0`'s
  VALUE, a self-contained Verilog sub-expression whose result is fixed
  by each of its own operands' types, independent of whatever operator
  later consumes it) got zero-extended instead. Confirmed against Icarus
  for `(a6 & a0) >> a7` with `a0` a signed 1-bit register: `a0` must
  sign-extend to -1 (giving `a6 & a0 == a6`), but the leaked override
  zero-extended it to +1 (giving `a6 & 1`). Fixed by only forcing the
  override when the left operand's own natural width is actually
  narrower than the shift's requested width (the one case an outer
  unsigned-widening decision genuinely needs to reach this deep); when
  no widening is needed, pass `None` so the nested op's own operand
  typing is undisturbed.
- **Compiled narrow-path shift mask returned the operand's raw UNSHIFTED
  mask** (`sim/compiled/_expr_emitter.py`'s `_emit_mask_expr`) -- for a
  known shift count, `<<`/`>>`'s mask fallback returned `lm` (the left
  operand's own mask) completely unshifted, when `<</>>` shift in KNOWN-
  zero bits (never x/z) at the vacated end -- a shift-in-zero position
  still read as ambiguous whenever the ORIGINAL (pre-shift) bit at that
  position happened to be x. Confirmed against Icarus for `~&((a2[6] ?
  a0 : a3) << a4[29:26])` with `a3` fully x: the shift legitimately
  produces known-0 low bits (forcing the NAND-reduction definitely 1),
  but the unshifted mask hid that, making the reduction read as fully
  ambiguous (0, per the convention above) instead. Fixed by actually
  shifting `lm` by the same amount (`<<`/`<<<`) or logically
  right-shifting it (`>>`), matching the value side's own shift
  computation; `>>>`'s sign-filled vacated bits are left as a separately
  documented residual gap (not addressed by this fix).
- **Compiled ternary's own combined-branch signedness leaked into a
  NESTED `+`/`-`/`*` branch's own operand extension** (`sim/compiled/
  _expr_emitter.py`, `_wide_emitter.py`) -- the ternary's `own_signed`
  (correct for WIDENING a selected branch's independently-computed value
  up to the ternary's combined width) was ALSO threaded as
  `signed_override` into the branch's entire recursive evaluation --
  harmless for a `&`/`|`/`^` branch (which has a genuinely separate later
  widening step this override legitimately governs) but wrong for a
  CONTEXT-DETERMINED `+`/`-`/`*` branch, which has NO separate widening
  step at all (it's requested directly at the ternary's width, so the
  override only ever reaches operand-level typing) -- a signed operand's
  own extension got silently overridden by the ternary's unrelated
  combined-unsigned decision. Confirmed against Icarus for `cond ? a5 :
  ({3{{a5, a7, a0}}} - a2)` with `a5` unsigned and `a2` a signed
  identifier: the ternary's combined type is unsigned (replication is
  always unsigned, so not both branches are signed), and forwarding that
  into the subtraction forced `a2` to zero- instead of sign-extend, even
  though IEEE governs `a2`'s own extension by its own declared type here,
  independent of the ternary. Fixed by passing `None` instead of
  `own_signed` specifically when a branch is directly a `+`/`-`/`*`
  BinaryOp.
- **Compiled wide comparison's `$signed(...)`-unwrap optimization broke
  the cast's self-determined-width barrier for compound arguments**
  (`sim/compiled/_wide_emitter.py`'s `_WIDE_CMP_PRIMS` path) -- to avoid
  a redundant sign-extension step, the comparison dispatch unwraps
  `$signed(x)` down to `x` and recurses `x` directly at the comparison's
  own (wider) shared width with `signed_override=combined_signed`
  forced -- correct for a plain leaf `x` (Identifier/Literal/BitSelect/
  RangeSelect/PartSelect, where "read directly at the wider width" and
  "read at own width then extend" are equivalent, no operator in between
  to make the order matter) but wrong for a compound `x` involving a
  context-determined operator: negating a small UNSIGNED value AFTER
  zero-extending it to a much wider width wraps around to a huge
  magnitude-near-2^width result, whereas the cast's real IEEE 1364-2005
  Table 5-22 self-determined semantics (already correctly implemented in
  this same file's general `FunctionCall` handler, just bypassed by this
  unwrap) negate at the operand's own narrow width first and only then
  extend -- an entirely different value. Confirmed against Icarus for
  `($unsigned({a0, a6}) < $signed((-a4[9:5])))`: unwrapping and negating
  `a4[9:5]` directly at the comparison's 81-bit combined width gave a
  huge (~2^81) wraparound value instead of the correct small negated-at-
  5-bits-then-extended one. Fixed by restricting the unwrap to leaf `x`
  only; a compound `x` now falls through to the un-unwrapped `$signed(
  ...)` FunctionCall, routing through the already-correct general
  handler instead.
- **Compiled division/modulus wrongly grouped with the "residue-safe"
  `+`/`-`/`*` context-determined treatment** (`sim/compiled/
  _wide_emitter.py`) -- `/`/`%`'s `op_width` was computed as `dst_width`
  directly (same as `+`/`-`/`*`), on the theory that context-determined
  arithmetic is safe to truncate to the enclosing width and widen the
  result afterward. That IS true for `+`/`-`/`*` (`(a+b) mod N == ((a mod
  N) + (b mod N)) mod N` holds unconditionally, a basic modular-
  arithmetic identity) but NOT for division (`(a/b) mod N != ((a mod N)/
  (b mod N)) mod N` in general) -- truncating the DIVIDEND to `dst_width`
  before dividing silently changes the quotient whenever the dividend's
  own natural width (e.g. a wide concatenation) exceeds the enclosing
  assignment's width. Confirmed against Icarus for a 202-bit-wide
  concatenation divided by `(a6[44:0] | 1)` inside a 96-bit assignment --
  computing the concatenation at 96 bits before dividing discarded the
  high bits the correct quotient depended on. Also had to widen
  `prim_width` (the `wide_div`/`wide_mod` primitive's OWN `dst_width`
  argument, which bounds how many bits the restoring-division algorithm
  itself iterates over, not just output tail-masking like the bitwise/
  arithmetic primitives) the same way -- capping it back down to
  `dst_width` would have silently discarded the same high dividend bits
  `op_width` was just widened to keep. Fixed by computing `op_width =
  max(dst_width, expr_width(left), expr_width(right))` for `/`/`%`
  specifically, mirroring the shift left-operand's identical
  `l_dst_width = max(lw, dst_width)` widening for the same underlying
  reason.
- **`vm`/`vm-fast`'s bytecode compiler truncated a shift's left operand
  to the enclosing context width before widening it** (`sim/vm/
  compiler.py`) -- for `>>`/`<<`/etc., the left operand was compiled
  directly at the (possibly narrower) outer `width`, and only
  AFTERWARDS resized up to `target = max(width, self_width(left))` --
  but `_compile_expr` for some node types (confirmed: `Replication`)
  directly SIZES its result to whatever width it's asked to compile at,
  so the narrower `width` already discarded bits above it DURING
  compilation, before the later resize could recover them (widening
  afterward only zero-pads on top of an already-truncated value).
  Confirmed against Icarus (cross-engine, vm/vm-fast vs reference/
  compiled) for `{2{$unsigned(a5)}} >> (a shift amount between the
  enclosing 96-bit context width and the replication's own true 130-bit
  width)`: bits 96-129 of the replication (needed once shifted down into
  the visible low bits) were silently dropped. Fixed by computing
  `target` BEFORE compiling the left operand and requesting it directly
  at `target`, not `width`.

All twelve bugs were verified via direct Icarus comparison scripts and/or
cross-engine agreement, then confirmed clean across five statement-fuzzer
seeds (`VERIFORGE_DIFF_STMT_SEED` = `99999`, `424242`, `13579`, `777777`,
`246810`; `VERIFORGE_DIFF_STMT_CASES=150`, `_STMT_COMPILED=1`, 30/30
batches each), the original 300-case expression-tree fuzzer (30/30
batches, `VERIFORGE_DIFF_COMPILED=1`), `test_power_operator.py` (60
passed, 1 xfail, unaffected), and a final full fast-suite regression
(7107 passed, 1 xfailed, 0 failed, `-n 8`, ~30 min) -- including the two
`ibex_cs_registers` tests the exponential-blowup bug had broken, now
completing in ~20s each instead of exhausting memory.

**Fifteenth wave (July 2026, work plan item 3.4 phase 4) -- `for`/`while`
loop statement fuzzing surfaces eleven more distinct bugs, most of them
pre-existing and only newly REACHABLE (the "phase N's grammar addition
surfaces phase-(N-1)-and-earlier gaps" pattern seen in every prior
phase), not newly introduced, by loops making a statement body execute
MORE THAN ONCE per always-block evaluation for the first time**:
extended `test_differential_statements.py` to generate `for`/`while`
loops at eligible recursion points (`_STMT_LOOP_PROB = 0.3`), `for` via
SystemVerilog's inline loop-variable declaration, `while` via a
module-level `integer` counter bounded by the same small `N` ANDed with
a genuinely data-dependent condition to exercise early exit without
risking non-termination. Default-scale runs (40 cases) stayed green
throughout; stress-testing at 150 cases across 14 seeds (the five
carried over from phase 3, plus nine fresh ones -- `999111`, `555000`,
`888222`, `111333`, `314159`, `500500`, `42424242`, `13131313`,
`90909090`) surfaced ten distinct bug findings (several bundling more
than one instance of the identical bug shape found together), all
confirmed against Icarus and/or cross-engine agreement -- the first
three found a genuine hard crash (a stack buffer overflow, not just a
wrong-answer bug) before any wrong-answer bug was even reachable:

- **Compiled `wide_mul`'s fixed 16-word `tmp` buffer was written before
  its own `n > 16` overflow guard ran** (`sim/compiled/
  _gen_wide_section.py`) -- the original code zeroed `tmp[i]` inside the
  FIRST loop, which ran unconditionally for the caller-supplied `n`
  (the shared per-module `_dynamic_max_wide_words` scratch-array word
  count, not this multiplication's own operand width) before the guard
  checked whether `n` actually fit in `tmp`'s 16 words -- a real stack
  buffer overflow, confirmed via glibc's `_FORTIFY_SOURCE`/
  `__fortify_fail` aborting the process, triggered by an UNRELATED
  sibling statement's own extreme width (a triple-nested 3x replication
  of an 80-bit ternary, `{3{{3{{3{(a0 ? a3 : a6)}}}}}}}`, true width 2160
  bits, needing 34 words) inflating the shared scratch size past 16 for
  a completely separate multiplication elsewhere in the same module.
  Fixed by moving the zero-fill loop to after the guard.
- **`wide_div`/`wide_mod` had the identical fixed-16-word-buffer-written-
  before-its-own-guard bug** (`Rv[16]`, same file) -- found by direct
  code reading immediately after the `wide_mul` fix above, same root
  cause, same fix shape. The FIRST attempt at this fix was itself
  incomplete for `wide_mod`: the buggy `Rv[i] = 0` line was correctly
  REMOVED from the premature first loop, but no replacement loop was
  added after the guard, leaving `Rv` completely UNINITIALIZED for the
  rest of the function -- caught by re-running the seed-999111 batch
  suite after the fix and finding `wide_mod`'s restoring-division
  algorithm now reading stack garbage instead of crashing or corrupting
  memory out of bounds.
- **`wide_mul`/`wide_div`/`wide_mod` conflated the CALLER's shared
  scratch-buffer word count with the number of words THIS operation
  actually needs, even after the crash fix above** (same file) -- the
  `n > 16` bail-to-all-x guard (a correctness-preserving fix for the
  crash above) used the inflated shared `n` directly, so ANY
  multiplication/division/modulus sharing a module with the 2160-bit
  sibling from the crash bug -- including a completely trivial `(2 *
  a0)` needing only 1-2 words of its own -- unconditionally bailed out
  to all-x. Confirmed against Icarus for `(2 * a0)` sharing a module
  with the 2160-bit sibling: `a0=0` should give `0`, but the inflated
  `n=34` forced an all-x result regardless of the actual (tiny) operand
  widths. Fixed by computing each primitive's own `eff_n =
  ceil(dst_width / 64)` (product truncation for `*` is residue-safe, so
  only the low `dst_width` bits of each operand can affect the result;
  `/`/`%`'s `dst_width` is already the widened dividend-width from an
  earlier wave's fix) and checking `eff_n > 16` instead of `n > 16`,
  using `eff_n` for every internal loop bound while still zero-filling/
  masking the full caller-sized `n` words on the way out.
- **Compiled `wide_load_signal`'s scalar (`words == 0`) branch never
  masked `c.val[sid]`/`c.mask[sid]` to the signal's own declared width**
  (same file) -- a narrow SIGNED signal's internal storage can carry its
  value already sign-extended across the full `long long` word (e.g. a
  1-bit signed register holding `1`, which equals `-1` in one-bit two's
  complement, stored with every bit of the word set), so this
  supposedly ZERO-extending load let those stale high bits leak straight
  through whenever a caller explicitly requested zero- (not sign-)
  extension. Confirmed against Icarus for `(a5[8] % (a0 | 1))` used as
  an `if` condition, with `a0` a signed 1-bit register: the `%`
  operator's own `div_mod_override` mechanism (from an earlier wave)
  correctly requested a zero-extending load for `a0`, but without this
  mask its value still carried its 1-bit `1`/`-1` sign-extended across
  the whole word, silently reintroducing the sign-extension the override
  was meant to suppress and flipping the whole `if` condition's outcome.
  Fixed by masking `dv[0]`/`dm[0]` to `wmask(c.width[sid])` in this
  branch, mirroring `wide_load_signal_s`'s already-correct scalar-branch
  handling.
- **`vm-fast`'s `OP_BIT_NOT`/`OP_BIT_XNOR` masked only the TOP word of a
  wide result after inverting, never zeroing the words ABOVE it**
  (`sim/vm/_interp_fast.pyx`) -- both opcodes invert every word in the
  fixed `WIDE_WORDS`-sized buffer unconditionally, including words
  beyond the value's own width that a well-behaved producer leaves
  zero-filled; inverting a clean zero word turns it all-ones, and the
  existing "mask top word" step never reached the words above it,
  leaving that all-ones garbage in place permanently. A later
  truthiness consumer that scans the FULL fixed word buffer without its
  own width bound (`OP_JUMP_IF_ZERO`/`OP_JUMP_IF_NONZERO`, `OP_TERNARY`'s
  wide-condition check) then misreads a genuinely-zero value as nonzero.
  Confirmed against Icarus for `if ((cond ? a2 : (~(-(a4 / (a5 |
  1))))))` where the `~` operand's true value is `0` (`a5` is 65 bits,
  so `WIDE_WORDS` words 2-5 are unused/zero-filled before the `~`, then
  flipped to all-ones by it) -- a plain ASSIGNMENT of the identical
  expression happened to "launder" this through a subsequent `OP_RESIZE`
  (which DOES zero every word above its own top word) before storing,
  masking the bug there but not when the same value was consumed
  directly as a jump condition. Fixed by zeroing every word above the
  top word in both opcodes, mirroring the pattern `OP_NEG` already used
  correctly a few cases above. Also hardened `OP_JUMP_IF_ZERO`/
  `OP_JUMP_IF_NONZERO`/`OP_TERNARY`'s wide-condition check to bound
  their own scan by the value's declared width instead of the fixed
  buffer size, as defense-in-depth against any other unfound producer of
  this same shape.
- **Compiled `case`/`casex`/`casez` truncated an over-width item literal
  to the SELECTOR's own width instead of widening the selector**
  (`sim/compiled/_stmt_emitters.py`'s `_emit_case`) -- `sel_w` was
  computed purely from the case expression's own self-determined width,
  silently discarding any item literal wider than that (Verilog widens
  the NARROWER operand at comparison time; it never truncates the wider
  one). Confirmed against Icarus for `casez (a4)` whose first item is a
  66-bit literal but `a4` itself is only 64 bits wide, with `a4` fully
  x: widening `a4` (sign-extended, so also x in the new top bits)
  correctly keeps every engine falling through to `default`, but
  truncating the 66-bit literal to 64 bits discarded its definite top
  two bits (`'1'`,`'0'`) and let it spuriously match. Fixed by computing
  `sel_w` as the max over the case expression's own width AND every
  item literal's own width, routing to a new `_emit_case_wide` path
  (below) whenever that max exceeds 64 bits.
- **The new `_emit_case_wide` path's own match-check computation was
  unsound against nested wide assignments inside an item's body** (same
  file) -- the first implementation computed each item's wide
  scratch-array comparison and left it as a raw inline expression
  (`_sc{slot}_v[wi] == ...`) referenced later inside a subsequent
  `elif`, on the assumption the scratch data would still be valid by
  the time that `elif` is reached -- but `_emit_wide_lhs_write_new`
  (any wide blocking/NBA assignment) unconditionally calls
  `_reset_scratch()` at its own start, so an EARLIER item's own body
  (if it contains a wide assignment, which for/while loops make far
  more likely by executing a body multiple times with different
  targets) can silently reuse and overwrite the SAME scratch-slot
  indices a LATER item's match check still needed to read. Confirmed
  against Icarus for a `casex` whose first item's own body contained an
  if/else with two wide (96-bit) blocking assigns: the SECOND item's
  match check got corrupted by the first item's own body execution even
  though the first item never actually matched. Fixed by restructuring
  into two passes: first compute every item's match check and reduce it
  to a single `cdef int _casematch{n}` local (a plain stack variable
  outside the `_sc{n}` scratch pool, immune to any number of later
  scratch resets) before any item body is emitted, then build the
  if/elif chain referencing only those flags in a second pass. (`cdef
  int`, not `cdef bint`: `_gen_sections.py`'s existing
  `_hoist_inline_cdefs` -- already required because Cython forbids
  `cdef` inside conditional blocks, exactly the position this match
  flag needs to live in for a case nested inside another's `default`
  arm -- only recognizes `int`/`long long`/`unsigned long long` via its
  regex, not `bint`; an unrecognized type passes through unhoisted and
  fails to compile.)
- **Both the new wide-case path AND the pre-existing narrow-case path
  sign-extended instead of zero-extending when widening the case
  expression or an item literal** (same file) -- once the truncation fix
  above started genuinely widening `sel_w` beyond either operand's own
  natural width (a scenario the narrow path had never actually been
  exercised at before, since it previously just silently truncated
  instead), `_emit_expr`'s/`_emit_wide_expr_to_scratch`'s DEFAULT
  extension behavior (natural signedness) sign-extends whenever a
  signed operand's sign bit is DEFINITELY known-1 -- only an AMBIGUOUS
  sign bit degenerates to a zero-extend, via the unrelated "value reads
  0 wherever ambiguous" convention, masking this exact bug for a fully-x
  selector but not a known one. Confirmed against Icarus for `casex
  (a0)` with a signed 1-bit `a0 == 1` (i.e. `-1`) against a 3-bit item
  `3'b0zz`: sign-extending `a0` gives `111`, mismatching the pattern's
  definite leading `0`, but Icarus zero-extends it to `001`, correctly
  matching regardless of `a0`'s own declared signedness. Fixed by
  passing `signed_override=False` explicitly for both the selector and
  every item value in both the narrow and wide case paths.
- **Compiled narrow-path shift-amount overflow guards compared the
  computed amount as SIGNED instead of UNSIGNED** (`sim/compiled/
  _expr_emitter.py`, both the value-side shift core and the mask-side
  `lm_shifted` helper) -- the `0 if (amount) >= 64 else (... shift ...)`
  guard (added in an earlier wave specifically because a native C shift
  instruction only consults the low 6 bits of the count) compared
  `amount` as a `long long`, but Verilog shift amounts are always an
  UNSIGNED magnitude: a shift-amount expression computed via negation of
  a large positive value (e.g. `x << (-a4)` with `a4` a large positive
  64-bit signed value) produces a bit pattern that reads as a huge
  magnitude when interpreted as unsigned but as a large NEGATIVE `long
  long` when compared as signed -- `(-huge) >= 64` is then false,
  letting a genuinely out-of-range shift amount slip past the guard into
  an ACTUAL negative-count shift (undefined behavior in C, not just a
  wrong-answer bug). Confirmed against Icarus for `$signed((a1 * a1) <<
  (-a4))` used as a ternary's own condition, embedded in a wider
  assignment. Fixed by casting the compared amount to `unsigned long
  long` in both guards.
- **Compiled wide `wide_load_signal_s` never masked its sign-extension
  down to the caller's requested `dst_width` when that width wasn't a
  whole-word multiple** (`sim/compiled/_wide_emitter.py`'s Identifier
  branch of `_emit_wide_expr_to_scratch`) -- the primitive itself only
  understands whole WORDS, always sign-filling every bit through the end
  of its last word regardless of how many of those bits actually belong
  to `dst_width` (mirroring the identical, already-fixed
  `wide_sign_extend`/`_wide_sign_extend_to_dst_lines` gap from an
  earlier wave, but never applied to this sibling primitive) -- for a
  1-bit signed operand widened to a 2-bit comparison context (`n_words =
  1`), the sign-extend fills the ENTIRE 64-bit word, not just the low 2
  bits, leaving extra high-order 1 bits set beyond `dst_width` that the
  caller assumed were 0. Confirmed against Icarus for `($signed(a3[8:7])
  != $signed(a7))` widened to a 256-bit destination: `a7` (1-bit)
  sign-extended via `wide_load_signal_s` to the full 64-bit word instead
  of just the comparison's own 2-bit width, so its raw word no longer
  bit-for-bit matched the OTHER (correctly 2-bit-masked, via a separate
  code path) operand's word even though both represented the same 2-bit
  value, spuriously making `!=` true. Fixed by adding the same explicit
  tail-masking step `_wide_sign_extend_to_dst_lines` already uses,
  directly after the `wide_load_signal_s` call.

All ten were verified via direct Icarus comparison scripts and/or
cross-engine agreement, then confirmed clean across fourteen statement-
fuzzer seeds (the five from phase 3 plus `999111`, `555000`, `888222`,
`111333`, `314159`, `500500`, `42424242`, `13131313`, `90909090`;
`VERIFORGE_DIFF_STMT_CASES=150`, `_STMT_COMPILED=1`, 30/30 batches each),
the original 300-case expression-tree fuzzer (15/15 batches,
`VERIFORGE_DIFF_COMPILED=1`), `test_power_operator.py` (60 passed, 1
xfail, unaffected), and a final full fast-suite regression (7107 passed,
1 xfailed, 0 failed, `-n 8`, ~29.5 min, no regressions).

### Wide/narrow arithmetic right shift (`>>>`) X-propagation

**Status**: Resolved (July 2026, work plan item 2.4). Formerly tracked in
its own plan file (`x_prop_work.md`, under `notes/plans/`), now removed
since its checklist is complete.

Reference, the pure-Python VM (`sim/vm/interpreter.py`), and the Cython VM
(`sim/vm/_interp_fast.pyx`, both narrow and wide opcode paths) each had
their own copy of an overly-conservative rule: if *any* bit of the `>>>`
operand was x, the entire result became x. Correct (IEEE 1364/1800)
semantics are precise — only bits actually derived from an unknown bit
(the shifted-through bits, plus the vacated top bits when the sign bit
itself is unknown) become x. Fixed in all three by extending the operand
to `(width + shift)` bits (sign-filling the top with correct x propagation
from the sign bit, using `Value.sign_extend`), then logically shifting —
`(a.sign_extend(width + shift) >> shift).resize(width)` — which reproduces
arithmetic-shift-right exactly, including precise x-propagation, using only
already-correct primitives. The compiled engine's `wide_ashr` (in
`_gen_wide_section.py`) had a matching conservative workaround (added to
match the then-buggy VM) that was reverted once the VM was fixed.

**Additional regression found and fixed while verifying the above**: a
separate, hand-written fast-path template family — `_whole_{assign,stage}
_sar_{op}_signal` in `sim/compiled/templates/{narrow_assign,narrow_stage}.pxi`
(30 functions total; used for patterns like `$signed(a | b) >>> N`,
bypassing the generic wide-emitter path) — had the identical conservative
bail-out, independently of `wide_ashr`. This wasn't part of the original
plan (which only knew about `wide_ashr`) and only surfaced as a compiled-
vs-vm mismatch once the VM became precise. Every affected function already
computed a fully precise 4-state mask for its bitwise combination (and,
or, xor, and mixed-with-xor variants) *before* the premature bail — the
fix was to delete the dead `if combined_mask != 0: bail-to-all-x; return`
line in each, letting the already-correct shift+sign-fill logic underneath
run instead (verified empirically before applying at scale: two functions
were fixed and tested individually first). The `add`/`sub` variants of this
same family (`_whole_assign_sar_add_signal`/`sar_sub_signal`) were
deliberately left untouched — they don't track a precise mask at all
(arithmetic carry propagation is harder), and this matches `Value.__add__`/
`__sub__`'s own equally-conservative behavior, so it isn't an inconsistency.

### Declared signedness is now honored (all engines)

**Status**: Resolved (June 2026)

The model carries `signed` through parse and elaboration (`Net.signed` /
`Variable.signed`), and all engines now respect it via IEEE 1364-2005 §5.5
expression-signedness propagation.  Signed comparison, arithmetic right-shift,
context-determined sign-extension, and assignment sign-extension all activate
when signal operands carry a declared-signed `True` flag — not only through
explicit `$signed()` calls.

See `tests/test_sim/test_testbench.py::TestSignedDeclarationSupport` for the
validation tests.

### x and z share one representation (3-state, not 4-state)

**Status**: By design — documented limitation

`sim/value.py` encodes x and z identically (`Value.z()` returns `Value.x()`).
Consequences: `===`/`!==` cannot distinguish x from z, tristate buses,
pullups, and high-impedance detection are not simulatable. This is a
deliberate RTL-subset trade-off (consistent with the support matrix's
"strength and tristate resolution: low priority"), but note that docs and
docstrings describing the simulator as "4-state" overstate it slightly.

### Icarus first-activation x-extension artifact (investigated, not a bug — do not replicate)

**Status**: Investigated and closed — not a simulator bug.
**Found**: triaging two fuzzer-generated mismatches deferred during the
`settle()`-bootstrap (Bug 2) work on the `random-verilog-gen` branch
(`mismatch_01066`, `mismatch_01045`).

`mismatch_01066` (an `o7`/`o8` self-referential combinational feedback
case) is now fixed — confirmed via `--repro`, all four engines match
Icarus's originally-recorded expected value, as a side effect of the
`settle()` bootstrap-convergence fix.

`mismatch_01045` looked like a second, distinct bug: `always @(*) o10 = r7
!= $signed(o9[0]);` with `r7`/`o9` both entirely undriven (all-x). Icarus's
original run recorded `o10` as fully ambiguous (all 63 bits x); all four
engines here agree with each other but only mark bit 0 ambiguous (the rest
deterministically zero, i.e. zero-extending the comparison's self-
determined-1-bit-x result — matching `!=`'s IEEE-unsigned-result rule and
this codebase's own established, differential-fuzzer-verified extension
logic from work plan item 2.7's ninth wave).

Minimal, isolated reproduction against Icarus directly
(`iverilog -g2012`) shows this is specific to a combinational `always`
block's **very first activation**, not a general procedural-vs-continuous
assignment-extension rule:
- `assign y = (a != b);` (continuous) with `a`,`b` both x: `y = 0000000x`
  (correct, deterministic zero-extend) — always, regardless of declared
  signedness.
- `always @(*) y = (a != b);` / `always @(a or b) y = (a != b);` (implicit
  or explicit sensitivity, identical result) with `a`,`b` both x **on the
  block's first-ever evaluation**: `y = xxxxxxxx` (fully ambiguous).
- The SAME `always @(*)` block, once it has already evaluated at least
  once with a fully-defined (non-x) result (e.g. after `a=b=0` settles
  `y=00000000`), then re-evaluated with `a`,`b` returned to x: `y =
  0000000x` — the CORRECT, deterministic zero-extend, matching the
  continuous-assign case exactly.
- A plain blocking assign inside an `initial` block (`y = 8'b11110000; a =
  1'bx; y = a;`, no `always` block involved at all) also gives the
  correct `0000000x`, even with pre-seeded non-zero "garbage" upper bits —
  ruling out any "leave prior bits untouched when ambiguous" theory.

Conclusion: Icarus has a first-activation-specific quirk where a
combinational always block's very first evaluation, if its RHS is an
ambiguous self-determined-1-bit value, writes the WHOLE destination as x
instead of correctly zero-extending — every subsequent (re-)evaluation of
the identical block, and every continuous-assign equivalent, is
deterministic and matches the zero-extension rule this codebase already
implements. This is not a documented IEEE requirement (extension rules
are expression-level, independent of assignment kind or activation
count), Verilator cannot be used to cross-check it (it does not model x
as a true third state), and reproducing it would require our engines to
track "has this specific always block's specific destination reg ever
been driven by a fully-defined value before" as extra hidden per-signal
state purely to match a one-off Icarus artifact — actively wrong, not a
fix. **Do not attempt to make this match Icarus.** Our current zero-
extension behavior (shared, engine-independent, and already validated by
the differential fuzzer across thousands of cases) is treated as correct.

### Compiled engine: 64-bit signal width limit

**Status**: Partially resolved
**Found**: Ibex simulation work (March 2026)
**Severity**: High — blocks real-world AXI/wide-bus designs

The compiled Cython engine originally stored all signals as C `long long` (64-bit),
which truncated wider values. That limitation is now only partially true: external
signal round-trips for `width > 64` have boundary support, but internal compiled
expression, assignment, NBA, and dirty-propagation codegen still has remaining
single-word assumptions. This still affects:

- AXI data buses (128, 256, 512 bits)
- Wide memory interfaces
- Large concatenations exceeding 64 total bits

The **reference** and **VM** engines use Python `int` and handle arbitrary widths
correctly. The remaining limitation is compiled-engine-specific.

See `notes/plans/architecture_review_2026-07.md` and
`notes/simulation/wide_signal_coverage.md` for status and potential approaches.

### Unary `-`/`~` are context-determined, not self-determined (corrects the entry below)

**Status**: Resolved (July 2026). Root-caused and precisely characterized
in work plan item 2.2; the compiled-only bug (3 below) was fixed in item
2.3 Part A; the cross-engine bug (1/2 below, reference/vm/vm-fast and the
compiled narrow path) was fixed in item 2.6 by merging `~` into the same
context-determined branch as `+`/`-` in `sim/evaluator.py`,
`sim/vm/compiler.py`, and `sim/compiled/_expr_emitter.py`. Exercised by
`tests/test_sim/test_compiled_edge_shapes.py` ("self_det_unary_*" cases,
all passing, no xfails left).
**Supersedes**: the previous version of this entry (below, kept struck
through for history) claimed, citing IEEE 1364-2005 Table 5-22, that unary
`-`/`~` are *self-determined* to the operand's own width. That claim was
verified WRONG against two independent, conformant tools (Icarus Verilog
and Verilator): when `-a`/`~a` is the top-level RHS of an assignment to a
wider target, both tools extend `a` to the **full assignment context
width first** (using `a`'s own declared signedness), and only then apply
the operator at that width — i.e. these operators are
**context-determined** in this position, the same as binary `+`/`-`.
(Table 5-22's "self-determined" row for these operators governs their use
as *subexpressions* of a larger context-determined expression, not their
use as the expression's own top-level operator — a distinction this
codebase's implementations, and this note before it, got backwards.)

**Verified empirically** (see the reproduction commands below): for
`reg [7:0] a; wire [15:0] y = -a;` / `~a`, real tools give `y=16'hFFFF` /
`16'hFFFE` for `a=8'd1` — NOT `16'h00FF` / `16'h00FE`, which is what
"self-determined" would predict.

This changed the diagnosis for **item 2.3 of `notes/plans/work_plan_2026-07.md`
("Fix the latent wide unary masking bug")**: as originally written, that
item's fix (change `wide_not`/`wide_neg` to use `op_width` instead of
`dst_width` for the tail mask) would have made the compiled engine's
wide-path (>64-bit) unary emitter match the already-broken narrow-path/
reference/vm behavior instead of fixing it — `dst_width` was already the
width-correct choice. Item 2.3 was rescoped (see the plan file) and its
Part A fixed the actual bug (signedness, not width) — see bug 3 below.

**Four distinct bugs found** (all via the `self_det_unary_*_65_to_80_*` and
`seam*_sh{l,r}64` cases in `tests/test_sim/test_compiled_edge_shapes.py`,
cross-checked against Icarus/Verilator):

1. **`~` was wrongly self-determined on reference, vm, and vm-fast** for an
   *unsigned* operand (all three engines identically): `~a` was computed at
   `a`'s own width, then zero-extended — the correct result has its
   extension bits all-1, not all-0 (zero-extension does not commute with
   bitwise complement the way sign-extension does, which is why the signed
   case below happened to already be right). **Status: resolved (July
   2026, work plan item 2.6)** — fixed in `sim/evaluator.py`'s
   `ExpressionEvaluator.eval` and `sim/vm/compiler.py`'s `_compile_expr`
   by merging the `~` `UnaryOp` case into the same context-determined
   branch as `+`/`-` (evaluate/compile the operand at the surrounding
   context width, extending it from its own width first, *before* applying
   the operator — not the reverse).
   Reproduce:
   ```python
   # module t(input [7:0] a, output [15:0] y); assign y = ~a;
   # a = 8'd1 -> now gives y=16'hFFFE on all of reference/vm/vm-fast/compiled.
   ```
2. **Compiled's narrow (<=64-bit) unary path had the same bug as (1)** for
   `~` on an unsigned operand; `-` was already correct on the narrow path
   at all widths/signedness tested. **Status: resolved (July 2026, work
   plan item 2.6)** — fixed in `sim/compiled/_expr_emitter.py`'s
   `_emit_unary` the same way as (1).
3. **Compiled's wide (>64-bit) unary path ignored declared signedness
   entirely** for both `~` and `-`: it always zero-extended the operand to
   context width before applying the operator (already correct for an
   unsigned operand, wrong for a signed one). **Status: resolved (July
   2026, work plan item 2.3 Part A)** — fixed in
   `src/veriforge/sim/compiled/_wide_emitter.py`'s `UnaryOp` handler by
   sign-extending the operand's scratch buffer to the context width before
   calling `wide_not`/`wide_neg`, via a new `wide_sign_extend` primitive in
   `_gen_wide_section.py` (mirrors the sign-fill logic already used by
   `wide_ashr`).

None of these should be "fixed" by copying one engine's behavior to
another — (1)/(2) and (3) were independent bugs in different code paths,
fixed independently against the verified-correct (context-determined)
semantics above. (A fourth, unrelated bug found by the same test file —
narrow-path shift by exactly 64 — was documented and resolved separately
below.)

<details>
<summary>Original (incorrect) entry, kept for history</summary>

~~In `_wide_emitter.py` (around line 3570), the `wide_not`/`wide_neg` primitive
call passes `dst_width` (context width) as the tail-mask parameter rather than
the operand width:~~

```python
lines.append(
    f"{pad}{prim}(_sc{slot}_v, _sc{slot}_m,"
    f" _sc{op_slot}_v, _sc{op_slot}_m, {n_words}, {dst_width})"
)
```

~~Per IEEE 1364-2005 Table 5-22, unary `-` and `~` are self-determined to the
operand width. If the primitive uses this parameter for masking, this is the same
class of bug that was fixed in the narrow-path `_emit_unary` (May 2026).~~ This
premise was wrong (see above) — `dst_width` here is actually the width-correct
choice; the real remaining bug is that this code path doesn't respect `a`'s
declared signedness when extending it to `dst_width`.

</details>

### Compiled engine: narrow blocking/nonblocking bare assignment drops the x-mask

**Status**: Resolved (July 2026, work plan item 2.7 sub-item 1). Was
exercised by `tests/test_sim/test_assignment_matrix.py` (strict xfail,
now removed — all cases pass).
**Found**: July 2026, work plan item 2.1 (cross-engine assignment-semantics matrix)

`b = a;` (blocking, in `always @(*)`) or `b <= a;` (non-blocking, in
`always @(posedge clk)`) lost the x-mask on the compiled engine whenever
**both** `a` and `b` were 64 bits or narrower (the single-word/"narrow-path"
codegen). Driving `a` with any x-contaminated bit and settling left `b`
fully-defined (mask forced to 0) instead of propagating the x bit(s).
Continuous assigns (`assign b = a;`) and port connections (which lower to
continuous assigns during flattening) were unaffected — only the
narrow-path procedural-assignment codegen dropped the mask. Cases where
either side is wider than 64 bits were also unaffected (different,
correctly mask-propagating wide-path codegen).

**Root cause**: `sim/compiled/_stmt_emitters.py`'s generic narrow-path LHS
fallback (used for any bare-identifier blocking/nonblocking RHS not
matched by one of the specialized shift/multiply/struct-field
pattern-matchers earlier in `_emit_lhs_write`) computed the RHS's *value*
via `_emit_expr` but hardcoded the emitted mask update to the literal `0`,
never consulting `_emit_mask_expr` at all.

**Fix**: compute `rhs_mask = self._emit_mask_expr(rhs, assign_width)`
alongside the existing `rhs_val`, and emit it (masked to the signal's
declared width, mirroring the existing value-masking logic) instead of the
hardcoded `0`. Also needed a new scratch cdef (`_cdm`, alongside the
existing `_cdv`) declared everywhere `_cdv` is, in
`sim/compiled/_gen_sections.py`.

**Two more real, previously-latent bugs surfaced once this fix started
actually consulting the RHS mask** (both were harmless before only because
this bug's blanket `mask = 0` override happened to paper over them on
read):

1. `CompiledScheduler.load_memory()` passed the *value* truncation bitmask
   (e.g. `0xFF` for an 8-bit element) as the *x-mask* argument to
   `mem_write`/`mem_write_wide` — marking freshly bulk-loaded memory as
   entirely unknown instead of entirely defined. Fixed to pass `0` (fully
   defined) for the mask argument, keeping the truncation mask (renamed
   `value_mask` for clarity) only for the value itself.
2. `_expr_emitter.py`'s `_emit_mask_expr` had three near-identical
   "select on a non-Identifier target" fallback paths (`BitSelect`,
   `RangeSelect`, `PartSelect`) that computed the packed-range base offset
   with `sig_base = 0 if not isinstance(expr.target, Identifier) else ...`
   — silently defaulting to 0 for a memory-element target (e.g.
   `mem[idx][msb:lsb]` on a packed range with a non-zero declared LSB
   base), extracting the wrong mask bits. The equivalent *value*-side code
   in `_emit_expr` already had a general `_select_base()` helper
   (`sim/compiled/codegen.py`) that correctly handles both scalar signals
   and memory-element targets — the mask side just never called it. Fixed
   all three call sites to use `_select_base()` instead of the
   Identifier-only inline check.

Reproduce (now fixed, all four engines agree):

```python
sim.drive("a", Value(0, width=8, mask=1))  # a[0] is x
sim.settle()
sim.read("b")  # all engines: 8'b0000000x
```

### Compiled engine: wide-emitter sign-extension wrong for the 65->80 width pair

**Status**: Resolved (July 2026, work plan item 2.7 sub-item 2). Was
exercised by `tests/test_sim/test_assignment_matrix.py` (strict xfail,
now removed — all cases pass).
**Found**: July 2026, work plan item 2.1 (cross-engine assignment-semantics matrix)

Assigning a declared-signed (or `$signed()`-cast) 65-bit value into an
80-bit target zero-extended instead of sign-extending, on the compiled
engine only, and only for this specific (65, 80) width pair — every kind
tested (continuous assign, blocking, non-blocking, port connection) was
affected. Other width pairs that also cross the 64-bit word boundary
((63,64), (64,65), (64,63), (65,64), (80,65)) sign-extended correctly — the
bug specifically needed a src/dst pair that both occupy the *same* number
of 64-bit words (65 bits: 1 full word + 1 bit; 80 bits: 1 full word + 16
bits — both 2 words) despite the dst having more bits in that shared word.

**Root cause, three separate code paths, all the same bug shape**: each of
these treats "does this word belong to the source's own last (partial)
word" and "does this word lie beyond the source's own word count entirely"
as the same case (copy verbatim vs. fill with `sign_fill`), when they are
not — a source's own last, partial word needs *its own* unused high bits
sign-extended in place, separately from whole extension words beyond it.
That distinction is invisible whenever `dst_words > src_words` (the usual
case, and where earlier testing happened to concentrate), since then the
source's last word is copied verbatim (correct — it's already full) and
the *subsequent* words correctly get `sign_fill`. It only misbehaves when
`dst_words == src_words` and the source's own last word is partial, which
first happens at exactly this width pair.

1. `sim/compiled/templates/narrow_stage.pxi`'s `_whole_assign_signal_s`
   (continuous assign / blocking) and `sim/compiled/templates/
   narrow_assign.pxi`'s `_whole_stage_signal_s` (non-blocking) — used via
   `_emit_wide_signal_copy_lines` for a bare-identifier RHS. Also had a
   latent, separate bug: the sign-bit-position check shifted by the
   signal's *absolute* width - 1 (e.g. 64) rather than that bit's position
   *within* its word (0) — undefined behavior in C for a 64-bit type,
   only coincidentally landing on the right bit on platforms whose shift
   instruction wraps the count mod 64 (verified this is why the `$signed`
   cast-form cases below weren't affected by this half of the bug: they
   never reached this function in the first place).
2. `sim/compiled/_gen_wide_section.py`'s `wide_load_signal` (used by the
   newer recursive scratch-space emitter, `_wide_emitter.py`'s
   `_emit_wide_expr_to_scratch`, reached by a `$signed()`/`$unsigned()`
   cast-wrapped RHS or any other expression shape not matched by the
   dedicated bare-identifier path above) had no sign-extension concept at
   all — it always zero-fills, appropriate for most of its other callers
   but wrong for a narrower *signed* Identifier operand widening into a
   wider destination.

**Fix**: (1) rewrote both `_whole_assign_signal_s`/`_whole_stage_signal_s`
to sign-extend the source's own last word's unused high bits in place
(computing a proper word-local sign-bit index instead of the UB-prone
absolute one) before applying the destination's per-word tail mask; (2)
added a new sign-extending counterpart, `wide_load_signal_s`, and threaded
a `signed_override: bool | None` parameter through
`_emit_wide_expr_to_scratch`'s recursive call graph so a `$signed`/
`$unsigned` cast can force the extension mode for a narrower Identifier
operand (`None` falls back to the identifier's own declared signedness,
matching pre-existing behavior for every other expression shape).

### Compiled engine: narrow-path shift by exactly the word width (64) is a no-op

**Status**: Resolved (July 2026, work plan item 2.3 Part B)

`a << 64` and `a >> 64` on a <=64-bit signal (widths 63 and 64 both
affected; width 65, which uses the wide/multi-word codegen path, was not)
returned `a` unchanged instead of `0`. Also affected `>>>` by exactly the
operand width, which returned the operand unchanged instead of saturating
to sign-bit-fill (all-0 or all-1s). Both were the classic C/hardware
undefined-behavior pattern where a native shift instruction only consults
the low log2(word-bits) bits of the shift amount (e.g. x86 `SHL`/`SHR` use
the count register's low 6 bits for a 64-bit operand), so shifting by
exactly 64 silently became a shift by 0. Fixed in
`src/veriforge/sim/compiled/_expr_emitter.py::_emit_binary` by guarding
`>>`/`<<`/`>>>` with an explicit `shift_amount >= 64` check before emitting
the native shift. Reference, VM, and vm-fast were unaffected (Python's
`<<`/`>>` have no such wraparound).

**Sixteenth wave (August 2026, work plan item 3.4 Phase 5) -- sequential
multi-statement block fuzzing (data-dependent local temps) surfaces four
more distinct signedness/width bugs, all pre-existing and only newly
REACHABLE by generated expressions nesting arbitrary sub-trees as operands
of `+`/`-`/`*`/`%`/bitwise ops for the first time (the same "phase N's
grammar addition surfaces phase-(N-1)-and-earlier gaps" pattern as every
prior wave), not newly introduced by Phase 5 itself**: extended
`test_differential_statements.py` with `_SeqState`/`_gen_seq_stmt` to
generate `begin...end` blocks of 2-3 statements where a later statement's
expression can read a local temp an earlier statement in the same block
just assigned (including, deliberately, the nonblocking `<=` case, so a
later read sees the OLD pre-block value per NBA staging semantics), and
threaded a new `extra_signals` parameter through `test_differential.py`'s
`_gen_leaf`/`_gen_expr` so those temps are eligible operands anywhere in
the generated expression tree, not just as the immediate assignment RHS.
Default-scale runs stayed green throughout; stress-testing at 150 cases
across the 14-seed rotation (5 carried over from phases 3/4 plus 9 fresh),
plus the closing full fast-suite regression (which caught a real design
exercising exactly the shape a first attempt at fixing one of these had
gotten subtly wrong), surfaced five distinct bugs, all confirmed against
Icarus and/or cross-engine agreement:

- **`+`/`-`/`*` used each operand's own individual signedness instead of
  the operator's combined signedness** (`sim/evaluator.py`,
  `sim/vm/compiler.py`, `sim/compiled/_expr_emitter.py`,
  `sim/compiled/_wide_emitter.py`) -- every engine special-cased `/`/`%`
  alone to widen both operands using the operator's own COMBINED
  signedness (signed only if both operands are individually signed, per
  IEEE 1364-2005 §5.5.1) before combining, but let `+`/`-`/`*` widen each
  operand using its OWN individual signedness instead, on the theory that
  modular arithmetic is "residue-safe" -- invariant to how each operand
  happens to be extended, as long as its value is "correct" at the target
  width. That reasoning is wrong: sign- vs. zero-extending a signed
  operand produces a genuinely DIFFERENT integer value in the first place
  (a 1-bit signed `1` means -1 sign-extended but +1 zero-extended), and
  `(a - b) mod N` differs depending on which of those two values `a` is
  taken to be -- "residue-safe" only holds once each operand's value is
  already fixed, it says nothing about which extension choice fixes it
  correctly. Confirmed against Icarus for `(sa - ub)` with `sa` a signed
  1-bit register holding `1` (i.e. -1) and `ub` an unsigned 2-bit `0`:
  Icarus gives `1` (zero-extending `sa` per the pair's combined-unsigned
  type), not `-1`/`3` (sign-extending `sa` on its own). Fixed by unifying
  `+`/`-`/`*`'s handling with `/`/`%`'s in all four engines, forcing the
  operator's own combined signedness into both operands' extension,
  propagating into whatever nested operator either operand is (mirroring
  how a ternary's combined signedness overrides its branches).
- **Compiled `wide_mul`/`wide_add`/`wide_sub`'s x-propagation missed
  operand bits truncated below the operator's own natural width**
  (`sim/compiled/_wide_emitter.py`) -- `op_width` (the width at which
  operand scratch arrays get filled before the primitive runs) was
  capped at `dst_width` for `+`/`-`/`*` (only `/`/`%` used the wider
  `max(dst_width, operand widths)`). The VALUE itself is genuinely
  truncation-safe (modular arithmetic commutes with truncation), but the
  primitive's `has_x` check is NOT: it scans exactly `op_width` bits of
  each filled operand, so an x bit living entirely above the truncation
  point was invisible to the check even though Verilog's conservative
  "any x anywhere in either operand -> x result" rule doesn't care
  whether that bit would have affected the truncated value -- under-
  counting x-propagation, producing a determinate result where the
  reference oracle (and Icarus) correctly produced x. Fixed by extending
  `op_width` to `max(dst_width, self_width(left), self_width(right))` for
  `+`/`-`/`*` too, matching `/`/`%`; the downstream `prim_width` (already
  `min(op_width, dst_width) = dst_width` once `op_width >= dst_width`)
  needed no change -- the wider `op_width` only affects how the operand
  scratch arrays get *filled*, not how much of them the value computation
  reads.
- **Bitwise ops (`&`/`|`/`^`/`~^`/`^~`) leaked an unrelated outer
  `signed_override` into a nested operand's own signedness decision**
  (`sim/evaluator.py`, `sim/vm/compiler.py`,
  `sim/compiled/_expr_emitter.py`, `sim/compiled/_wide_emitter.py`) --
  the general shape behind several bugs already fixed in earlier waves
  (`>>`'s forced-unsigned leaking into a nested `&`; a ternary's combined
  signedness leaking into a nested arithmetic branch; a wide comparison's
  `$signed()`-unwrap leaking into a compound argument), newly found for
  `%`'s divisor-widening override specifically: a bitwise op's combined
  signedness is entirely self-contained (governed solely by its own two
  operands' types), unlike `>>`'s left operand or a ternary branch, which
  genuinely need an outer decision to reach in -- an incoming
  `signed_override` is meant only to control how the bitwise op's
  *already-computed result* gets read by whatever outer context requested
  it, never how its own operands get computed. Confirmed against Icarus
  for `(|a3[45]) % (($signed(a4[23]) - a0) | 1)`: the dividend's unsigned
  reduction forces `%`'s own combined decision unsigned, and every engine
  was forwarding that `False` decision straight through the divisor's `|`
  into the divisor's own nested `-`, even though both of the `-`'s
  operands are genuinely, individually signed -- wrongly zero-extending
  `a0` instead of sign-extending it, corrupting the divisor's own
  computed value from `1` to a huge wraparound magnitude. Fixed in all
  four engines' bitwise-op branch by no longer forwarding the incoming
  override into either operand's own recursive evaluation/compilation/
  emission (each operand now widens using its own individually-computed
  signedness instead); only the bitwise op's own RESULT-level extension
  still consults it.
- **Compiled engine (narrow path): re-sign-extending a nested context-
  determined operand by its *self-determined* width corrupted an already
  wider-computed value** (`sim/compiled/_expr_emitter.py`) -- found while
  fixing the bitwise-op leak above; fixing that bug exposed this second,
  previously-latent bug in the same repro (previously masked because the
  leaked `signed_override` happened to evaluate `False` in that specific
  case, disabling this buggy code path entirely). `_emit_binary`'s post-
  operand-emission step (`if op_width > lw: left = _sign_ext(left, lw)`,
  `lw = self._expr_width(expr.left)`) re-sign-extends an operand's
  already-generated Cython value string using `_expr_width()` -- a
  STATIC, AST-shape-based self-width estimate -- whenever the caller's
  `op_width` exceeds it. That's correct for a LEAF operand (Identifier/
  Literal), whose own internal emission naturally stops at its
  self-width, but for a NESTED context-determined operator (another
  `+`/`-`/`*`/bitwise BinaryOp) the earlier recursive `_emit_expr`
  call already computed and masked that operand's value DIRECTLY at the
  wider `op_width` (this codebase's arithmetic/bitwise operand handling
  deliberately computes at the target width throughout, since e.g. unary
  `-` doesn't commute with extension) -- re-sign-extending that already-
  `op_width` value using the narrower self-width `lw` reinterprets an
  arbitrary INTERIOR bit of the correctly-computed value as its sign bit.
  Confirmed against Icarus for the same `(|a3[45]) % (($signed(a4[23]) -
  a0) | 1)` repro: the nested `-` was already correctly computed at the
  `|`'s 32-bit `op_width`, but this step's `lw=1` (the `-` node's own
  static self-width) then `_sign_ext`'d that already-32-bit value as if
  bit 0 were its sign bit. Fixed by restricting this re-sign-extension
  step to `+`/`-`/`*`/`/`/`%` only (which genuinely need it -- C's raw
  arithmetic on a native register needs the operand's sign truly
  replicated up the whole register for correct carry/borrow/division
  behavior, not just masked to width); bitwise ops don't need it at all,
  since their `core` gets masked to `op_width` again immediately after
  combining regardless. `_wide_emitter.py` was never affected: its
  scratch-array design fills each operand directly at the requested width
  via the recursive call alone, with no analogous separate re-extension
  step to get wrong.
- **Compiled engine: `>>`'s logical-shift core didn't mask the left
  operand's raw C register before shifting, then a first attempt at
  fixing that masked it to the wrong width** (`sim/compiled/
  _expr_emitter.py`) -- two bugs found back-to-back, the second only via
  the full fast-suite regression (`tests/test_pulp_axi_examples.py`)
  AFTER the rest of this wave was believed complete. Original bug: `>>`'s
  core computation shifted the left operand's raw C `long long` value
  directly, but a narrower signed operand's `_sign_ext` representation
  fills the WHOLE native register unbounded by the operand's own true
  width (most other consumers either further widen from there or mask
  the final result afterward) -- a logical (zero-fill) right shift must
  not let those extra high "padding" bits participate. Confirmed against
  Icarus for `($signed(a0) >> a0)` with `a0` a signed 1-bit register
  holding `1` (i.e. -1) used as an `if` condition: `-1 >> 1` (logical)
  must give `0`, but shifting the unmasked full-64-bit sign-extension of
  -1 gave a large nonzero value, flipping the condition. First fix
  attempt: mask the left operand to `op_width` (the outer caller's
  requested width) before shifting -- fixed the original repro (where
  the operand's own natural width happened to equal `op_width`) but
  introduced a NEW regression, caught by the full-suite run: `op_width`
  for `>>` is just the outer context's requested width, which can be
  NARROWER than the operand's own true width whenever the `>>` result is
  immediately truncated by an outer cast (`sel_t'(addr >> SelOffset)`
  requesting a 1-bit result from a 32-bit address) -- masking the address
  down to that same 1 bit BEFORE shifting discarded real address bits
  the shift still needed to correctly extract the upper ones. Confirmed
  against Icarus (cross-engine, and via `git stash` bisection proving
  this was a same-session regression, not pre-existing) for the PULP
  AXI-Lite data-width upsizer's write-select logic
  (`examples/pulp/axi/axi_lite_dw_converter`): masking a 32-bit AXI
  address down to `sel_t`'s own 1-bit result width before the `>>`
  zeroed the address's bit 1 before it could ever reach the output,
  wrongly computing `wr_sel_q = 0` instead of `1` for address `0x2` and
  silently disabling a downstream strobe left-shift that depended on it.
  Final fix: mask to `max(op_width, self._expr_width(expr.left))` instead
  -- widening (never narrowing) the naive `op_width`-only mask to also
  cover the operand's own natural self-determined width, so the mask
  only ever strips genuine sign-extension padding, never bits that are
  part of the operand's real value.

Verified via all 14 statement-fuzzer seeds (150 cases each, 8/8 batches,
`VERIFORGE_DIFF_STMT_COMPILED=1`), all 8 expression-tree fuzzer seeds
(150 cases each, 15/15 batches, `VERIFORGE_DIFF_COMPILED=1`),
`test_power_operator.py` (60 passed, 1 xfail, unaffected), and a final
full fast-suite regression (7107 passed, 1 xfailed, 0 failed, `-n 8`,
~30 min, no regressions).

**Seventeenth wave (August 2026, work plan item 3.4 Phase 6) --
user-defined `function` call fuzzing surfaces fifteen more distinct
bugs across all four engines, the majority pre-existing and only newly
reachable now that a call's argument/return values get exercised by
randomly generated expressions for the first time**: added
`test_differential_functions.py`, a new dedicated fuzzer (own file,
not folded into `test_differential.py`/`test_differential_statements.py`
-- see that file's own docstring for why: the compiled engine cannot
handle a `FunctionCall` node feeding a >64-bit destination, the same
constraint that already excluded `**` into its own file) generating
calls to a small fixed pool of user-defined functions
(`FIXED_FUNCTIONS`, added to `test_differential.py` and reused via a
new `callables` parameter on `_gen_expr`, itself a no-op for every
pre-existing call site) from arbitrary expression positions with
randomly generated argument expressions. Default-scale runs (reference/
vm/vm-fast only) were clean from the start and stayed clean throughout;
stress-testing at 150 cases across the 8-seed rotation with
`VERIFORGE_DIFF_FUNC_COMPILED=1` surfaced the following, all confirmed
against Icarus and/or cross-engine agreement:

- **Reference engine's function-call argument binding never narrowed an
  over-width argument to its port's own declared width**
  (`sim/evaluator.py`'s `_eval_user_function`) -- a call's argument-to-
  port binding is really an implicit assignment, but the raw `Value`
  from evaluating the argument expression was stored directly into the
  local port signal with no resize/sign-extend step, unlike every real
  assignment (which gets this for free from `_write_target` writing
  into the destination's own storage). Confirmed against Icarus for
  `fn_sel1(a4[27:8], a1)` with `fn_sel1(input a, input [62:0] b)`:
  passing the 20-bit range-select `a4[27:8]` into the 1-bit port `a`
  unchanged left it nonzero (hence "truthy") regardless of its own low
  bit. Fixed by explicitly resizing/sign-extending each argument to its
  port's own width after evaluation.
- **...then refined: evaluating an argument at its own self-determined
  width and resizing afterward is not equivalent to evaluating it
  DIRECTLY at the port's width**, whenever the argument is itself a
  nested context-determined operator (unary `-` in particular, which
  does not commute with extension -- the same principle already
  established for ordinary context-determined arithmetic). Confirmed
  against Icarus for `fn_neg(-(!a7))` with `fn_neg(input signed [15:0]
  a)` and `a7 = 0`: negating `!a7`=1 at its own 1-bit self-determined
  width first (mod-2 arithmetic) gives `1`, zero-extended to 16 bits =
  `1` -- wrong; Icarus negates directly at the port's 16-bit context,
  correctly giving `-1` (0xFFFF). Fixed by evaluating each argument
  directly at its port's width (mirroring every "evaluate RHS at target
  width" call site elsewhere in this file), keeping the post-hoc
  resize/sign_extend as a safety net for expression types (like
  `RangeSelect`) whose own dispatch never narrows on its own.
- **Reference engine's (and `sim/vm/compiler.py`'s identical copy of)
  self-determined-width estimator hardcoded a user-defined function
  call's own width to a generic `32` fallback**, never consulting the
  function's real declared return width (`_expr_self_width`/
  `_expr_width`'s `FunctionCall` case only special-cased `$signed`/
  `$unsigned`). Confirmed against Icarus for `($unsigned(fn_sel1(...))
  ^ (~^(-a4)))` with `fn_sel1` declared `function [62:0] fn_sel1(...)`:
  the hardcoded `32` silently truncated the XOR's own combined-width
  computation, corrupting the zero-extension of the call's real 63-bit
  return value. Fixed in both files by looking up the function's own
  `return_range` (via a new `EvalContext._functions` registry,
  populated in `scheduler.py` alongside the executor's own
  `_function_map`, for `sim/evaluator.py`; via the existing
  `_function_map` for `sim/vm/compiler.py`).
- **`sim/vm/compiler.py`'s argument compilation had the identical
  "compiled at self-width, resized afterward" gap as the reference-
  engine fixes above**, plus a SEPARATE issue specific to the bytecode
  VM: when an argument's own self-determined width exceeds 64 bits
  (e.g. a wide ternary operand), `_compile_expr` emits WIDE bytecode
  for it regardless of the port's own narrow width, leaving a wide-
  flagged value on the interpreter stack that `OP_STORE_SIG`'s narrow-
  destination branch can't read directly. Confirmed against Icarus for
  `fn_xor64s(((cond) ? a6 : (-a3)), a6[35])` with `fn_xor64s(input
  signed [63:0] a, ...)`: the 80-bit-wide ternary argument passed to
  the 64-bit port `a` gave a near-zero garbage value on `vm-fast`
  (`vm` itself was unaffected -- pure Python, no wide/narrow stack
  distinction). Fixed by compiling each argument directly at its
  port's width (fixing the first issue) with an explicit RESIZE/
  SIGN_EXT safety net immediately after (fixing the second).
- **`vm-fast`'s `OP_SIGN_EXT` never demoted a wide-flagged source back
  to the narrow scalar stack representation when the requested result
  width is <=64 bits** (`sim/vm/_interp_fast.pyx`) -- unlike `OP_RESIZE`
  (which already correctly extracts the low bits and clears
  `wflag` when narrowing from wide), `OP_SIGN_EXT`'s wide-source path
  always left the result wide-flagged with a stale zeroed narrow slot,
  regardless of the requested width. This is exactly what the RESIZE
  safety net above emits, so it was reachable the moment that fix
  landed. Confirmed against Icarus for `fn_xor64s(a5, a6)` narrowing
  an 80-bit signed argument down to an 8-bit port: the result was a
  near-zero garbage value. Fixed by adding the missing demotion,
  mirroring `OP_RESIZE`'s own pattern.
- **`sim/vm/compiler.py`'s nested-call depth counter incremented too
  late, letting two calls to the SAME function (one nested inside the
  other's own argument) collide on the identical local port/return
  signals** -- `_func_call_depth` was only bumped immediately before
  compiling the function BODY, not before compiling its ARGUMENTS, so
  a nested call encountered while compiling an outer call's own
  argument read the same (not-yet-incremented) depth and therefore the
  identical `__func_{name}_{depth}` signal-name prefix as the outer
  call itself. Confirmed against Icarus for `fn_xor64s(a2[12:0],
  {fn_xor64s(a0, a2[0]), $signed(a2)})`: the inner call's own argument-
  binding writes corrupted the outer call's not-yet-consumed port
  signals. Fixed by bumping the depth counter immediately, before the
  argument loop, so any nested call reached while compiling arguments
  gets a distinct depth from its own outer call.
- **Compiled engine's `_emit_func_call` hardcoded every argument's
  VALUE-side width to `32` regardless of the port's own declared
  width** (`sim/compiled/_expr_emitter.py`) -- correct by coincidence
  for most expression shapes (the generated `_user_func_XXX` helper
  re-masks the incoming raw value to the port's real width on its own
  storage side), but genuinely wrong for a `TernaryOp` argument whose
  own "ambiguous-condition bitwise-merge" fallback masks its result to
  the REQUESTED width as part of computing the merge itself. Confirmed
  against Icarus for `fn_xor64s(((cond) ? a6 : (-a3)), a6[35])`: the
  hardcoded `32` truncated the ternary's own merge computation for the
  63/64-bit-wide argument down to 32 bits, corrupting high-order value
  bits before they reached the function. Fixed by computing each
  argument at its own port's real declared width (via a new shared
  `_emit_user_func_call_expr` helper, reused by both the value- and
  mask-side call sites below).
- **Compiled engine's function-call ABI had no mask (x/z) channel at
  all, in either direction** (`sim/compiled/_gen_sections.py`'s
  `_gen_user_functions`, `_expr_emitter.py`'s `_emit_func_call`/
  `_emit_mask_expr`) -- `_user_func_XXX` took only VALUE arguments
  (`long long arg_i`, hardcoding `c.mask[port_sid] = 0` on the way in)
  and returned only a VALUE (discarding `c.mask[ret_sid]`, correctly
  computed internally, on the way out). Confirmed against Icarus for
  `fn_sub16s(a5, a5[35])` with `a5` fully x: the correct result is x
  only in `fn_sub16s`'s own 16-bit return width, but the missing INPUT
  channel meant the function body's subtraction always saw fully-
  defined-looking operands, computing a spurious definite value instead
  of x -- and separately, before that was fixed, the missing OUTPUT
  channel meant the caller had to approximate the call's own mask by
  ORing together its argument masks (a crude guess, also wrong -- see
  the ternary/`&`/`*` findings below). Fixed by changing the generated
  function signature to take `(value, mask)` pairs per argument and
  storing both into the local port signals; the mask-side caller now
  re-invokes the same call expression (forcing it to run again, safe
  since Verilog functions are pure/input-only) purely to read
  `c.mask[ret_sid]` afterward, via `(c.mask[ret_sid] if (CALL or 1)
  else 0)` -- a value-discarding "run this call as a side effect of
  evaluating this condition" idiom matching this codebase's existing
  ternary-as-expression-sequencing convention.
- **`+`/`-`/`*`/`/`/`%`'s mask-combining formula in the compiled
  engine's narrow path was missing the "ANY x/z bit ANYWHERE in either
  operand -> the ENTIRE result is x" rule for `*`/`/`/`%` specifically**
  (`sim/compiled/_expr_emitter.py`'s `_emit_mask_expr`) -- `+`/`-`
  already had it, but `*`/`/`/`%` silently fell through to the generic
  per-bit-position `lm | rm` fallback (correct for bitwise ops, wrong
  for arithmetic, whose carry/borrow/product/quotient chain can't be
  computed with partial unknowns). This is a genuine, PRE-EXISTING gap
  UNRELATED to function calls -- confirmed with a plain, function-free
  repro, `((0 - a3) * (a0 && a0))` with `a0` fully x: `a0 && a0`'s own
  1-bit x result, zero-extended into the wider multiplication, has a
  mask with only its own low bit set (the zero-extension padding bits
  are definitely 0) -- the old fallback let that single x bit's
  position alone determine which RESULT bits read as x, instead of
  correctly tainting the entire product. Fixed by extending the
  existing `+`/`-` rule to `*`/`/`/`%` too.
- **A ternary branch's mask gets queried at its own self-determined
  width even when its VALUE was computed directly at the ternary's own
  (wider) combined width** (`sim/compiled/_expr_emitter.py`'s
  `_emit_ternary_value_mask_exprs`) -- `+`/`-`/`*`/`/`/`%` branches are
  a documented special case (this file's own comment: "those ops
  already extend directly to whatever width they're asked for... not
  self-width-then-extend"), but the corresponding MASK query still used
  the branch's own self-width (`tw`/`fw`) unconditionally, mismatching
  what the value side actually computed. Confirmed against Icarus for
  `cond ? ((a >> b) * $signed(a2)) : {2{a4[52]}}` with `a2` fully x:
  the `*` branch's mask, queried at its own 63-bit self-width instead
  of the ternary's 64-bit outer width, left bit 63 spuriously reading
  as definite (0) instead of x. Fixed by using the ternary's own outer
  width for an arithmetic branch's mask query too, matching what its
  value query already does.
- **The compiled engine's wide emitter had no support for user-defined
  function calls at all** (`sim/compiled/_wide_emitter.py`'s
  `_emit_wide_expr_to_scratch`) -- its `FunctionCall` case only handled
  `$signed`/`$unsigned`, returning `None` (the generic "not yet
  handled" signal) for anything else, silently falling through to a
  narrow-scalar last-resort path that never accounted for the call's
  own contribution at all. Confirmed against Icarus for `(~^$signed({a3,
  fn_sub16s(a7, a1[6:4])}))`: a function call as one member of a
  concatenation whose own self-determined width exceeds 64 bits (here
  reached through a reduction, not the assignment's own destination,
  which stayed narrow) silently dropped the call's contribution.
  `_user_func_XXX` always returns at most 64 bits by construction of
  its own calling convention, so its result always fits in scratch
  word 0 regardless of how wide the enclosing destination is -- fixed
  by adding a case that emits the call (reusing the same
  `_emit_user_func_call_expr` helper) and loads the result into word 0,
  sign/zero-extending to the destination width as needed.
- **The compiled engine's reduction operators (`&`/`|`/`^`/`~&`/`~|`/
  `~^`/`^~`/`!`) computed entirely via native `long long` operand/mask
  strings, silently losing any operand bits beyond the register's own
  64** (`sim/compiled/_expr_emitter.py`'s `_emit_unary`/
  `_emit_reduction`/`_emit_mask_expr`) -- a GENUINE, PRE-EXISTING gap
  UNRELATED to function calls, confirmed with a plain, function-free
  repro: `((!{a0, a7, a4}) & 64'hFFFFFFFFFFFFFFFF)` with `a0` (1 bit,
  x) as the MSB of a 66-bit concat reduced by `!` -- both `wmask(ow)`
  and `_cy_hex((1 << ow) - 1)` silently overflow/wrap a 64-bit C
  literal for `ow > 64`, collapsing to an effectively-64-bit mask
  regardless of the operand's true width, so the ambiguous top bit
  read as spuriously definite once the reduction was embedded as an
  operand of `&` (reached through `_emit_mask_expr`'s generic dispatch
  -- a bare top-level assignment RHS apparently has its own unaffected
  fast path, which is why this had never surfaced before). The wide
  emitter already has fully correct multi-word reduction primitives
  (`wide_reduce_and`/`_or`/`_xor`, already dispatched for every
  reduction op and `!`) -- just never reachable from the narrow
  emitter. Fixed by adding `_emit_wide_reduction_to_value` (routes a
  reduction whose operand exceeds 64 bits through the wide emitter,
  hoisting the multi-word computation into `_et` temps and returning
  plain `long long` value/mask expressions, since the reduction's own
  result always fits in 1 bit regardless of its operand's width).
  Fixing this exposed two further, independent PRE-EXISTING gaps in
  the wide emitter's own reduction dispatch, both only ever masked
  before because every prior caller reaching that code already sat
  inside an already-wide context: (a) it never updated
  `_dynamic_max_wide_words` (the per-module running peak scratch-array
  word count) for its own operand's word count, so a module whose ONLY
  wide computation is a reduction like this one got its scratch arrays
  declared one word too small -- caught by Cython's own compiler as a
  type error rather than silently miscompiling; (b) reaching the wide
  emitter from a narrow calling context never set `_needs_wide_helpers`
  (the flag controlling whether the module's wide-primitive helper
  functions get emitted into the generated `.pyx` at all), so a module
  with no wide SIGNAL anywhere skipped emitting `wide_reduce_or`/etc.
  entirely even though the generated code now called them. Both fixed
  alongside the main routing fix.

**Fixed as a direct follow-up: `TernaryOp` conditions exceeding 64
bits, reached from a narrow calling context** (`sim/compiled/
_expr_emitter.py`) -- the reduction-operator fix above confirmed the
same general family of bug (a computed value exceeding 64 bits reached
from a narrow calling context that the compiled engine's narrow
emitter was never designed to handle) recurs in other node types
beyond reductions. Confirmed against Icarus for `(({a3[28:10], (~^a4),
a6} ? a4[54:20] : {3{(a6 != a6)}}) <= a0)` (no function call involved
at all): a ternary CONDITION whose own concatenation exceeds 100 bits,
reached from a narrow (<=64-bit) comparison context, hit the identical
"native long long can't represent this" class of gap for `TernaryOp`
conditions specifically. `_emit_ternary_value_mask_exprs` always
computed its condition's value/mask via the narrow `_emit_expr`/
`_emit_mask_expr` (correct only up to 64 bits), even though the
downstream "cond_known1"/"cond_mask_zero" branch-selection logic only
ever consumes them through a "reduce to one known-truth scalar" lens --
exactly what `wide_logical_truth` (the C primitive already used by
`_stmt_emitters.py`'s `_emit_condition_lines_and_expr` for `if`/`while`/
`for` statement conditions) computes. Fixed by adding a new
`_emit_wide_truthy_to_value` helper mirroring that existing statement-
condition precedent (same wide-detection check, same primitive), but
returning BOTH the value and mask (not just the value, since the
statement-condition helper's callers don't need the mask but the
ternary's branch-selection logic does), hoisted via the same `_et_pending`
temp mechanism used by the reduction fix; wired into
`_emit_ternary_value_mask_exprs`'s non-`py` branch only (the `py=True`
elaboration-time path already uses arbitrary-precision Python-bignum
evaluation and was never affected). Verified with zero regressions and
one additional previously-failing case now passing (seed 44 of the
ad-hoc verification sweep below, 7 failures -> 6).

**Known, pre-existing, separately-scoped gaps found while verifying the
fix above** -- confirmed the general "wide value in narrow context" bug
family is broader than either the reduction or `TernaryOp`-condition
fixes cover, recurring in yet more node shapes. An ad-hoc sweep of 8
arbitrary seeds (150 cases each, `VERIFORGE_DIFF_FUNC_COMPILED=1`,
seeds `11/22/33/44/55/66/77/20260701` -- NOT the same 8 seeds
documented as clean above) surfaced two more distinct instances, both
confirmed pre-existing via `git stash` bisection against the fix above
(identical failure counts with and without it, except the one case it
newly fixes):
- A reduction whose operand is itself a further wide context-determined
  operator (unary `-`) nested inside a function-call argument still
  fails: `fn_sel1({2{((~|a6[62]) && {a7, a6[5]})}}, (^(-a5)))` with
  `a5` 65 bits -- `(^(-a5))`'s operand self-width (65) DOES route
  through `_emit_wide_reduction_to_value` (`ow > _WORD_BITS`), but
  something in the wide emitter's own recursive handling of a nested
  unary `-` at that width still produces a wrong answer (root cause not
  yet isolated). Not fixed at the time this paragraph was written --
  **since fixed as a further follow-up a few waves later** (missing
  `_et_pending` scope in `_emit_wide_lhs_write_new`); see the "Fixed as
  a further follow-up" entry below with regression test
  `test_reduction_of_wide_unary_minus_function_argument`.
The `vm`-vs-`vm-fast` divergence noted above (`sim/vm/interpreter.py`
giving a different answer than `sim/vm/_interp_fast.pyx` for the exact
same `TernaryOp`-condition repro, despite executing identical bytecode)
turned out to be a **false alarm, not a real interpreter bug** -- see
below.

**Root-caused and fixed: non-ANSI (Verilog-1995 style) module port
declarations silently lost all width/direction/signedness metadata
during parsing.** The original ad-hoc repro used old-style port syntax
(`module t(a0, ...); input [62:0] a3; ...`) purely for hand-written-
script convenience -- a style the differential fuzzers never generate
(they always emit ANSI-style headers). Re-tested with ANSI-style syntax
(matching the fuzzers), `vm` and `vm-fast` agree correctly; the
divergence only reproduces with old-style declarations. Digging into
*why* revealed a real, unrelated, and more consequential bug:
`Module.ports` (and `.nets`/`.variables`) end up completely EMPTY after
parsing any old-style module -- `sim/vm/compiler.py`'s `_get_signal_id`
then silently auto-registers every referenced signal at a default width
of 1 the first time it's compiled. Two independent, stacked defects in
`src/veriforge/transforms/`, both now fixed:
- **`_design_builder.py`'s `_MODULE_SKIP_NODES` unconditionally skips
  `port_declaration` nodes** -- correct for ANSI-style modules (where
  `port_declaration` is already consumed inline via
  `list_of_port_declarations` in the header), but for old-style
  modules the body's OWN `input`/`output`/`inout` re-declaration (e.g.
  `input signed [62:0] a3;`) is parsed as an *additional*, separate
  `module_item -> port_declaration` node, and is the ONLY place a
  non-ANSI port's real width/direction/signedness ever appears -- the
  header's `list_of_ports` supplies just the bare name. Skipping it
  there discarded that information entirely. Fixed by special-casing
  `module_item` nodes that directly wrap a `port_declaration` (new
  `_wraps_port_declaration` helper) and merging the extracted `Port`
  (reusing the existing ANSI-style `extract_ports_from_declarations`
  callback, which happens to work unmodified on this node shape too)
  into `ctx.ports` BY NAME (new `_merge_ports_by_name` helper),
  replacing the header's placeholder stub rather than appending a
  duplicate.
- **`_declarations.py`'s `_extract_port_names` (the header
  `list_of_ports` bare-name extractor) never actually found any names**
  -- it scanned only each `port` node's DIRECT children for a `Token`,
  but the identifier is nested three levels deeper (`port ->
  port_expression -> port_reference -> PORT_IDENTIFIER`), so it
  silently produced zero header stubs for every old-style module. This
  compounded the first bug: with no header stubs to merge into, `Port`s
  from the body-declaration fix above landed in `ctx.ports` in BODY
  DECLARATION order (via plain append) instead of the header's own
  `list_of_ports` order -- broken for any old-style module whose body
  declarations aren't already in header order (legal, common
  Verilog-1995 style), which matters for POSITIONAL port connections at
  instantiation. Fixed by descending fully into each `port` subtree
  (`scan_values`) rather than only its direct children.

Verified: `top.ports` now correctly shows full width/direction/signed
for every non-ANSI port, in header order, regardless of body
declaration order; the original `vm`-vs-`vm-fast` repro now agrees
across reference/vm/vm-fast (both were being fed a mangled 1-bit signal
model before -- `vm-fast`'s agreement with reference was itself
coincidental, not evidence it was already correct). Two new regression
tests added in `tests/test_model/test_module.py`
(`test_non_ansi_port_width_and_direction`,
`test_non_ansi_port_order_preserved`). `tests/test_model/` and
`tests/test_verilog_parser/` (951 passed) and a full fast-suite
regression (7894 passed, 16 pre-existing failures unrelated and
unchanged -- confirmed via `git stash` bisection: 12
`test_memories.py::TestWideSignalMemory` parametrizations failing with
a Cython "Converting to Python object not allowed without gil" compile
error, and `test_codegen_basic.py::TestOrChainTemporaries::
test_or_chain_max_line_length` -- both present identically without
this fix, `-n 8`, ~34 min, zero new failures) all pass.

**Fixed as a further follow-up: a reduction over a wide unary `-`
operand nested inside a function-call argument** (`sim/compiled/
_wide_emitter.py`) -- confirmed against Icarus for `fn_sel1({2{((~|a6
[62]) && {a7, a6[5]})}}, (^(-a5)))` with `a5` 65 bits: the destination
(`y0`) is only 64 bits, but `(^(-a5))`'s 65-bit internal computation
routes the WHOLE statement through `_emit_wide_lhs_write_new` ->
`_emit_wide_expr_to_scratch` (via `_rhs_needs_wide_eval`) to compute
the function call. That recursive wide emitter's own `FunctionCall`
case computes each argument through the NARROW emitter regardless of
the call's destination width (`_emit_user_func_call_expr` always calls
`_emit_expr`/`_emit_mask_expr`), and the reduction argument's own
attempt to hoist its wide sub-computation through `_emit_wide_
reduction_to_value` (the fix two waves back) requires an active
`_et_pending` list to append to -- silently returning `None` (falling
back to the native-`long long`-only reduction formula, wrong beyond 64
bits) whenever `_et_pending` is `None`. Every OTHER top-level-statement
compiler that can reach the narrow emitter (continuous-assign and
blocking/nonblocking-assignment fallback paths) already opens its own
fresh `_et_pending` scope before calling into it; `_emit_wide_lhs_
write_new` never did, since it doesn't itself need `_et_pending` (it
writes its own multi-line output directly) -- leaving `_et_pending` at
whatever a PRIOR statement last left it as (`None` by default, if this
is the first statement compiled in the module). Fixed by having
`_emit_wide_lhs_write_new` open its own `_et_pending`/`_et_node_vals`/
`_et_node_masks`/`_et_count` scope around the recursive
`_emit_wide_expr_to_scratch` call, threading any hoisted lines into its
own output (mirroring the identical save/reset/restore pattern already
used by its sibling top-level-statement compilers).

Verified: a new dedicated regression test,
`test_reduction_of_wide_unary_minus_function_argument` in
`tests/test_sim/test_differential_functions.py`, fails on the pre-fix
baseline and passes after (confirmed via `git stash`). A 9-seed x
300-case sweep (`11/22/33/44/55/66/77/111111/20260701`,
`VERIFORGE_DIFF_FUNC_COMPILED=1`) shows a STRICT improvement with zero
regressions (53 total failures on the pre-fix baseline -> 47 with the
fix, confirmed case-by-case via `git stash` bisection per seed --
several other, still-undiscovered instances of the same general "wide
value in narrow context" bug family evidently remain, consistent with
the framing below). `test_differential.py`/`test_differential_
statements.py` (`VERIFORGE_DIFF_COMPILED=1`/`VERIFORGE_DIFF_STMT_
COMPILED=1`, both fully green, unaffected by this shared-file change),
`test_function_task.py` (29 passed), `test_power_operator.py` (60
passed, 1 xfail), and a full fast-suite regression (7894 passed, the
same 16 pre-existing failures, `-n 8`, ~34 min, zero new failures) all
pass.

The general "wide value in narrow context" bug family remains larger
than the reduction, `TernaryOp`-condition, and this fix collectively
cover -- confirmed by the 9-seed sweep above still showing 47 residual
failures. Making the compiled engine's narrow emitter correctly route
EVERY node type through the wide emitter whenever a nested sub-
expression's own self-determined width exceeds 64 bits remains a
larger, more systematic undertaking than any single fix in this
sequence, mirroring the existing "compiled engine's 64-bit width limit
is only partially resolved" work plan item 2.7 sub-item 4.
`test_differential_functions.py`'s default (non-compiled) run and the
originally-documented 8-seed `VERIFORGE_DIFF_FUNC_COMPILED=1` rotation
remain fully green; other seeds will continue to hit residual gaps
until a more systematic fix lands.

**Systematic audit and single highest-leverage root-cause fix:
`_rhs_needs_wide_eval` was checking the wrong thing.** Rather than keep
finding and fixing individual node-shape instances of the "wide value
in narrow context" family one at a time, two parallel audits (one over
`sim/compiled/_expr_emitter.py`'s expression-level dispatch, one over
`sim/compiled/_process_compiler.py`/`_stmt_emitters.py`'s statement-
level entry points) were run to find every remaining gap systematically.
The audits confirmed a single, high-leverage root cause: `_rhs_needs_
wide_eval` (`sim/compiled/_wide_emitter.py`) -- the gate deciding
whether a narrow-destination statement even ATTEMPTS wide-path
evaluation at all -- was a hand-maintained list of per-operator special
cases (`_WIDE_CMP_PRIMS` for comparisons, shifts, `_WIDE_BINARY_PRIMS`,
reductions, `&&`/`||`, ternary branches), each checking only its own
immediate operands' self-determined width, ending in a catch-all of
`_expr_uses_wide_signal` (does the tree reference an individually wide
*signal* anywhere?). Two independent failure modes fell through this:
(1) a computed wide value assembled from several individually-narrow
signals or literals (a concatenation/replication whose own combined
width exceeds 64 bits) is invisible to `_expr_uses_wide_signal`; (2)
several of the per-op branches (`&&`/`||` in particular) `return`
UNCONDITIONALLY based on their own two operands' small self-determined
width, without ever falling through to the catch-all that might
otherwise have found a wide signal reference nested deeper in the
tree. Confirmed against Icarus (cross-engine) for `((~&fn_add8({a4,
a6, a7}, $unsigned(a5))) && {3{(~^$unsigned(a5[47:37]))}})` with `a5`
(65 bits) and `a6` (80 bits): the outer `&&`'s own two operands are
both self-determined-tiny (a reduction, and a 3-bit replication of a
reduction), so the old `&&` branch returned `False` immediately,
skipping wide-path evaluation for the whole statement even though `a5`/
`a6` are referenced deep inside `fn_add8`'s arguments -- the pure-
narrow fallback computed the `~&` reduction over only their low 64
bits, spuriously marking bits 1-2 of the result as ambiguous (`mask=
0x7` instead of the correct `0x1`). Fixed by replacing the entire
function body with `self._expr_max_internal_width(rhs) > _WORD_BITS`
-- the same recursive width-scanner already used correctly for
scratch-array sizing and the `TernaryOp`-condition fix, proven to be a
strict superset of every one of the old per-op checks (each one only
ever inspected `_expr_width` of an operand, which `_expr_max_internal_
width` already folds into its own `max(...)` at every recursion level)
plus dedicated `Concatenation`/`Replication`/`FunctionCall`-argument
cases the old checks lacked entirely. Being more inclusive than
strictly necessary is safe -- `_emit_wide_lhs_write_new`/`_emit_wide_
expr_to_scratch` already return `None` (falling through to the narrow
path, unchanged) for any node shape the wide emitter doesn't yet
support, so this can only additionally CORRECT cases that used to be
silently wrong, never regress a case that used to work.

Verified: new dedicated regression test
`test_computed_wide_function_argument_from_narrow_signals` in
`tests/test_sim/test_differential_functions.py` (confirmed to fail on
the pre-fix baseline via `git stash`, passes after). A 9-seed x
300-case sweep shows a MUCH larger improvement than any prior single
fix in this sequence: 47 total failures (the prior wave's baseline) ->
26, with every individual seed either improving or staying the same
(zero regressions; several seeds dropped from 4-8 failures down to
1-4). `test_differential.py`/`test_differential_statements.py`
(unaffected, fully green), `test_function_task.py` (29 passed),
`test_power_operator.py` (60 passed, 1 xfail), `tests/test_sim/
compiled/test_wide_ops.py` (106 passed), and a full fast-suite
regression (7895 passed, the same 16 pre-existing failures, `-n 8`,
~34 min, zero new failures) all pass.

The two audits also surfaced two further findings, deliberately left
out of the `_rhs_needs_wide_eval` fix's own scope:

**Follow-up: the function-call wide-port/return finding is now
converted from a silent wrong answer into a loud compile-time error**
(`sim/compiled/_gen_sections.py`'s `_gen_user_functions`). Investigating
it further than the initial "unconditionally broken" characterization
above found it is NOT a routing gap the way every other "wide value in
narrow context" bug this wave was -- it is a genuine architectural
limitation: the generated `_user_func_XXX` call ABI is hardcoded to a
single native `long long` per argument/return at THREE separate points
(the C function signature itself, the port-binding write inside the
function body -- `c.val[sid] = arg_i_v & wmask(w)`, a single scalar
write regardless of the port's real width -- and the return statement,
`return c.val[ret_sid] & wmask(ret_w)`). There is no multi-word
representation anywhere in this boundary for it to reach even if every
CALLER correctly computed a wide argument -- unlike the reduction/
`TernaryOp`-condition/`_rhs_needs_wide_eval` fixes earlier this wave,
which were all cases where correct wide storage/computation already
existed elsewhere and just wasn't being reached. Properly supporting a
wide port or return would mean redesigning the call ABI for multi-word
argument/return passing (pointer/array-based, touching the signature,
every call site across the narrow value emitter, narrow mask emitter,
and wide emitter, and the return-value handling) -- a substantial,
multi-file feature addition, deliberately out of scope; confirmed with
the user before proceeding. Fixed instead by detecting any function
port or return exceeding 64 bits at codegen time and raising
`NotImplementedError` with a clear message (mirroring the established
"Compiled engine: ... not yet supported ... Use engine='vm' or
engine='reference' ..." phrasing used elsewhere in this file), rather
than silently corrupting the port/return value as before. Confirmed
against Icarus (cross-engine, both a 64-bit and a 128-bit destination)
for a function with a 71-bit port: `compiled` previously gave `val=0x0,
mask=<all ones>` where reference/vm/vm-fast agreed on the correct
value; now raises immediately at `Simulator(..., engine="compiled")`
construction instead. New regression test
`test_compiled_function_wide_port_raises` in `tests/test_sim/
test_differential_functions.py` (covers both the wide-port and
wide-return cases). None of Phase 6's `FIXED_FUNCTIONS` have a port
wider than 64 bits, so this shape was never exercised by the existing
fuzzer -- a genuinely new, previously-undiscovered gap. Verified: the
other three engines (reference/vm/vm-fast) are unaffected (still
compute the wide-port case correctly, since they don't share this call
ABI); `test_differential_functions.py` (18 passed),
`test_function_task.py` (29 passed), `test_power_operator.py` (60
passed, 1 xfail), and a full fast-suite regression (7897 passed, the
same 16 pre-existing failures, `-n 8`, ~37 min, zero new failures).

**Follow-up: `_emit_binary`'s comparison/logical/bitwise dispatch now
has its own wide-detection.** Previously it relied entirely on callers
(like `_rhs_needs_wide_eval`) routing the whole statement through the
wide path first -- fixed by adding a new `_emit_wide_binary_to_value`
helper (mirroring `_emit_wide_reduction_to_value`/`_emit_wide_truthy_
to_value`'s established pattern), wired into both `_emit_binary` (VALUE
side) and `_emit_mask_expr`'s BinaryOp branch (MASK side) whenever
`op_width = max(width(left), width(right)) > 64`. Confirmed against
Icarus (cross-engine) for `fn_sel1((a5 == a6), a3)` (a wide comparison
argument to a narrow port) and `fn_sel1(a0, (a5 & a6))` (a wide bitwise
AND argument): both silently wrong on compiled, matching reference/vm/
vm-fast everywhere else.

**Eighteenth wave (August 2026): a systematic follow-up campaign found
and fixed six more distinct, confirmed compiled-engine bugs while
chasing the 26-failure residual from an ad-hoc 9-seed x 300-case sweep
down to zero (bar one deliberately-deferred item -- see below)**, per
explicit user direction to pursue every known failure rather than stop
at diminishing returns:
- **`_emit_mask_expr`'s `~`/unary-`+` fallback never sign-extended the
  operand's mask when widening a signed operand to a wider destination**
  -- unlike the VALUE side (`_emit_unary`), which already correctly
  sign-extends `operand_mask` when building its own formula. Fixed by
  mirroring that same conditional sign-extension on the mask side.
  Confirmed against Icarus for `(~a0)` with `a0` a signed 1-bit x-valued
  register assigned into a 64-bit destination: Icarus gives all 64 bits
  ambiguous; the unextended mask left only bit 0 marked ambiguous.
  Further refined once a SECOND, deeper bug in the same fallback was
  found: querying the operand's mask at its own bare self-width `ow`
  (rather than the full context `eval_width`) is only correct for a
  `_is_fixed_self_determined` operand (comparison/reduction/`!`/`&&`/
  `||`) -- for any OTHER (context-determined) operand shape, chiefly a
  TernaryOp, the VALUE side computes it directly at `eval_width` (since
  a nested context-determined sub-expression within a ternary branch
  needs that outer width to correctly compute itself), and querying the
  mask at the narrower `ow` silently understated how widely an x/z
  bit's ambiguity should have propagated. Confirmed against Icarus for
  `(~((~(!a1)) ? (-(a3 == a7)) : (~&a0)))` with `a3` (63 bits) entirely
  x and the ternary's condition definitely selecting the `-(a3 == a7)`
  branch.
- **`$unsigned(X)`'s value AND mask formulas never masked their result
  to the argument's own width** (`_emit_func_call`/`_emit_mask_expr`'s
  FunctionCall case) -- harmless for a plain leaf/self-determined
  argument (whose raw value string is already meaningfully bounded to
  its own width, upper bits 0 by convention), but wrong when the
  argument is itself a further sign-extending computation (a nested
  `$signed(...)`, whose own `_sign_ext` call deliberately fills every
  bit through the FULL native register once triggered, per `_sign_ext`'s
  own contract in `narrow_tail.pxi`, which requires the CALLER to mask
  first) -- those already-sign-filled upper bits leaked straight through
  an unmasked `$unsigned(...)` instead of being discarded as
  "reinterpret this same bit pattern as unsigned" requires. Fixed by
  adding `& wmask(arg_w)` to both formulas. Confirmed against Icarus for
  `$unsigned($signed(a4[3:0]))` with `a4[3:0] == 4'b1101`: Icarus gives
  `13` (the same bit pattern reinterpreted unsigned); the unmasked
  version leaked the inner `$signed`'s sign-extended `-3` through
  unchanged.
- **`==`/`!=`/`&&`/`||`'s genuinely-ambiguous mask case returned the raw
  `lm | rm` operand-mask pattern directly instead of collapsing it to
  the literal `1`** -- these ops are ALL self-determined to exactly 1
  bit (IEEE 1364-2005 Table 5-22), so their mask, like every other
  self-determined-1-bit operator in this file (reductions, `!`), must
  read as exactly bit-0-ambiguous when unknown; `lm | rm` instead
  returns the raw per-BIT-POSITION operand disagreement pattern (up to
  `op_width` bits wide), which then gets used DIRECTLY as the whole
  expression's own mask at whatever wider destination requested it.
  Fixed by collapsing to `1`. **A first attempt collapsed
  UNCONDITIONALLY** (`0 if known_diff else 1`), which is itself a
  regression caught by re-running the fuzzer: reaching the "not
  known_diff" branch does NOT by itself mean genuine ambiguity -- it is
  also reached when both operands are fully DEFINED and genuinely equal
  (`lm == rm == 0`), which is a definite, non-ambiguous result.
  Corrected to `0 if known_diff else (1 if (lm or rm) else 0)`.
  Confirmed against Icarus for `(a2 == a3)` with `a3` (63 bits) entirely
  x (needs the `1`-collapse) and `{2{(a0 == a0)}}` with `a0` fully
  DEFINED `0` (needs the `(lm or rm)` gate, or the unconditional-`1`
  version wrongly marks a trivially-true self-comparison as ambiguous).
  Comparable `<`/`<=`/`>`/`>=` (previously reaching a generic `lm | rm`
  fallback with the identical gap) got an explicit new branch with the
  same `(1 if (lm or rm) else 0)` form -- these have no "known bit
  differs" short-circuit (relational ordering needs every bit, not just
  A difference), so any ambiguity in either operand makes the whole
  result ambiguous.
- **The MASK side's `op_width` gate only widened for `_COMPARISON_OPS`,
  not the broader `_NATURAL_WIDTH_OPS` the VALUE side uses** -- leaving
  bitwise `&`/`|`/`^`/`~^`/`^~` computing their own operand masks at the
  OUTER context width instead of their own combined `max(left, right)`
  width. Fixed by widening the gate to `_NATURAL_WIDTH_OPS`, matching
  `_emit_binary` exactly, and adding a trailing `& wmask(op_width)` to
  the `|`/`&` mask formulas (needed once `op_width` was correctly
  narrower: an operand mask can carry garbage bits above `op_width` even
  when `op_width` already equals the operand's own width, via the same
  "`_sign_ext` doesn't self-clean" mechanism as the `~`/`$unsigned`
  fixes above). Confirmed against Icarus for `(fn_sub16s(a2[11:10], a7)
  | $signed(a3))` with `a3` (63 bits) entirely x.
- **`_emit_ternary_value_mask_exprs`'s branch-mask-width selection only
  special-cased BinaryOp arithmetic (`+ - * / %`), not the identical
  "computed directly at the outer width" property UnaryOp `-` (always)
  and `~` (when not `_is_fixed_self_determined`) share** -- so a `-`/`~`
  branch's mask was queried at the branch's own bare self-width `tw`/
  `fw` instead of the ternary's true (often much wider) combined width.
  Fixed by extending the check to those UnaryOp shapes too. Confirmed
  against Icarus for `(~((~(!a1)) ? (-(a3 == a7)) : (~&a0)))` (paired
  with the `~`-fallback `eval_width` fix above -- both were needed
  together: without this one, the ternary never even RECEIVED the wider
  width to forward into its own branch computation).
- **`_emit_unary`'s general (non-fixed-self-determined) `~`/unary-`-`
  handling redundantly re-applied `_sign_ext(operand, ow)` to a VALUE
  already fully and correctly computed at `eval_width`** -- a genuine
  no-op for a plain Identifier operand (whose own `_emit_expr` call
  already performed the identical extension internally), but actively
  WRONG for a COMPOUND operand (a further UnaryOp, BinaryOp, TernaryOp,
  etc.), whose value at `eval_width` is a genuinely COMPUTED result, not
  "a raw `ow`-bit value with unfilled upper bits" -- reinterpreting its
  own bit 0 as an unfilled sign bit to propagate corrupts an
  already-correct wide value. Fixed by restricting this redundant
  (harmless-only-for-Identifier) step to `type(expr.operand) is
  Identifier`. Confirmed against Icarus for `(-(-a0))` with `a0` a
  signed 1-bit `-1`: the inner `-a0`, computed directly at `eval_width`,
  correctly gives `1` -- but this step then re-sign-extended that
  already-clean `1` using `a0`'s own bare 1-bit self-width, spuriously
  treating its bit 0 as an unfilled sign bit and corrupting it to `-1`,
  so the outer `-` negated an already-wrong value back to `1` instead of
  correctly negating the true `1` to `-1`.
- **`_emit_user_func_call_expr` always computed each argument at the
  PORT's own declared width via the plain narrow emitter** -- correct
  for most shapes, but wrong for a context-determined arithmetic
  argument (`%`/`/` in particular, NOT "residue-safe": `(a % b) mod N !=
  ((a mod N) % (b mod N)) mod N`) whose own operand is wider than 64
  bits: computing the modulo directly at the narrow port width first
  truncates the DIVIDEND before the remainder is even determined. Fixed
  by adding a new general-purpose `_emit_wide_arg_to_value` helper
  (triggered by `_expr_max_internal_width(arg) > _WORD_BITS`, the same
  general scanner `_rhs_needs_wide_eval` uses -- broader than the three
  narrower existing wide-routing helpers, which only trigger for
  specific node shapes) and routing each function-call argument through
  it before falling back to the narrow emitter. Confirmed against
  Icarus for `fn_add8({2{{2{a0}}}}, (a6 % (a4[28:22] | 1)))` with `a6`
  80 bits bound to an 8-bit port.

New regression tests added in `tests/test_sim/test_differential_functions.py`:
`test_compiled_natural_width_op_wide_operand_mask`,
`test_compiled_nested_context_determined_operator_signedness`,
`test_compiled_wide_arithmetic_function_argument`. Verified after each
individual fix (standard suites) and via a full 9-seed x 300-case sweep
after all six: the original 26-failure baseline (before this wave)
dropped to 1 -- see "Deliberately deferred" below for that one residual
case. `test_differential.py`/`test_differential_statements.py`/
`test_function_task.py`/`test_power_operator.py`/`tests/test_sim/
compiled/test_wide_ops.py` all unaffected throughout, and a final full
fast-suite regression (7897 passed, the same 16 pre-existing failures
as every prior wave's baseline, `-n 8`, ~34 min, zero new failures).

**Follow-up: the deferred `TernaryOp` `own_signed`/`signed_override`
conflict is now resolved.** `_emit_ternary_value_mask_exprs`'s
`own_signed` (the ternary's own combined signedness, threaded into its
branches as `t_signed_override`/`f_signed_override`) previously let an
inherited `signed_override` parameter WIN over the ternary's own fresh
computation from its two branches (`own_signed = signed_override if
signed_override is not None else self._expr_signed(expr)`). Fixed by
making it ALWAYS compute fresh, UNCONDITIONALLY ignoring the inherited
override -- `own_signed = self._expr_signed(expr)` -- mirroring `sim/
evaluator.py`'s TernaryOp handling exactly ("This establishes a *fresh*
override for both branches, replacing whatever override... was active
from further out", per its own comment).

Root-caused by bisecting: the letting-override-win design was itself a
DELIBERATE, previously-confirmed fix (documented earlier in this same
section) for `(a0 <= (a2 ? a0 : a6))` (`a0`/`a6` both declared signed).
A temporary `own_signed = self._expr_signed(expr)` experiment (always
fresh) was tested against BOTH that confirmed case and the new one --
and gave the CORRECT answer for both. The two designs turn out to be
INDISTINGUISHABLE for the `(a0 <= ...)` case specifically: the
comparison's inherited override and the ternary's own fresh computation
independently evaluate to the SAME value (`True`) there, so removing
the override's influence never actually changed that case's outcome --
the letting-override-win design was not WRONG for that case, just
never actually NECESSARY for it, and happened to be actively wrong for
this new one. (First bisection attempt looked like it DIDN'T fix the
new case even with the always-fresh experiment in place -- turned out
to be the established "Compiled-engine cache collision" gotcha:
testing two different modules named `t` back-to-back in the same
Python process without clearing `.cycache` between them. Re-tested
each module in a fully separate process to confirm.)

Verified: new regression test `test_compiled_ternary_own_signed_
ignores_inherited_override` in `tests/test_sim/test_differential_
functions.py` (covers BOTH confirmed shapes -- the original
letting-override-win fix, and the case that fix's design got wrong --
to guard against a future change re-introducing either direction of
this bug; confirmed to fail without the fix via a temporary revert). A
9-seed x 300-case sweep, previously showing exactly this one residual
failure, is now FULLY GREEN across all 9 seeds (36/36 passing batches
each). `test_differential.py`/`test_differential_statements.py`/
`test_function_task.py`/`test_power_operator.py`/`test_wide_ops.py`
all unaffected, and a final full fast-suite regression (7900 passed,
the same 16 pre-existing failures as every prior wave's baseline,
`-n 8`, ~34 min, zero new failures).

Verified via all 8 expression-tree fuzzer seeds (150 cases each, 15/15
batches) and all 14 statement-fuzzer seeds (150 cases each, 8/8
batches) with `VERIFORGE_DIFF_COMPILED=1`/`VERIFORGE_DIFF_STMT_COMPILED=1`
respectively (both fuzzers unaffected by all the shared-file changes
above); all 8 seeds of the new function-call fuzzer with no compiled
(fully green) and with `VERIFORGE_DIFF_FUNC_COMPILED=1` (green except
for the known, separately-scoped gap just above); `test_function_task.py`
(29 passed, unaffected) and `test_power_operator.py` (60 passed, 1
xfail, unaffected); and a final full fast-suite regression (7122
passed, 1 xfailed, 0 failed, `-n 8`, ~31 min, no regressions).

**Nineteenth wave (August 2026): `TestWideSignalMemory`'s 12
long-standing pre-existing failures (a Cython "Converting to Python
object not allowed without gil" compile error) were a genuine
compiled-engine correctness bug, not a Cython/tooling limitation** --
root-caused and fixed while pursuing every remaining known failure per
explicit user direction, rather than continuing to carry them as an
accepted baseline. `_wide_emitter.py`'s `_emit_wide_expr_to_scratch`
BitSelect case assumed every `BitSelect` node means a genuine
single-bit select (`vec[3]`, always 1 bit, self-determined and
unsigned per IEEE 1364-2005 §5.5.1) and unconditionally called
`self._emit_expr(expr, 1)` / `self._emit_mask_expr(expr, 1)`, ANDing
the result with `1` and zero-filling every other scratch word -- but
`BitSelect` is also how the AST represents a *memory element* access
(`mem[addr]`, the whole `elem_width`-bit word, not 1 bit), which
`_expr_width` already special-cases correctly (`self._mem_info[mid][0]`,
not 1 -- see `_expr_emitter.py`) but this function never checked. For a
memory whose element width exceeds 64 bits, read combinationally into
a wide-context destination (`always @(*) data_out = mem[rd_addr];`
with `data_out`/`mem`'s elements 65/96/129 bits), this silently
extracted only BIT 0 of word 0 of the memory element and zeroed every
other word -- not merely "wrong," but discarding essentially the
entire value. The `& gil` Cython compile error was collateral damage
from a *different*, narrower code shape reached by the SAME bug (word
0's read expression resolving, via the correctly-memory-aware narrow
`_emit_expr`, to `c.mem_{mid}_val[idx]` -- a NARROW-memory-only struct
field that was never declared for a wide memory in the first place,
since wide memories only get `wide_mem_{mid}_val`/`wide_mem_{mid}_mask`
-- Cython's "Converting to Python object" error was a confusing,
misleading symptom of referencing an undeclared/mistyped field, not a
genuine GIL issue).

Fixed by special-casing memory-element access FIRST in this BitSelect
branch (via the same `_resolve_memory_element_access` helper the
narrow emitter already uses): for each scratch word, read from the
already-existing per-word helpers `_wmem{mid}_word_val`/`_wmem{mid}_
word_mask` (generated for every wide memory regardless of whether
anything else in a given design happens to use them) when the memory
itself is wide, or `c.mem_{mid}_val`/`c.mem_{mid}_mask` word 0 when
narrow, masking the tail word to the element's own remaining bits and
zero-filling (or, when `signed_override` is set, sign-extending via
the existing `_wide_sign_extend_to_dst_lines` helper) beyond the
element's own width up to the destination width -- mirroring the
proven-correct masking logic already used by `_whole_assign_mem_elem_
{mid}` (the analogous helper for continuous-assign whole-memory-element
reads, which this BitSelect path doesn't share since it's reached from
the general recursive wide-scratch emitter, not the continuous-assign-
specific compiler).

Verified: all 15 `TestWideSignalMemory` parametrized tests now pass
(12 previously failing + 3 already passing); `tests/test_sim/
compiled/test_memories.py`/`test_wide_ops.py`/`tests/test_sim/
test_memory.py` (295 passed, unaffected); and a full fast-suite
regression (7921 passed -- 15 more than the prior wave's 7906, exactly
matching the 15 tests that flipped from failing to passing -- down to
just 1 pre-existing failure, `test_or_chain_max_line_length`, an
unrelated codegen line-length formatting check; `-n 8`, ~33 min, zero
new failures). This closes 15 of the 16 failures that had been carried
as an accepted "pre-existing baseline" through this entire multi-
session bug-hunt, confirming the user's standing "pursue every known
failure" directive was warranted even for failures that had been
carried as accepted baseline for a long time.
