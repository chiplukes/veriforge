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
  multiplication's own self-determined width is the SUM of its operand
  widths (IEEE 1364-2005 Table 5-22), correct when `*` is unconstrained —
  but when `*` is itself an operand of a further context-determined operator
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
- The narrow/scalar compiled-engine emitter (`_emit_concat`/
  `_emit_replication` in `_expr_emitter.py`) silently drops any part whose
  shift amount would reach or exceed 64 bits (`if shift >= 64: continue`)
  — correct when the ENCLOSING expression's own width is ≤64 bits (the
  normal case for this code path), but when a genuinely wide (>64-bit)
  subexpression is embedded as e.g. a ternary's CONDITION inside an
  otherwise-narrow-result context (a comparison, an arithmetic op nested
  under a narrow destination), the condition's higher-order contributions
  are silently truncated away. Did not cause an observed wrong answer in
  either wave-four fix above (the informative/nonzero bits happened to
  survive truncation in both cases) but is a latent correctness gap for
  the general case — fixing it properly likely means routing such
  subexpressions through wide scratch + `wide_logical_truth` even when the
  enclosing statement's own destination width is narrow, mirroring
  `_rhs_needs_wide_eval`'s existing statement-level "narrow result, wide
  internals" detection but applied recursively per-subexpression rather
  than only at the top level.
- `AssignmentPattern`'s theoretical `signed_override` gap (all three
  sub-branches only ever `.resize()`, never `.sign_extend()`) remains
  unfixed — deferred as low-priority/rare, not observed in fuzzer output.
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

**Newly-discovered, NOT YET FIXED, distinct bugs** (documented here rather
than rushed, since each needs its own careful root-causing/verification
pass like the ones above; do not assume these are the same shape as
anything fixed so far, INCLUDING each other):
- batch=11/case=111: `{{a2, $unsigned($unsigned(a1)), $unsigned($signed(
  a3))}, (a1[2:1] ? a3 : (~&{2{a4}})), a5}` -- as detailed above,
  `_emit_flat_concat_whole_assign` IS reached and appears structurally
  sound (correct word-splitting, correct signal extraction calls), yet the
  computed value is still wrong for some vectors. Suspect the
  "expr"-kind flat part (the ternary, which spans the 64-bit word
  boundary alongside `a5`) has an off-by-one or incorrect bit-position
  assumption in how `_concat_word_expr` shifts/masks it into word1 versus
  word0. Next step: isolate with the same bisection methodology, then
  manually verify the EXACT bit-for-bit arithmetic of the generated
  word1 expression against Icarus (do not assume; this session's earlier
  attempts to hand-verify similarly complex generated expressions were
  repeatedly wrong on the first pass).
- batch=28/case=286: `{a3[37], (!{2{a6[36]}}), (((a7 ? a1[2] : a3) ?
  (a4 ? a3[42:8] : a1) : a2[2:0]) ? (~&$signed(a5)) : $signed({3{a0}}))}`
  -- not yet root-caused; 6 of 8 vectors mismatch with a small, consistent
  bit-pattern offset between expected and got, suggestive of a
  fill-boundary or bit-position miscalculation rather than a totally
  wrong value. Not yet confirmed whether this also routes through
  `_flatten_concat_identifier_parts`/`_emit_flat_concat_whole_assign` (a
  plausible next thing to check, given case 111's shape) or a completely
  separate path.
- case 298's residual failures (vectors 0, 1, 7 of `a2 << {2{$signed((a4
  <= a6))}}`, `a6` 80 bits) are a THIRD, independently-confirmed instance
  of "a signal wider than 64 bits read through a narrow/scalar codepath
  that doesn't know it's wide" -- this time in `_emit_binary`'s signed
  comparison codegen (`_sign_ext(c.val[7], 80)` is a no-op since `_sign_
  ext` only extends when `w < 64`, so the comparison only ever sees `a6`'s
  low 64 bits), reached via the shift-amount computation
  (`_shift_amount_width`/`self._emit_expr(expr.right, ...)` in both
  `_wide_emitter.py` and `_expr_emitter.py`), which -- like the eighth
  wave's `_flatten_concat_identifier_parts` fallback -- has no
  `_expr_uses_wide_signal` guard forcing a wide-aware fallback. Fixing
  this properly likely means teaching comparison operators (and
  potentially other binary ops) reached through ANY narrow/scalar
  codepath to detect a wide operand and route through the wide comparison
  primitives (`wide_cmp_lt` etc., already correct and used by
  `_emit_wide_expr_to_scratch`'s own `_WIDE_CMP_PRIMS` handling) instead
  of silently reading `c.val[sid]`. Given this is now the THIRD
  independently-found instance of the same underlying "narrow codepath
  meets wide signal" shape (concat-flatten fallback, now fixed; shift
  amount; and likely more not yet found), a more systematic audit of
  every `self._emit_expr(...)`/`self._emit_mask_expr(...)` call site that
  ISN'T already guarded by an `_expr_uses_wide_signal` check may be more
  productive than continuing to patch each instance as the fuzzer happens
  to trip over it -- a good candidate for the next dedicated session on
  this item.

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
