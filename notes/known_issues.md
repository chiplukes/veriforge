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

**Status**: Open — noted in `setup.py` docstring, unverified recently

The Cython VM extension (`sim/vm/_interp_fast.pyx`) has drifted from the
pure-Python interpreter and was last observed failing ~18 tests under
`tests/test_sim/test_bench_native.py` (memory read-after-write divergence).
The `vm-fast` engine silently falls back to pure Python when the extension is
not built, so environments without the built extension are unaffected.
Workarounds: set `VERIFORGE_DISABLE_CYTHON_VM=1` or delete the built
`_interp_fast.*.pyd`/`.so`. Before relying on `vm-fast` with the extension
built, re-run that test file to confirm current status.

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

**Status**: Open — root-caused and precisely characterized July 2026 (work
plan item 2.2); exercised by `tests/test_sim/test_compiled_edge_shapes.py`
("self_det_unary_*" cases, strict xfail where wrong)
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

This changes the diagnosis for **item 2.3 of `notes/plans/work_plan_2026-07.md`
("Fix the latent wide unary masking bug")**: as originally written, that
item's fix (change `wide_not`/`wide_neg` to use `op_width` instead of
`dst_width` for the tail mask) would make the compiled engine's wide-path
(>64-bit) unary emitter **match the already-broken narrow-path/reference/vm
behavior instead of fixing it** — `dst_width` is the width-correct choice.
Item 2.3 must be rewritten before it is executed; see the plan file's own
note on this item.

**Four distinct, now-precisely-characterized bugs** (all found by generating
the `self_det_unary_*_65_to_80_*` and `seam*_sh{l,r}64` cases in
`tests/test_sim/test_compiled_edge_shapes.py` and cross-checking against
Icarus/Verilator):

1. **`~` is wrongly self-determined on reference, vm, and vm-fast** (all
   widths, all three engines identically): `~a` is computed at `a`'s own
   width, then extended using `a`'s *declared signedness* — which happens
   to equal the correct context-determined result when `a` is signed (a
   coincidence: sign-extension commutes with bitwise complement) but is
   wrong when `a` is unsigned (zero-extension does not commute with
   complement — the correct result has its extension bits all-1, not
   all-0). Reproduce:
   ```python
   # module t(input [7:0] a, output [15:0] y); assign y = ~a;
   # a = 8'd1 -> reference/vm/vm-fast give y=16'h00FE (wrong); correct is 16'hFFFE.
   ```
2. **Compiled's narrow (<=64-bit) unary path has the same bug as (1)** —
   both `~` and (for reasons not yet isolated) apparently just `~`; `-` is
   correct on the narrow path at all widths/signedness tested.
3. **Compiled's wide (>64-bit) unary path ignores declared signedness
   entirely** for both `~` and `-`: it always zero-extends the operand to
   context width before applying the operator (which happens to be
   *correct* when the operand is unsigned, but wrong when it is signed —
   compiled then produces the same bit pattern as if the operand had been
   unsigned).

None of these three should be "fixed" by copying one engine's behavior to
another — (1)/(2) and (3) are independent bugs in different code paths
requiring independent fixes against the verified-correct (context-determined)
semantics above, not against each other. (A fourth, unrelated bug found by
the same test file — narrow-path shift by exactly 64 — is documented
separately below.)

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

**Status**: Open — exercised by `tests/test_sim/test_compiled_edge_shapes.py`
("seam63_shl64", "seam63_shr64", "seam64_shl64", "seam64_shr64", strict xfail)
**Found**: July 2026, work plan item 2.2 (compiled-engine edge-case suites)

`a << 64` and `a >> 64` on a <=64-bit signal (widths 63 and 64 both
affected; width 65, which uses the wide/multi-word codegen path, is not)
return `a` unchanged on the compiled engine instead of `0` (a shift amount
equal to or greater than the operand's width must produce an all-zero
result). This is the classic C/hardware undefined-behavior pattern where a
native shift instruction only consults the low log2(word-bits) bits of the
shift amount (e.g. x86 `SHL`/`SHR` use the count register's low 6 bits for
a 64-bit operand), so shifting by exactly 64 silently becomes a shift by 0.
The generated narrow-path shift code does not clamp/special-case shift
amounts >= the word width before emitting the native shift. Reference, VM,
and vm-fast are unaffected (Python's `<<`/`>>` have no such wraparound).
