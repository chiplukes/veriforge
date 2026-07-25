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
