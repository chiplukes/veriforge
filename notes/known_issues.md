# Known Issues

## Test suite

### test_compiled.py — runtime and cache size

**Status**: Partially addressed (May 2026)

`tests/test_sim/test_compiled.py` is the compiled-engine regression suite.

#### Test count and runtime

The bulk of the tests live in `TestWideSignalExternalIO` (~3843 parametrized
tests across wide-signal ops and values). They are tagged `@pytest.mark.slow`
and **skipped by default**. Use `--run-slow` to include them:

```
uv run pytest tests/test_sim/test_compiled.py --run-slow
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
uv run pytest tests/test_sim/test_compiled.py -n auto

# full suite with slow tests: parallel over all cores
uv run pytest tests/test_sim/test_compiled.py -n auto --run-slow
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

These were found via `tests/test_sim/test_compiled.py::TestNarrow64BitUnsignedOps`,
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

**Deferred (not fixed)**: the compiled engine's ternary/context-determined
operator codegen (`sim/compiled/_expr_emitter.py`, `_wide_emitter.py`) never
received the `signed_override`-threading fix described above for the
conditional operator — it is a separate, much larger codegen architecture
than `sim/vm/compiler.py`, and replicating the fix there is a substantial
follow-up, not yet scheduled. Running the harness with
`VERIFORGE_DIFF_COMPILED=1` will show ternary-related compiled-engine
divergences; this is why the harness's default run does not include the
compiled engine (see the module docstring in `test_differential.py`).

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

**Status**: Partially resolved. Root-caused and precisely characterized
July 2026 (work plan item 2.2); the compiled-only bug (3 below) was fixed
in item 2.3 Part A. The cross-engine bug (1/2 below, reference/vm/vm-fast)
remains open as item 2.6. Exercised by
`tests/test_sim/test_compiled_edge_shapes.py` ("self_det_unary_*" cases,
strict xfail where still wrong).
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

1. **`~` is wrongly self-determined on reference, vm, and vm-fast** for an
   *unsigned* operand (all three engines identically): `~a` is computed at
   `a`'s own width, then zero-extended — the correct result has its
   extension bits all-1, not all-0 (zero-extension does not commute with
   bitwise complement the way sign-extension does, which is why the signed
   case below happened to already be right). **Status: open — this is
   item 2.6 in `notes/plans/work_plan_2026-07.md`, not yet fixed.**
   Reproduce:
   ```python
   # module t(input [7:0] a, output [15:0] y); assign y = ~a;
   # a = 8'd1 -> reference/vm/vm-fast give y=16'h00FE (wrong); correct is 16'hFFFE.
   ```
2. **Compiled's narrow (<=64-bit) unary path has the same bug as (1)** for
   `~` on an unsigned operand; `-` is correct on the narrow path at all
   widths/signedness tested. **Status: open — also item 2.6.**
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

**Status**: Open — exercised by `tests/test_sim/test_assignment_matrix.py` (strict xfail)
**Found**: July 2026, work plan item 2.1 (cross-engine assignment-semantics matrix)

`b = a;` (blocking, in `always @(*)`) or `b <= a;` (non-blocking, in
`always @(posedge clk)`) loses the x-mask on the compiled engine whenever
**both** `a` and `b` are 64 bits or narrower (the single-word/"narrow-path"
codegen). Driving `a` with any x-contaminated bit and settling leaves `b`
fully-defined (mask forced to 0) instead of propagating the x bit(s).
Continuous assigns (`assign b = a;`) and port connections (which lower to
continuous assigns during flattening) are unaffected — only the narrow-path
procedural-assignment codegen drops the mask. Cases where either side is
wider than 64 bits are also unaffected (they use a different, correctly
mask-propagating wide-path codegen).

Reproduce:

```python
sim.drive("a", Value(0, width=8, mask=1))  # a[0] is x
sim.settle()
sim.read("b")  # compiled: 8'b00000000 (wrong); reference/vm: 8'b0000000x (correct)
```

### Compiled engine: wide-emitter sign-extension wrong for the 65->80 width pair

**Status**: Open — exercised by `tests/test_sim/test_assignment_matrix.py` (strict xfail)
**Found**: July 2026, work plan item 2.1 (cross-engine assignment-semantics matrix)

Assigning a declared-signed (or `$signed()`-cast) 65-bit value into an
80-bit target zero-extends instead of sign-extending, on the compiled
engine only, and only for this specific (65, 80) width pair — every kind
tested (continuous assign, blocking, non-blocking, port connection) is
affected. Other width pairs that also cross the 64-bit word boundary
((63,64), (64,65), (64,63), (65,64), (80,65)) sign-extend correctly, so this
looks like a narrow gap in the wide-emitter's sign-extension fill logic
specifically for a src/dst pair that both occupy two 64-bit words (65 bits:
1 full word + 1 bit; 80 bits: 1 full word + 16 bits) rather than a general
word-count issue. Not yet root-caused beyond that; likely in the same
`_wide_emitter.py` family as the wide-emitter unary masking bug above.

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
